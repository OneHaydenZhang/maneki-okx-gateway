"""Run the gateway as its own process against a remote Maneki core.

    MANEKI_CORE_BASE=https://your-maneki MANEKI_GATEWAY_KEY=... OKX_PAY_TO=0x... \
    OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=... OKX_PUBLIC_BASE=https://gw.example \
    uvicorn okx_gateway.standalone:app --host 127.0.0.1 --port 4182

Note the core accepts gateway assertions only from loopback; a remote core
therefore needs an SSH tunnel or a same-host deployment.
"""
from __future__ import annotations

from fastapi import FastAPI

from .mount import mount

app = FastAPI(title="ManekiAI x OKX AI gateway")
mount(app)


@app.get("/")
async def root():
    return {"ok": True, "see": "/okx/v1"}
