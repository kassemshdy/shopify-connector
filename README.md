# Shopify Connector

One Shopify app, many websites. A website (a **tenant**, such as Janoubona)
lets each of its shops connect the shop's **own** Shopify store. The connector
then keeps that store's products in step with what the website sends, gives
each store an address on the website's domain, and tells the website, through
signed webhooks, where each product can be bought.

```
website ──REST (tenant API key)──▶ connector ──GraphQL──▶ Shopify
website ◀──signed webhooks──────── connector ◀──webhooks── Shopify
                                   connector ──DNS──▶ the website's Cloudflare zone
```

- **The connector never knows anything else about the website.** It knows the
  website's ids for its shops and products (`store_ref`, `product_ref`) and
  the product fields it is sent.
- **Shop owners own their stores.** The connector holds an encrypted access
  token. Disconnecting revokes it, and products already in Shopify stay.
- **Prices are never converted.** A product in a currency the store does not
  sell in, or with no price, is not sent, and the website is told why.

The integration reference for websites is [`docs/API.md`](docs/API.md). The
interactive OpenAPI page is at `/docs` on a running instance.

## Run it locally

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --port 8100
```

Tests run against a real Postgres database, `connector_test`:

```bash
./.venv/bin/pytest -q
./.venv/bin/ruff check .
./.venv/bin/mypy app scripts
```

## Register a website

```bash
python -m scripts.tenant create --slug janoubona --name Janoubona \
  --origin https://janoubona.net \
  --webhook-url https://janoubona.net/api/integrations/connector/webhooks \
  --subdomain-base janoubona.net
```

This prints the website's **API key** and **webhook secret** once. Send both
to the website's team privately. The connector stores only a hash of the key
and the secret encrypted.

Optional settings for a website:
- `set --slug ... --cloudflare-zone Z --cloudflare-token T` lets the website's
  stores get `<name>.<subdomain-base>` addresses. Use a token limited to DNS
  Edit on that zone.
- `set --slug ... --signup-url URL` sets where shops without a store are sent
  to sign up, for example a Shopify Partner referral link.
- `rotate-key` issues a new API key, and the old one stops working at once.
- `set --active no` turns a website off.

## Deploy

The service runs one Docker image on Railway with its own Postgres. The start
command runs `alembic upgrade head`.

**Environment variables:**

| Variable | |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://…` |
| `PUBLIC_BASE_URL` | This service's public origin |
| `SECRET_KEY` | Signs the OAuth state. At least 32 random characters |
| `ENCRYPTION_KEY` | A Fernet key. Never the same as `SECRET_KEY` |
| `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET` | The shared Shopify app's credentials |
| `SHOPIFY_API_VERSION` | Pinned, default `2025-07` |
| `APP_ENV=production` | Refuses to start with unsafe settings |

**Shopify app settings**, in the Partner or Dev Dashboard:
- App URL: `<PUBLIC_BASE_URL>`
- Allowed redirection URL: `<PUBLIC_BASE_URL>/shopify/callback`
- Compliance webhooks (`customers/data_request`, `customers/redact`,
  `shop/redact`): `<PUBLIC_BASE_URL>/shopify/webhooks`

`app/uninstalled` and `shop/update` are subscribed automatically on every
connect.
