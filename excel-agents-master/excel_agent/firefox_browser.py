#!/usr/bin/env python3
"""
Persistent Browser Session Setup
Uses persistent browser context to save complete session including auth
"""

import argparse
import asyncio
import atexit
import logging
import os
import random
import yaml
from dotenv import load_dotenv

from core.browser_manager import kill_all_browser_processes
from core.logging_setup import setup_basic_logging

# Import both playwright options
from playwright.async_api import async_playwright as regular_playwright

try:
    from patchright.async_api import async_playwright as patchright_playwright

    HAS_PATCHRIGHT = True
except ImportError:
    HAS_PATCHRIGHT = False

# Setup logging with Windows-compatible Unicode handling
setup_basic_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log patchright availability after logger is configured
if not HAS_PATCHRIGHT:
    logger.warning("⚠️ Patchright not found, using regular Playwright")


def cleanup_playwright_lock_files():
    """Clean up stale Playwright lock files in cache directory.

    This is especially important on grid systems where lock files from previous
    sessions on different nodes may be stale symlinks.
    """
    try:
        import time

        cache_dir = os.path.expanduser("~/.cache/ms-playwright")
        if not os.path.exists(cache_dir):
            return

        cleaned_files = []
        lock_file_names = ["lock", ".parentlock", "SingletonLock", "lockfile", "LOCK"]

        # Walk through all browser directories in the cache
        for root, dirs, files in os.walk(cache_dir):
            for file in files:
                if file in lock_file_names:
                    lock_path = os.path.join(root, file)
                    try:
                        # Remove broken symlinks (common on grid systems)
                        if os.path.islink(lock_path) and not os.path.exists(lock_path):
                            os.remove(lock_path)
                            cleaned_files.append(lock_path)
                        # Remove stale regular files (older than 1 minute)
                        elif os.path.isfile(lock_path):
                            if os.path.getmtime(lock_path) < time.time() - 60:
                                os.remove(lock_path)
                                cleaned_files.append(lock_path)
                    except Exception as e:
                        logger.debug(f"Could not remove lock file {lock_path}: {e}")

        if cleaned_files:
            logger.info(f"🧹 Cleaned up {len(cleaned_files)} stale lock file(s)")
    except Exception as e:
        logger.debug(f"Could not clean Playwright lock files: {e}")


# Register emergency cleanup for force kills — intentional: ensures all browser
# processes are terminated when the script exits (including force kills / SIGTERM)
atexit.register(kill_all_browser_processes)


def get_browser_instance(playwright, browser_type):
    """Get the appropriate browser instance based on config"""
    browser_type = browser_type.lower()

    if browser_type == "firefox":
        return playwright.firefox
    elif browser_type == "webkit":
        return playwright.webkit
    elif browser_type in ("chromium", "chrome", "chrome_canary", "cdp"):
        return playwright.chromium
    else:
        logger.warning(
            f"⚠️ Unknown browser type '{browser_type}', defaulting to Firefox"
        )
        return playwright.firefox


def get_browser_args(browser_type):
    """Get browser-specific arguments - minimal settings"""
    browser_type = browser_type.lower()

    if browser_type == "firefox":
        return []  # Firefox doesn't need special args
    elif browser_type == "webkit":
        return []
    else:  # chromium
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]


async def wait_for_cloudflare(page):
    """Wait for Cloudflare challenge to complete"""
    max_wait = 30  # seconds
    check_interval = 0.5

    for _ in range(int(max_wait / check_interval)):
        try:
            # Check if Cloudflare challenge is present
            title = await page.title()
            url = page.url

            # Check for Cloudflare indicators
            if "Just a moment" in title or "challenge-platform" in url:
                logger.info("⏳ Cloudflare challenge detected, waiting...")
                await asyncio.sleep(check_interval)
                continue

            # Check if we got through
            if "onedrive" in url.lower() or "microsoft" in url.lower():
                logger.info("✅ Cloudflare challenge passed!")
                return True

        except Exception:
            pass

        await asyncio.sleep(check_interval)

    logger.warning("⚠️ Cloudflare challenge may still be present")
    return False


def _apply_env_credential_overrides(config):
    """Override credential fields in config with environment variables if set.

    Supported variables:
      - ONEDRIVE_EMAIL / ONEDRIVE_PASSWORD
      - TABAI_EMAIL / TABAI_PASSWORD (for TabAI agent)
      - CLAUDE_EXCEL_AGENT_EMAIL / CLAUDE_EXCEL_AGENT_PASSWORD (for Claude agent)
    """
    # Get agent type to determine which login env vars to check
    agent_type = config.get("agent_type", "tabai")
    agent_upper = agent_type.upper()

    env_mappings = [
        ("login_onedrive", "email", "ONEDRIVE_EMAIL"),
        ("login_onedrive", "password", "ONEDRIVE_PASSWORD"),
        (f"login_{agent_type}", "email", f"{agent_upper}_EMAIL"),
        (f"login_{agent_type}", "password", f"{agent_upper}_PASSWORD"),
    ]

    for section_name, key_name, env_key in env_mappings:
        env_value = os.getenv(env_key)
        if env_value:
            config.setdefault(section_name, {})
            config[section_name][key_name] = env_value


