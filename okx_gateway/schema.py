"""Parameter schemas the OKX client understands.

`outputSchema.input` (object map: name → {carrier, required, type, description})
is what `onchainos payment quote` parses to build its parameter plan for the
paid replay, and what a2mcp-probe reads from an input_required answer. It goes
into the 402 body of paid routes and into every 400 input_required body.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _p(typ: str, desc: str, required: bool = True) -> Dict[str, Any]:
    return {"carrier": "body", "required": required, "type": typ, "description": desc}


API_KEY = _p("string", "Your Maneki api_key from 'Maneki Account and Gas' (mk_demo = read-only demo)")
SYMBOL = _p("string", "US-stock perp ticker on Hyperliquid, e.g. NVDA, TSLA, AAPL")

INPUTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "/okx/v1/register": {"nickname": _p("string", "display name for the account", required=False)},
    "/okx/v1/analyze": {"api_key": API_KEY, "symbol": SYMBOL,
                        "question": _p("string", "what you want to know", required=False)},
    "/okx/v1/agents/create": {"api_key": API_KEY, "symbol": SYMBOL,
                              "persona": _p("string", "conservative|balanced|navigator|aggressive|extreme", False),
                              "mode": _p("string", "virtual (default) or live", False),
                              "model": _p("string", "LLM id", False),
                              "capital_max": _p("number", "USD budget, default 200", False),
                              "max_leverage": _p("integer", "default 3", False),
                              "max_ticks": _p("integer", "rounds to run, default 24", False),
                              "label": _p("string", "name for the agent", False),
                              "confirm": _p("boolean", "live only: must be true", False)},
    "/okx/v1/agents/status": {"api_key": API_KEY, "agent_id": _p("string", "one agent for details", False)},
    "/okx/v1/agents/control": {"api_key": API_KEY, "agent_id": _p("string", "the agent to control"),
                               "action": _p("string", "start|stop|close_position|add_ticks|update"),
                               "ticks": _p("integer", "for add_ticks", False)},
    "/okx/v1/authorize": {"api_key": API_KEY, "force": _p("boolean", "mint a new link even if authorized", False)},
    "/okx/v1/account": {"api_key": API_KEY, "fresh": _p("boolean", "re-check Hyperliquid", False)},
    "/okx/v1/report": {"symbol": SYMBOL, "focus": _p("string", "the question the report should answer", False),
                       "api_key": _p("string", "link the order to your account", False)},
    "/okx/v1/report/get": {"order_id": _p("string", "order id from Maneki Research Report (ord_demo = sample)"),
                           "api_key": _p("string", "optional", False)},
}


def output_schema(path: str) -> Dict[str, Any]:
    return {"method": "POST", "bodyType": "json", "input": INPUTS.get(path, {})}


def required_names(path: str) -> List[str]:
    return [k for k, v in INPUTS.get(path, {}).items() if v.get("required")]
