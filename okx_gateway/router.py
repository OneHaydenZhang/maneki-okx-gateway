"""The A2MCP endpoints.

Conventions the OKX probe understands (onchainos a2mcp-probe):
  * POST with a JSON body; every parameter rides in the body.
  * A missing parameter is answered with HTTP 400 and
    {"status": "input_required", "fields": [...], "message": "..."} — the
    user's agent then collects the fields and retries.
  * Paid routes (/register, /report) are behind the x402 middleware; by the
    time a handler runs, request.state.payment_payload is a verified payment.
  * We never answer HTTP 402 ourselves for business conditions (e.g. not
    enough Gas) — that status belongs to x402. Such conditions come back as
    200 with a "status" the agent can read out to the user.
  * Results are data. Any URL we return is for the human to open.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import listing, store, xlayer_anchor
from .config import settings
from .core_client import CoreClient, CoreError
from .facilitator import payer_of

router = APIRouter(prefix="/okx/v1", tags=["okx-gateway"])

_TICKER = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")
PERSONAS = ("conservative", "balanced", "navigator", "aggressive", "extreme", "custom")
ACTIONS = ("start", "stop", "close_position", "add_ticks")


# ---- helpers ---------------------------------------------------------------------

def _core() -> CoreClient:
    return CoreClient()


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _field(name: str, typ: str, desc: str, required: bool = True) -> Dict[str, Any]:
    return {"name": name, "type": typ, "required": required, "carrier": "body", "description": desc}


def _input_required(fields: List[Dict[str, Any]], message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"status": "input_required", "fields": fields,
                                                  "message": message, "method": "POST"})


def _err(status: int, message: str, **extra: Any) -> JSONResponse:
    # 402 is reserved for x402; a core 402 (not enough Gas) becomes a readable 200.
    if status == 402:
        return JSONResponse(status_code=200, content={"status": "insufficient_gas", "message": message,
                                                      "how_to_top_up": "Call 'Maneki Account and Gas' again "
                                                      "(each payment credits more Gas).", **extra})
    return JSONResponse(status_code=status if status in (400, 403, 404, 409, 429) else 502,
                        content={"status": "error", "error": message, **extra})


def _core_err(e: CoreError) -> JSONResponse:
    extra = {"log_id": e.log_id} if e.log_id else {}
    return _err(e.status, e.message, **extra)


def _payer(request: Request) -> str:
    """Verified payer for a paid route (set by the x402 middleware), else ''."""
    pp = getattr(request.state, "payment_payload", None)
    if pp is not None:
        return payer_of(pp)
    return ""


def _symbol(raw: Any) -> str:
    sym = str(raw or "").strip().upper()
    if sym.startswith("XYZ:"):
        sym = sym[4:]
    return sym if _TICKER.match(sym) else ""


def _account(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    acct = store.account_for_key(str(body.get("api_key") or ""))
    if acct and acct.get("status") == "paid":
        return acct
    return None


def _need_account(body: Dict[str, Any]):
    if not str(body.get("api_key") or "").strip():
        return _input_required([_field("api_key", "string", "Your Maneki api_key (from 'Maneki Account and Gas')")],
                               "api_key is required. Register first with the 'Maneki Account and Gas' service.")
    acct = _account(body)
    if acct is None:
        pending = store.account_for_key(str(body.get("api_key") or ""))
        if pending is not None:
            return JSONResponse(status_code=403, content={
                "status": "payment_pending",
                "error": "this api_key's registration payment has not settled yet — retry in a few seconds"})
        return JSONResponse(status_code=403, content={"status": "error", "error": "unknown api_key"})
    return acct


def _agent_brief(a: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("agent_id", "label", "symbol", "persona", "model", "mode", "virtual", "status", "running",
            "interval_s", "max_ticks", "tick_count", "max_leverage", "capital_max", "stop_loss_pct",
            "last_action", "last_confidence", "estimated_profit", "trade_volume", "closed_trades", "win_rate",
            "next_tick_in_s", "created_at")
    return {k: a.get(k) for k in keys if k in a}


def _decision_brief(d: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("ts", "tick", "action", "confidence", "reasoning", "reasoning_zh", "size_usd", "leverage",
            "executed", "mark", "mid", "note")
    return {k: d.get(k) for k in keys if k in d}


def _chat_unusable(data: Dict[str, Any]) -> bool:
    """True when the core answered with a placeholder (model busy / not ready /
    rate-limited): nothing was delivered and nothing was billed."""
    st = data.get("structured") or {}
    return not (st.get("points") or st.get("analysis"))


def _point_text(p: Any) -> str:
    if isinstance(p, dict):
        label, text = p.get("label"), p.get("text")
        return f"**{label}** — {text}" if label else str(text or "")
    return str(p)


def _dashboard(path: str = "#agent") -> str:
    return f"{settings().dashboard_base}/{path}"


# ---- info ------------------------------------------------------------------------

@router.get("")
@router.get("/")
async def info() -> Dict[str, Any]:
    s = settings()
    return {
        "service": "ManekiAI x OKX AI gateway", "version": "0.1.0",
        "network": s.network, "pay_to": s.pay_to,
        "prices_usd": {"register": s.price_register_usd, "report": s.price_report_usd},
        "register_credits": s.register_credits,
        "paid_endpoints": ["/okx/v1/register", "/okx/v1/report"],
        "free_endpoints": ["/okx/v1/analyze", "/okx/v1/agents/create", "/okx/v1/agents/status",
                           "/okx/v1/agents/control", "/okx/v1/report/get", "/okx/v1/symbols"],
        "listing": s.public_url("/okx/v1/listing"),
        "anchor": xlayer_anchor.anchor_status(),
        "dev_accept": s.dev_accept,
    }


@router.get("/listing")
async def listing_json() -> Dict[str, Any]:
    return {"agent_name": listing.AGENT_NAME, "agent_description": listing.AGENT_DESCRIPTION,
            "services": listing.services()}


@router.get("/symbols")
async def symbols() -> Any:
    try:
        data = await _core().us_stocks()
    except CoreError as e:
        return _core_err(e)
    return data


# ---- paid: register / top-up -------------------------------------------------------

@router.post("/register")
async def register(request: Request):
    s = settings()
    payer = _payer(request)
    if not payer:
        # Only reachable when the middleware is not installed (misconfiguration).
        return JSONResponse(status_code=503, content={"status": "error",
                                                      "error": "payment layer not active on this route"})
    body = await _body(request)
    acct = store.ensure_account(payer)
    first = acct.get("status") != "paid"
    nickname = str(body.get("nickname") or "").strip()[:40]
    out = {
        "status": "registered" if first else "topped_up",
        "account": payer,
        "api_key": acct["api_key"],
        "credits_on_settlement": int(round(s.price_register_usd * 1000)),
        "note": (f"{int(round(s.price_register_usd * 1000))} Agent Gas is credited the moment this payment settles on "
                 f"X Layer (seconds). Keep api_key: every other Maneki service needs it."),
        "next_steps": [
            "Maneki Market Analysis: {\"api_key\": ..., \"symbol\": \"NVDA\"}",
            "Maneki Virtual Trading Agent: {\"api_key\": ..., \"symbol\": \"NVDA\", \"persona\": \"navigator\"}",
        ],
        "dashboard_url": _dashboard("#agent"),
    }
    if nickname:
        out["nickname"] = nickname
    return out


# ---- free: analysis ------------------------------------------------------------------

@router.post("/analyze")
async def analyze(request: Request):
    body = await _body(request)
    acct = _need_account(body)
    if isinstance(acct, JSONResponse):
        return acct
    sym = _symbol(body.get("symbol"))
    if not sym:
        return _input_required([_field("symbol", "string", "US-stock perp ticker, e.g. NVDA, TSLA, AAPL")],
                               "symbol is required (a Hyperliquid US-stock perp ticker such as NVDA).")
    question = str(body.get("question") or "").strip()[:500]
    message = question or f"Give me a structured market read on {sym} with a concrete trade idea."
    try:
        data = await _core().chat(acct["payer"], message, symbol=sym, advice=True)
    except CoreError as e:
        return _core_err(e)
    if data.get("insufficient_credits"):
        return _err(402, (data.get("structured") or {}).get("headline") or "not enough Gas")
    st = data.get("structured") or {}
    sug = data.get("suggestion") or {}
    if _chat_unusable(data):
        return {"status": "busy", "symbol": sym,
                "message": st.get("headline") or "model busy — nothing was charged; retry in a few seconds"}
    return {
        "status": "ok", "symbol": sym, "asked": message,
        "headline": st.get("headline"), "points": [_point_text(p) for p in (st.get("points") or [])],
        "analysis": st.get("analysis"),
        "note": st.get("note"),
        "trade_idea": {k: sug.get(k) for k in ("has_trade_idea", "side", "size_usd", "leverage", "stop_loss",
                                               "take_profit", "confidence", "rationale", "mark")},
        "gas_cost": 8, "ts": data.get("ts") or time.time(),
        "disclaimer": "Analysis, not advice. Virtual agents simulate fills; nothing here places a real order.",
    }


# ---- free: agents ---------------------------------------------------------------------

@router.post("/agents/create")
async def agents_create(request: Request):
    s = settings()
    body = await _body(request)
    acct = _need_account(body)
    if isinstance(acct, JSONResponse):
        return acct
    sym = _symbol(body.get("symbol"))
    if not sym:
        return _input_required([_field("symbol", "string", "US-stock perp ticker, e.g. NVDA")],
                               "symbol is required.")
    persona = str(body.get("persona") or s.default_persona).strip().lower()
    if persona not in PERSONAS:
        return _input_required([_field("persona", "string", "one of " + "|".join(PERSONAS))],
                               f"persona must be one of {', '.join(PERSONAS)}.")
    fields: Dict[str, Any] = {
        "symbol": f"xyz:{sym}",
        "label": str(body.get("label") or f"OKX {sym} {persona}")[:40],
        "persona": persona,
        "custom_prompt": str(body.get("custom_prompt") or "")[:4000] if persona == "custom" else "",
        "model": str(body.get("model") or s.default_model),
        "interval_s": int(body.get("interval_s") or s.default_interval_s),
        "max_ticks": int(body.get("max_ticks") if body.get("max_ticks") is not None else s.default_max_ticks),
        "max_leverage": int(body.get("max_leverage") or s.default_max_leverage),
        "capital_mode": "range",
        "capital_max": float(body.get("capital_max") or s.default_capital_max),
        "margin_mode": "cross",
        "dry_run": 0,
        "mode": "paper",          # virtual: decides, simulates fills, never orders
        "start": True,
    }
    if body.get("stop_loss_pct") is not None:
        fields["stop_loss_pct"] = float(body.get("stop_loss_pct"))
    try:
        data = await _core().create_agent(acct["payer"], fields)
    except CoreError as e:
        return _core_err(e)
    agent = data.get("agent") or {}
    out = {"status": "created", "agent": _agent_brief(agent),
           "how_it_works": ("Every round the agent reads live Hyperliquid market data, decides with its persona under "
                            "code-enforced leverage/notional/stop-loss limits, and simulates the fill. Use 'Maneki "
                            "Agent Status' with this agent_id to follow its reasoning."),
           "dashboard_url": _dashboard("#agent")}
    if data.get("start_error"):
        out["status"] = "created_not_started"
        out["start_error"] = data["start_error"]
    return out


@router.post("/agents/status")
async def agents_status(request: Request):
    body = await _body(request)
    acct = _need_account(body)
    if isinstance(acct, JSONResponse):
        return acct
    addr = acct["payer"]
    agent_id = str(body.get("agent_id") or "").strip()
    core = _core()
    try:
        if not agent_id:
            data = await core.list_agents(addr)
            pts = await core.points(addr)
            return {"status": "ok", "gas_balance": pts.get("balance"),
                    "agents": [_agent_brief(a) for a in data.get("agents") or []],
                    "dashboard_url": _dashboard("#agent")}
        a = (await core.get_agent(addr, agent_id)).get("agent") or {}
        dec = (await core.decisions(addr, agent_id, limit=int(body.get("decisions") or 5))).get("decisions") or []
        eq = await core.virtual_equity(addr, agent_id, limit=50)
        pts = await core.points(addr)
    except CoreError as e:
        return _core_err(e)
    latest = eq.get("latest") or {}
    return {"status": "ok", "agent": _agent_brief(a),
            "virtual_equity": {"latest": latest, "points": (eq.get("points") or [])[-12:]},
            "recent_decisions": [_decision_brief(d) for d in dec],
            "gas_balance": pts.get("balance"),
            "dashboard_url": _dashboard("#agent")}


@router.post("/agents/control")
async def agents_control(request: Request):
    body = await _body(request)
    acct = _need_account(body)
    if isinstance(acct, JSONResponse):
        return acct
    agent_id = str(body.get("agent_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    missing = []
    if not agent_id:
        missing.append(_field("agent_id", "string", "the agent to control"))
    if action not in ACTIONS:
        missing.append(_field("action", "string", "one of " + "|".join(ACTIONS)))
    if missing:
        return _input_required(missing, "agent_id and action (start|stop|close_position|add_ticks) are required.")
    payload: Dict[str, Any] = {}
    core_action = {"start": "start", "stop": "stop", "close_position": "close-position", "add_ticks": "add-ticks"}[action]
    if action == "add_ticks":
        payload = {"ticks": int(body.get("ticks") or 12)}
    if action == "close_position":
        payload = {"origin": "okx_gateway"}
    try:
        data = await _core().agent_action(acct["payer"], agent_id, core_action, payload)
    except CoreError as e:
        return _core_err(e)
    out: Dict[str, Any] = {"status": "ok", "action": action}
    if isinstance(data, dict):
        if data.get("agent"):
            out["agent"] = _agent_brief(data["agent"])
        else:
            out["result"] = data
    return out


# ---- paid: research report ------------------------------------------------------------

REPORT_PROMPT = (
    "Write a professional research report on {symbol} perpetual (Hyperliquid xyz dex) for a trader. "
    "Focus: {focus}. Cover: 1) market structure and recent price action, 2) funding and open interest read, "
    "3) momentum/volatility regime, 4) bull and bear scenarios with levels, 5) a concrete plan: side, entry zone, "
    "invalidation, targets, position sizing at modest leverage, 6) key risks and what would change the view. "
    "Be specific and numeric where the data allows; say clearly what is uncertain."
)


def _report_markdown(symbol: str, focus: str, data: Dict[str, Any], order_id: str) -> str:
    st = data.get("structured") or {}
    sug = data.get("suggestion") or {}
    lines = [f"# ManekiAI Research Report — {symbol}", "",
             f"Order: {order_id}  ·  Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             f"Focus: {focus or 'general outlook'}", ""]
    if st.get("headline"):
        lines += ["## Summary", st["headline"], ""]
    if st.get("points"):
        lines += ["## Key points"] + [f"- {_point_text(p)}" for p in st["points"]] + [""]
    if st.get("analysis"):
        lines += ["## Analysis", str(st["analysis"]), ""]
    if sug.get("has_trade_idea"):
        lines += ["## Plan",
                  f"- Side: {sug.get('side')}  ·  Size: ${sug.get('size_usd')}  ·  Leverage: {sug.get('leverage')}x",
                  f"- Stop loss: {sug.get('stop_loss')}  ·  Take profit: {sug.get('take_profit')}",
                  f"- Confidence: {sug.get('confidence')}",
                  f"- Rationale: {sug.get('rationale')}", ""]
    if st.get("note"):
        lines += ["## Note", str(st["note"]), ""]
    lines += ["---", "Not financial advice. Virtual-agent research; no order is placed by this report."]
    return "\n".join(lines)


async def _generate_report(order: Dict[str, Any], address: str) -> Dict[str, Any]:
    symbol, focus, oid = order["symbol"], order["focus"], order["order_id"]
    store.update_order(oid, status="generating")
    try:
        data = await _core().chat(address, REPORT_PROMPT.format(symbol=symbol, focus=focus or "general outlook"),
                                  symbol=symbol, advice=True, timeout=120.0)
    except CoreError as e:
        store.update_order(oid, status="failed", error=e.message[:300])
        raise
    if data.get("insufficient_credits"):
        # The report itself was paid via x402; core chat billing must not block it.
        # Runs against the gateway's own service account in that case.
        store.update_order(oid, status="failed", error="core refused: insufficient gas on service account")
        raise CoreError(502, "report generation unavailable (service account out of Gas)")
    if _chat_unusable(data):
        store.update_order(oid, status="failed", error="model busy")
        raise CoreError(503, "model busy — the order is kept; retrieve it again in a minute (no new payment)")
    md = _report_markdown(symbol, focus, data, oid)
    sha = xlayer_anchor.digest(md)
    order = store.update_order(oid, status="delivered", report_md=md, sha256=sha, delivered_at=time.time(),
                               anchor_status="pending" if settings().anchor_key else "skipped") or order
    if settings().anchor_key:
        asyncio.get_running_loop().create_task(_anchor(oid, sha))
    return order


async def _anchor(order_id: str, sha: str) -> None:
    try:
        res = await asyncio.to_thread(xlayer_anchor.anchor_hash, sha)
        store.update_order(order_id, anchor_tx=res["tx"], anchor_status="anchored")
    except Exception as e:
        store.update_order(order_id, anchor_status="failed", error=f"anchor: {e!r}"[:300])


def _order_view(o: Dict[str, Any], include_text: bool = True) -> Dict[str, Any]:
    s = settings()
    out = {"order_id": o["order_id"], "status": o["status"], "symbol": o["symbol"], "focus": o["focus"],
           "usd": o["usd"], "created_at": o["created_at"], "delivered_at": o.get("delivered_at"),
           "sha256": o.get("sha256") or None,
           "anchor": {"status": o.get("anchor_status") or "skipped", "tx": o.get("anchor_tx") or None,
                      "explorer_url": (s.explorer_tx + o["anchor_tx"]) if o.get("anchor_tx") else None,
                      "network": s.network},
           "verify_url": s.public_url(f"/okx/v1/report/{o['order_id']}/verify"),
           "payment_tx": o.get("txhash") or None}
    if o.get("error"):
        out["error"] = o["error"]
    if include_text and o.get("report_md"):
        out["report_markdown"] = o["report_md"]
    return out


@router.post("/report")
async def report(request: Request):
    s = settings()
    payer = _payer(request)
    if not payer:
        return JSONResponse(status_code=503, content={"status": "error",
                                                      "error": "payment layer not active on this route"})
    body = await _body(request)
    sym = _symbol(body.get("symbol"))
    if not sym:
        return _input_required([_field("symbol", "string", "US-stock perp ticker, e.g. NVDA"),
                                _field("focus", "string", "what the report should answer", required=False)],
                               "symbol is required.")
    focus = str(body.get("focus") or "").strip()[:300]
    # Whose Maneki account generates it: the api_key's if given, else the payer's
    # own (auto-created; the x402 payment identifies them).
    acct = _account(body) if body.get("api_key") else None
    address = acct["payer"] if acct else payer
    order = store.create_order(payer, "report", sym, focus, s.price_report_usd)
    try:
        done = await asyncio.wait_for(asyncio.shield(_generate_report(order, address)), timeout=s.report_inline_budget_s)
    except asyncio.TimeoutError:
        view = _order_view(store.get_order(order["order_id"]) or order, include_text=False)
        view.update({"status": "generating",
                     "note": "Still generating — fetch it in a minute with 'Maneki Report Retrieval' using this order_id. "
                             "Already paid; retrieval is free."})
        return view
    except CoreError as e:
        # Paid but not delivered: keep the order, tell the user how to recover, never charge again.
        view = _order_view(store.get_order(order["order_id"]) or order, include_text=False)
        view.update({"status": "failed", "recovery": "Retry 'Maneki Report Retrieval' with this order_id; "
                                                    "the order is kept and will be re-generated without a new payment."})
        view["error"] = e.message
        return view
    return _order_view(done)


@router.post("/report/get")
async def report_get(request: Request):
    body = await _body(request)
    oid = str(body.get("order_id") or "").strip()
    if not oid:
        return _input_required([_field("order_id", "string", "the order id returned by Maneki Research Report")],
                               "order_id is required.")
    o = store.get_order(oid)
    if not o:
        return JSONResponse(status_code=404, content={"status": "error", "error": "unknown order_id"})
    if o["status"] in ("failed", "pending"):
        # Free re-delivery of a paid-but-undelivered order.
        acct = _account(body) if body.get("api_key") else None
        try:
            o = await asyncio.wait_for(asyncio.shield(_generate_report(o, acct["payer"] if acct else o["payer"])),
                                       timeout=settings().report_inline_budget_s)
        except asyncio.TimeoutError:
            o = store.get_order(oid) or o
        except CoreError:
            o = store.get_order(oid) or o
    return _order_view(o)


@router.get("/report/{order_id}/verify")
async def report_verify(order_id: str) -> Any:
    o = store.get_order(order_id)
    if not o or o["status"] != "delivered":
        return JSONResponse(status_code=404, content={"status": "error", "error": "no delivered report for this id"})
    view = _order_view(o, include_text=False)
    view["how_to_verify"] = ("sha256 of the report text (CRLF→LF, trailing whitespace stripped, then stripped) must "
                             "equal `sha256`; the X Layer transaction `anchor.tx` carries the same 32 bytes as calldata.")
    if o.get("anchor_tx"):
        onchain = await asyncio.to_thread(xlayer_anchor.read_anchor, o["anchor_tx"])
        view["onchain_calldata"] = onchain
        view["matches_chain"] = bool(onchain and onchain.lower() == (o.get("sha256") or "").lower())
    return view