async def setup_browser_session(browser_type="firefox"):
    """Set up a persistent browser session with saved auth state."""

    # Create minimal config from parameters
    config = {
        "tabai": {"browser": {"type": browser_type, "headless": False}}
    }  # Always run with GUI for testing

    # Apply environment variable overrides
    _apply_env_credential_overrides(config)

    # Clean up any existing processes and lock files
    kill_all_browser_processes()
    cleanup_playwright_lock_files()
    await asyncio.sleep(1)  # Reduced wait time

    # Get agent type to determine which config section to use
    agent_type = config.get(
        "agent_type", "tabai"
    )  # Default to tabai for backward compat

    # Set up auth state path
    browser_type = config.get(agent_type, {}).get("browser", {}).get("type", "firefox")
    agentic_workflow_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    logger.info(f"🌐 Using {browser_type.title()} browser")

    # Determine browser mode and playwright context
    is_chrome_canary = browser_type.lower() in ("chrome", "chrome_canary", "cdp")

    if is_chrome_canary:
        # Import Chrome Canary utilities
        try:
            from chrome_browser import (
                is_chrome_canary_running,
                launch_chrome_canary,
                wait_for_chrome_ready,
            )
        except ImportError:
            logger.error("chrome_browser module not found")
            return

        playwright_context = regular_playwright
    else:
        use_patchright = HAS_PATCHRIGHT and browser_type.lower() == "chromium"
        playwright_context = (
            patchright_playwright if use_patchright else regular_playwright
        )

    async with playwright_context() as p:
        try:
            logger.info("🚀 Starting browser for auth setup...")

            # Handle Chrome Canary CDP mode
            if is_chrome_canary:
                # Launch if not running
                if not is_chrome_canary_running():
                    process = launch_chrome_canary(headless=False)
                    if not process or not await wait_for_chrome_ready():
                        logger.error("❌ Chrome Canary launch failed")
                        return

                # Connect via CDP
                try:
                    browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                except Exception as e:
                    logger.error(f"❌ CDP connection failed: {e}")
                    return

                # Get or create context
                contexts = browser.contexts
                context = contexts[0] if contexts else await browser.new_context()

                # Always create fresh page (avoid closed page issues)
                page = await context.new_page()

            else:
                # Classic browser launch
                browser_instance = get_browser_instance(p, browser_type)

                if browser_type.lower() == "firefox":
                    # Firefox with persistent profile to look like a real user
                    browser = await browser_instance.launch_persistent_context(
                        user_data_dir=os.path.join(
                            agentic_workflow_dir, "browser_session_firefox"
                        ),
                        headless=False,
                        args=[],
                        firefox_user_prefs={
                            # ONLY disable webdriver - nothing else
                            "dom.webdriver.enabled": False,
                        },
                        viewport=None,  # Use browser's default
                        locale=None,  # Use system default
                    )
                    # browser is actually a context in persistent mode
                    context = browser
                else:
                    browser = await browser_instance.launch(
                        headless=False,
                        args=get_browser_args(browser_type),
                        chromium_sandbox=False,
                    )

                # Create context (skip for Firefox since we used persistent context)
                if browser_type.lower() != "firefox":
                    context = await browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        locale="en-US",
                    )

                # Get or create page
                if browser_type.lower() == "firefox":
                    # For persistent context, get existing pages or create new one
                    pages = context.pages
                    if pages:
                        page = pages[0]
                    else:
                        page = await context.new_page()
                else:
                    page = await context.new_page()

            # Go to OneDrive
            logger.info("🌐 Opening OneDrive...")

            # Add random human-like delay before navigation
            await asyncio.sleep(random.uniform(0.5, 1.5))

            try:
                # Navigate to OneDrive
                await page.goto(
                    "https://onedrive.live.com",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                logger.info("📄 Page loaded, checking for Cloudflare...")

                # Wait for Cloudflare challenge to complete automatically
                await wait_for_cloudflare(page)

                # Give it a moment to settle
                await asyncio.sleep(random.uniform(0.5, 1.0))

            except Exception as e:
                logger.error(f"❌ Failed to open OneDrive: {e}")
                logger.info("   Check your internet connection")
                raise

            current_url = page.url
            logger.info(f"📍 Current URL: {current_url}")

            # Check for Cloudflare challenge
            title = await page.title()
            if "Just a moment" in title or "Attention Required" in title:
                logger.warning("⚠️ Cloudflare verification still showing!")
                logger.info(
                    "🖱️  Please complete the Cloudflare verification manually in the browser"
                )
                logger.info("⏳ Waiting for you to complete it...")

                # Wait for Cloudflare to clear
                while True:
                    await asyncio.sleep(2)
                    title = await page.title()
                    current_url = page.url
                    if (
                        "Just a moment" not in title
                        and "Attention Required" not in title
                    ):
                        logger.info("✅ Cloudflare verification complete!")
                        break

            # Check if we need to log in
            if "login" in current_url.lower() or await page.query_selector(
                'input[name="loginfmt"]'
            ):
                logger.info("🔐 Please log into OneDrive and TabAI in the browser")
                logger.info("⏳ Waiting for login...")

                # Wait for user to complete login
                while True:
                    await asyncio.sleep(5)
                    current_url = page.url
                    if (
                        "onedrive" in current_url.lower()
                        and "login" not in current_url.lower()
                    ):
                        logger.info("✅ OneDrive login complete")
                        break
            else:
                logger.info("✅ Already logged into OneDrive")

            # Save auth state every 30 seconds
            logger.info("")
            logger.info("📋 Now open an Excel file and log into TabAI")
            logger.info("💾 Auto-saving auth state every 30 seconds")
            logger.info("⏳ Press Ctrl+C when done")

            auth_state_path = os.path.join(
                agentic_workflow_dir, "browser_auth_state.json"
            )

            try:
                while True:
                    await asyncio.sleep(30)
                    try:
                        await context.storage_state(path=auth_state_path)
                        logger.info("💾 Saved")
                    except Exception:
                        pass  # Ignore save errors, keep trying

            except KeyboardInterrupt:
                # Save on Ctrl+C too
                logger.info("💾 Saving auth state...")
                try:
                    await context.storage_state(path=auth_state_path)
                    logger.info(f"✅ Saved to {auth_state_path}")
                except Exception as e:
                    logger.error(f"❌ Save failed: {e}")
                logger.info("👋 Exiting...")

        except Exception as e:
            logger.error(f"❌ Error: {e}")

        finally:
            # Clean up
            logger.info("🧹 Cleaning up...")
            try:
                await context.close()
            except Exception as e:
                logger.debug(f"Browser close error: {e}")
            kill_all_browser_processes()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Persistent Browser Session Setup")
    parser.add_argument(
        "--browser",
        default=None,
        choices=["chromium", "firefox", "webkit", "chrome", "chrome_canary"],
        help="Browser type to use (overrides template.yaml)",
    )
    parser.add_argument(
        "--kill-all-browsers",
        action="store_true",
        help="[DANGEROUS] Kill all browser processes before starting",
    )
    args = parser.parse_args()

    try:
        # Set working directory to excel_agent if running from base agentic_workflow
        script_dir = os.path.dirname(os.path.abspath(__file__))
        original_cwd = os.getcwd()

        os.chdir(script_dir)
        logger.info(f"📁 Working directory: {os.getcwd()}")

        # Load environment variables from .env located next to this script (optional)
        try:
            env_path = os.path.join(script_dir, ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
                logger.info(f"🔐 Loaded environment variables from: {env_path}")
            else:
                logger.info(
                    f"ℹ️ No .env file found at: {env_path} (using config credentials)"
                )
        except Exception as e:
            logger.warning(f"⚠️ Could not load .env file: {e}")

        # Load browser type from template if --browser not provided
        browser_type = args.browser
        if browser_type is None:
            # Find template in tasks_configs/templates/ directory (relative to project root)
            # script_dir is agentic_workflow/excel_agent, so one dirname gets us to agentic_workflow
            agentic_workflow_dir = os.path.dirname(script_dir)
            templates_dir = os.path.join(
                agentic_workflow_dir, "tasks_configs", "templates"
            )
            template_path = os.path.join(templates_dir, "tabai.yaml")

            if os.path.exists(template_path):
                try:
                    with open(template_path, "r") as f:
                        template_data = yaml.safe_load(f)
                        template_section = template_data.get("template", {})

                        # Get agent type to determine which config section to use
                        agent_type = template_section.get("agent_type", "tabai")

                        # Read browser type from the appropriate agent section
                        browser_type = (
                            template_section.get(agent_type, {})
                            .get("browser", {})
                            .get("type", "firefox")
                        )
                        logger.info(
                            f"📖 Loaded browser type from template ({agent_type} section): {browser_type}"
                        )
                except Exception as e:
                    logger.warning(
                        f"⚠️ Could not load template: {e}, defaulting to firefox"
                    )
                    browser_type = "firefox"
            else:
                logger.info(
                    f"ℹ️ Template file not found at {template_path}, defaulting to firefox"
                )
                browser_type = "firefox"
        else:
            logger.info(
                f"📖 Using browser type from command-line argument: {browser_type}"
            )

        logger.info(f"🌐 Using {browser_type} browser")
        asyncio.run(setup_browser_session(browser_type))
    except KeyboardInterrupt:
        logger.info("👋 Script terminated by user")
        kill_all_browser_processes()
    except Exception as e:
        logger.error(f"❌ Script failed: {e}")
        kill_all_browser_processes()
