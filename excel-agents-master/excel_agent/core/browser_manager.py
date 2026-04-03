"""
Browser management utilities for the Excel Agent Engine.

Handles browser setup, process management, and platform-specific configurations.

Supports two modes:
1. Classic Playwright mode (firefox, chromium, webkit) - uses Playwright-bundled browsers
2. Chrome Canary CDP mode (chrome_canary) - connects to real Chrome Canary via CDP
   This mode bypasses Cloudflare detection by using the real browser's TLS fingerprint.
"""

import asyncio
import logging
import os
import platform
import socket
import subprocess
import time

logger = logging.getLogger(__name__)

# Chrome Canary CDP Configuration
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CHROME_CANARY_PROFILE_DIR = os.path.expanduser("~/.chrome-canary-automation")

# Chrome paths for CDP mode (tries Canary first, then regular Chrome)
CHROME_CDP_PATHS = [
    # Chrome Canary (preferred - less likely to auto-update mid-session)
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",  # macOS
    os.path.expanduser(
        "~/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
    ),  # macOS user
    "/usr/bin/google-chrome-canary",  # Linux
    "/usr/bin/google-chrome-unstable",  # Linux alt
    # Windows Chrome Canary (in user's AppData)
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Google",
        "Chrome SxS",
        "Application",
        "chrome.exe",
    ),  # Windows Canary
    # Regular Chrome (fallback)
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
    os.path.expanduser(
        "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ),  # macOS user
    "/usr/bin/google-chrome",  # Linux
    "/usr/bin/google-chrome-stable",  # Linux alt
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",  # Windows
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",  # Windows x86
]


def kill_all_browser_processes():
    """Kill all browser processes to prevent zombies.

    WARNING: Only use for manual cleanup. Don't use with parallel runs!
    """
    try:
        if os.name == "nt":
            # Windows: use taskkill
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["taskkill", "/F", "/IM", "firefox.exe"],
                check=False,
                capture_output=True,
            )
        else:
            # macOS/Linux: use pkill
            # Chromium/Chrome processes
            subprocess.run(
                ["pkill", "-f", "Chromium"], check=False, capture_output=True
            )
            subprocess.run(
                ["pkill", "-f", "chromium"], check=False, capture_output=True
            )
            subprocess.run(["pkill", "-f", "chrome"], check=False, capture_output=True)
            subprocess.run(
                ["pkill", "-9", "-f", "ms-playwright.*chromium"],
                check=False,
                capture_output=True,
            )

            # Firefox processes
            subprocess.run(["pkill", "-f", "firefox"], check=False, capture_output=True)
            subprocess.run(["pkill", "-f", "Firefox"], check=False, capture_output=True)
            subprocess.run(
                ["pkill", "-9", "-f", "ms-playwright.*firefox"],
                check=False,
                capture_output=True,
            )

            # WebKit processes
            subprocess.run(["pkill", "-f", "webkit"], check=False, capture_output=True)
            subprocess.run(
                ["pkill", "-9", "-f", "ms-playwright.*webkit"],
                check=False,
                capture_output=True,
            )

        logger.info("🧹 Browser processes cleaned up")
    except Exception as e:
        logger.warning(f"⚠️ Error cleaning up processes: {e}")


def get_modifier_key():
    """Get the correct modifier key for the current platform (Meta for Mac, Control for Windows/Linux)."""
    system = platform.system().lower()
    if system == "darwin":  # macOS
        return "Meta"  # Command key
    else:  # Windows, Linux, and others
        return "Control"  # Ctrl key


def get_select_all_key():
    """Get the correct 'Select All' key combination for the current platform."""
    return f"{get_modifier_key()}+A"


def get_save_as_key():
    """Get the correct 'Save As' key combination for the current platform."""
    return f"{get_modifier_key()}+Shift+S"


def _find_chrome():
    """Find Chrome installation path (tries Canary first, then regular Chrome)."""
    for path in CHROME_CDP_PATHS:
        if os.path.exists(path):
            return path
    return None


