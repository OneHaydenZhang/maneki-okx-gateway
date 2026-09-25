"""The ASP listing: the exact `--service` JSON array for
`onchainos agent create --role asp ... --service '<json>'`.

OKX validates each A2MCP entry's description as four numbered lines —
[Service Description] / [Parameter Spec] / [Request Method] / [Request Example]
— and parses the curl in line 4 to decide GET vs POST, so the curl must target
the real public endpoint. Everything is generated from settings so the listing
can never drift from the routes.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .config import settings

AGENT_NAME = "ManekiAI"
AGENT_DESCRIPTION = ("Autonomous Hyperliquid perp trading agents you can hire from your own AI: "
                     "one-off market analysis, 24/7 virtual trading agents with explainable decisions, "
                     "and paid research reports whose digest is anchored on X Layer.")


def _curl(path: str, body: Dict[str, Any]) -> str:
    s = settings()
    return (f"curl -X POST {s.public_url(path)} -H 'Content-Type: application/json' "
            f"-d '{json.dumps(body, separators=(',', ':'))}'")


ERRORS = ("Errors: missing or invalid parameters return HTTP 400 with status=input_required and a fields list "
          "(never after payment); other failures return status=error with a readable message. "
          "api_key mk_demo tries the service read-only.")


def _desc(summary: str, params: str, path: str, example: Dict[str, Any]) -> str:
    return "\n".join([
        f"1. [Service Description] {summary} {ERRORS}",
        f"2. [Parameter Spec] {params}",
        "3. [Request Method] POST",
        f"4. [Request Example] {_curl(path, example)}",
    ])


def services() -> List[Dict[str, Any]]:
    s = settings()
    fee_reg = f"{s.price_register_usd:g}"
    fee_rep = f"{s.price_report_usd:g}"
    return [
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Account and Gas",
            "fee": fee_reg,
            "endpoint": s.public_url("/okx/v1/register"),
            "serviceDescription": _desc(
                f"Opens (or tops up) your ManekiAI account with the paying wallet as identity and credits "
                f"{s.register_credits} Agent Gas per ${s.price_register_usd:g} paid. Returns the api_key every other "
                f"Maneki tool needs. Call again any time to buy more Gas.",
                "nickname(string, optional): display name for the account",
                "/okx/v1/register", {"nickname": "my-okx-agent"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Market Analysis",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/analyze"),
            "serviceDescription": _desc(
                "Structured analysis of a Hyperliquid US-stock perp (xyz dex): headline, key points, trade idea with "
                "side, size, leverage, stop and take-profit, and confidence. Costs 8 Agent Gas from your Maneki balance. "
                "If the reply is status=generating, call again with the returned analysis_id after about 10 seconds.",
                "api_key(string, required): from Maneki Account and Gas, or mk_demo; symbol(string, required): ticker chosen "
                "by the user such as NVDA, TSLA, AAPL; question(string, optional): what you want to know; "
                "analysis_id(string, optional): to collect a result that was still generating",
                "/okx/v1/analyze", {"api_key": "mk_demo", "symbol": "NVDA", "question": "Is momentum still intact?"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Virtual Trading Agent",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/agents/create"),
            "serviceDescription": _desc(
                "Creates and starts a Maneki virtual trading agent on a US-stock perp: it decides every round with real "
                "market data and code-enforced risk limits, simulates fills, and records every reasoning. Never touches an "
                "exchange. Each decision round costs Agent Gas (18 to 88 depending on model).",
                "api_key(string, required); symbol(string, required): ticker; persona(string, optional): conservative|"
                "balanced|navigator|aggressive|extreme; mode(string, optional): virtual (default) or live (needs Maneki "
                "Live Authorization + confirm=true); model(string, optional); capital_max(number, optional): USD budget, "
                "default 200; max_leverage(integer, optional): default 3; max_ticks(integer, optional): rounds to run, "
                "default 24; label(string, optional)",
                "/okx/v1/agents/create", {"api_key": "mk_demo", "symbol": "NVDA", "persona": "navigator", "capital_max": 200}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Agent Status",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/agents/status"),
            "serviceDescription": _desc(
                "Lists your Maneki agents, or for one agent returns status, virtual equity, open position, trade "
                "records, the latest decisions with reasoning, and Gas balance.",
                "api_key(string, required); agent_id(string, optional): one agent's id for details",
                "/okx/v1/agents/status", {"api_key": "mk_demo"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Agent Control",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/agents/control"),
            "serviceDescription": _desc(
                "Start, stop, extend, update the strategy parameters of, or close the position of one of your Maneki agents.",
                "api_key(string, required); agent_id(string, required); action(string, required): start|stop|"
                "close_position|add_ticks|update; ticks(integer, optional): rounds to add for add_ticks; for update: "
                "persona, model, interval_s, max_ticks, max_leverage, capital_max, stop_loss_pct, custom_prompt, label "
                "(any subset)",
                "/okx/v1/agents/control", {"api_key": "mk_demo", "agent_id": "ag_demo", "action": "stop"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Live Authorization",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/authorize"),
            "serviceDescription": _desc(
                "Returns a one-time link the user opens in a browser to connect the wallet whose real Hyperliquid "
                "account Maneki may trade, sign in (gasless) and approve Maneki's API wallet. Nothing is signed by "
                "the agent; the link is for the human. Needed only for live agents; virtual agents need no wallet.",
                "api_key(string, required); force(boolean, optional): mint a new link even if already authorized",
                "/okx/v1/authorize", {"api_key": "mk_demo"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Account Status",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/account"),
            "serviceDescription": _desc(
                "Shows your Maneki account: Gas balance, number of agents, linked wallet and its Hyperliquid "
                "authorization state (live_ready), and what is still missing for live trading.",
                "api_key(string, required); fresh(boolean, optional): re-check Hyperliquid instead of the cache",
                "/okx/v1/account", {"api_key": "mk_demo"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Research Report",
            "fee": fee_rep,
            "endpoint": s.public_url("/okx/v1/report"),
            "serviceDescription": _desc(
                "A paid deep-dive report on one US-stock perp (market structure, funding, momentum, scenarios, risk). "
                "The report's SHA-256 is anchored on X Layer and a public verify URL is returned, so the delivered text "
                "can be proven unchanged. Ask the user for the ticker first; the example symbol is only an example.",
                "symbol(string, required): ticker; focus(string, optional): the question the report should answer; "
                "api_key(string, optional): link the order to your Maneki account",
                "/okx/v1/report", {"symbol": "NVDA", "focus": "Earnings week risk for a swing long"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Watch",
            "fee": f"{s.price_watch_usd:g}",
            "endpoint": s.public_url("/okx/v1/watch"),
            "serviceDescription": _desc(
                f"A {s.watch_hours}-hour monitoring brief on one US-stock perp: one report every "
                f"{s.watch_interval_s // 3600} hours ({s.watch_checks} in total) covering what changed, levels, funding, "
                "momentum and a stance. Pay once; every report's SHA-256 is anchored on X Layer. Ask the user for the "
                "ticker first.",
                "symbol(string, required): ticker chosen by the user; focus(string, optional): what each report should "
                "focus on; api_key(string, optional): link the watch to your Maneki account",
                "/okx/v1/watch", {"symbol": "NVDA", "focus": "Swing setup and risk"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Watch Reports",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/watch/get"),
            "serviceDescription": _desc(
                "Fetches a watch by id: schedule, progress and every report delivered so far (text, SHA-256, X Layer "
                "anchor). Free to read as often as you like.",
                "watch_id(string, required); api_key(string, optional)",
                "/okx/v1/watch/get", {"watch_id": "wat_demo"}),
        },
        {
            "serviceType": "A2MCP",
            "serviceName": "Maneki Report Retrieval",
            "fee": "0",
            "endpoint": s.public_url("/okx/v1/report/get"),
            "serviceDescription": _desc(
                "Fetches a paid report by order id (text, SHA-256, X Layer anchor status) — already-bought reports are "
                "free to read again.",
                "order_id(string, required); api_key(string, optional)",
                "/okx/v1/report/get", {"order_id": "ord_demo"}),
        },
    ]


def create_command() -> str:
    """Ready-to-paste onchainos command (the human runs it after logging in)."""
    arr = json.dumps(services(), ensure_ascii=False)
    return (f"onchainos agent create --role asp --name '{AGENT_NAME}' "
            f"--description '{AGENT_DESCRIPTION}' --picture '<CDN URL from: onchainos agent upload --file logo.png>' "
            f"--service '{arr}'")
