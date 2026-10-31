# Handoff: where the connector work stands (31 Oct 2026)

Written at the end of the session that built this service, for whoever
continues it. No secrets are in this file. Every credential is a Railway
variable.

## What exists

- **This repository**, branch `claude/initial-connector` (not merged to `main`,
  no PR yet):
  - the Shopify Connector, one Shopify app serving many websites;
  - 21 tests, ruff and mypy all pass.
  - `README.md` covers running and operating it. `docs/API.md` is the
    reference for websites.
- **Janoubona as a client:** `kassemshdy/South`, PR #320, branch
  `claude/laughing-pasteur-cm22ww`. Commit `7c77aac` removed Janoubona's
  built-in Shopify code: it now calls this connector and mirrors its signed
  webhooks in `store_links` / `item_links`. All 778 backend tests pass. See
  `docs/COMMERCE.md` there.

## Railway: project `janoubona`, environment `shopify-test`

| Service | State |
|---|---|
| `shopify-connector` | Deployed from this branch: https://shopify-connector-shopify-test.up.railway.app, `/health` = ok, `APP_ENV=production`. Variables set: `SECRET_KEY`, `ENCRYPTION_KEY`, `SHOPIFY_API_KEY/SECRET` (copied from the test API), `DATABASE_URL` -> `Postgres-DXdS`. |
| `Postgres-DXdS` | The connector's database. **It has a temporary public TCP proxy, `switchyard.proxy.rlwy.net:43494`. Remove it** (Railway: service → Settings → Networking → TCP Proxy → delete). It was opened to register the first tenant from outside, which did not work, because the sandbox blocks raw TCP. |
| `api-shopify-test` | Janoubona, deployed from PR #320's branch. It runs the connector-client code, but **still has the old variables** (`SHOPIFY_*`, `CLOUDFLARE_*`, `INTEGRATION_ENCRYPTION_KEY`, `STORE_SUBDOMAIN_BASE`) and **no `CONNECTOR_*` yet**, so its Shopify card is hidden. |
| `Postgres-QVww` | Janoubona test database. Migration `b7e2d4f6a913` cleans out the old Shopify tables. |

## Next steps

1. **Remove the TCP proxy** on `Postgres-DXdS` (above).
2. **Register Janoubona as a tenant, from inside Railway.** Two ways:
   - Open a Railway shell on `shopify-connector` and run:
     ```bash
     python -m scripts.tenant create --slug janoubona --name Janoubona \
       --origin https://api-shopify-test-shopify-test.up.railway.app \
       --webhook-url https://api-shopify-test-shopify-test.up.railway.app/api/integrations/connector/webhooks \
       --subdomain-base janoubona.net
     ```
     Keep the printed API key and webhook secret.
   - Or add a one-time bootstrap: create the tenant at startup from an env
     variable, then delete the variable.

   Then `set --slug janoubona --cloudflare-zone … --cloudflare-token …`,
   using the zone and token that are on `api-shopify-test` today. Optionally
   add `--signup-url <Shopify referral link>`.
3. **On `api-shopify-test`:**
   - set `CONNECTOR_URL=https://shopify-connector-shopify-test.up.railway.app`,
     `CONNECTOR_API_KEY` and `CONNECTOR_WEBHOOK_SECRET`;
   - delete `SHOPIFY_*`, `CLOUDFLARE_*`, `INTEGRATION_ENCRYPTION_KEY` and
     `STORE_SUBDOMAIN_BASE`.
4. **In the Shopify app** (Partner or Dev Dashboard):
   - App URL: `https://shopify-connector-shopify-test.up.railway.app`
   - Redirect: `…/shopify/callback`
   - Compliance webhooks: `…/shopify/webhooks`
5. **End to end:**
   - Sign in on the test site as demo owner `03111111` and connect the dev
     store (it was connected before as `tfolekids`; reconnect it).
   - Check that products appear in Shopify and buy links show on Janoubona.
   - Change a price; the connector logs "Product pushed".
6. **Then:**
   - open a PR for this branch into `main`;
   - add CI secrets if needed (CI uses a Postgres service; no secrets are
     required);
   - merge South PR #320 into `develop-claude` once its CI is green.

## Things not to undo

- No Shopify or Cloudflare credential belongs in Janoubona. They live here,
  encrypted.
- Prices are never converted.
- Tenant API keys are stored as SHA-256 only.
- Every query is scoped to the tenant.
