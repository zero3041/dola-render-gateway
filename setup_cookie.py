"""Inject a dola.com cookie into a browser profile and keep the original.

Usage:
    python setup_cookie.py <account_name> [cookie_file]
    # or pass the cookie via env var DOLA_COOKIE_STRING

Accepts all three browser-export formats (JSON array / Netscape file /
`k=v; k2=v2` header string) through the same parser as the dashboard import.
The original text is stored in the pool database so generate / resume / verify
can re-inject it automatically if the profile loses its session.
"""
import asyncio
import os
import sys

import cookie_import


async def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "acc_cookie"
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8-sig") as f:
            raw = f.read()
    else:
        raw = os.getenv("DOLA_COOKIE_STRING", "")
    raw = raw.strip()
    if not raw:
        print("Provide a cookie file argument or DOLA_COOKIE_STRING env var.",
              file=sys.stderr)
        sys.exit(2)

    parsed = cookie_import.parse_cookie_input(raw)
    check = cookie_import.validate_cookies(parsed["cookies"])
    if not check["ok"]:
        print(f"Invalid cookie input: {'; '.join(check['errors'])}", file=sys.stderr)
        sys.exit(2)
    for warning in check["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    count = await cookie_import.inject_cookies_into_profile(account, parsed["cookies"])
    if count <= 0:
        print("No cookies persisted into browser profile.", file=sys.stderr)
        sys.exit(2)

    # Keep the original paste for automatic re-injection in pool flows.
    from browser_pool import BrowserPool
    BrowserPool().set_raw_cookie(account, raw)

    print(f"[{account}] Injected {len(parsed['cookies'])} cookie(s) "
          f"({parsed['format']}) into accounts/{account}; original cookie stored")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
