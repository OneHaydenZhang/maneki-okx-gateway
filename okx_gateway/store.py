"""Gateway-local state: accounts (payer → api key) and paid orders (reports).

One small SQLite file, separate from the Maneki core DB. Rows are never
deleted — an order that failed keeps its row so the same payment is never
charged twice and a retry can re-deliver.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_PATH: Optional[Path] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  payer      TEXT PRIMARY KEY,
  api_key    TEXT UNIQUE NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',   -- pending | paid
  created_at REAL NOT NULL,
  paid_at    REAL,
  txhash     TEXT DEFAULT '',
  credits    INTEGER DEFAULT 0,
  usd        REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders(
  order_id     TEXT PRIMARY KEY,
  payer        TEXT NOT NULL,
  kind         TEXT NOT NULL,                   -- report
  symbol       TEXT DEFAULT '',
  focus        TEXT DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'pending', -- pending | generating | delivered | failed
  usd          REAL DEFAULT 0,
  txhash       TEXT DEFAULT '',
  settle_status TEXT DEFAULT '',                -- '' | success | failed
  report_md    TEXT DEFAULT '',
  sha256       TEXT DEFAULT '',
  anchor_tx    TEXT DEFAULT '',
  anchor_status TEXT DEFAULT '',                -- '' | pending | anchored | skipped | failed
  error        TEXT DEFAULT '',
  created_at   REAL NOT NULL,
  delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_orders_payer ON orders(payer, created_at);
CREATE TABLE IF NOT EXISTS watches(
  watch_id     TEXT PRIMARY KEY,
  payer        TEXT NOT NULL,
  address      TEXT NOT NULL,                 -- Maneki account that generates the reports
  symbol       TEXT NOT NULL,
  focus        TEXT DEFAULT '',
  interval_s   INTEGER NOT NULL,
  checks_total INTEGER NOT NULL,
  checks_done  INTEGER DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'pending_payment', -- pending_payment | active | completed | stopped | failed
  usd          REAL DEFAULT 0,
  txhash       TEXT DEFAULT '',
  next_due     REAL,
  fails        INTEGER DEFAULT 0,
  error        TEXT DEFAULT '',
  created_at   REAL NOT NULL,
  updated_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_watches_due ON watches(status, next_due);
CREATE TABLE IF NOT EXISTS watch_runs(
  run_id       TEXT PRIMARY KEY,
  watch_id     TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  ts           REAL NOT NULL,
  status       TEXT NOT NULL,                 -- delivered | failed
  headline     TEXT DEFAULT '',
  report_md    TEXT DEFAULT '',
  sha256       TEXT DEFAULT '',
  anchor_tx    TEXT DEFAULT '',
  anchor_status TEXT DEFAULT '',
  error        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_watch_runs ON watch_runs(watch_id, seq);
CREATE TABLE IF NOT EXISTS settlements(
  txhash   TEXT PRIMARY KEY,
  payer    TEXT NOT NULL,
  resource TEXT NOT NULL,
  usd      REAL DEFAULT 0,
  ts       REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    global _CONN, _PATH
    want = settings().data_dir / "gateway.db"
    if _CONN is None or _PATH != want:
        want.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(want), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # additive migration: effective Maneki address (payer until linked)
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)").fetchall()}
        if "address" not in cols:
            c.execute("ALTER TABLE accounts ADD COLUMN address TEXT DEFAULT ''")
            c.commit()
        scols = {r[1] for r in c.execute("PRAGMA table_info(settlements)").fetchall()}
        if "credited" not in scols:
            # 0 = core credit still owed (retried on later calls), 1 = done, -1 = not a credit (report)
            c.execute("ALTER TABLE settlements ADD COLUMN credited INTEGER DEFAULT 1")
            c.execute("ALTER TABLE settlements ADD COLUMN credits INTEGER DEFAULT 0")
            c.execute("ALTER TABLE settlements ADD COLUMN target TEXT DEFAULT ''")
            c.commit()
        _CONN, _PATH = c, want
    return _CONN


def _row(r: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(r) if r is not None else None


# ---- accounts ------------------------------------------------------------------

def ensure_account(payer: str) -> Dict[str, Any]:
    """Row for this payer, created pending if missing. Idempotent."""
    payer = payer.lower()
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT * FROM accounts WHERE payer=?", (payer,)).fetchone()
        if row:
            return dict(row)
        key = "mk_" + secrets.token_urlsafe(24)
        c.execute("INSERT INTO accounts(payer, api_key, status, created_at) VALUES(?,?,?,?)",
                  (payer, key, "pending", time.time()))
        c.commit()
        return dict(c.execute("SELECT * FROM accounts WHERE payer=?", (payer,)).fetchone())


def effective_address(acct: Dict[str, Any]) -> str:
    """Where this account's agents/Gas live: the linked wallet, else the payer."""
    return (acct.get("address") or acct.get("payer") or "").lower()


