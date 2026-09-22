"""Attach the gateway to a Maneki FastAPI app (auto_service/app.py).

Only called when OKX_GATEWAY_ENABLED=1. Adds the /okx/v1/* router and the x402
middleware; nothing else in the host app changes.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import paywall
from .config import settings
from .router import router


def mount(app: FastAPI) -> None:
    s = settings()
    for p in s.problems():
        print(f"[okx-gateway] WARNING: {p}")
    app.include_router(router)
    try:
        paywall.install(app, s)
        print(f"[okx-gateway] mounted: network={s.network} pay_to={s.pay_to or '(unset)'} "
              f"public={s.public_base} facilitator={'okx' if s.facilitator_configured else ('dev' if s.dev_accept else 'none')}")
    except RuntimeError as e:
        # Free routes still work; paid routes answer 503 from the handlers.
        print(f"[okx-gateway] paid routes disabled: {e}")
