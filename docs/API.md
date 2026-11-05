# Shopify Connector API (v1)

This is for websites (tenants) that let their shops sell through their own
Shopify stores. All requests and responses are JSON over HTTPS.

## Authentication

```
Authorization: Bearer sc_live_...
```

The operator gives you a key and a **webhook secret** once. A missing or wrong
key answers `401`.

Errors look like this:

```json
{"error": {"code": "bad_shop", "message": "..."}}
```

## Your ids

You choose the ids, and the connector never needs anything else about your
shops:

- `store_ref`: your id for a shop, for example its database id.
- `product_ref`: your id for one of its products.

## The flow

1. **The owner presses "Connect Shopify" on your site.** You call `POST
   /v1/stores/{store_ref}/connect` with their store name and a `return_url`
   on your site, then redirect the browser to the `authorize_url` you get
   back.
2. **The owner approves in Shopify.** The connector sends the browser back to
   your `return_url` with `?shopify=connected`, or `?shopify=<error code>`. You
   also receive a `store.connected` webhook.
3. **You send the shop's products** with `POST
   /v1/stores/{store_ref}/products/bulk`. After that, send `PUT` or `DELETE`
   for each product whenever it changes on your site.
4. **The connector pushes them to Shopify** within seconds and sends you
   `product.synced` with each product's `buy_url`. Show a "Buy online" link
   pointing to it.
5. **Keeping your copy in step.** If the owner uninstalls the app in Shopify,
   you receive `store.updated` (status `UNINSTALLED`) and `product.synced`
   with `buy_url: null`. Hide the links.

## Endpoints

### `GET /v1/tenant`

```json
{"slug": "janoubona", "name": "Janoubona", "signup_url": "https://...",
 "subdomain_base": "janoubona.net", "subdomains_available": true}
```

Your own settings. Use them to decide what to show before a shop connects.

### `POST /v1/stores/{store_ref}/connect`

Request: `{"shop": "souk" | "souk.myshopify.com", "return_url": "https://your-site/..."}`

Response: `{"authorize_url": "https://souk.myshopify.com/admin/oauth/authorize?..."}`

- `return_url` must be on your site's allow-list (the origins you registered).
- Errors: `422 bad_shop`, `422 bad_return_url`.

### `GET /v1/stores/{store_ref}`

```json
{
  "store_ref": "42",
  "status": "PENDING | CONNECTED | UNINSTALLED",
  "shop_domain": "souk.myshopify.com",
  "storefront_url": "https://souk.janoubona.net",
  "subdomain": "souk",
  "subdomain_hostname": "souk.janoubona.net",
  "subdomain_live": true,
  "currency": "USD",
  "connected_at": "...",
  "last_sync_at": "...",
  "last_error": null,
  "products_synced": 12,
  "jobs_pending": 0,
  "signup_url": "https://..."
}
```

`signup_url` is where to send a shop that has no Shopify store yet. Shopify
offers no way to create stores automatically.

### `DELETE /v1/stores/{store_ref}` → `204`

Revokes the app's access and removes the store's DNS record. Products already
in the Shopify store stay; they are the owner's.

### `POST /v1/stores/{store_ref}/subdomain`

Request: `{"subdomain": "souk"}`

- Creates `souk.<your domain>` as a DNS-only CNAME to Shopify, in your
  Cloudflare zone. The owner then adds that domain once in Shopify (Settings →
  Domains → Connect existing domain). `subdomain_live` becomes true when the
  store answers on it.
- Errors: `422 bad_subdomain`, `422 reserved_subdomain`, `409 subdomain_taken`,
  `404 subdomains_off` (your site has no Cloudflare zone configured).

### `PUT /v1/stores/{store_ref}/profile` → `202`

Writes the shop's logo and social links into its store's live theme.

```json
{ "name": "Boutique Lilac",
  "logo_url": "https://www.janoubona.net/media/.../logo.jpg",
  "social": { "instagram": "https://instagram.com/...", "facebook": "https://facebook.com/..." } }
