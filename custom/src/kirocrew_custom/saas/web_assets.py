"""Serve a fixed browser asset allowlist, never caller-selected filesystem paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ASSETS = {
    "/knowledge.mjs": ("knowledge.mjs", "text/javascript"),
    "/": ("index.html", "text/html"),
    "/app.mjs": ("app.mjs", "text/javascript"),
    "/organizations.mjs": ("organizations.mjs", "text/javascript"),
    "/oauth.mjs": ("oauth.mjs", "text/javascript"),
    "/i18n.mjs": ("i18n.mjs", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


def asset_response(event: dict[str, Any], domain: str) -> dict[str, Any] | None:
    if event.get("requestContext", {}).get("http", {}).get("method") != "GET":
        return None
    asset = ASSETS.get(event.get("rawPath", ""))
    if asset is None:
        return None
    filename, content_type = asset
    return {
        "statusCode": 200,
        "headers": {
            "content-type": content_type + "; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
            + "connect-src 'self' "
            + domain
            + "; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        },
        "body": (Path(__file__).parent / "web" / filename).read_text(encoding="utf-8"),
    }
