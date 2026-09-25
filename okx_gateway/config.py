"""Gateway settings — all from the environment, all with safe defaults.

Nothing here is secret except OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE
(the OKX developer-portal credentials the x402 facilitator client signs with),
MANEKI_GATEWAY_KEY (shared with the Maneki core) and XLAYER_ANCHOR_PRIVATE_KEY
(an X Layer key that only pays gas for report-hash anchoring). They live in the
server's live.env, never in git.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# x402 / X Layer constants (OKX payments docs, 2026-09):
#   mainnet eip155:196  USD₮0 0x779d…3736 (6 dp) — the SDK's default "$" asset
#   testnet eip155:1952 USD₮0 0x9e29…fb0c (6 dp) — absent from the Python SDK's
#   NETWORK_CONFIGS, so it has to be spelled out explicitly.
KNOWN_ASSETS = {
    "eip155:196": {"address": "0x779ded0c9e1022225f8e0630b35a9b54be713736", "name": "USD₮0", "version": "1", "decimals": 6},
    "eip155:1952": {"address": "0x9e29b3aada05bf2d2c827af80bd28dc0b9b4fb0c", "name": "USD₮0", "version": "1", "decimals": 6},
}
EXPLORER_TX = {
    "eip155:196": "https://www.okx.com/web3/explorer/xlayer/tx/",
    "eip155:1952": "https://www.okx.com/web3/explorer/xlayer-test/tx/",
}
RPC = {
    "eip155:196": "https://rpc.xlayer.tech",
    "eip155:1952": "https://testrpc.xlayer.tech/terigon",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: str = "0") -> bool:
    return _env(name, default) in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default)) or default))
    except ValueError:
        return default


@dataclass
class Settings:
    enabled: bool = field(default_factory=lambda: _flag("OKX_GATEWAY_ENABLED"))
    # Where the Maneki core answers. Mounted in-process this is the same server.
    core_base: str = field(default_factory=lambda: _env("MANEKI_CORE_BASE") or f"http://127.0.0.1:{_env('PORT', '4180')}")
    gateway_key: str = field(default_factory=lambda: _env("MANEKI_GATEWAY_KEY"))
    # Public origin the endpoints are reachable at (listing + x402 resource URLs).
    public_base: str = field(default_factory=lambda: _env("OKX_PUBLIC_BASE", "http://127.0.0.1:4180").rstrip("/"))
    # Where a human goes to look at the same account in a browser.
    dashboard_base: str = field(default_factory=lambda: _env("MANEKI_DASHBOARD_BASE", "https://manekiai.io").rstrip("/"))
    # Payments
    pay_to: str = field(default_factory=lambda: (_env("OKX_PAY_TO") or _env("XLAYER_TREASURY_ADDRESS")).lower())
    network: str = field(default_factory=lambda: _env("OKX_X402_NETWORK", "eip155:196"))
    okx_api_key: str = field(default_factory=lambda: _env("OKX_API_KEY"))
    okx_secret_key: str = field(default_factory=lambda: _env("OKX_SECRET_KEY"))
    okx_passphrase: str = field(default_factory=lambda: _env("OKX_PASSPHRASE"))
    okx_base_url: str = field(default_factory=lambda: _env("OKX_BASE_URL", "https://web3.okx.com"))
    # Local development: accept any well-formed payment without a facilitator.
    dev_accept: bool = field(default_factory=lambda: _flag("OKX_X402_DEV_ACCEPT"))
    # Prices (USD) and what a registration buys. 1 USD = 1,000 Gas (Maneki anchor).
    price_register_usd: float = field(default_factory=lambda: _float("OKX_PRICE_REGISTER_USD", 1.0))
    register_credits: int = field(default_factory=lambda: _int("OKX_REGISTER_CREDITS", 1000))
    price_report_usd: float = field(default_factory=lambda: _float("OKX_PRICE_REPORT_USD", 0.5))
    # Watch: one payment, a report every `watch_interval_s` for `watch_hours`.
    price_watch_usd: float = field(default_factory=lambda: _float("OKX_PRICE_WATCH_USD", 1.5))
    watch_interval_s: int = field(default_factory=lambda: _int("OKX_WATCH_INTERVAL_S", 21600))
    watch_hours: int = field(default_factory=lambda: _int("OKX_WATCH_HOURS", 24))
    # Default shape of an agent created from OKX AI (all overridable per call).
    default_persona: str = field(default_factory=lambda: _env("OKX_DEFAULT_PERSONA", "navigator"))
    default_model: str = field(default_factory=lambda: _env("OKX_DEFAULT_MODEL", "deepseek/deepseek-chat"))
    default_interval_s: int = field(default_factory=lambda: _int("OKX_DEFAULT_INTERVAL_S", 3600))
    default_max_ticks: int = field(default_factory=lambda: _int("OKX_DEFAULT_MAX_TICKS", 24))
    default_capital_max: float = field(default_factory=lambda: _float("OKX_DEFAULT_CAPITAL_MAX", 200.0))
    default_max_leverage: int = field(default_factory=lambda: _int("OKX_DEFAULT_MAX_LEVERAGE", 3))
    # Report generation budget before we hand back an order id instead of the text.
    report_inline_budget_s: float = field(default_factory=lambda: _float("OKX_REPORT_INLINE_S", 20.0))
    # X Layer anchoring of report hashes (optional; off when the key is empty).
    anchor_key: str = field(default_factory=lambda: _env("XLAYER_ANCHOR_PRIVATE_KEY"))
    anchor_rpc: str = field(default_factory=lambda: _env("XLAYER_ANCHOR_RPC"))
    # Live (real Hyperliquid) agents through the gateway: off by default. Needs a
    # linked + authorized browser wallet (route A) even when on.
    live_agents: bool = field(default_factory=lambda: _flag("OKX_LIVE_AGENTS"))
    # Hard ceilings for agents created from a conversation (real money only).
    live_capital_max: float = field(default_factory=lambda: _float("OKX_LIVE_CAPITAL_MAX", 500.0))
    live_max_leverage: int = field(default_factory=lambda: _int("OKX_LIVE_MAX_LEVERAGE", 3))
    # Demo key: lets anyone (including OKX.AI's listing reviewer, who runs the
    # Request Example curl literally) exercise the read-only tools against a
    # house account. Writes are previewed, never executed, under this key.
    demo_api_key: str = field(default_factory=lambda: _env("OKX_DEMO_API_KEY", "mk_demo"))
    demo_payer: str = field(default_factory=lambda: _env("OKX_DEMO_PAYER").lower())
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(_env("OKX_GATEWAY_DATA") or (ROOT / "data" / "okx_gateway")))

    @property
    def chain_id(self) -> int:
        try:
            return int(self.network.split(":", 1)[1])
        except (IndexError, ValueError):
            return 196

    @property
    def asset(self) -> dict:
        return KNOWN_ASSETS.get(self.network, KNOWN_ASSETS["eip155:196"])

    @property
    def explorer_tx(self) -> str:
        return EXPLORER_TX.get(self.network, EXPLORER_TX["eip155:196"])

    @property
    def rpc(self) -> str:
        return self.anchor_rpc or RPC.get(self.network, RPC["eip155:196"])

    @property
    def facilitator_configured(self) -> bool:
        return bool(self.okx_api_key and self.okx_secret_key and self.okx_passphrase)

    @property
    def watch_checks(self) -> int:
        return max(1, int(self.watch_hours * 3600 // max(60, self.watch_interval_s)))

    def public_url(self, path: str) -> str:
        return f"{self.public_base}{path}"

    def problems(self) -> list[str]:
        """Configuration issues worth refusing to start over (production) or
        just reporting (dev)."""
        out = []
        if not self.gateway_key:
            out.append("MANEKI_GATEWAY_KEY is empty — the gateway cannot act for users on the core")
        if not self.pay_to:
            out.append("OKX_PAY_TO / XLAYER_TREASURY_ADDRESS is empty — nowhere to receive x402 payments")
        if not self.facilitator_configured and not self.dev_accept:
            out.append("OKX_API_KEY/OKX_SECRET_KEY/OKX_PASSPHRASE missing and OKX_X402_DEV_ACCEPT is off — paid tools cannot settle")
        return out


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload() -> Settings:
    """Re-read the environment (tests)."""
    global _settings
    _settings = Settings()
    return _settings
