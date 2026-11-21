# One Shopify app per store

Yalla Pay works with one Shopify app per store (custom distribution), and so
does the connector: a store registered on the operator page is installed,
connected and checked with its **own** app; every other store uses the default
app (`SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET`). Custom apps need no App Store
listing and no Shopify review.

The checklist below is the one that brought Ghaith Mouneh live on
21 Nov 2026, with every trap it hit written in where it bites. About 30
minutes per store, most of it in Shopify's admin.

## A. The Shopify store

1. **Create the store** under the Halyoon organization (Stores → Create
   store), with its address in **Lebanon**.
2. **Currency first.** A Lebanese address makes Shopify start the store in
   **LBP**, and the connector never converts prices: every USD product is
   skipped until this is changed. Settings → General → **⋯** beside *Currency
   display* → **Change store currency → USD**. Only possible before the first
   sale.
3. On the same page: time zone **Beirut** (it starts on US Eastern), and the
   shop's square logo under **Brand → Logo** (Yalla Pay shows it as the store's
   picture).
4. **Shipping.** Shopify adds its own rate, named «قياسي» ($4, free over $45)
   for a Lebanese store. Rename it **Standard Delivery** or delete it:
   *Prepare store* (step 12) updates a rate of that name and would otherwise add
   a second one. Add an **Express** rate yourself if the shop offers one; the
   connector creates only Standard.
5. *(Optional)* **Arabic storefront:** Settings → Languages → add Arabic,
   publish it and make it the default, so names and prices read right to left.

## B. The store's own app

6. **dev.shopify.com → Apps → Create app**, signed in to Halyoon:
   - Name: `Janoubona - <shop name>`
   - App URL: `https://connect.halyoon.com/`; *Embed app in Shopify admin* on;
     *Use legacy install flow* off
   - Allowed redirection URL: `https://connect.halyoon.com/shopify/callback`
   - Scopes (one line; Yalla Pay's and the connector's):

     ```
     read_products,write_products,read_publications,write_publications,read_themes,write_themes,write_files,read_markets,write_markets,read_shipping,write_shipping,read_inventory,read_locations,write_orders,write_draft_orders,read_fulfillments,unauthenticated_read_product_listings
     ```

   - **Create app.** Its *Settings* tab shows the client id and secret.
7. **Distribution.** Partner Dashboard → the app → Distribution → **Custom
   distribution** → the store's address, copied from its admin URL
   (`admin.shopify.com/store/<this part>` + `.myshopify.com`) → **Generate
   link**. This is permanent: a wrong address means a new app. Do not open the
   link yet.
8. **Register it with the connector.** connect.halyoon.com/operator →
   *Shopify apps per store* → Add: the store's address, the app's name, and
   **this app's** client id and secret, copied with the copy buttons. The page
   refuses the default app's client id; the secret is stored encrypted and never
   shown again.
9. **Install.** Open the install link while signed in to that store's admin
   and approve. Shopify's notice "This app is exclusive to your store" is
   normal for a custom app.

## C. Janoubona (`/admin/shopify`)

10. Pick the shop, enter its `*.myshopify.com` address, **Connect**. It links
    at once. A Shopify screen saying *Janoubona Live Connect … under review*
    means step 8 was skipped or used another address: cancel, fix the
    registration, connect again.
11. **«إرسال جميع المنتجات مجددًا».** Within a minute or two the count
    equals the shop's products and no red line shows. A count stuck at 0
    with nothing in Shopify usually means the currency (step 2); the card
    warns when it is not USD.
12. **«تجهيز المتجر»** with the delivery price. Its report should say Lebanon
    is in an active USD market and the rate is set.
13. *(Optional)* A **janoubona.net address**: save the name on the card, then
    in Shopify Settings → Domains → **Connect existing domain** →
    `<name>.janoubona.net`. Until that step Shopify answers the address with
    a Cloudflare "DNS points to prohibited IP" page.
14. *(Optional)* **«إرسال هوية المتجر»** sends the logo and social links into
    the theme.

## D. Opening the store

15. **Plan:** Settings → Plan → **Basic**, then make sure Settings → Billing
    has nothing outstanding. While billing is not active Shopify answers every
    call with **402**: the card shows *Shopify answered 402* and its product
    count drops (the products stay in the store; resend once billing clears).
16. **Payments:** Settings → Payments → Manual payment methods → **Cash on
    Delivery**. Shopify Payments is not available in Lebanon, and without a
    method the checkout says *This store can't accept payments right now*.
17. **Password off:** Online Store → Preferences → untick password protection.
18. **Theme:** Customize → the product card's Image → aspect ratio **Square**,
    on the home page and the collection page, so photos of different shapes
    line up.
19. *(Optional)* A **WhatsApp button**: a *Custom Liquid* section in the
    footer with a floating `https://wa.me/961…` link.

## E. Yalla Pay

20. Send them the store's address (both, if it was renamed), the app's client
    id and secret through a **one-time secret link**, never by email or chat.
21. Ask for a test order: products and prices show in their app, the delivery
    price is right (not $0), and the order arrives in Shopify → Orders.

## Removing a store's app

Remove it on the operator page and uninstall it from the store. The store's
connection keeps working until it is next reconnected, which then uses the
default app.

## A renamed store

A store keeps its first `*.myshopify.com` address for good; a new name only
adds an address that redirects to it, and Shopify answers with the first one.
Register and connect the store under either: when Shopify answers with the
permanent address and its signature checks out with the store's own app, the
connector accepts it and moves the app to that address.

## Compliance webhooks (optional)

Shopify's privacy webhooks can only be set from a configuration file. To add
them to a store's app: set the `SHOPIFY_CLI_PARTNERS_TOKEN` secret once, then
GitHub → Actions → *Deploy Shopify app configuration* with config `store`, the
app's client id and its name. It releases a version from
`shopify.app.store-template.toml`, with the same URLs and scopes as step 6.
