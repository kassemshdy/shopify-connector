# Security and incident response policy

This policy covers the Shopify connector (Halyoon Connect, serving
Janoubona Live Connect and the earlier YallaPay Connect app), the
merchant data it touches, and the people who operate it. It is what the
"security incident response policy" answer in Shopify's protected customer
data questions refers to.

**Owner of this policy:** the Halyoon account owner (currently Kassem
Shehady). PLACEHOLDER: confirm the name, and name a deputy who can act
when the owner cannot.
**Security contact:** `support@janoubona.net` (shown on
`connect.halyoon.com/support` when `SUPPORT_EMAIL` is set).
**Reviewed:** at least once a year, and after every incident.

## 1. What we hold and where

| Data | Where | Protection |
|---|---|---|
| Store address, name, currency | Connector database (Railway Postgres) | Private network only; no public port |
| Store access tokens | Same | Encrypted with `ENCRYPTION_KEY` (Fernet) before they are written |
| Products sent from websites | Same | Public catalogue data |
| Tenant (website) API keys | Same | Stored only as SHA-256 hashes |
| Customer name, email, phone, address, orders | **Not stored by the connector.** Read in Shopify by Yalla Pay under its own policy | — |
| App secrets, encryption key, Cloudflare tokens | Railway variables | Never in code, logs or chat |

Logs and Sentry reports exclude personal data and credentials; query strings
and request bodies are not sent to Sentry.

## 2. Everyday controls

- **Access.** Only the owners hold Railway, GitHub, Shopify Partner and
  Cloudflare access. Each person uses their own account, never a shared one.
  Access is removed the day someone stops working on the service.
- **Passwords and two-factor authentication.** Two-factor authentication is
  on for Railway, GitHub, Shopify Partner, Cloudflare and Google accounts.
  The connector's operator page refuses a password shorter than 16
  characters in production and locks out after repeated failures.
- **Least privilege.** The app requests only the scopes it uses; staff on
  Janoubona have roles that limit what each can change.
- **Separation of test and production.** Test stores are development stores;
  Janoubona's develop site has its own database and bucket; production
  secrets are never used in development.
- **Logging.** Every request to the connector is logged with its route and
  result (never its body); Railway keeps the logs for the incident window.
- **Backups.** The connector database is backed up by Railway; it holds no
  customer personal data. Janoubona's database is copied to private buckets
  daily.
- **Updates.** Dependencies are kept current; CI runs the test suite on every
  change before it is merged into `main`.

## 3. Incident response

An incident is any event that may have exposed, changed or lost merchant or
customer data, or let someone use the service who should not: a leaked
secret, an unexpected login, a database or log exposed, a compromised
device or account, or a report from Shopify, a merchant or a partner.

### Step 1 – Report (immediately)
Anyone who suspects an incident tells the policy owner at once, by phone or
WhatsApp, and writes down what they saw and when. Do not investigate alone
in a way that destroys evidence.

### Step 2 – Contain (within hours)
Do whatever applies:
- **Rotate secrets:** the Shopify app secret (Dev Dashboard → app →
  Settings, then `SHOPIFY_API_SECRET` on Railway), `ENCRYPTION_KEY` with a
  re-encryption of stored tokens, `SECRET_KEY`, `OPERATOR_PASSWORD`, tenant
  API keys and webhook secrets (operator page → the website → replace), and
  Cloudflare tokens.
- **Revoke access:** remove the affected person or device from Railway,
  GitHub, Shopify Partner and Cloudflare; end their sessions.
- **Revoke store tokens** if they may be exposed: disconnecting a store
  revokes its token; merchants then reconnect.
- **Disable a website** that is misbehaving (operator page → deactivate).
- Keep the logs: export the relevant Railway logs before they roll over.

### Step 3 – Assess (within 24 hours)
Establish what happened, when, which stores, merchants and customers are
affected, which data, and whether it is still happening. Record it in the
incident log (section 5).

### Step 4 – Notify (within 72 hours of becoming aware)
- **Affected merchants:** by email, with what happened, what data, what we
  did and what they should do. This is the commitment in our merchant terms
  (`connect.halyoon.com/terms`).
- **Yalla Pay:** if order or customer data in their systems may be involved.
- **Shopify:** through Partner support, when the incident touches Shopify
  data or app credentials.
- **Authorities:** where the law of the affected customers requires it
  (PLACEHOLDER: confirm with a lawyer which apply, e.g. Lebanon, GDPR for EU
  customers).

### Step 5 – Recover and review (within 2 weeks)
Restore normal service, confirm the cause is closed, and hold a short
review: what happened, why, what we change. Update this policy and the
code (with a test that would have caught it) where it applies.

## 4. Deletion requests

Shopify's `customers/data_request`, `customers/redact` and `shop/redact`
webhooks are honoured: the connector holds no customer data, and
`shop/redact` deletes the store's record and its subdomain. Uninstalling the
app deletes the store's token at once.

## 5. Incident log

Kept by the policy owner (a private document, not this repository): date,
summary, data and merchants affected, actions, notifications sent, review
outcome.

---
PLACEHOLDER: this policy was drafted for the owners; read it, name the
people, and have a lawyer check section 3 step 4 before relying on it.
