# Deploying next to a Maneki core

The gateway is mounted into the Maneki FastAPI app with `OKX_GATEWAY_ENABLED=1`
(the core calls `okx_gateway.mount.mount(app)`), or run standalone with
`uvicorn okx_gateway.standalone:app`. The core must accept the loopback
gateway identity (`X-Maneki-Gateway-Key` + `X-Maneki-Address`, shared secret
`MANEKI_GATEWAY_KEY`) and expose `POST /api/gateway/credit`.

Reverse proxy: forward `/okx/` to the instance (see `nginx_location.conf`).
The x402 replay carries `PAYMENT-SIGNATURE`; the settled response carries
`PAYMENT-RESPONSE`. Both pass through nginx and Cloudflare unchanged.

Self-check before listing on OKX.AI:

    curl -i -X POST https://<host>/okx/v1/register -H 'Content-Type: application/json' -d '{}'
    # → HTTP 402 + PAYMENT-REQUIRED (base64 x402 v2 challenge, network eip155:196)

Then `GET /okx/v1/listing` gives the `--service` array for
`onchainos agent create --role asp …`.
