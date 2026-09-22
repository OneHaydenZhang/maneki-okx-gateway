# ManekiAI × OKX AI — agent-service gateway

ManekiAI runs autonomous trading agents on Hyperliquid perpetuals. This gateway
lets any OKX AI agent (OpenClaw, Hermes, Claude Code, Codex with the `onchainos`
skills) hire those agents: pay once with the OKX Agentic Wallet on X Layer, get
an account, and drive **virtual trading agents** by conversation.

```
user's AI agent ──(onchainos a2mcp-probe / x402)──► this gateway ──(loopback, gateway key)──► Maneki core
                                                       │
                                                       └── X Layer: USD₮0 payments in, report digests out
```

Everything here is **virtual**: the agents decide every round on live market
data with code-enforced risk limits and simulate their fills. No order reaches
an exchange. The live-account path (route A below) is designed in [docs/LIVE_PATH.md](docs/LIVE_PATH.md); deployment notes in [docs/DEPLOY.md](docs/DEPLOY.md).

## Services (A2MCP, all POST + JSON body)

| Service | Path | Price | Needs |
|---|---|---|---|
| Maneki Account and Gas | `/okx/v1/register` | $1 (x402) → 1,000 Agent Gas | — |
| Maneki Market Analysis | `/okx/v1/analyze` | free (8 Gas) | `api_key`, `symbol` |
| Maneki Virtual Trading Agent | `/okx/v1/agents/create` | free (Gas per round) | `api_key`, `symbol`, `persona?` |
| Maneki Agent Status | `/okx/v1/agents/status` | free | `api_key`, `agent_id?` |
| Maneki Agent Control | `/okx/v1/agents/control` | free | `api_key`, `agent_id`, `action` |
| Maneki Live Authorization | `/okx/v1/authorize` | free | `api_key` |
| Maneki Account Status | `/okx/v1/account` | free | `api_key` |
| Maneki Research Report | `/okx/v1/report` | $0.5 (x402) | `symbol`, `focus?` |
| Maneki Report Retrieval | `/okx/v1/report/get` | free | `order_id` |
| Verify a report | `GET /okx/v1/report/{order_id}/verify` | public | — |

Identity is the **x402 payer**: the wallet that paid for registration becomes
the Maneki account (its EVM address), and the returned `api_key` is what the
free tools accept afterwards. A second registration payment tops the same
account up.

Missing parameters come back as HTTP 400 `{"status":"input_required","fields":[...]}`,
which the OKX probe turns into a question for the user. Business conditions
(not enough Gas, model busy) are never HTTP 402 — that status is x402's.

`GET /okx/v1/listing` returns the exact `--service` array for
`onchainos agent create --role asp …`, generated from the same code as the routes.

## Live accounts (route A)

`/okx/v1/authorize` returns a one-time link (`https://<dashboard>/#authorize?code=…`).
The human opens it, signs in with the browser wallet whose Hyperliquid account
Maneki may trade (one gasless signature), and approves Maneki's API wallet on
Hyperliquid in Settings. From then on the gateway acts for that wallet: its
Agent Gas moves there, `/okx/v1/account` reports `live_ready`, and
`/okx/v1/agents/create` accepts `mode: "live"` — only when the deployment sets
`OKX_LIVE_AGENTS=1`, the wallet is linked and approved, **and** the call
repeats with `confirm: true`. Nothing in this flow signs on the user's behalf;
the link is data the user's agent shows them.

## Payments

x402 v2, scheme `exact` (EIP-3009), network `eip155:196` (X Layer mainnet),
asset USD₮0, settled through OKX's facilitator with the official
`okxweb3-app-x402` SDK. A registration payment credits Gas on the Maneki core
**at settlement time**, idempotent on the settlement tx hash. Testnet
(`eip155:1952`) is a one-line switch (`OKX_X402_NETWORK`).

## Report verification

A paid report's text is hashed (SHA-256 over LF-normalised, trailing-space-
stripped text) and the digest is written as calldata of an X Layer
transaction. `verify` returns the hash, the tx and whether the chain's
calldata matches. Report bodies never go on chain.

## Running

Mounted inside the Maneki service (what production does):

```
OKX_GATEWAY_ENABLED=1 MANEKI_GATEWAY_KEY=<shared secret> \
OKX_PAY_TO=0x<X Layer treasury> OKX_PUBLIC_BASE=https://<public origin> \
OKX_API_KEY=… OKX_SECRET_KEY=… OKX_PASSPHRASE=… \
uvicorn auto_service.app:app --port 4180
```

Standalone against a Maneki core on the same host:

```
MANEKI_CORE_BASE=http://127.0.0.1:4180 … uvicorn okx_gateway.standalone:app --port 4182
```

Local development with no OKX account: `OKX_X402_DEV_ACCEPT=1` swaps the
facilitator for one that accepts any well-formed authorization.

### Configuration

| Variable | Meaning | Default |
|---|---|---|
| `OKX_GATEWAY_ENABLED` | mount the gateway | `0` |
| `MANEKI_GATEWAY_KEY` | shared secret; the core accepts it only from loopback | — |
| `MANEKI_CORE_BASE` | core origin | `http://127.0.0.1:$PORT` |
| `OKX_PUBLIC_BASE` | public origin used in the listing and x402 resource URLs | `http://127.0.0.1:4180` |
| `OKX_PAY_TO` / `XLAYER_TREASURY_ADDRESS` | X Layer address that receives payments | — |
| `OKX_X402_NETWORK` | `eip155:196` or `eip155:1952` | `eip155:196` |
| `OKX_API_KEY` `OKX_SECRET_KEY` `OKX_PASSPHRASE` | OKX developer-portal credentials for the facilitator | — |
| `OKX_X402_DEV_ACCEPT` | dev facilitator | `0` |
| `OKX_PRICE_REGISTER_USD` / `OKX_PRICE_REPORT_USD` | prices | `1` / `0.5` |
| `OKX_DEFAULT_PERSONA` `OKX_DEFAULT_MODEL` `OKX_DEFAULT_INTERVAL_S` `OKX_DEFAULT_MAX_TICKS` `OKX_DEFAULT_CAPITAL_MAX` `OKX_DEFAULT_MAX_LEVERAGE` | agent defaults | navigator / deepseek / 3600 / 24 / 200 / 3 |
| `XLAYER_ANCHOR_PRIVATE_KEY` | key that pays gas for report anchoring (optional) | — |
| `OKX_LIVE_AGENTS` | allow `mode: live` agents (still needs link + approvals + confirm) | `0` |
| `MANEKI_DASHBOARD_BASE` | origin of the Maneki web app the authorize link opens | `https://manekiai.io` |
| `OKX_GATEWAY_DATA` | SQLite dir for accounts/orders | `data/okx_gateway` |

## Try it

```
python okx_gateway/scripts/demo_flow.py --base http://127.0.0.1:4180 --key 0x<private key>
```

Plays a buyer end to end with the official SDK client: 402 → signed EIP-3009
authorization → registration → analysis → virtual agent → paid report → digest check.
Add `--link-key 0x<another key>` to replay the human side of the live authorization (wallet login + claim).

From an OKX AI agent, the same thing is: *"find ManekiAI on OKX.AI and open an
account"*, then *"create a Maneki virtual agent on NVDA"*.

## Live demo

Public endpoint (virtual agents, hackathon build): `https://paper.manekiai.io/okx/v1`

## Part of

[ManekiAI](https://manekiai.io) — autonomous, explainable trading agents on Hyperliquid. This repository holds only the OKX AI integration layer.
