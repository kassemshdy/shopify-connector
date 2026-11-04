# Shopify Connector

Merchants see it as **Halyoon Connect**, the Shopify app's name.

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

### Without a shell

When there is no shell on the deployment, set `BOOTSTRAP_TENANTS` to a JSON
list of websites (see `app/bootstrap.py`). They are created or updated at
startup, with an API key and webhook secret you choose. **Remove the variable
once the deploy has applied it**: the tenant is in the database, and the
variable would keep the key in plaintext.

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
| `APP_NAME` | The app's name on its pages, default `Halyoon Connect`; keep it the same as in the Shopify dashboard |
| `OPERATOR_PASSWORD` | Turns on the operator page at `/operator` (16+ characters). Unset: the page is a 404 |
| `SENTRY_DSN`, `SENTRY_RELEASE` | Optional error reports; no query string, body or key is sent |

**Shopify app settings**, in the Partner or Dev Dashboard:
- App URL: `<PUBLIC_BASE_URL>`
- Allowed redirection URL: `<PUBLIC_BASE_URL>/shopify/callback`
- Compliance webhooks (`customers/data_request`, `customers/redact`,
  `shop/redact`): `<PUBLIC_BASE_URL>/shopify/webhooks`

`app/uninstalled` and `shop/update` are subscribed automatically on every
connect.

**The App URL page** (`GET /`, `app/api/home.py`) is what a merchant sees on
opening the app in their Shopify admin. With Shopify's signed launch
parameters (checked, and at most ten minutes old) it shows which website the
store is connected to and how the sync is going, with a link back to that
website; without them it describes the app and names no store. It can be
framed only by that shop's admin. Its wording is a placeholder until the
owners choose their own.


## The operator page

`/operator` (`app/api/operator.py`) is for whoever runs the connector, not
for the websites: add a website, edit its addresses and webhook, set or share
a Cloudflare zone, replace its API key or webhook secret, disable it, and see
every store it has connected with its sync counts and last problem, plus its
recent webhooks. It does what `scripts/tenant.py` does, without a shell.

- One password, `OPERATOR_PASSWORD`; without it the page does not exist.
- The session is an HttpOnly, SameSite=Strict, Secure cookie on `/operator`,
  valid twelve hours, and invalid as soon as the password changes.
- Every form carries a token derived from the session.
- Five wrong passwords from one address lock it out for fifteen minutes.
- A new key or secret is shown once, on the page that made it. The Cloudflare
  token can be replaced but never read back.
- Pages cannot be framed and are not cached.

`tests/test_operator.py` pins each of these.