def _is_cdp_available():
    """Check if Chrome with debugging port is already running."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", CDP_PORT))
    sock.close()
    return result == 0


def _check_cdp_health(timeout=10):
    """Verify Chrome responds to CDP commands (not just port open).

    Returns True if Chrome is healthy and responding, False otherwise.
    """
    import urllib.request

    try:
        req = urllib.request.Request(f"{CDP_URL}/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _launch_chrome_cdp(headless=False):
    """Launch Chrome with remote debugging enabled (tries Canary first, then regular Chrome)."""
    chrome_path = _find_chrome()

    if not chrome_path:
        logger.error("❌ Chrome not found!")
        logger.error("   Please install Chrome or Chrome Canary")
        logger.error("   Or change browser.type to 'firefox' in template.yaml")
        return None

    args = [
        chrome_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_CANARY_PROFILE_DIR}",
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

    logger.info(f"🚀 Launching Chrome with CDP: {chrome_path}")
    logger.info(f"   Profile: {CHROME_CANARY_PROFILE_DIR}")

    # Ensure profile directory exists
    os.makedirs(CHROME_CANARY_PROFILE_DIR, exist_ok=True)

    # Launch Chrome as a subprocess
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # On Windows, prevent subprocess from closing when parent exits
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    return process


async def _wait_for_chrome_ready(timeout=30):
    """Wait for Chrome to be ready for CDP connection."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        if _is_cdp_available():
            logger.info("✅ Chrome is ready for CDP connection")
            return True
        await asyncio.sleep(0.5)

    logger.error(f"❌ Chrome didn't start within {timeout} seconds")
    return False


