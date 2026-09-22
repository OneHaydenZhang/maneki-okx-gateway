"""ManekiAI × OKX AI gateway.

Exposes Maneki's virtual-trading agents to OKX AI agents as A2MCP services:
plain HTTPS JSON endpoints, some free, some paid through x402 on X Layer.

Two ways to run it:
  * mounted inside the Maneki service (auto_service/app.py, OKX_GATEWAY_ENABLED=1),
  * standalone (okx_gateway/standalone.py) against any Maneki instance over HTTP.

Everything the gateway knows about Maneki goes through core_client.CoreClient,
so this package has no import from the trading engine.
"""
__version__ = "0.1.0"
