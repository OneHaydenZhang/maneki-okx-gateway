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
