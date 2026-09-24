"""x402 paywall for the two paid routes + what happens when a payment settles.

Settlement is the moment money moved on X Layer, so it is also the moment we
(a) mark the payer's account paid and credit Agent Gas on the Maneki core for a
registration, or (b) attach the payment tx to the payer's newest report order.
Both are idempotent on the settlement tx hash.
"""
from __future__ import annotations

import json
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


def _unpaid(status: str, message: str, path: str):
    """402 body: besides the human message, the parameter schema the OKX client
    needs to carry parameters on the paid replay (`outputSchema.input`)."""
    from . import schema
    def body(ctx: Any) -> HTTPResponseBody:
        return HTTPResponseBody(content_type="application/json",
                                body={"status": status, "message": message,
                                      "outputSchema": schema.output_schema(path),
                                      "required": schema.required_names(path)})
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
                                         f"and receive {int(round(s.price_register_usd * 1000))} Agent Gas.", REGISTER_PATH)),
        f"POST {REPORT_PATH}": RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=s.pay_to, price=_price(s, s.price_report_usd),
                                   network=s.network, **common)],
            resource=s.public_url(REPORT_PATH),
            description="ManekiAI research report (SHA-256 anchored on X Layer)",
            mime_type="application/json",
            unpaid_response_body=_unpaid("payment_required",
                                         f"Pay ${s.price_report_usd:g} on X Layer for one research report; "
                                         "body needs symbol (and optional focus).", REPORT_PATH)),
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


async def effective_address_of(payer: str) -> str:
    """Where this payer's Gas lives right now, asked of the core (link record),
    falling back to the gateway's cached column, then the payer itself."""
    try:
        st = await CoreClient().link_status(payer, light=True)
        if st.get("linked") and st.get("wallet"):
            return str(st["wallet"]).lower()
        return payer
    except Exception:
        acct = store.account_for_payer(payer)
        return store.effective_address(acct) if acct else payer


async def retry_pending_credits(payer: str = "") -> int:
    """Re-attempt core credits that failed at settlement time. The core is
    idempotent on txhash, so a retry can never double-credit. Returns the
    number of settlements credited on this pass."""
    done = 0
    for row in store.pending_credits(payer):
        target = row.get("target") or await effective_address_of(row["payer"])
        try:
            await CoreClient().credit(target, int(row["credits"]), row["txhash"], float(row["usd"]),
                                      note=f"OKX AI x402 registration/top-up {row['txhash'][:12]} (retry)")
            store.mark_credited(row["txhash"])
            done += 1
        except Exception as e:
            print(f"[okx-gateway] credit retry failed for {row['payer']} tx={row['txhash']}: {e!r}")
    return done


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
    if url.endswith(REGISTER_PATH) or REGISTER_PATH in url:
        credits = int(round(usd * 1000)) if usd > 0 else s.register_credits
        target = await effective_address_of(payer)
        if not store.record_settlement(tx, payer, url, usd, credits=credits, target=target):
            return   # replayed receipt (a still-pending one is retried by retry_pending_credits)
        acct = store.mark_paid(payer, tx, credits, usd)
        if target != store.effective_address(acct):
            store.set_address(payer, target)
        try:
            await CoreClient().credit(target, credits, tx, usd, note=f"OKX AI x402 registration/top-up {tx[:12]}")
            store.mark_credited(tx)
        except Exception as e:
            # Money is in; the credit is owed. Left pending → retried on the
            # payer's next call (and by anyone hitting /account).
            print(f"[okx-gateway] core credit failed for {payer} tx={tx}: {e!r} — queued for retry")
        return
    if not store.record_settlement(tx, payer, url, usd):
        return   # replayed receipt
    if url.endswith(REPORT_PATH) or REPORT_PATH in url:
        o = store.latest_pending_order(payer, "report")
        if o is None:
            # Handler already delivered (fast path) — attach to the newest order instead.
            orders = store.orders_for(payer, limit=1)
            o = orders[0] if orders else None
        if o is not None:
            store.update_order(o["order_id"], txhash=tx, settle_status="success")


class ParamPrecheck:
    """Pure-ASGI middleware that runs BEFORE the paywall: a paid route whose
    required parameters are missing answers 400 input_required right away, so
    nobody signs a payment for a call that could never be delivered (OKX
    listing rule: validate before returning the x402 challenge)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in (REPORT_PATH, REGISTER_PATH):
            return await self.app(scope, receive, send)
        chunks = []
        while True:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        raw = b"".join(chunks)
        from .router import parse_params, precheck_paid
        params = parse_params(raw, scope)
        problem = precheck_paid(scope["path"], params)
        if problem is not None:
            body = json.dumps(problem).encode()
            await send({"type": "http.response.start", "status": 400,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        sent = {"done": False}
        async def replay():
            if sent["done"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return await self.app(scope, replay, send)


def install(app: FastAPI, s: Settings | None = None) -> x402ResourceServer:
    """Adds the x402 middleware (and the pre-paywall parameter check) to `app`.
    Returns the resource server (tests)."""
    s = s or settings()
    facilitator = build_facilitator(s, on_settled)
    server = x402ResourceServer(facilitator)
    server.register(s.network, ExactEvmScheme())
    app.add_middleware(PaymentMiddlewareASGI, routes=routes_config(s), server=server)
    app.add_middleware(ParamPrecheck)   # added last = outermost = runs first
    return server
