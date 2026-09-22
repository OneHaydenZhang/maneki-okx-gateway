"""HTTP client for the Maneki core.

The gateway never imports the trading engine. It talks to a Maneki instance
over its public JSON API, asserting the user's wallet address with the shared
gateway secret (accepted by the core only from loopback — see
auto_service/auth.py::gateway_address). Every call returns the core's JSON or
raises CoreError with the core's sanitized message + log_id.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .config import settings


class CoreError(Exception):
    def __init__(self, status: int, message: str, log_id: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.log_id = log_id

    def as_dict(self) -> Dict[str, Any]:
        out = {"error": self.message, "status": self.status}
        if self.log_id:
            out["log_id"] = self.log_id
        return out


class CoreClient:
    def __init__(self, base: Optional[str] = None, key: Optional[str] = None, timeout: float = 60.0):
        s = settings()
        self.base = (base or s.core_base).rstrip("/")
        self.key = key if key is not None else s.gateway_key
        self.timeout = timeout

    def _headers(self, address: str) -> Dict[str, str]:
        return {"X-Maneki-Gateway-Key": self.key, "X-Maneki-Address": address,
                "Content-Type": "application/json"}

    async def _req(self, method: str, path: str, address: str = "", json: Any = None,
                   params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        headers = self._headers(address) if address else {"X-Maneki-Gateway-Key": self.key}
        async with httpx.AsyncClient(base_url=self.base, timeout=timeout or self.timeout) as c:
            try:
                r = await c.request(method, path, headers=headers, json=json, params=params)
            except httpx.HTTPError as e:
                raise CoreError(502, f"core unreachable: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("error") or data.get("detail") or "")
            raise CoreError(r.status_code, msg or f"core error {r.status_code}",
                            str(data.get("log_id") or "") if isinstance(data, dict) else "")
        return data

    # ---- user-scoped -------------------------------------------------------
    async def me(self, address: str) -> Dict[str, Any]:
        return await self._req("GET", "/api/auth/me", address)

    async def points(self, address: str) -> Dict[str, Any]:
        return await self._req("GET", "/api/points", address)

    async def chat(self, address: str, message: str, symbol: str = "", advice: bool = True,
                   timeout: float = 90.0) -> Dict[str, Any]:
        return await self._req("POST", "/api/chat/send", address,
                               json={"message": message, "symbol": symbol, "advice": advice}, timeout=timeout)

    async def create_agent(self, address: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return await self._req("POST", "/api/agents", address, json=fields)

    async def list_agents(self, address: str) -> Dict[str, Any]:
        return await self._req("GET", "/api/agents", address)

    async def get_agent(self, address: str, agent_id: str) -> Dict[str, Any]:
        return await self._req("GET", f"/api/agents/{agent_id}", address)

    async def agent_action(self, address: str, agent_id: str, action: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self._req("POST", f"/api/agents/{agent_id}/{action}", address, json=body or {})

    async def decisions(self, address: str, agent_id: str, limit: int = 10) -> Dict[str, Any]:
        return await self._req("GET", f"/api/agents/{agent_id}/decisions", address, params={"limit": limit})

    async def virtual_equity(self, address: str, agent_id: str, limit: int = 100) -> Dict[str, Any]:
        return await self._req("GET", f"/api/agents/{agent_id}/virtual-equity", address, params={"limit": limit})

    async def tickets(self, address: str, agent_id: str = "") -> Dict[str, Any]:
        params = {"agent": agent_id} if agent_id else None
        return await self._req("GET", "/api/tickets", address, params=params)

    # ---- gateway-only --------------------------------------------------------
    async def credit(self, address: str, credits: int, txhash: str, usd: float, note: str = "") -> Dict[str, Any]:
        return await self._req("POST", "/api/gateway/credit", address,
                               json={"address": address, "credits": credits, "txhash": txhash,
                                     "usd": usd, "note": note, "token": "x402", "chain": "XLAYER"})

    # ---- public --------------------------------------------------------------
    async def us_stocks(self) -> Any:
        return await self._req("GET", "/api/us-stocks")

    async def version(self) -> Dict[str, Any]:
        return await self._req("GET", "/api/version")
