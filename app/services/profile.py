"""The store's profile: its logo and social links, written into its live theme.

A website sends what it knows about the shop (``PUT /v1/stores/{ref}/profile``)
and a job does the rest:

1. the logo is uploaded to the store's Files, Shopify fetching it from the
   website's public address, and its ``shopify://shop_images/...`` name kept;
2. the main theme's ``config/settings_schema.json`` says which settings the
   theme has; ``config/settings_data.json`` holds their values;
3. only settings the theme declares are set -- the logo and the
   ``social_*_link`` fields of Dawn-family themes -- and nothing else changes;
4. Horizon-family themes keep their social links in a ``social-links`` block
   of ``sections/footer-group.json`` instead (``instagram_url`` ...); every
   such block there gets the links too;
5. the changed files are written back in one ``themeFilesUpsert``.

Two things Shopify decides, not us. Editing theme files needs the
``write_themes`` scope **and** an exemption Shopify grants per app; until it
does, the write answers ACCESS_DENIED, which ends the job at once with a
plain reason rather than five retries. And the store's name and homepage
description have no API an app may use, so they are not part of the profile.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit

from app.models import Store
from app.shopify import ShopifyClient, ShopifyError, raise_user_errors

logger = logging.getLogger(__name__)

PROFILE = "store.profile"
NEEDED_SCOPES = frozenset({"read_themes", "write_themes", "write_files"})

#: Website-side names to the setting ids Dawn-family themes use.
SOCIAL_SETTINGS = {
    "facebook": "social_facebook_link",
    "instagram": "social_instagram_link",
    "tiktok": "social_tiktok_link",
    "x": "social_twitter_link",
    "twitter": "social_twitter_link",
    "youtube": "social_youtube_link",
    "pinterest": "social_pinterest_link",
    "snapchat": "social_snapchat_link",
}

SETTINGS_DATA = "config/settings_data.json"
SETTINGS_SCHEMA = "config/settings_schema.json"
FOOTER_GROUP = "sections/footer-group.json"
SOCIAL_BLOCK = "social-links"
LOGO_POLLS, LOGO_POLL_SECONDS = 10, 2.0

MAIN_THEME = """
query MainTheme($files: [String!]!) {
  themes(first: 1, roles: [MAIN]) {
    nodes {
      id
      name
      files(filenames: $files, first: 3) {
        nodes { filename body { ... on OnlineStoreThemeFileBodyText { content } } }
      }
    }
  }
}
"""

FILE_CREATE = """
mutation FileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { id fileStatus ... on MediaImage { image { url } } }
    userErrors { field message }
  }
}
"""

FILE_STATUS = """
query FileStatus($id: ID!) {
  node(id: $id) { ... on MediaImage { fileStatus image { url } } }
}
"""

THEME_UPSERT = """
mutation ThemeUpsert($themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
  themeFilesUpsert(themeId: $themeId, files: $files) {
    upsertedThemeFiles { filename }
    userErrors { field message code }
  }
}
"""


class ThemeAccessDenied(ShopifyError):
    """Shopify has not granted this app the theme-file exemption: do not retry."""


def missing_scopes(store: Store) -> set[str]:
    granted = {s.strip() for s in store.scopes.split(",") if s.strip()}
    # A write scope implies its read scope.
    granted |= {s.replace("write_", "read_") for s in granted if s.startswith("write_")}
    return set(NEEDED_SCOPES) - granted


_COMMENT = re.compile(r"^\s*/\*.*?\*/\s*", re.S)


def _split_comment(text: str) -> tuple[str, str]:
    match = _COMMENT.match(text)
    return (match.group(0), text[match.end():]) if match else ("", text)


def _declared_settings(schema_text: str) -> set[str]:
    _, body = _split_comment(schema_text)
    ids: set[str] = set()
    for group in json.loads(body):
        for setting in group.get("settings", []) if isinstance(group, dict) else []:
            if isinstance(setting, dict) and setting.get("id"):
                ids.add(str(setting["id"]))
    return ids


def apply_settings(
    data_text: str, schema_text: str, values: dict[str, str]
) -> tuple[str, list[str]]:
    """Return the new settings_data.json and the setting ids it changed.

    Only ids the theme declares are written; ``current`` set to a preset name
    is first expanded into that preset's values, as the theme editor does.
    """
    declared = _declared_settings(schema_text)
    comment, body = _split_comment(data_text)
    data: dict[str, Any] = json.loads(body)
    current = data.get("current")
    if isinstance(current, str):
        current = dict((data.get("presets") or {}).get(current) or {})
    elif not isinstance(current, dict):
        current = {}
    changed = [
        key for key, value in values.items() if key in declared and current.get(key) != value
    ]
    for key in changed:
        current[key] = values[key]
    data["current"] = current
    return comment + json.dumps(data, ensure_ascii=False, indent=2), changed


def apply_social_blocks(text: str, links: dict[str, str]) -> tuple[str, list[str]]:
    """Set ``<platform>_url`` on every ``social-links`` block of a section group.

    ``links`` maps Horizon's platform names (``instagram``, ``twitter`` ...)
    to addresses. Returns the new file and the settings changed, as
    ``<block id>.<setting>``.
    """
    comment, body = _split_comment(text)
    data: dict[str, Any] = json.loads(body)
    changed: list[str] = []

    def walk(blocks: Any) -> None:
        if not isinstance(blocks, dict):
            return
        for block_id, block in blocks.items():
            if not isinstance(block, dict):
                continue
            if block.get("type") == SOCIAL_BLOCK:
                settings = block.setdefault("settings", {})
                for platform, url in links.items():
                    key = f"{platform}_url"
                    if settings.get(key) != url:
                        settings[key] = url
                        changed.append(f"{block_id}.{key}")
            walk(block.get("blocks"))

    for section in (data.get("sections") or {}).values():
        if isinstance(section, dict):
            walk(section.get("blocks"))
    if not changed:
        return text, []
    return comment + json.dumps(data, ensure_ascii=False, indent=2), changed


def _theme(client: ShopifyClient) -> tuple[str, dict[str, str]]:
    nodes = (client.graphql(MAIN_THEME,
                            {"files": [SETTINGS_DATA, SETTINGS_SCHEMA, FOOTER_GROUP]})
             .get("themes") or {}).get("nodes") or []
    if not nodes:
        raise ShopifyError("the store has no published theme")
    theme = nodes[0]
    files = {
        f["filename"]: ((f.get("body") or {}).get("content") or "")
        for f in (theme.get("files") or {}).get("nodes") or []
    }
    if SETTINGS_DATA not in files or SETTINGS_SCHEMA not in files:
        raise ShopifyError("the theme's settings could not be read")
    return str(theme["id"]), files


def _upload_logo(client: ShopifyClient, url: str, alt: str) -> str:
    """Upload the logo and return its ``shopify://shop_images/...`` reference."""
    result = client.graphql(
        FILE_CREATE,
        {"files": [{"originalSource": url, "contentType": "IMAGE", "alt": alt[:500]}]},
    ).get("fileCreate") or {}
    raise_user_errors(result)
    created = (result.get("files") or [{}])[0]
    file_id, image = created.get("id"), (created.get("image") or {}).get("url")
    for _ in range(LOGO_POLLS):
        if image:
            break
        time.sleep(LOGO_POLL_SECONDS)
        node = client.graphql(FILE_STATUS, {"id": file_id}).get("node") or {}
        if node.get("fileStatus") == "FAILED":
            raise ShopifyError("Shopify could not read the logo image")
        image = (node.get("image") or {}).get("url")
    if not image:
        raise ShopifyError("the logo is still being processed by Shopify")
    return "shopify://shop_images/" + urlsplit(str(image)).path.rsplit("/", 1)[-1]