class BrowserManager:
    """Manages browser instances and configurations."""

    def __init__(self, config: dict):
        """
        Initialize browser manager.

        Args:
            config: Configuration dictionary with browser settings
        """
        self.config = config

        # Get agent type to determine which config section to use
        agent_type = config.get(
            "agent_type", "tabai"
        )  # Default to tabai for backward compat

        # Get browser config from the appropriate agent section
        self.browser_config = config.get(agent_type, {}).get("browser", {})
        self.browser_type = self.browser_config.get("type", "firefox").lower()
        self.headless = self.browser_config.get("headless", False)
        self.timeout = self.browser_config.get("timeout", 10000)

    def is_cdp_mode(self):
        """Check if using Chrome CDP mode (connects to real Chrome Canary binary)."""
        return self.browser_type in ("chrome", "chrome_canary", "cdp")

    def get_browser_instance(self, playwright):
        """Get the appropriate browser instance based on config."""
        if self.browser_type == "firefox":
            return playwright.firefox
        elif self.browser_type == "webkit":
            return playwright.webkit
        elif self.browser_type in ("chromium", "chrome", "chrome_canary", "cdp"):
            return playwright.chromium
        else:
            logger.warning(
                f"⚠️ Unknown browser type '{self.browser_type}', defaulting to Firefox"
            )
            return playwright.firefox

    def get_browser_args(self):
        """Get browser-specific arguments for optimal performance."""
        if self.browser_type == "firefox":
            return [
                "--no-remote",  # Allow multiple instances
            ]
        elif self.browser_type == "webkit":
            return [
                "--disable-web-security",
                "--disable-crash-reporter",
            ]
        else:  # chromium
            return [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-crash-reporter",
                "--no-crash-upload",
                "--disable-logging",
                "--disable-gpu-sandbox",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]

    def get_user_data_dir(self):
        """Get persistent browser session directory (for sequential mode only)."""
        # Always create browser sessions in agentic_workflow/ directory
        # This ensures consistent location regardless of where script is run from
        agentic_workflow_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(
            agentic_workflow_dir, f"browser_session_{self.browser_type}"
        )

    def get_auth_state_path(self):
        """Get path to shared auth state file."""
        agentic_workflow_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(agentic_workflow_dir, "browser_auth_state.json")

    def _cleanup_lock_files(self, user_data_dir):
        """Clean up browser lock files to prevent zombie browser issues."""
        import os

        # Common lock files that browsers create
        lock_files = [
            ".parentlock",  # Firefox
            "lock",  # Firefox symlink (critical for grid systems!)
            "SingletonLock",  # Chrome/Chromium
            "lockfile",  # General
            "LOCK",  # General
        ]

        cleaned_files = []
        for lock_file in lock_files:
            lock_path = os.path.join(user_data_dir, lock_file)
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                    cleaned_files.append(lock_file)
                except Exception as e:
                    logger.debug(f"Could not remove lock file {lock_file}: {e}")

        if cleaned_files:
            logger.info(f"🧹 Cleaned up lock files: {', '.join(cleaned_files)}")

    def _cleanup_playwright_lock_files(self):
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
            lock_file_names = [
                "lock",
                ".parentlock",
                "SingletonLock",
                "lockfile",
                "LOCK",
            ]

            # Walk through all browser directories in the cache
            for root, dirs, files in os.walk(cache_dir):
                for file in files:
                    if file in lock_file_names:
                        lock_path = os.path.join(root, file)
                        try:
                            # Remove broken symlinks (common on grid systems)
                            if os.path.islink(lock_path) and not os.path.exists(
                                lock_path
                            ):
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
                logger.info(
                    f"🧹 Cleaned up {len(cleaned_files)} stale Playwright lock file(s)"
                )
        except Exception as e:
            logger.debug(f"Could not clean Playwright lock files: {e}")

    async def launch_browser(self, playwright):
        """
        Launch browser with automatic mode detection.

        Supports two modes:
        1. Classic mode (firefox, chromium, webkit): Uses Playwright-bundled browsers
           with browser_auth_state.json for authentication.
        2. Chrome Canary CDP mode (chrome_canary, cdp): Connects to real Chrome Canary
           via Chrome DevTools Protocol. This bypasses Cloudflare detection.

        Args:
            playwright: Playwright instance

        Returns:
            tuple: (browser, context) instances
        """
        # Check if using Chrome Canary CDP mode
        if self.is_cdp_mode():
            return await self._launch_browser_cdp(playwright)
        else:
            return await self._launch_browser_classic(playwright)

    async def _launch_browser_cdp(self, playwright):
        """
        Launch browser using Chrome Canary CDP mode with retry logic.

        Connects to a real Chrome Canary instance via Chrome DevTools Protocol.
        Includes health checking and automatic restart if Chrome is unresponsive.

        Args:
            playwright: Playwright instance

        Returns:
            tuple: (browser, context) instances

        Raises:
            RuntimeError: If Chrome cannot be started or connected after 3 attempts
        """
        logger.info("🌐 Using Chrome CDP mode (bypasses Cloudflare)")

        for attempt in range(3):
            # Ensure Chrome is running and healthy
            if _is_cdp_available():
                if not _check_cdp_health():
                    logger.warning(
                        f"⚠️ Chrome is running but unresponsive "
                        f"(attempt {attempt + 1}/3) — restarting..."
                    )
                    kill_all_browser_processes()
                    await asyncio.sleep(3)
                    process = _launch_chrome_cdp(headless=self.headless)
                    if not process:
                        raise RuntimeError(
                            "Chrome not found. Please install Chrome or Chrome Canary, "
                            "or change browser.type to 'firefox' in template.yaml"
                        )
                    if not await _wait_for_chrome_ready():
                        raise RuntimeError("Chrome failed to restart with CDP")
                else:
                    logger.info("✅ Chrome already running with CDP and healthy")
            else:
                logger.info("🚀 Chrome not running with CDP, launching...")
                process = _launch_chrome_cdp(headless=self.headless)
                if not process:
                    raise RuntimeError(
                        "Chrome not found. Please install Chrome or Chrome Canary, "
                        "or change browser.type to 'firefox' in template.yaml"
                    )
                if not await _wait_for_chrome_ready():
                    raise RuntimeError("Chrome failed to start with CDP")

            # Try to connect via CDP
            try:
                browser = await playwright.chromium.connect_over_cdp(
                    CDP_URL, timeout=30000
                )
                logger.info("✅ Connected to Chrome via CDP")
            except Exception as e:
                if attempt < 2:
                    logger.warning(
                        f"⚠️ CDP connection attempt {attempt + 1}/3 failed: {e} — retrying..."
                    )
                    kill_all_browser_processes()
                    await asyncio.sleep(3)
                    continue
                else:
                    logger.error(
                        f"❌ Failed to connect to Chrome after 3 attempts: {e}"
                    )
                    raise RuntimeError(
                        f"Failed to connect to Chrome after 3 attempts: {e}"
                    )

            # Get existing context or create new one
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                logger.info(f"✅ Using existing context ({len(context.pages)} page(s))")
            else:
                context = await browser.new_context(ignore_https_errors=True)
                logger.info("✅ Created new browser context")

            context.set_default_timeout(self.timeout)
            logger.info(
                "✅ Chrome CDP mode ready (real browser - no Cloudflare issues!)"
            )
            return browser, context

        # Should not reach here, but just in case
        raise RuntimeError("Failed to connect to Chrome after 3 attempts")

    async def _launch_browser_classic(self, playwright):
        """
        Launch browser using persistent context mode.

        Uses launch_persistent_context() with a permanent profile directory so the
        browser retains full session state (cookies, localStorage, IndexedDB, etc.)
        across runs -- matching how firefox_browser.py works.

        Args:
            playwright: Playwright instance

        Returns:
            tuple: (browser, context) instances
                   browser is None since persistent context manages its own browser.
        """
        browser_instance = self.get_browser_instance(playwright)
        user_data_dir = self.get_user_data_dir()

        logger.info(
            f"🌐 Launching {self.browser_type} browser (persistent context mode)..."
        )
        logger.info(f"📂 Profile directory: {user_data_dir}")
        logger.info(f"🔧 Browser config: headless={self.headless}")

        # Ensure profile directory exists
        os.makedirs(user_data_dir, exist_ok=True)

        # Clean up stale lock files before launching
        self._cleanup_lock_files(user_data_dir)
        self._cleanup_playwright_lock_files()

        # Build launch kwargs
        launch_kwargs = {
            "user_data_dir": user_data_dir,
            "headless": self.headless,
            "ignore_https_errors": True,
        }

        if self.browser_type == "firefox":
            # Match firefox_browser.py settings for Firefox
            launch_kwargs["args"] = []
            launch_kwargs["firefox_user_prefs"] = {
                "dom.webdriver.enabled": False,
            }
            launch_kwargs["viewport"] = None
            launch_kwargs["locale"] = None
        else:
            launch_kwargs["args"] = self.get_browser_args()

        context = await browser_instance.launch_persistent_context(**launch_kwargs)

        context.set_default_timeout(self.timeout)
        logger.info("✅ Browser launched (persistent context mode)")

        # browser is None -- persistent context manages its own browser lifecycle
        return None, context

    async def close_browser(self, context, browser=None):
        """Close browser resources. In CDP mode, skips shared context/browser."""
        # CDP mode: don't close shared resources (pages already closed separately)
        if self.is_cdp_mode():
            logger.debug("CDP mode: keeping shared context alive")
            return

        # Save updated auth state before closing so subsequent tasks get fresh session
        if context:
            try:
                auth_state_path = self.get_auth_state_path()
                await context.storage_state(path=auth_state_path)
                logger.info(f"💾 Saved auth state to {auth_state_path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not save auth state: {e}")

        # Classic mode: close context and browser
        if context:
            try:
                await context.close()
                logger.info("🔒 Browser context closed")
            except Exception as e:
                logger.warning(f"⚠️ Error closing context: {e}")

        if browser:
            try:
                await browser.close()
            except Exception:
                pass
