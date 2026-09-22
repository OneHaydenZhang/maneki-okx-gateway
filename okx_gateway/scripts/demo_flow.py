#!/usr/bin/env python3
"""End-to-end buyer demo against a running gateway.

Plays the role of an OKX AI user's agent: hits the paid endpoint, gets the
x402 challenge, signs a real EIP-3009 authorization with a local key (the
official SDK client does the protocol), registers, creates a virtual agent,
asks for analysis, buys a report and verifies its digest.

    python okx_gateway/scripts/demo_flow.py --base http://127.0.0.1:4180 --key 0x<hex private key>

Against the dev facilitator (OKX_X402_DEV_ACCEPT=1) any funded-or-not key
works. Against the real facilitator the key must hold USD₮0 on X Layer
(mainnet 196) or test USD₮0 on the testnet (1952, faucet:
https://web3.okx.com/xlayer/faucet). The OKX Agentic Wallet does the same
thing through `onchainos payment quote/pay`.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import sys
import time
from typing import Any

import httpx
from eth_account import Account

from x402.client import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.mechanisms.evm.exact.client import ExactEvmScheme


class LocalSigner:
    """ClientEvmSigner over eth-account."""

    def __init__(self, key: str):
        self.acct = Account.from_key(key)

    @property
    def address(self) -> str:
        return self.acct.address

    def sign_typed_data(self, domain, types, primary_type, message) -> bytes:
        dom = _to_dict(domain)
        tps = {k: [_to_dict(f) for f in v] for k, v in types.items()}
        full = {"types": {"EIP712Domain": _domain_fields(dom), **tps}, "primaryType": primary_type,
                "domain": dom, "message": message}
        signed = self.acct.sign_typed_data(full_message=full)
        return bytes(signed.signature)


def _to_dict(o: Any) -> dict:
    """dict / pydantic model / dataclass / plain object → dict without Nones."""
    if isinstance(o, dict):
        d = o
    elif hasattr(o, "model_dump"):
        d = o.model_dump()
    elif dataclasses.is_dataclass(o):
        d = dataclasses.asdict(o)
    else:
        d = vars(o)
    ren = {"chain_id": "chainId", "verifying_contract": "verifyingContract"}
    return {ren.get(k, k): v for k, v in d.items() if v is not None}


def _domain_fields(dom: dict) -> list:
    out = []
    for name, typ in (("name", "string"), ("version", "string"), ("chainId", "uint256"),
                      ("verifyingContract", "address"), ("salt", "bytes32")):
        if dom.get(name) is not None:
            out.append({"name": name, "type": typ})
    return out


def digest(text: str) -> str:
    canon = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def show(title: str, data: Any) -> None:
    print(f"\n=== {title}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2500])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:4180")
    ap.add_argument("--key", required=True, help="hex private key of the paying wallet")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--link-key", default="", help="hex private key of a BROWSER wallet: plays the human side of "
                                                   "'Maneki Live Authorization' (wallet login + claim) over the API")
    ap.add_argument("--wait", type=int, default=0, help="seconds to wait before reading agent status")
    args = ap.parse_args()

    signer = LocalSigner(args.key)
    print(f"payer: {signer.address}")
    x402c = x402Client()
    x402c.register("eip155:*", ExactEvmScheme(signer))

    # 1) the bare challenge, as the OKX probe sees it
    r = httpx.post(f"{args.base}/okx/v1/register", json={}, timeout=30)
    print(f"probe -> HTTP {r.status_code}, PAYMENT-REQUIRED present: {bool(r.headers.get('payment-required'))}")
    show("unpaid body", r.json())

    # 2) pay + register (the SDK client retries with PAYMENT-SIGNATURE)
    async with x402HttpxClient(x402c, timeout=120) as client:
        r = await client.post(f"{args.base}/okx/v1/register", json={"nickname": "demo"})
        print(f"register -> HTTP {r.status_code}, PAYMENT-RESPONSE present: {bool(r.headers.get('payment-response'))}")
        reg = r.json()
        show("register", reg)
        if r.status_code != 200:
            return 1
        api_key = reg["api_key"]

        st = httpx.post(f"{args.base}/okx/v1/agents/status", json={"api_key": api_key}, timeout=30).json()
        show("status (balance after settlement)", {"gas_balance": st.get("gas_balance"), "agents": len(st.get("agents", []))})

        # 3) analysis (free at x402 level, 8 Gas on the account)
        an = httpx.post(f"{args.base}/okx/v1/analyze",
                        json={"api_key": api_key, "symbol": args.symbol, "question": "Is momentum intact?"},
                        timeout=120).json()
        show("analyze", an)

        # 4) a virtual agent
        cr = httpx.post(f"{args.base}/okx/v1/agents/create",
                        json={"api_key": api_key, "symbol": args.symbol, "persona": "navigator",
                              "capital_max": 200, "max_ticks": 6}, timeout=60).json()
        show("agents/create", cr)
        agent_id = (cr.get("agent") or {}).get("agent_id")
        if agent_id and args.wait:
            print(f"waiting {args.wait}s for the first decision round…")
            time.sleep(args.wait)
        if agent_id:
            st = httpx.post(f"{args.base}/okx/v1/agents/status",
                            json={"api_key": api_key, "agent_id": agent_id}, timeout=30).json()
            show("agents/status", st)

        # 4b) route A: link a browser wallet (the human's part, replayed over the API)
        if args.link_key:
            au = httpx.post(f"{args.base}/okx/v1/authorize", json={"api_key": api_key}, timeout=30).json()
            show("authorize", au)
            code = (au.get("authorize_url") or "").split("code=")[-1]
            if code:
                w = Account.from_key(args.link_key)
                n = httpx.post(f"{args.base}/api/auth/nonce", json={"address": w.address}, timeout=30).json()
                from eth_account.messages import encode_defunct
                sig = w.sign_message(encode_defunct(text=n["message"])).signature.hex()
                if not sig.startswith("0x"):
                    sig = "0x" + sig
                v = httpx.post(f"{args.base}/api/auth/verify", json={"address": w.address, "signature": sig}, timeout=30).json()
                tok = v.get("token", "")
                cl = httpx.post(f"{args.base}/api/link/claim", json={"code": code},
                                headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()
                show("claim (as the browser wallet)", cl)
                acc = httpx.post(f"{args.base}/okx/v1/account", json={"api_key": api_key, "fresh": True}, timeout=60).json()
                show("account after link", acc)

        # 5) a paid report + digest verification
        if not args.no_report:
            r = await client.post(f"{args.base}/okx/v1/report",
                            json={"symbol": args.symbol, "focus": "swing setup this week", "api_key": api_key})
            rep = r.json()
            show("report", {k: v for k, v in rep.items() if k != "report_markdown"})
            if rep.get("report_markdown"):
                ok = digest(rep["report_markdown"]) == rep["sha256"]
                print(f"local digest matches: {ok}")
            v = httpx.get(f"{args.base}/okx/v1/report/{rep.get('order_id')}/verify", timeout=30).json()
            show("verify", v)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