def set_address(payer: str, address: str) -> None:
    with _LOCK:
        c = _conn()
        c.execute("UPDATE accounts SET address=? WHERE payer=?", (address.lower(), payer.lower()))
        c.commit()


def mark_paid(payer: str, txhash: str, credits: int, usd: float) -> Dict[str, Any]:
    payer = payer.lower()
    with _LOCK:
        c = _conn()
        c.execute("UPDATE accounts SET status='paid', paid_at=COALESCE(paid_at, ?), txhash=?, "
                  "credits=credits+?, usd=usd+? WHERE payer=?",
                  (time.time(), txhash, int(credits), float(usd), payer))
        c.commit()
        return dict(c.execute("SELECT * FROM accounts WHERE payer=?", (payer,)).fetchone())


def account_for_key(api_key: str) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    with _LOCK:
        return _row(_conn().execute("SELECT * FROM accounts WHERE api_key=?", (api_key.strip(),)).fetchone())


def account_for_payer(payer: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _row(_conn().execute("SELECT * FROM accounts WHERE payer=?", (payer.lower(),)).fetchone())


# ---- settlements (idempotency) ---------------------------------------------------

def record_settlement(txhash: str, payer: str, resource: str, usd: float,
                      credits: int = 0, target: str = "") -> bool:
    """True the first time this txhash is seen. A registration settlement
    starts with credited=0 (core credit owed) until mark_credited()."""
    if not txhash:
        return False
    with _LOCK:
        c = _conn()
        try:
            c.execute("INSERT INTO settlements(txhash, payer, resource, usd, ts, credited, credits, target) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (txhash.lower(), payer.lower(), resource, float(usd), time.time(),
                       0 if credits > 0 else -1, int(credits), target.lower()))
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def mark_credited(txhash: str) -> None:
    with _LOCK:
        c = _conn()
        c.execute("UPDATE settlements SET credited=1 WHERE txhash=?", (txhash.lower(),))
        c.commit()


def pending_credits(payer: str = "") -> List[Dict[str, Any]]:
    """Registration settlements whose core credit has not landed yet."""
    with _LOCK:
        c = _conn()
        if payer:
            rows = c.execute("SELECT * FROM settlements WHERE credited=0 AND payer=? ORDER BY ts", (payer.lower(),)).fetchall()
        else:
            rows = c.execute("SELECT * FROM settlements WHERE credited=0 ORDER BY ts").fetchall()
        return [dict(r) for r in rows]


# ---- orders ----------------------------------------------------------------------

