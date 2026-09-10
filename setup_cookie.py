"""Inject a dola.com cookie string into a new browser profile.

Usage:
    python setup_cookie.py <account_name>
    or read cookie from DOLA_COOKIE_STRING env var.

The cookie string must be passed via env var DOLA_COOKIE_STRING to avoid
command-line exposure. Accepts the raw browser Cookie header (`k=v; k2=v2`).
"""
import asyncio
import os
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path

from patchright.async_api import async_playwright

from browser import LAUNCH_ARGS
import config

# Cookie string from a logged-in browser typically contains only name=value,
# so no expires/secure flags survive copy-paste. Without an expires the browser
# treats them as session cookies and evicts them on close, so we pin an expiry.
_COOKIE_EXPIRE = int(time.time()) + 86400 * 365 * 3  # 3 years from now


def parse_cookie_string(raw: str) -> list[dict]:
    """Parse a `k=v; k2=v2; ...` cookie header into patchright add_cookies list."""
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
    result = []
    for morsel in c.values():
        result.append({
            "name": morsel.key,
            "value": morsel.value,
            "domain": "dola.com",
            "path": "/",
            "expires": _COOKIE_EXPIRE,
            "secure": True,  # dola.com is HTTPS; auth cookies must be Secure to be sent
        })
    return result


async def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "acc_cookie"
    raw = os.getenv("DOLA_COOKIE_STRING", "").strip()
    if not raw:
        print("Please provide cookie via DOLA_COOKIE_STRING env var.", file=sys.stderr)
        sys.exit(2)
    cookies = parse_cookie_string(raw)
    if not cookies:
        print("No cookies parsed from input.", file=sys.stderr)
        sys.exit(2)
    required = {"sessionid", "msToken", "s_v_web_id"}
    missing = required.difference(c["name"] for c in cookies)
    if missing:
        print(f"Missing required cookies: {missing}", file=sys.stderr)
        sys.exit(2)

    profile_dir = Path("accounts") / account
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=LAUNCH_ARGS,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            proxy={"server": config.PROXY} if config.PROXY else None,
        )
        try:
            await context.add_cookies(cookies)
            print(f"[{account}] Injected {len(cookies)} cookies into {profile_dir}")
            cookies_after = await context.cookies("https://www.dola.com")
            print(f"[{account}] Cookies persisted: {len(cookies_after)}")
        finally:
            await context.close()
    print(f"[{account}] Profile ready at accounts/{account}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)