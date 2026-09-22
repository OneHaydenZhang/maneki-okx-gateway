"""x402 facilitator wiring.

Production: OKX's facilitator (web3.okx.com /api/v6/pay/x402) through the
official SDK client, authenticated with the developer-portal key triple.
Development: DevFacilitator accepts any well-formed EIP-3009 authorization and
"settles" it with a synthetic tx hash, so the whole register → credit → use
flow runs on a laptop with no OKX account. It is only constructed when
OKX_X402_DEV_ACCEPT=1 and never when real credentials are present.

SettleObserver wraps either client and reports every successful settlement to
the gateway (that is where Gas gets credited and orders get marked paid).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Awaitable, Callable, Optional

from x402.http.facilitator_client import (FacilitatorClient, SettleResponse, SupportedResponse,
                                          VerifyResponse)

from .config import Settings

SettledHook = Callable[[Any, Any, SettleResponse], Awaitable[None]]


def payer_of(payload: Any) -> str:
    """Payer address from a v2 payment payload (exact scheme), lowercased."""
    try:
        inner = payload.payload if hasattr(payload, "payload") else payload.get("payload")
        if isinstance(inner, dict):
            auth = inner.get("authorization") or inner.get("permit2Authorization") or {}
            frm = auth.get("from") or (auth.get("permitted") or {}).get("from") or ""
            return str(frm).lower()
    except Exception:
        pass
    return ""


def nonce_of(payload: Any) -> str:
    try:
        inner = payload.payload if hasattr(payload, "payload") else payload.get("payload")
        if isinstance(inner, dict):
            auth = inner.get("authorization") or {}
            return str(auth.get("nonce") or "")
    except Exception:
        pass
    return ""


class DevFacilitator:
    """Accept-everything facilitator for local development and tests."""

    def __init__(self, network: str):
        self.network = network
        self.settled: list[dict] = []

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse.model_validate(
            {"kinds": [{"x402Version": 2, "scheme": "exact", "network": self.network}],
             "extensions": [], "signers": {}})

    async def verify(self, payload: Any, requirements: Any) -> VerifyResponse:
        payer = payer_of(payload)
        if not payer:
            return VerifyResponse(is_valid=False, invalid_reason="invalid_signature",
                                  invalid_message="no authorization.from in payload")
        return VerifyResponse(is_valid=True, payer=payer)

    async def verify_signature(self, payload: Any, requirements: Any = None) -> VerifyResponse:
        return await self.verify(payload, requirements)

    async def settle(self, payload: Any, requirements: Any) -> SettleResponse:
        payer = payer_of(payload)
        seed = json.dumps({"n": nonce_of(payload), "p": payer}, sort_keys=True)   # replay-stable
        tx = "0xdev" + hashlib.sha256(seed.encode()).hexdigest()[:60]
        self.settled.append({"payer": payer, "tx": tx})
        return SettleResponse(success=True, status="success", payer=payer, transaction=tx,
                              network=self.network)


class SettleObserver:
    """Delegates to a real client; fires `on_settled` after a successful settle."""

    def __init__(self, inner: FacilitatorClient, on_settled: Optional[SettledHook] = None):
        self.inner = inner
        self.on_settled = on_settled

    def get_supported(self) -> SupportedResponse:
        return self.inner.get_supported()

    async def verify(self, payload: Any, requirements: Any) -> VerifyResponse:
        return await self.inner.verify(payload, requirements)

    async def verify_signature(self, payload: Any, requirements: Any = None) -> VerifyResponse:
        return await self.inner.verify_signature(payload, requirements)

    async def settle(self, payload: Any, requirements: Any) -> SettleResponse:
        res = await self.inner.settle(payload, requirements)
        if res.success and self.on_settled is not None:
            try:
                await self.on_settled(payload, requirements, res)
            except Exception as e:   # never turn a settled payment into a 402
                print(f"[okx-gateway] on_settled hook failed: {e!r}")
        return res

    async def get_settle_status(self, tx_hash: str) -> Any:   # pass-through when available
        fn = getattr(self.inner, "get_settle_status", None)
        if fn is None:
            raise AttributeError("get_settle_status")
        return await fn(tx_hash)


def build_facilitator(s: Settings, on_settled: Optional[SettledHook] = None) -> SettleObserver:
    if s.facilitator_configured:
        from x402.http import OKXAuthConfig, OKXFacilitatorClient, OKXFacilitatorConfig
        inner: Any = OKXFacilitatorClient(OKXFacilitatorConfig(
            auth=OKXAuthConfig(api_key=s.okx_api_key, secret_key=s.okx_secret_key, passphrase=s.okx_passphrase),
            base_url=s.okx_base_url, sync_settle=True, timeout=30.0))
    elif s.dev_accept:
        inner = DevFacilitator(s.network)
    else:
        raise RuntimeError("x402 facilitator not configured (set OKX_API_KEY/OKX_SECRET_KEY/OKX_PASSPHRASE, "
                           "or OKX_X402_DEV_ACCEPT=1 for local development)")
    return SettleObserver(inner, on_settled)