def push_profile(store: Store, client: ShopifyClient, payload: dict[str, Any]) -> list[str]:
    """Write the profile into the store's main theme; return the settings changed."""
    theme_id, files = _theme(client)
    values: dict[str, str] = {}
    block_links: dict[str, str] = {}
    for name, link in (payload.get("social") or {}).items():
        setting = SOCIAL_SETTINGS.get(str(name).lower())
        if setting and link:
            values[setting] = str(link)
            # Horizon's names: social_twitter_link -> twitter_url.
            block_links[setting.removeprefix("social_").removesuffix("_link")] = str(link)
    declared = _declared_settings(files[SETTINGS_SCHEMA])
    if payload.get("logo_url") and "logo" in declared:
        values["logo"] = _upload_logo(
            client, str(payload["logo_url"]), str(payload.get("name") or "")
        )
    content, changed = apply_settings(files[SETTINGS_DATA], files[SETTINGS_SCHEMA], values)
    writes = {SETTINGS_DATA: content} if changed else {}
    if FOOTER_GROUP in files and block_links:
        footer, footer_changed = apply_social_blocks(files[FOOTER_GROUP], block_links)
        if footer_changed:
            writes[FOOTER_GROUP] = footer
            changed += footer_changed
    if not changed:
        logger.info("Store profile already up to date", extra={"store_id": str(store.id)})
        return []
    result = client.graphql(
        THEME_UPSERT,
        {"themeId": theme_id,
         "files": [{"filename": name, "body": {"type": "TEXT", "value": value}}
                   for name, value in writes.items()]},
    ).get("themeFilesUpsert") or {}
    errors = result.get("userErrors") or []
    if any(str(e.get("code") or "") == "ACCESS_DENIED" for e in errors):
        raise ThemeAccessDenied(
            "Shopify has not yet allowed this app to edit themes (the write_themes exemption)."
        )
    raise_user_errors(result)
    logger.info("Store profile pushed", extra={"store_id": str(store.id), "settings": changed})
    return changed