def create_order(payer: str, kind: str, symbol: str, focus: str, usd: float) -> Dict[str, Any]:
    oid = "ord_" + secrets.token_hex(6)
    with _LOCK:
        c = _conn()
        c.execute("INSERT INTO orders(order_id, payer, kind, symbol, focus, status, usd, created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (oid, payer.lower(), kind, symbol, focus, "pending", float(usd), time.time()))
        c.commit()
        return dict(c.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone())


def update_order(order_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_order(order_id)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        c = _conn()
        c.execute(f"UPDATE orders SET {cols} WHERE order_id=?", (*fields.values(), order_id))
        c.commit()
        return _row(c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone())


def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _row(_conn().execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone())


def orders_for(payer: str, limit: int = 20) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM orders WHERE payer=? ORDER BY created_at DESC LIMIT ?",
                               (payer.lower(), int(limit))).fetchall()
        return [dict(r) for r in rows]


def latest_pending_order(payer: str, kind: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _row(_conn().execute(
            "SELECT * FROM orders WHERE payer=? AND kind=? AND status IN ('pending','generating') "
            "ORDER BY created_at DESC LIMIT 1", (payer.lower(), kind)).fetchone())


def reset_for_tests() -> None:
    global _CONN, _PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN, _PATH = None, None


# ---- watches -------------------------------------------------------------------

def create_watch(payer: str, address: str, symbol: str, focus: str, interval_s: int, checks_total: int,
                 usd: float) -> Dict[str, Any]:
    wid = "wat_" + secrets.token_hex(6)
    now = time.time()
    with _LOCK:
        c = _conn()
        c.execute("INSERT INTO watches(watch_id, payer, address, symbol, focus, interval_s, checks_total, status, usd, "
                  "next_due, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (wid, payer.lower(), address.lower(), symbol, focus, int(interval_s), int(checks_total),
                   "pending_payment", float(usd), now, now, now))
        c.commit()
        return dict(c.execute("SELECT * FROM watches WHERE watch_id=?", (wid,)).fetchone())


def get_watch(watch_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _row(_conn().execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone())


def update_watch(watch_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        c = _conn()
        c.execute(f"UPDATE watches SET {cols} WHERE watch_id=?", (*fields.values(), watch_id))
        c.commit()
        return _row(c.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone())


def latest_pending_watch(payer: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _row(_conn().execute("SELECT * FROM watches WHERE payer=? AND status='pending_payment' "
                                    "ORDER BY created_at DESC LIMIT 1", (payer.lower(),)).fetchone())


def watches_for(payer: str, limit: int = 20) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM watches WHERE payer=? ORDER BY created_at DESC LIMIT ?",
                               (payer.lower(), int(limit))).fetchall()
        return [dict(r) for r in rows]


def due_watches(now: float, limit: int = 3) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM watches WHERE status='active' AND next_due<=? "
                               "ORDER BY next_due LIMIT ?", (now, int(limit))).fetchall()
        return [dict(r) for r in rows]


def add_watch_run(watch_id: str, seq: int, status: str, headline: str = "", report_md: str = "",
                  sha256: str = "", anchor_status: str = "", error: str = "") -> Dict[str, Any]:
    rid = "run_" + secrets.token_hex(5)
    with _LOCK:
        c = _conn()
        c.execute("INSERT INTO watch_runs(run_id, watch_id, seq, ts, status, headline, report_md, sha256, "
                  "anchor_status, error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (rid, watch_id, int(seq), time.time(), status, headline[:200], report_md, sha256, anchor_status, error[:300]))
        c.commit()
        return dict(c.execute("SELECT * FROM watch_runs WHERE run_id=?", (rid,)).fetchone())


def update_watch_run(run_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        c = _conn()
        c.execute(f"UPDATE watch_runs SET {cols} WHERE run_id=?", (*fields.values(), run_id))
        c.commit()


def watch_runs(watch_id: str) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM watch_runs WHERE watch_id=? ORDER BY seq DESC", (watch_id,)).fetchall()
        return [dict(r) for r in rows]


# ---- anchoring backlog ------------------------------------------------------------

def unanchored(limit: int = 5) -> List[Dict[str, Any]]:
    """Delivered reports (orders and watch runs) whose digest is not on chain yet."""
    with _LOCK:
        c = _conn()
        out = [dict(r, kind="order") for r in c.execute(
            "SELECT order_id AS id, sha256 FROM orders WHERE status='delivered' AND sha256<>'' AND "
            "(anchor_tx IS NULL OR anchor_tx='') AND anchor_status IN ('', 'pending', 'skipped', 'failed') "
            "ORDER BY created_at LIMIT ?", (int(limit),)).fetchall()]
        out += [dict(r, kind="run") for r in c.execute(
            "SELECT run_id AS id, sha256 FROM watch_runs WHERE status='delivered' AND sha256<>'' AND "
            "(anchor_tx IS NULL OR anchor_tx='') AND anchor_status IN ('', 'pending', 'skipped', 'failed') "
            "ORDER BY ts LIMIT ?", (int(limit),)).fetchall()]
        return out[:limit]
