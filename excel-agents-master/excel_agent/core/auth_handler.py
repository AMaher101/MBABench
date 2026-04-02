"""
Authentication handler for TabAI and Google sign-in.

Handles authentication flows, session management, and sign-in detection.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class AuthHandler:
    """Manages authentication flows for TabAI."""

    def __init__(self, page, config):
        """
        Initialize auth handler.

        Args:
            page: Playwright page instance
            config: Configuration dictionary
        """
        self.page = page
        self.config = config

    async def check_authentication_status(self) -> bool:
        """
        Check if TabAI requires authentication.

        Returns:
            True if authenticated, False if sign-in required
        """
        try:
            logger.info("🔐 Checking TabAI authentication status...")

            # Look for sign-in indicators
            signin_selectors = [
                'button:has-text("Sign in")',
                'button:has-text("Sign In")',
                '[data-testid="signin-button"]',
                'text="Sign in to use TabAI"',
            ]

            for selector in signin_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        logger.info("⚠️ Sign-in required")
                        return False

                    # Check frames
                    for frame in self.page.frames:
                        try:
                            element = await frame.query_selector(selector)
                            if element and await element.is_visible():
                                logger.info("⚠️ Sign-in required (found in frame)")
                                return False
                        except Exception:
                            continue

                except Exception:
                    continue

            logger.info("✅ Already authenticated")
            return True

        except Exception as e:
            logger.error(f"❌ Error checking auth status: {e}")
            return False

    async def click_signin_button(self) -> bool:
        """
        Click the sign-in button in TabAI.

        Returns:
            True if button was clicked
        """
        try:
            logger.info("🔐 Clicking sign-in button...")

            signin_selectors = [
                'button:has-text("Sign in")',
                'button:has-text("Sign In")',
                '[data-testid="signin-button"]',
                '[aria-label="Sign in"]',
            ]

            for selector in signin_selectors:
                try:
                    # Try main page
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        await element.click()
                        logger.info(f"✅ Clicked sign-in button: {selector}")
                        return True

                    # Try frames
                    for frame in self.page.frames:
                        try:
                            element = await frame.query_selector(selector)
                            if element and await element.is_visible():
                                await element.click()
                                logger.info("✅ Clicked sign-in in frame")
                                return True
                        except Exception:
                            continue

                except Exception:
                    continue

            logger.error("❌ Could not find sign-in button")
            return False

        except Exception as e:
            logger.error(f"❌ Error clicking sign-in: {e}")
            return False

    async def find_signin_tab(self):
        """
        Find the sign-in popup tab.

        Returns:
            Page object of sign-in tab, or None
        """
        try:
            logger.info("🔍 Looking for sign-in popup tab...")

            # Wait for new tab to open
            await asyncio.sleep(2)

            context = self.page.context
            pages = context.pages

            # Find the sign-in tab (usually the last opened)
            for page in pages:
                try:
                    url = page.url
                    if "accounts.google.com" in url or "login" in url.lower():
                        logger.info(f"✅ Found sign-in tab: {url}")
                        return page
                except Exception:
                    continue

            # If not found by URL, return last opened page
            if len(pages) > 1:
                signin_tab = pages[-1]
                logger.info("✅ Using last opened tab as sign-in tab")
                return signin_tab

            logger.warning("⚠️ Could not find sign-in tab")
            return None

        except Exception as e:
            logger.error(f"❌ Error finding sign-in tab: {e}")
            return None

    async def click_continue_with_google(self, signin_tab) -> bool:
        """
        Click "Continue with Google" button.

        Args:
            signin_tab: Sign-in tab page instance

        Returns:
            True if button was clicked
        """
        try:
            logger.info("🔐 Clicking 'Continue with Google'...")

            google_selectors = [
                'button:has-text("Continue with Google")',
                'button:has-text("Google")',
                '[data-provider="google"]',
                'button[aria-label*="Google"]',
            ]

            for selector in google_selectors:
                try:
                    element = await signin_tab.query_selector(selector)
                    if element and await element.is_visible():
                        await element.click()
                        logger.info(f"✅ Clicked: {selector}")
                        return True
                except Exception:
                    continue

            logger.warning("⚠️ Could not find Google button")
            return False

        except Exception as e:
            logger.error(f"❌ Error clicking Google button: {e}")
            return False

    async def handle_google_authentication(self, signin_tab) -> bool:
        """
        Handle Google authentication flow.

        Args:
            signin_tab: Sign-in tab page instance

        Returns:
            True if authentication completed
        """
        try:
            logger.info("🔐 Handling Google authentication...")

            # Wait for Google page to load
            await asyncio.sleep(3)

            # Check if already signed in (account picker)
            account_selectors = [
                "[data-identifier]",  # Google account tiles
                'div[role="button"]:has-text("@")',  # Account buttons
            ]

            for selector in account_selectors:
                try:
                    accounts = await signin_tab.query_selector_all(selector)
                    if accounts:
                        logger.info(f"✅ Found {len(accounts)} Google account(s)")
                        # Click first account
                        await accounts[0].click()
                        logger.info("✅ Selected Google account")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            # Check if we need to enter email
            email_input = await signin_tab.query_selector('input[type="email"]')
            if email_input and await email_input.is_visible():
                # Get agent type to determine which login section to use
                agent_type = self.config.get("agent_type", "tabai")
                login_section = f"login_{agent_type}"

                email = self.config.get(login_section, {}).get("email", "")
                if email:
                    await email_input.fill(email)
                    await signin_tab.keyboard.press("Enter")
                    logger.info("✅ Entered email")
                    await asyncio.sleep(2)
                else:
                    logger.warning("⚠️ No email configured")

            # Check if we need to enter password
            password_input = await signin_tab.query_selector('input[type="password"]')
            if password_input and await password_input.is_visible():
                # Get agent type to determine which login section to use
                agent_type = self.config.get("agent_type", "tabai")
                login_section = f"login_{agent_type}"

                password = self.config.get(login_section, {}).get("password", "")
                if password:
                    await password_input.fill(password)
                    await signin_tab.keyboard.press("Enter")
                    logger.info("✅ Entered password")
                    await asyncio.sleep(3)
                else:
                    logger.warning("⚠️ No password configured")

            logger.info("✅ Google authentication flow completed")
            return True

        except Exception as e:
            logger.error(f"❌ Error in Google authentication: {e}")
            return False

    async def handle_signin_flow(self) -> bool:
        """
        Handle complete sign-in flow for TabAI.

        Returns:
            True if sign-in completed successfully
        """
        try:
            logger.info("🔐 Starting sign-in flow...")

            # Click sign-in button
            if not await self.click_signin_button():
                return False

            # Find sign-in tab
            signin_tab = await self.find_signin_tab()
            if not signin_tab:
                return False

            # Click Continue with Google
            if not await self.click_continue_with_google(signin_tab):
                # Maybe already on Google page
                logger.info("⚠️ Skipping 'Continue with Google' step")

            # Handle Google authentication
            if not await self.handle_google_authentication(signin_tab):
                return False

            # Close sign-in tab if still open
            try:
                if signin_tab and not signin_tab.is_closed():
                    await signin_tab.close()
                    logger.info("✅ Closed sign-in tab")
            except Exception:
                pass

            # Switch back to main page
            await self.page.bring_to_front()
            await asyncio.sleep(2)

            logger.info("✅ Sign-in flow completed")
            return True

        except Exception as e:
            logger.error(f"❌ Error in sign-in flow: {e}")
            return False
