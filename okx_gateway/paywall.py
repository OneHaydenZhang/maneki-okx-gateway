"""x402 paywall for the two paid routes + what happens when a payment settles.

Settlement is the moment money moved on X Layer, so it is also the moment we
(a) mark the payer's account paid and credit Agent Gas on the Maneki core for a
registration, or (b) attach the payment tx to the payer's newest report order.
Both are idempotent on the settlement tx hash.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI
from x402.http import PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import HTTPResponseBody, RouteConfig
from x402.mechanisms.evm.exact.server import ExactEvmScheme
from x402.server import x402ResourceServer

from . import store
from .config import Settings, settings
from .core_client import CoreClient
from .facilitator import build_facilitator, payer_of, nonce_of

REGISTER_PATH = "/okx/v1/register"
REPORT_PATH = "/okx/v1/report"


def _price(s: Settings, usd: float) -> Any:
    """'$x' works only for networks the SDK knows; testnet needs the asset spelled out."""
    from x402.mechanisms.evm import constants as c
    if s.network in c.NETWORK_CONFIGS:
        return f"${usd:.6f}".rstrip("0").rstrip(".")
    from x402.http.types import AssetAmount   # type: ignore
    a = s.asset
    return AssetAmount(amount=str(int(round(usd * (10 ** a["decimals"])))), asset=a["address"],
                       extra={"name": a["name"], "version": a["version"]})


def _unpaid(status: str, message: str):
    def body(ctx: Any) -> HTTPResponseBody:
        return HTTPResponseBody(content_type="application/json",
                                body={"status": status, "message": message})
    return body


def routes_config(s: Settings) -> Dict[str, RouteConfig]:
    common = dict(max_timeout_seconds=300)
    return {
        f"POST {REGISTER_PATH}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=s.pay_to, price=_price(s, s.price_register_usd),
                                   network=s.network, **common)],
            resource=s.public_url(REGISTER_PATH),
            description=f"ManekiAI account + {s.register_credits} Agent Gas",
            mime_type="application/json",
            unpaid_response_body=_unpaid("payment_required",
                                         f"Pay ${s.price_register_usd:g} on X Layer to open/top up a ManekiAI account "
                                         f"and receive {int(round(s.price_register_usd * 1000))} Agent Gas.")),
        f"POST {REPORT_PATH}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=s.pay_to, price=_price(s, s.price_report_usd),
                                   network=s.network, **common)],
            resource=s.public_url(REPORT_PATH),
            description="ManekiAI research report (SHA-256 anchored on X Layer)",
            mime_type="application/json",
            unpaid_response_body=_unpaid("payment_required",
                                         f"Pay ${s.price_report_usd:g} on X Layer for one research report; "
                                         "body needs symbol (and optional focus).")),
    }


def _usd_of(requirements: Any, s: Settings) -> float:
    try:
        amount = int(getattr(requirements, "amount", None) or requirements.get("amount"))
        return amount / (10 ** s.asset["decimals"])
    except Exception:
        return 0.0


def _resource_url(payload: Any) -> str:
    try:
        res = payload.resource if hasattr(payload, "resource") else payload.get("resource")
        return str(getattr(res, "url", None) or (res or {}).get("url") or "")
    except Exception:
        return ""


async def on_settled(payload: Any, requirements: Any, res: Any) -> None:
    s = settings()
    payer = (getattr(res, "payer", None) or payer_of(payload) or "").lower()
    if not payer:
        return
    tx = str(getattr(res, "transaction", None) or "").lower()
    if not tx:   # deferred schemes settle later; key on the authorization nonce instead
        tx = "nonce:" + (nonce_of(payload) or "unknown")
    url = _resource_url(payload)
    usd = _usd_of(requirements, s)
    if not store.record_settlement(tx, payer, url, usd):
        return   # replayed receipt
    if url.endswith(REGISTER_PATH) or REGISTER_PATH in url:
        credits = int(round(usd * 1000)) if usd > 0 else s.register_credits
        store.mark_paid(payer, tx, credits, usd)
        try:
            await CoreClient().credit(payer, credits, tx, usd, note=f"OKX AI x402 registration/top-up {tx[:12]}")
        except Exception as e:
            print(f"[okx-gateway] core credit failed for {payer} tx={tx}: {e!r}")
    elif url.endswith(REPORT_PATH) or REPORT_PATH in url:
        o = store.latest_pending_order(payer, "report")
        if o is None:
            # Handler already delivered (fast path) — attach to the newest order instead.
            orders = store.orders_for(payer, limit=1)
            o = orders[0] if orders else None
        if o is not None:
            store.update_order(o["order_id"], txhash=tx, settle_status="success")


def install(app: FastAPI, s: Settings | None = None) -> x402ResourceServer:
    """Adds the x402 middleware to `app`. Returns the resource server (tests)."""
    s = s or settings()
    facilitator = build_facilitator(s, on_settled)
    server = x402ResourceServer(facilitator)
    server.register(s.network, ExactEvmScheme())
    app.add_middleware(PaymentMiddlewareASGI, routes=routes_config(s), server=server)
    return server
