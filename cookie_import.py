"""Parse & validate dola.com cookies pasted into the dashboard.

Supports three common browser-export formats:
  1. JSON array        (Cookie-Editor / EditThisCookie export)
  2. Netscape file     (curl / Cookies.txt format with #HttpOnly_ prefix)
  3. Header string     (`k=v; k2=v2; ...` from DevTools -> Network -> Cookie)

Cookie validation is lenient: only `sessionid` is strictly required for a
dola.com session. Missing msToken / s_v_web_id surfaces as a warning (not an
error) because the runtime treats msToken as optional (video_worker_ui.py:369).
"""
from __future__ import annotations

import json
import re
import time
from http.cookies import SimpleCookie
from pathlib import Path

DOLA_DOMAIN = "dola.com"
REQUIRED = ("sessionid",)
IMPORTANT_WARN = ("msToken", "s_v_web_id")

# Cookie pinned expiry when the source does not carry one (3 years from now).
# Browser session cookies are evicted on close without an expires field.
_EXPIRE_PIN = int(time.time()) + 86400 * 365 * 3

_NETSCAPE_LINE_RE = re.compile(
    r"^(?:#HttpOnly_)?([^\t]+)\t(TRUE|FALSE)\t([^\t]*)\t"
    r"(TRUE|FALSE)\t([^\t]*)\t([^\t]+)\t(.*)$"
)


def _belongs_to_dola(domain: str) -> bool:
    """True if a cookie domain is dola.com or a subdomain of it."""
    d = domain.strip().lstrip(".")
    return d.lower() == DOLA_DOMAIN or d.lower().endswith("." + DOLA_DOMAIN)


def _clean_domain(domain: str) -> str:
    """Normalizes a cookie domain for patchright add_cookies (leading dot ok)."""
    return domain.strip().lstrip(".")


# ---- Format-specific parsing ----


def _from_json_list(data: list) -> list[dict]:
    cookies = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name is None or value is None:
            continue
        domain = item.get("domain") or DOLA_DOMAIN
        if not _belongs_to_dola(domain):
            continue
        http_only = bool(item.get("httpOnly", item.get("http_only", False)))
        secure = bool(item.get("secure", False))
        same_site = item.get("sameSite") or None
        cookies.append({
            "name": str(name),
            "value": str(value),
            "domain": _clean_domain(domain),
            "path": str(item.get("path") or "/"),
            "httpOnly": http_only,
            "secure": secure,
            "sameSite": same_site,
            # expirationDate (seconds float) or expires; session cookie if absent
            "expires": item.get("expirationDate") or item.get("expires"),
        })
    return cookies


def _from_netscape(raw: str) -> list[dict]:
    cookies = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        m = _NETSCAPE_LINE_RE.match(line)
        if not m:
            continue
        domain, include_sub, path, is_secure, expiry, name, value = m.groups()
        if not _belongs_to_dola(domain):
            continue
        http_only = line.startswith("#HttpOnly_")
        cookies.append({
            "name": name,
            "value": value,
            "domain": _clean_domain(domain),
            "path": path or "/",
            "httpOnly": http_only,
            "secure": is_secure == "TRUE",
            "sameSite": None,
            "expires": expiry,  # integer epoch seconds; "0" = session cookie
        })
    return cookies


def _from_header_string(raw: str) -> list[dict]:
    c = SimpleCookie()
    try:
        c.load(raw)
    except Exception:
        # SimpleCookie chokes on some bytes; fall back to manual split.
        c = SimpleCookie()
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                c[k.strip()] = v.strip()
    cookies = []
    for morsel in c.values():
        cookies.append({
            "name": morsel.key,
            "value": morsel.value,
            "domain": DOLA_DOMAIN,
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": None,
            "expires": None,  # session cookie; gets pinned on inject
        })
    return cookies


# ---- Public API ----


