"""Maneki Watch — one payment, a report every N hours for a window.

Modelled on the Celo "paid research task" lane: the buyer pays once for the
whole run, a small background runner delivers one report per check, every
report is digest-anchored on X Layer, and reading them back is free. Deliber-
ately self-contained: it only calls the core's chat (prepaid) and the gateway
store; a restart just resumes from `watches.next_due`.

Two loops start with the gateway (mount.py):
  * run_forever      — every 60 s, generate the reports that are due;
  * anchor_forever   — every 2 min, put the digest of any delivered report
                       (orders and watch runs) on X Layer once a key exists.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from . import store, xlayer_anchor
from .config import settings
from .core_client import CoreClient, CoreError

RUN_EVERY_S = 60
ANCHOR_EVERY_S = 120
MAX_FAILS = 3

WATCH_PROMPT = (
    "You are writing check #{seq} of {total} in a {hours}-hour monitoring brief on {symbol} perpetual (Hyperliquid xyz dex), "
    "one check every {every} hours. Focus: {focus}. Cover what changed since the previous check where relevant: "
    "price action and levels, funding and open interest, momentum/volatility, and a concrete stance (side, invalidation, "
    "targets) with confidence. Be specific and numeric; say clearly what is uncertain. Write in English."
)


def _md(w: Dict[str, Any], seq: int, data: Dict[str, Any]) -> str:
    from .router import _point_text
    st = data.get("structured") or {}
    sug = data.get("suggestion") or {}
    lines = [f"# ManekiAI Watch — {w['symbol']} · check {seq}/{w['checks_total']}", "",
             f"Watch: {w['watch_id']}  ·  Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             f"Focus: {w['focus'] or 'general outlook'}  ·  Every {w['interval_s'] // 3600}h", ""]
    if st.get("headline"):
        lines += ["## Summary", st["headline"], ""]
    if st.get("points"):
        lines += ["## Key points"] + [f"- {_point_text(p)}" for p in st["points"]] + [""]
    if st.get("analysis"):
        lines += ["## Analysis", str(st["analysis"]), ""]
    if sug.get("has_trade_idea"):
        lines += ["## Stance",
                  f"- Side: {sug.get('side')}  ·  Size: ${sug.get('size_usd')}  ·  Leverage: {sug.get('leverage')}x",
                  f"- Stop loss: {sug.get('stop_loss')}  ·  Take profit: {sug.get('take_profit')}",
                  f"- Confidence: {sug.get('confidence')}", f"- Rationale: {sug.get('rationale')}", ""]
    lines += ["---", "Not financial advice. Monitoring brief; no order is placed by this report."]
    return "\n".join(lines)


async def run_check(w: Dict[str, Any]) -> Dict[str, Any]:
    """Generate one check for a watch. Returns the run row."""
    from .router import _chat_unusable
    s = settings()
    seq = int(w["checks_done"]) + 1
    prompt = WATCH_PROMPT.format(seq=seq, total=w["checks_total"], hours=s.watch_hours, symbol=w["symbol"],
                                 every=max(1, w["interval_s"] // 3600), focus=w["focus"] or "general outlook")
    try:
        data = await CoreClient().chat(w["address"], prompt, symbol=w["symbol"], advice=True, timeout=120.0, prepaid=True)
        if _chat_unusable(data):
            raise CoreError(503, "model busy")
    except CoreError as e:
        fails = int(w.get("fails") or 0) + 1
        run = store.add_watch_run(w["watch_id"], seq, "failed", error=e.message)
        if fails >= MAX_FAILS:
            store.update_watch(w["watch_id"], status="failed", fails=fails, error=e.message[:200])
        else:   # retry soon, keep the schedule
            store.update_watch(w["watch_id"], fails=fails, next_due=time.time() + 120)
        return run
    md = _md(w, seq, data)
    sha = xlayer_anchor.digest(md)
    headline = str((data.get("structured") or {}).get("headline") or "")[:200]
    run = store.add_watch_run(w["watch_id"], seq, "delivered", headline=headline, report_md=md, sha256=sha,
                              anchor_status="pending" if s.anchor_key else "skipped")
    done = seq
    fields: Dict[str, Any] = {"checks_done": done, "fails": 0}
    if done >= int(w["checks_total"]):
        fields["status"] = "completed"
        fields["next_due"] = None
    else:
        fields["next_due"] = float(w["next_due"] or time.time()) + int(w["interval_s"])
    store.update_watch(w["watch_id"], **fields)
    # Anchoring is done by anchor_forever (checks the key's OKB balance first), so
    # an unfunded key never produces a trail of failed transactions.
    return run


async def run_due(limit: int = 3) -> int:
    n = 0
    for w in store.due_watches(time.time(), limit=limit):
        await run_check(w)
        n += 1
    return n


async def anchor_one(kind: str, id_: str, sha: str) -> bool:
    try:
        res = await asyncio.to_thread(xlayer_anchor.anchor_hash, sha)
    except Exception as e:
        if kind == "order":
            store.update_order(id_, anchor_status="failed", error=f"anchor: {e!r}"[:300])
        else:
            store.update_watch_run(id_, anchor_status="failed", error=f"anchor: {e!r}"[:300])
        return False
    if kind == "order":
        store.update_order(id_, anchor_tx=res["tx"], anchor_status="anchored")
    else:
        store.update_watch_run(id_, anchor_tx=res["tx"], anchor_status="anchored")
    return True


async def anchor_backlog(limit: int = 5) -> int:
    """Every delivered report gets its digest on chain — including ones
    delivered before the anchoring key existed."""
    if not settings().anchor_key:
        return 0
    if await asyncio.to_thread(xlayer_anchor.anchor_balance) <= 0:
        return 0   # key exists but holds no OKB yet: leave the backlog for later, no failing txs
    n = 0
    for row in store.unanchored(limit):
        if await anchor_one(row["kind"], row["id"], row["sha256"]):
            n += 1
        await asyncio.sleep(2)   # one nonce at a time
    return n


async def run_forever() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_due()
        except Exception as e:
            print(f"[okx-gateway] watch runner error: {e!r}")
        await asyncio.sleep(RUN_EVERY_S)


async def anchor_forever() -> None:
    await asyncio.sleep(40)
    while True:
        try:
            await anchor_backlog()
        except Exception as e:
            print(f"[okx-gateway] anchor loop error: {e!r}")
        await asyncio.sleep(ANCHOR_EVERY_S)


def view(w: Dict[str, Any], with_reports: bool = True) -> Dict[str, Any]:
    s = settings()
    runs = store.watch_runs(w["watch_id"])
    out: Dict[str, Any] = {
        "watch_id": w["watch_id"], "status": w["status"], "symbol": w["symbol"], "focus": w["focus"],
        "every_hours": w["interval_s"] // 3600, "checks_total": w["checks_total"], "checks_done": w["checks_done"],
        "next_due": w.get("next_due"), "created_at": w["created_at"], "usd": w["usd"],
        "payment_tx": w.get("txhash") or None,
        "get_url": s.public_url("/okx/v1/watch/get"),
    }
    if w.get("error"):
        out["error"] = w["error"]
    if with_reports:
        out["reports"] = [{
            "seq": r["seq"], "ts": r["ts"], "status": r["status"], "headline": r["headline"],
            "sha256": r["sha256"] or None,
            "anchor": {"status": r["anchor_status"] or "skipped", "tx": r["anchor_tx"] or None,
                       "explorer_url": (s.explorer_tx + r["anchor_tx"]) if r.get("anchor_tx") else None},
            "report_markdown": r["report_md"] if r["status"] == "delivered" else None,
            "error": r["error"] or None,
        } for r in runs]
    return out
