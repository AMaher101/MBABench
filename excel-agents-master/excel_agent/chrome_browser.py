#!/usr/bin/env python3
"""
Chrome Canary Browser Connection via CDP

This script launches Chrome Canary with remote debugging enabled and connects
via Chrome DevTools Protocol (CDP). This bypasses Cloudflare detection because
it uses the REAL Chrome browser with a real TLS fingerprint.

Usage:
    python chrome_browser.py              # Launch and keep open for auth
    python chrome_browser.py --headless   # For automation (no GUI)
"""

import argparse
import asyncio
import logging
import os
import subprocess
import time

from playwright.async_api import async_playwright

from core.logging_setup import setup_basic_logging

# Setup logging with Windows-compatible Unicode handling
setup_basic_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
# Use os.path.normpath to ensure proper path separators on Windows
PROFILE_DIR = os.path.normpath(os.path.expanduser("~/.chrome-canary-automation"))

# Chrome Canary paths for different OS
CHROME_CANARY_PATHS = [
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",  # macOS
    os.path.expanduser(
        "~/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
    ),  # macOS user
    "/usr/bin/google-chrome-canary",  # Linux
    "/usr/bin/google-chrome-unstable",  # Linux alt
    # Windows - use LOCALAPPDATA for reliability
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Google",
        "Chrome SxS",
        "Application",
        "chrome.exe",
    ),
]


def find_chrome_canary():
    """Find Chrome Canary installation path"""
    for path in CHROME_CANARY_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_chrome_canary_running():
    """Check if Chrome Canary with debugging port is already running"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", CDP_PORT))
    sock.close()
    return result == 0


def launch_chrome_canary(headless=False):
    """Launch Chrome Canary with remote debugging enabled"""
    chrome_path = find_chrome_canary()

    if not chrome_path:
        logger.error("❌ Chrome Canary not found! Searched paths:")
        for path in CHROME_CANARY_PATHS:
            exists = "✓" if os.path.exists(path) else "✗"
            logger.error(f"   {exists} {path}")
        return None

    # Ensure profile directory exists
    os.makedirs(PROFILE_DIR, exist_ok=True)
    logger.info(f"📁 Profile directory: {PROFILE_DIR}")

    args = [
        chrome_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",  # Use Default profile within our data dir
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        # Disable profile picker and related popups
        "--disable-features=ProfilePicker,ChromeWhatsNewUI",
        "--disable-popup-blocking",
        "--disable-session-crashed-bubble",
    ]

    if headless:
        args.append("--headless=new")

    logger.info("🚀 Launching Chrome Canary...")
    logger.info(f"   Profile: {PROFILE_DIR}")

    # Launch Chrome Canary as a subprocess
    # Don't suppress stderr so we can see Chrome errors
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # On Windows, prevent the subprocess from closing when parent script terminates
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    # Give Chrome a moment to start and check for immediate failures
    time.sleep(1)
    if process.poll() is not None:
        # Process already exited - something went wrong
        stderr_output = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr
            else ""
        )
        logger.error(
            f"❌ Chrome Canary exited immediately with code {process.returncode}"
        )
        if stderr_output:
            logger.error(f"   Error: {stderr_output[:500]}")
        return None

    return process


async def wait_for_chrome_ready(timeout=30):
    """Wait for Chrome Canary to be ready for CDP connection"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        if is_chrome_canary_running():
            logger.info("✅ Chrome Canary is ready!")
            return True
        await asyncio.sleep(0.5)

    logger.error(f"❌ Chrome Canary didn't start within {timeout} seconds")
    return False


async def connect_to_chrome_canary():
    """Connect to Chrome Canary via CDP and return browser, context, page"""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            logger.info("✅ Connected to Chrome Canary via CDP")

            # Get existing context or create new one
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                logger.info(
                    f"   Using existing context with {len(context.pages)} page(s)"
                )
            else:
                context = await browser.new_context()
                logger.info("   Created new context")

            # Get existing page or create new one
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = await context.new_page()

            return browser, context, page

        except Exception as e:
            logger.error(f"❌ Failed to connect to Chrome Canary: {e}")
            logger.error(
                "   Make sure Chrome Canary is running with --remote-debugging-port=9222"
            )
            return None, None, None


