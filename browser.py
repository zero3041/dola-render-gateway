"""Patchright persistent context launcher: Explicit proxy and anti-detection parameters."""
from pathlib import Path

import config

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]


async def launch_account_context(p, account: str, headless: bool = None, use_extension: bool = False):
    """Launches accounts/<account> profile, returns BrowserContext. Caller must close.

    p: async_playwright() instance
    headless: None = uses config.HEADLESS
    """
    profile_dir = Path("accounts") / account
    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Account profile does not exist: {profile_dir} (run python add_account.py {account} first)"
        )
    launch_headless = config.HEADLESS if headless is None else headless
    args = list(LAUNCH_ARGS)
    if use_extension:
        if not config.EXTENSION_ENABLED:
            raise RuntimeError("Dola extension is disabled (DOLA_EXTENSION_ENABLED=0)")
        extension_dir = Path(config.EXTENSION_DIR).resolve()
        if not extension_dir.exists():
            raise FileNotFoundError(f"Dola extension directory does not exist: {extension_dir}")
        # Chromium debugger extension requires headed window to intercept skill/action-bar responses
        launch_headless = False
        args.extend([
            f"--disable-extensions-except={extension_dir}",
            f"--load-extension={extension_dir}",
        ])
    kwargs = {
        "headless": launch_headless,
        "args": args,
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }
    if config.PROXY:
        proxy_kwargs = {"server": config.PROXY}
        # Patchright may reject inline user:pass@ in the proxy URL, so split
        # credentials into dedicated fields when present.
        from urllib.parse import urlsplit, unquote
        parts = urlsplit(config.PROXY)
        if parts.username is not None:
            proxy_kwargs["server"] = f"{parts.scheme}://{parts.hostname}:{parts.port}"
            proxy_kwargs["username"] = unquote(parts.username)
            proxy_kwargs["password"] = unquote(parts.password or "")
        kwargs["proxy"] = proxy_kwargs
    context = await p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
    # Every pool flow (generate / resume / verify) launches through here. If the
    # profile lost its session, re-inject the original cookie kept at import
    # time instead of failing with "session expired".
    try:
        from cookie_import import ensure_session_cookies
        injected = await ensure_session_cookies(context, account)
        if injected:
            print(f"[{account}] session missing in profile — re-injected "
                  f"{injected} stored cookie(s)", flush=True)
    except Exception as e:
        print(f"[{account}] stored-cookie re-inject skipped: {e}", flush=True)
    return context


def cookie_value(cookies: list, name: str) -> str:
    """Extracts cookie value from context.cookies() result."""
    return next((c["value"] for c in cookies if c["name"] == name and c["value"]), "")


async def check_login_state(account: str) -> bool:
    """Opens Dola in headless mode and checks whether session is active.

    A present sessionid is not enough — dola serves a login overlay when the
    session is rejected. The page is checked for the logged-out header button
    ("Đăng nhập" / "ログイン" / "Log in") and, conversely, for a user avatar
    (only rendered when logged in).
    """
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        context = await launch_account_context(p, account)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            cookies = await context.cookies("https://www.dola.com")
            if not cookie_value(cookies, "sessionid"):
                return False
            if await page.locator(
                    'img[class*="avatar"], [class*="avatar"] img').count() > 0:
                return True
            for label in ("Đăng nhập", "ログイン", "Log in"):
                loc = page.get_by_text(label, exact=True).first
                if await loc.count() and await loc.is_visible():
                    return False
            return bool(await page.evaluate(
                """() => !!(document.querySelector('textarea')
                        || document.querySelector('[contenteditable="true"]')
                        || document.querySelector('input[type="text"]'))"""
            ))
        finally:
            await context.close()