def parse_cookie_input(raw: str) -> dict:
    """Parses paste input into {formats, cookies, invalid_lines, session_id}.

    format is one of: json, netscape, header, none. Cookies are deduped by
    (name, domain) with later entries winning; only dola.com-domain cookies
    are kept.
    """
    raw = (raw or "").strip()
    if not raw:
        return {"format": "none", "cookies": [], "invalid_lines": [], "session_id": ""}

    # 1. JSON array (most specific, try first)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            cookies = _from_json_list(data)
            if cookies:
                return _dedupe_result(cookies, "json", [])
    except Exception:
        pass

    # 2. Netscape file
    if _looks_like_netscape(raw):
        cookies = _from_netscape(raw)
        invalid = [l for l in raw.splitlines()
                   if l.strip() and not l.startswith("#")
                   and not _NETSCAPE_LINE_RE.match(l.strip())]
        return _dedupe_result(cookies, "netscape", invalid)

    # 3. Header string (fallback)
    cookies = _from_header_string(raw)
    return _dedupe_result(cookies, "header", [])


def _looks_like_netscape(raw: str) -> bool:
    """Heuristic: any non-comment line that is not a k=v pair."""
    lines = [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        return False
    if not any("\t" in l for l in lines):
        return False
    # A header string would contain lots of '=' but no tabs.
    return any("\t" in l for l in lines)


def _dedupe_result(cookies: list[dict], fmt: str, invalid: list[str]) -> dict:
    seen: dict[tuple[str, str], dict] = {}
    for c in cookies:
        key = (c["name"], c["domain"])
        seen[key] = c  # later entries win
    result = list(seen.values())
    return {
        "format": fmt,
        "cookies": result,
        "invalid_lines": invalid,
        "session_id": next((c["value"] for c in result if c["name"] == "sessionid"), ""),
    }


def validate_cookies(cookies: list[dict]) -> dict:
    """Returns {ok, errors, warnings}. Only sessionid is required."""
    names = {c["name"] for c in cookies}
    missing_required = [r for r in REQUIRED if r not in names]
    missing_warn = [w for w in IMPORTANT_WARN if w not in names]
    return {
        "ok": not missing_required,
        "errors": [f"missing required cookie: {r}" for r in missing_required],
        "warnings": [f"missing optional cookie: {w}" for w in missing_warn],
    }


def normalize_to_add_cookie(c: dict) -> dict:
    """Maps a parsed cookie into patchright context.add_cookies() shape.

    Session cookies (no expires) are pinned to a far-future expiry so the
    browser does not evict them on close (mirrors setup_cookie.py).
    """
    expires = c.get("expires")
    if expires is None or not float(expires):
        expires = _EXPIRE_PIN
    add = {
        "name": c["name"],
        "value": c.get("value", ""),
        "domain": c.get("domain") or DOLA_DOMAIN,
        "path": c.get("path") or "/",
        "httpOnly": bool(c.get("httpOnly")),
        "secure": bool(c.get("secure")),
        "expires": int(float(expires)),
    }
    same_site = c.get("sameSite")
    if same_site:
        # Patchright/Chromium protocol only accepts Strict | Lax | None.
        # Chrome exports "no_restriction" (== None) and "unspecified" (== unset);
        # normalize to the values the browser protocol understands.
        normalized = str(same_site).strip().lower()
        if normalized == "strict":
            add["sameSite"] = "Strict"
        elif normalized == "lax":
            add["sameSite"] = "Lax"
        elif normalized in ("none", "no_restriction"):
            add["sameSite"] = "None"
        # "unspecified" / anything else: omit sameSite entirely (default)
    return add


def build_cookie_header(cookies: list[dict]) -> str:
    """Builds a `k=v; k2=v2` header string for debugging/diagnostics."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


async def inject_cookies_into_profile(account: str, cookie_dicts: list[dict],
                                      headless: bool = True) -> int:
    """Injects cookies into the account's persistent profile.

    Returns the number of cookies persisted back from the browser. The profile
    directory is created if it does not exist (registration happens here).
    """
    from patchright.async_api import async_playwright
    from browser import LAUNCH_ARGS

    import config

    profile_dir = Path("accounts") / account
    profile_dir.mkdir(parents=True, exist_ok=True)

    add = [normalize_to_add_cookie(c) for c in cookie_dicts]
    if not add:
        return 0

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            args=LAUNCH_ARGS,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            proxy={"server": config.PROXY} if config.PROXY else None,
        )
        try:
            await context.add_cookies(add)
            await context.cookies("https://www.dola.com")  # force persist
            after = await context.cookies("https://www.dola.com")
            return len(after)
        finally:
            await context.close()