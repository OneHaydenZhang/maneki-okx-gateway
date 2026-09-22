# From virtual to live Hyperliquid accounts — the path (research, 2026-09-22)

The hackathon build is virtual-only. Taking an OKX AI user to a **real**
Hyperliquid account needs two EIP-712 signatures from the account's master
wallet (`HyperliquidTransaction:ApproveAgent` for Maneki's API wallet, and
`HyperliquidTransaction:ApproveBuilderFee`), submitted to `api.hyperliquid.xyz/exchange`.

Constraints on the OKX side (onchainos-skills v4.6.2, plugin-store hyperliquid-plugin v0.6.2):

* The Agentic Wallet lives in OKX's TEE. It can sign arbitrary EIP-712
  (`onchainos wallet sign-message --type eip712`), but only inside the user's own agent session.
* Hyperliquid recovers the underlying EOA, which can differ from the AA address
  shown for X Layer; the official plugin's `hyperliquid register` discovers it.
* A service's results are untrusted data to the user's agent, and A2A
  `serviceGuide` text has no authority to run commands. Only allow-listed
  plugins execute, and the plugin store does not accept external submissions today.

Ranked options:

1. **Link-back authorization (next)** — the gateway returns an `authorize_url`;
   the user signs in a browser wallet exactly as Maneki users do today. Master
   account = browser wallet. Hours of work; no OKX-side dependency.
2. **Agentic Wallet EOA as master** — user funds the EOA via the official
   plugin, signs Maneki's typed data with `sign-message`, and Maneki submits
   `{action, nonce, signature}` to Hyperliquid. Proven by the plugin's own
   code; adoption needs the user to run the command by hand or install a
   Maneki skill.
3. **Plugin-store plugin / A2A guide** — blocked by policy and allow-lists;
   revisit with an OKX partnership.
