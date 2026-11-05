# Submitting Halyoon Connect for Shopify's app review

Why it is needed: editing a store's theme (`themeFilesUpsert`, used for the
store profile) needs an exemption Shopify grants per app, and its request form
asks for the app's listing. A listed app passes review first. Product sync
works without any of this.

Everything marked **PLACEHOLDER** is wording for the owners to choose.

## 1. Blocker to fix before submitting: install must start the connection

Reviewers install the app from its listing. Shopify requires an app to
**authenticate straight after install**: the install lands on the App URL,
and the app must go to the grant screen before showing anything else. Today a
store connects only from the website (`POST /v1/stores/{ref}/connect`). A store
installing from the listing therefore sees «Not connected yet» on
`GET /`, which fails the review.

Two ways to fix it, to decide before submitting:

- **(a) Install from Shopify, link to a website afterwards.** A signed launch
  from a store with no token is redirected to the grant screen. The store is
  saved with no website yet, and an administrator links it from the website's
  `/admin/shopify` page. This is a schema change, because a store currently
  always belongs to a tenant.
- **(b) Unlisted listing plus a reviewer website account.** The flow stays
  website-first. The reviewer gets an account on the develop site and
  connects from there (section 4). This is weaker: reviewers have rejected
  apps whose install does not start the connection.

Recommended: (a).

## 2. Configuration (`shopify.app.toml`)

The repository root holds the app's configuration: URLs, scopes and the
mandatory privacy webhooks. Before deploying it, check it against the version
live in the Partner dashboard:

```bash
shopify app config link        # once, pick Halyoon Connect
shopify app deploy             # releases a new app version from the file
```

| Requirement | Where it is met |
|---|---|
| HMAC on every webhook | `app/api/shopify_routes.py` (`verify_webhook_hmac`); 401 otherwise |
| `customers/data_request`, `customers/redact` | Acknowledged. No customer data is held, and no customer or order scope is requested. |
| `shop/redact` | Deletes the store row and its products, and releases its subdomain (`app/services/stores.py`). |
| `app/uninstalled` | Clears the token at once. |
| OAuth `state` and HMAC | Signed, expiring state; the return URL is allowlisted (`app/services/stores.py`). |
| Embedded page | `GET /` with App Bridge, framed only by the store's admin. |
| Privacy policy URL | `https://connect.halyoon.com/privacy` (PLACEHOLDER text) |
| Support URL | `https://connect.halyoon.com/support`. Set `SUPPORT_EMAIL` on Railway. |
| TLS | Railway and Cloudflare; HSTS still to be enabled in Cloudflare. |

## 3. Scope justifications (for the submission form)

| Scope | Why |
|---|---|
| `write_products`, `read_products` | Creates and updates the products the shop manages on its directory website. |
| `write_publications`, `read_publications` | Publishes those products to the Online Store sales channel. |
| `write_files` | Uploads the shop's logo to Files for the theme. |
| `read_themes`, `write_themes` | Sets the logo and social links in the live theme's settings (`config/settings_data.json`, and Horizon's footer `social-links` block). Only these settings are changed. |

Theme-file exemption request text (PLACEHOLDER):

> Halyoon Connect lets a local directory website keep a shop's Shopify store in
> step with its listing. When the shop asks, the app writes the shop's logo
> and social media links into the published theme's settings, and nothing else.
> It changes only setting ids that the theme itself declares
> (`logo`, `social_*_link`, or Horizon's `social-links` block). It never edits
> Liquid, CSS, JavaScript or templates, and it leaves an unchanged file alone.

## 4. Reviewer test instructions (PLACEHOLDER; final form depends on section 1)

1. Install Halyoon Connect on your development store.
2. Sign in at `https://<develop site>/admin` with the reviewer account. Give
   the credentials in the submission form only, never in this file.
3. Open **Shopify** in the admin, pick the shop «Review Shop», and choose
   **Connect** with your store's `*.myshopify.com` address. Approve the
   grant screen.
4. Choose **Sync now**. The shop's products appear in your store, published
   to the Online Store.
5. Change a product's price on the website. The new price reaches your store
   within a minute.
6. Choose **Send store profile**. The logo and social links appear in your
   theme. This step needs the exemption; until then the page shows why it
   stopped.
7. Uninstall the app. The website shows the store as disconnected.

Prepare beforehand:
- a reviewer staff account on the develop site;
- a demo shop with a logo, links and three products.

## 5. Listing (all PLACEHOLDER)

- **Name:** Halyoon Connect
- **Tagline (≤ 62 chars):** Sync your directory listing's products to Shopify
- **Description:** what it does (product sync, a subdomain, the store
  profile) and who it is for: shops listed on a partner directory website.
- **Pricing:** Free.
- **Distribution:** public, but **unlisted** if it should not appear in App
  Store search. It is still reviewed.

## 6. Screencast and screenshots

Screencast (2-4 minutes, English, a voice-over or captions):
1. installation and the grant screen;
2. the app page in the admin;
3. connecting from the website and the first sync;
4. a price change arriving;
5. the store profile;
6. uninstalling.

Screenshots (1600×900):
- the app page, connected;
- products in the store;
- the website's Shopify admin page;
- the theme with the logo and links.

## 7. Checklist

- [ ] Section 1 decided and built
- [ ] Owners' wording for the privacy page, the listing and the exemption text
- [ ] `SUPPORT_EMAIL` set on the connector
- [ ] `shopify app deploy` from `shopify.app.toml`
- [ ] Reviewer account and demo shop on the develop site
- [ ] Screencast and screenshots
- [ ] Submit; then request the theme-file exemption with the listing URL
- [ ] HSTS on in Cloudflare
