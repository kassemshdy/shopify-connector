# Handoff: where the connector work stands (1 Nov 2026)

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
| `api-shopify-test` | Janoubona, deployed from PR #320's branch, **connected to the connector**: `CONNECTOR_URL`, `CONNECTOR_API_KEY` and `CONNECTOR_WEBHOOK_SECRET` are set, and the old `SHOPIFY_*`, `CLOUDFLARE_*`, `INTEGRATION_ENCRYPTION_KEY` and `STORE_SUBDOMAIN_BASE` are removed. Checked on 1 Nov: the owner's Shopify card loads through the connector (`subdomains_available: true`), and the webhook route refuses unsigned calls (401). |
| `Postgres-QVww` | Janoubona test database. Migration `b7e2d4f6a913` cleans out the old Shopify tables. |

## Done on 1 Nov

- **Janoubona registered as a tenant** through `BOOTSTRAP_TENANTS`
  (`app/bootstrap.py`), with its Cloudflare zone and token. The variable was
  removed afterwards, and the tenant lives in the database.
- **`api-shopify-test` switched to the connector**: its variables were
  swapped and both services redeployed.

## Next steps

1. **Remove the TCP proxy** on `Postgres-DXdS` (above). It is still open.
2. **In the Shopify app** (Partner or Dev Dashboard):
   - App URL: `https://shopify-connector-shopify-test.up.railway.app`
   - Redirect: `https://shopify-connector-shopify-test.up.railway.app/shopify/callback`
   - Compliance webhooks: `https://shopify-connector-shopify-test.up.railway.app/shopify/webhooks`
3. **Reconnect the dev store.** On the test site, sign in as demo owner
   `03111111`, open the shop's products page and press Connect. The old
   connection lived in Janoubona's database and is gone.
   - Then check that products appear in Shopify and buy links show on
     Janoubona.
   - Change a price; the connector logs "Product pushed".
   - Save the `tfolekids` subdomain again; the CNAME already exists in
     Cloudflare, so delete the old record first or pick a new name.
4. **Optional:** set a sign-up link with `python -m scripts.tenant set
   --slug janoubona --signup-url <Shopify referral link>`, or through
   `BOOTSTRAP_TENANTS`.
5. **Then:**
   - open a PR for this branch into `main`;
   - merge South PR #320 into `develop-claude` once its CI is green.

## Things not to undo

- No Shopify or Cloudflare credential belongs in Janoubona. They live here,
  encrypted.
- Prices are never converted.
- Tenant API keys are stored as SHA-256 only.
- Every query is scoped to the tenant.
