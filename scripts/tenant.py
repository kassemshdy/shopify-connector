"""Operator CLI: register a website and manage its settings.

    python -m scripts.tenant create --slug janoubona --name Janoubona \
        --origin https://janoubona.net \
        --webhook-url https://janoubona.net/api/integrations/connector/webhooks \
        --subdomain-base janoubona.net
    python -m scripts.tenant rotate-key --slug janoubona
    python -m scripts.tenant set --slug janoubona --cloudflare-zone ZONE --cloudflare-token TOKEN \
        --signup-url https://shopify.pxf.io/... --webhook-url ... --origin ...
    python -m scripts.tenant list

The API key and webhook secret are printed once; only the key's hash and the
secret's ciphertext are stored.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import get_settings
from app.crypto import encrypt
from app.db import SessionLocal
from app.models import Tenant
from app.services import tenants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tenant")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("--slug", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--origin", action="append", default=[], required=True)
    create.add_argument("--webhook-url")
    create.add_argument("--subdomain-base")
    rotate = sub.add_parser("rotate-key")
    rotate.add_argument("--slug", required=True)
    update = sub.add_parser("set")
    update.add_argument("--slug", required=True)
    update.add_argument("--origin", action="append")
    update.add_argument("--webhook-url")
    update.add_argument("--subdomain-base")
    update.add_argument("--cloudflare-zone")
    update.add_argument("--cloudflare-token")
    update.add_argument("--signup-url")
    update.add_argument("--active", choices=["yes", "no"])
    sub.add_parser("list")
    args = parser.parse_args(argv)
    settings = get_settings()

    with SessionLocal() as db:
        if args.cmd == "create":
            tenant, key, secret = tenants.create(
                db, settings, slug=args.slug, name=args.name, return_origins=args.origin,
                webhook_url=args.webhook_url, subdomain_base=args.subdomain_base,
            )
            print(f"tenant:          {tenant.slug}")
            print(f"API key:         {key}")
            print(f"webhook secret:  {secret}")
            print("Store both now; they are not shown again.")
            return 0
        if args.cmd == "list":
            for t in db.scalars(select(Tenant).order_by(Tenant.slug)):
                print(f"{t.slug}\t{t.api_key_prefix}…\tactive={t.active}\t{t.return_origins}")
            return 0
        found = db.scalars(select(Tenant).where(Tenant.slug == args.slug)).first()
        if found is None:
            print(f"no tenant {args.slug}", file=sys.stderr)
            return 1
        tenant = found
        if args.cmd == "rotate-key":
            print(f"API key: {tenants.rotate_key(db, tenant)}")
            return 0
        if args.origin:
            tenant.return_origins = " ".join(o.rstrip("/") for o in args.origin)
        if args.webhook_url is not None:
            tenant.webhook_url = args.webhook_url or None
        if args.subdomain_base is not None:
            tenant.subdomain_base = args.subdomain_base or None
        if args.cloudflare_zone is not None:
            tenant.cloudflare_zone_id = args.cloudflare_zone or None
        if args.cloudflare_token is not None:
            tenant.cloudflare_token_enc = (
                encrypt(settings, args.cloudflare_token) if args.cloudflare_token else None
            )
        if args.signup_url is not None:
            tenant.signup_url = args.signup_url or None
        if args.active is not None:
            tenant.active = args.active == "yes"
        db.commit()
        print(f"updated {tenant.slug}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
