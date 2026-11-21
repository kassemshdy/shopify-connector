# One Shopify app per store

Yalla Pay works with one Shopify app per store (custom distribution), and so
does the connector: a store registered on the operator page is installed,
connected and checked with its **own** app; every other store uses the default
app (`SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET`). Custom apps need no App Store
listing and no Shopify review.

## Onboarding a store (about 10 minutes)

1. **Create the app** — dev.shopify.com → Apps → Create app (manually).
   Name: `Janoubona - <shop name>`.
2. **Configure it** — GitHub → this repository → Actions → *Deploy Shopify app
   configuration* → Run workflow:
   - config: `store`
   - client_id: the new app's client id (Dev Dashboard → the app → Overview)
   - app_name: the same name as in step 1 (letters, digits, spaces, hyphens)

   This releases a version with the URLs, scopes and compliance webhooks from
   `shopify.app.store-template.toml`.
3. **Distribution** — Partner Dashboard → App distribution → the app →
   Distribution → **Custom distribution** → the store's `*.myshopify.com`
   address. Copy the install link it generates.
4. **Register it with the connector** — connect.halyoon.com/operator →
   *Shopify apps per store* → Add: store address, app name, client id, client
   secret (copied from the Dev Dashboard; it is stored encrypted and never
   shown again).
5. **Install** — open the install link while signed in to the store's admin and
   approve. The app page says "Installed".
6. **Link it on the website** — Janoubona → /admin/shopify → the shop → its
   `*.myshopify.com` address → Connect. It links at once. Then *Sync now* and
   *Prepare store*.
7. **Yalla Pay** — send them the store address, client id and client secret
   through a one-time secret link, never by email or chat.

## Removing a store's app

Remove it on the operator page and uninstall it from the store. The store's
connection keeps working until it is next reconnected, which then uses the
default app.

## Order matters

Register the app on the operator page (step 4) **before** installing it
(step 5): the install is completed with the credentials registered for that
store.
