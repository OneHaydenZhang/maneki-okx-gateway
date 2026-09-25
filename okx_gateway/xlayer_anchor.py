"""Anchor a report digest on X Layer.

A plain self-transfer of 0 OKB whose calldata is the report's SHA-256. No
contract, no dependency beyond eth-account + JSON-RPC over httpx. The
transaction is the verifiable record: anyone can fetch it from the explorer,
read the 32-byte calldata and compare it with the hash of the report text
they were given. Report bodies and personal data never go on chain.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

import httpx
from eth_account import Account

from .config import settings

GAS_LIMIT = 60_000


def digest(text: str) -> str:
    """Canonical hash: UTF-8 bytes of the report text with trailing whitespace
    stripped and line endings normalised, so a copy pasted from a chat still
    verifies."""
    canon = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _rpc(url: str, method: str, params: list) -> Any:
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20.0)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"rpc {method}: {data['error']}")
    return data["result"]


def anchor_hash(sha256_hex: str) -> Dict[str, Any]:
    """Send the anchoring tx. Returns {tx, explorer_url, from}. Raises on failure.
    Sync — call from a thread."""
    s = settings()
    if not s.anchor_key:
        raise RuntimeError("XLAYER_ANCHOR_PRIVATE_KEY not set")
    acct = Account.from_key(s.anchor_key)
    url = s.rpc
    chain_id = int(_rpc(url, "eth_chainId", []), 16)
    nonce = int(_rpc(url, "eth_getTransactionCount", [acct.address, "pending"]), 16)
    gas_price = int(_rpc(url, "eth_gasPrice", []), 16)
    tx = {
        "nonce": nonce, "gasPrice": max(gas_price, 1), "gas": GAS_LIMIT,
        "to": acct.address, "value": 0, "data": "0x" + sha256_hex.lower(), "chainId": chain_id,
    }
    signed = acct.sign_transaction(tx)
    raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
    txh = _rpc(url, "eth_sendRawTransaction", ["0x" + raw.hex() if not raw.hex().startswith("0x") else raw.hex()])
    return {"tx": txh, "explorer_url": s.explorer_tx + txh, "from": acct.address, "chain_id": chain_id}


def anchor_status() -> Dict[str, Any]:
    s = settings()
    out: Dict[str, Any] = {"enabled": bool(s.anchor_key), "network": s.network, "rpc": s.rpc}
    if s.anchor_key:
        try:
            out["from"] = Account.from_key(s.anchor_key).address
        except Exception:
            out["from"] = "(invalid key)"
    return out


def read_anchor(txh: str) -> Optional[str]:
    """The 32-byte calldata of an anchoring tx as hex (without 0x), or None."""
    s = settings()
    try:
        tx = _rpc(s.rpc, "eth_getTransactionByHash", [txh])
    except Exception:
        return None
    if not tx:
        return None
    data = str(tx.get("input") or "")
    return data[2:] if data.startswith("0x") and len(data) == 66 else None


def anchor_balance() -> float:
    """Native OKB balance of the anchoring account (0.0 when unconfigured or unreachable)."""
    s = settings()
    if not s.anchor_key:
        return 0.0
    try:
        acct = Account.from_key(s.anchor_key)
        return int(_rpc(s.rpc, "eth_getBalance", [acct.address, "latest"]), 16) / 1e18
    except Exception:
        return 0.0