```

- **The logo** is uploaded to the store's Files: Shopify fetches it from `logo_url`, which must be a public https address.
- **Social keys:** `facebook`, `instagram`, `tiktok`, `x`, `youtube`, `pinterest`, `snapchat`.
- **Only settings the theme declares are written** (Dawn-family themes have them all). Nothing else in the theme changes, and an unchanged profile writes nothing.
- **Not covered:** the store's name and its homepage description. Shopify gives apps no way to set them.

**Errors:**
- `409 reconnect_required` means the store was connected before theme access was asked for. Connect it again.
- `409 not_connected`: the store is not connected.

**Shopify's exemption:** theme writes also need an exemption Shopify grants each app. Until it is granted, the job ends with *"Shopify has not yet allowed this app to edit themes"* in the store's `last_error`, and is not retried.

### `PUT /v1/stores/{store_ref}/products/{product_ref}` → `202`

```json
{
  "title": "Zaatar man'oushe",
  "description": "Plain text; blank lines become paragraphs.",
  "price": "4.50",
  "currency": "USD",
  "images": ["https://your-site/media/zaatar.jpg"],
  "available": true,
  "vendor": "Abu Khalil Bakery"
}
```

- The response is the product's state, `QUEUED` at first.
- Images must be public `https://` URLs; Shopify downloads them.
- `available: false` keeps the product in Shopify as a draft, not for sale.
- A product with no `price`, or a `currency` other than the store's, is not
  pushed. Its state becomes `SKIPPED`, and `error` says why.

### `POST /v1/stores/{store_ref}/products/bulk` → `202`

Request: `{"products": [{"product_ref": "7", ...same fields...}, ...]}`, up to
500 per call.

Response: `{"queued": n}`

### `GET /v1/stores/{store_ref}/products/{product_ref}`

```json
{"store_ref": "42", "product_ref": "7", "state": "QUEUED | SYNCED | SKIPPED | FAILED",
 "handle": "zaatar", "active": true,
 "buy_url": "https://souk.janoubona.net/products/zaatar", "error": null, "synced_at": "..."}
```

### `DELETE /v1/stores/{store_ref}/products/{product_ref}` → `202`

Deletes the product from the Shopify store.

## Webhooks the connector sends you

These are POSTed to the `webhook_url` you registered:

```
X-Connector-Event: product.synced
X-Connector-Delivery: <uuid>
X-Connector-Signature: t=1730000000,v1=<hex HMAC-SHA256>
```

```json
{"id": "...", "event": "product.synced", "created_at": "...", "data": {...}}
```

| Event | `data` |
|---|---|
| `store.connected`, `store.updated`, `store.disconnected` | `store_ref`, `status`, `shop_domain`, `storefront_url`, `subdomain`, `subdomain_hostname`, `currency`, `last_error` |
| `product.synced`, `product.failed` | `store_ref`, `product_ref`, `state`, `handle`, `active`, `buy_url`, `error` |

**Delivery and retries:**
- Answer `2xx` within 30 seconds.
- Anything else is retried with backoff (30 s, 1 m, 2 m … up to 6 h), up to 10
  attempts.
- Events can arrive more than once. `id` is unique per event, so you can skip
  repeats.

### Verifying the signature

Compute `HMAC-SHA256(secret, "{t}." + raw_body)` as hex and compare it with
`v1`. Also refuse a `t` more than 5 minutes old.

**Python**
```python
import hashlib, hmac, time
def verify(secret: str, body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])
```

**PHP**
```php
function verify(string $secret, string $body, string $header): bool {
    parse_str(str_replace(',', '&', $header), $p);
    if (abs(time() - (int)$p['t']) > 300) return false;
    $mac = hash_hmac('sha256', $p['t'] . '.' . $body, $secret);
    return hash_equals($mac, $p['v1']);
}
```

**Node**
```js
const crypto = require('crypto');
function verify(secret, body /* Buffer */, header) {
  const p = Object.fromEntries(header.split(',').map(s => s.split('=')));
  if (Math.abs(Date.now() / 1000 - Number(p.t)) > 300) return false;
  const mac = crypto.createHmac('sha256', secret).update(`${p.t}.`).update(body).digest('hex');
  return crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(p.v1));
}
```

Always verify against the **raw** request body, before parsing it.