async def run_browser_session(headless=False):
    """
    Main function to launch Chrome Canary and keep session open for authentication.
    This is the replacement for firefox_browser.py
    """
    chrome_process = None

    # Check if Chrome Canary is already running
    if is_chrome_canary_running():
        logger.info("✅ Chrome Canary already running with debugging port")
    else:
        # Launch Chrome Canary
        chrome_process = launch_chrome_canary(headless=headless)
        if not chrome_process:
            return

        # Wait for it to be ready
        if not await wait_for_chrome_ready():
            if chrome_process:
                chrome_process.terminate()
            return

    async with async_playwright() as p:
        try:
            # Connect via CDP
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            logger.info("✅ Connected to Chrome Canary!")

            # Get or create context
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
            else:
                context = await browser.new_context()

            # Get or create page
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = await context.new_page()

            # Navigate to OneDrive
            logger.info("🌐 Opening OneDrive...")
            await page.goto(
                "https://onedrive.live.com",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            current_url = page.url
            title = await page.title()

            logger.info(f"📍 Current URL: {current_url}")
            logger.info(f"📄 Page title: {title}")

            # Check if Cloudflare challenge
            if "Just a moment" in title or "challenge" in current_url.lower():
                logger.warning("⚠️ Cloudflare challenge detected!")
                logger.info("   Please complete it manually in the browser window")
            elif "login" in current_url.lower():
                logger.info("🔐 Login page detected - please log in")
            else:
                logger.info("✅ OneDrive loaded successfully!")

            # Keep session alive
            logger.info("")
            logger.info("=" * 60)
            logger.info("📋 INSTRUCTIONS:")
            logger.info("   1. Complete any login/Cloudflare challenges in the browser")
            logger.info("   2. Navigate to TabAI and log in there too")
            logger.info("   3. Press Ctrl+C when done")
            logger.info("")
            logger.info("💾 Your session is automatically saved to:")
            logger.info(f"   {PROFILE_DIR}")
            logger.info("=" * 60)

            # Keep running
            try:
                while True:
                    await asyncio.sleep(60)
                    logger.info("💾 Session active...")
            except KeyboardInterrupt:
                logger.info("👋 Done! Session saved.")

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            import traceback

            logger.error(f"   Traceback: {traceback.format_exc()}")

        finally:
            # Don't kill Chrome - let user close it manually or keep it for automation
            logger.info("")
            logger.info("ℹ️  Chrome Canary is still running.")
            logger.info("   - Close it manually if you're done")
            logger.info("   - Or leave it open for automation scripts to connect")


async def get_authenticated_page():
    """
    Utility function for other scripts to get an authenticated page.
    Launches Chrome Canary if needed, connects via CDP, and returns the page.

    Returns:
        tuple: (playwright, browser, context, page) or (None, None, None, None) on failure
    """
    # Check if Chrome Canary is running, launch if not
    if not is_chrome_canary_running():
        logger.info("🚀 Chrome Canary not running, launching...")
        process = launch_chrome_canary(headless=False)
        if not process:
            return None, None, None, None

        if not await wait_for_chrome_ready():
            process.terminate()
            return None, None, None, None

    # Connect via CDP
    p = await async_playwright().start()

    try:
        browser = await p.chromium.connect_over_cdp(CDP_URL)

        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        return p, browser, context, page

    except Exception as e:
        logger.error(f"❌ Failed to connect: {e}")
        await p.stop()
        return None, None, None, None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Chrome Canary Browser Session (CDP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python chrome_browser.py           # Launch with GUI for auth setup
  python chrome_browser.py --headless  # Launch headless for automation

First-time setup:
  1. Run without --headless
  2. Log into OneDrive and TabAI in the browser
  3. Press Ctrl+C when done
  4. Your session is saved and will be reused!
        """,
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run in headless mode (no GUI)"
    )
    args = parser.parse_args()

    try:
        logger.info(f"🔍 Platform: {os.name} / {__import__('platform').system()}")
        logger.info(f"🔍 Chrome Canary path: {find_chrome_canary()}")
        asyncio.run(run_browser_session(headless=args.headless))
    except KeyboardInterrupt:
        logger.info("👋 Bye!")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback

        logger.error(traceback.format_exc())
        input("Press Enter to exit...")  # Keep window open on Windows
