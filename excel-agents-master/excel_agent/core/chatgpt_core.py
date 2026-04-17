"""
ChatGPT Excel add-in core interaction logic.

Handles ChatGPT-specific implementation extending AIAgentCore.
Mirrors the structure of claude_core.py with ChatGPT selectors and UI flow.
"""

import asyncio
import logging
import time
from pathlib import Path

from .ai_agent_base import AIAgentCore

logger = logging.getLogger(__name__)


class ChatGPTCore(AIAgentCore):
    """ChatGPT Excel add-in specific implementation."""

    def __init__(self, page, config, shutdown_event, completion_logger):
        super().__init__(page, config, shutdown_event, completion_logger)
        self._chatgpt_frame = None
        self._setup_completed = False

    def get_agent_type(self) -> str:
        return "chatgpt_excel_agent"

    def get_addon_name(self) -> str:
        return "ChatGPT"

    def get_open_button_text(self) -> str:
        return "Open ChatGPT"

    def requires_addins_menu(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Selectors for the ChatGPT chat input, ordered by likelihood.
    # ------------------------------------------------------------------
    _INPUT_SELECTORS = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
        'textarea[placeholder*="Ask anything"]',
        'textarea[role="textbox"]',
    ]

    # URL fragments that identify the ChatGPT add-in iframe.
    _FRAME_URL_HINTS = ["chatgpt.com", "openai.com"]

    # ------------------------------------------------------------------
    # Frame detection
    # ------------------------------------------------------------------

    async def _get_chatgpt_frame(self):
        """Find and cache the ChatGPT add-in iframe.

        Strategy: try frames matching known URLs first, then fall back to
        any frame that contains a chat-input element.
        """
        if self._chatgpt_frame:
            try:
                for selector in self._INPUT_SELECTORS:
                    el = await self._chatgpt_frame.query_selector(selector)
                    if el:
                        return self._chatgpt_frame
            except Exception:
                pass
            self._chatgpt_frame = None

        # Pass 1: frames whose URL matches known hints (fast path)
        for f in self.page.frames:
            url = f.url or ""
            if not any(hint in url for hint in self._FRAME_URL_HINTS):
                continue
            try:
                for selector in self._INPUT_SELECTORS:
                    el = await f.query_selector(selector)
                    if el:
                        self._chatgpt_frame = f
                        return f
            except Exception:
                continue

        # Pass 2: brute-force all frames (URL changed or new domain)
        for f in self.page.frames:
            try:
                for selector in self._INPUT_SELECTORS:
                    el = await f.query_selector(selector)
                    if el:
                        self._chatgpt_frame = f
                        return f
            except Exception:
                continue
        return None

    async def _find_input_element(self, frame=None):
        """Find the chat input element in the given frame (or cached frame).

        Returns the element handle, or None.
        """
        frame = frame or await self._get_chatgpt_frame()
        if not frame:
            return None
        for selector in self._INPUT_SELECTORS:
            try:
                el = await frame.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Panel verification
    # ------------------------------------------------------------------

    async def _verify_panel_opened(self) -> bool:
        """ChatGPT-specific: poll for chat-input with configurable boot timeout."""
        boot_timeout = self.config.get("panel_boot_timeout_seconds", 20)
        poll_interval = 2
        elapsed = 0

        while elapsed < boot_timeout:
            if self.shutdown_event and self.shutdown_event.is_set():
                logger.info("   Shutdown requested during panel verification")
                return False
            self._chatgpt_frame = None  # fresh lookup each time
            frame = await self._get_chatgpt_frame()
            if frame:
                logger.info(
                    f"   ✅ Panel verified: ChatGPT chat-input found after {elapsed}s"
                )
                return True
            if elapsed > 0 and elapsed % 6 == 0:
                logger.info(
                    f"   ⏳ [{elapsed}s] Waiting for ChatGPT app to initialize..."
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Last-ditch: dump what frames and elements exist for debugging
        logger.warning(f"   ⚠️ ChatGPT chat-input not found after {boot_timeout}s")
        try:
            frame_names = []
            for f in self.page.frames:
                try:
                    url = f.url[:80] if f.url else "(no url)"
                    textareas = await f.query_selector_all("textarea")
                    editables = await f.query_selector_all('[contenteditable="true"]')
                    frame_names.append(
                        f"{f.name or '(anon)'}({url}) "
                        f"textareas={len(textareas)} editables={len(editables)}"
                    )
                except Exception:
                    frame_names.append(f"{f.name or '(anon)'} (error)")
            logger.warning(f"   Frames: {frame_names}")
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Initial setup: Heavy mode + Apply edits automatically
    # ------------------------------------------------------------------

    async def handle_initial_setup(self) -> bool:
        """Select 'Heavy' mode and enable 'Apply edits automatically'."""
        if self._setup_completed:
            return True

        try:
            logger.info("🔧 Running ChatGPT initial setup...")
            frame = await self._get_chatgpt_frame()
            if not frame:
                logger.warning("⚠️ Could not find ChatGPT frame for setup")
                return False

            # Step 1: Select Heavy mode
            await self._select_reasoning_mode(frame)

            # Step 2: Enable "Apply edits automatically"
            await self._enable_auto_apply_edits(frame)

            self._setup_completed = True
            logger.info("✅ ChatGPT initial setup complete")
            return True

        except Exception as e:
            logger.warning(f"⚠️ Error in initial setup: {e}")
            try:
                frame = await self._get_chatgpt_frame()
                if frame:
                    await frame.press("body", "Escape")
                    await asyncio.sleep(0.3)
            except Exception:
                pass
            return False

    # Valid reasoning modes for the ChatGPT Excel add-in dropdown.
    VALID_MODES = {"fast", "standard", "heavy"}

    async def _select_reasoning_mode(self, frame):
        """Select the configured reasoning mode (Fast / Standard / Heavy).

        Reads ``chatgpt_excel_agent.model`` from config. Defaults to
        ``heavy`` if not set. The dropdown shows three options with a
        truncated span indicator.
        """
        target = self.config.get("chatgpt_excel_agent", {}).get("model") or "heavy"
        target_lower = target.lower()

        if target_lower not in self.VALID_MODES:
            logger.warning(
                "Unknown ChatGPT Excel mode '%s'. "
                "Valid options: %s. Defaulting to 'heavy'.",
                target,
                ", ".join(sorted(self.VALID_MODES)),
            )
            target_lower = "heavy"

        # Capitalise for UI matching ("fast" -> "Fast")
        target_display = target_lower.capitalize()

        try:
            logger.info("Checking reasoning mode...")

            # Find the current mode indicator — a span with class containing
            # "max-w-40" and "truncate" showing Fast / Standard / Heavy.
            current_mode = None
            mode_button = None

            # Strategy 1: find span.truncate whose text is one of the modes
            spans = await frame.query_selector_all("span")
            for span in spans:
                try:
                    text = (await span.text_content() or "").strip()
                    if text in ("Fast", "Standard", "Heavy"):
                        cls = await span.get_attribute("class") or ""
                        # The mode indicator has max-w-40 truncate classes
                        if "truncate" in cls:
                            current_mode = text
                            # Navigate up to the clickable button/dropdown trigger
                            mode_button = await span.evaluate_handle(
                                'el => el.closest("button") || el.parentElement'
                            )
                            break
                except Exception:
                    continue

            if current_mode and current_mode.lower() == target_lower:
                logger.info("Already in '%s' mode", target_display)
                return

            if not mode_button:
                logger.warning("Mode selector not found — skipping")
                return

            logger.info(
                "Current mode: '%s' — switching to '%s'...",
                current_mode,
                target_display,
            )

            # Click to open mode dropdown
            clickable = mode_button.as_element() or mode_button
            await clickable.click()
            await asyncio.sleep(1.0)

            # Find target menu item (role="menuitem" containing target text)
            mode_clicked = False
            menu_items = await frame.query_selector_all('[role="menuitem"]')
            for item in menu_items:
                try:
                    text = (await item.text_content() or "").strip()
                    if target_lower in text.lower():
                        await item.click()
                        mode_clicked = True
                        logger.info("Selected '%s' mode", target_display)
                        break
                except Exception:
                    continue

            if not mode_clicked:
                # Fallback: click span with exact text
                span = await frame.query_selector(f'span:text-is("{target_display}")')
                if span:
                    await span.click()
                    logger.info("Selected '%s' mode (via span)", target_display)
                else:
                    logger.warning("'%s' option not found in dropdown", target_display)

            await asyncio.sleep(0.5)

            # Dismiss dropdown
            try:
                await frame.press("body", "Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        except Exception as e:
            logger.debug("Could not set reasoning mode: %s", e)
            try:
                await frame.press("body", "Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _find_settings_button(self, frame):
        """Find the three-dot settings/menu button in the ChatGPT frame.

        The button contains an SVG with a path drawing three horizontal dots:
        M3 12a2 2 0 1 1 4 0 ... M10 12a2 2 0 1 1 4 0 ... M17 12a2 2 0 1 1 4 0 ...
        """
        # Strategy 1: aria-label
        for sel in [
            'button[aria-label*="settings" i]',
            'button[aria-label*="option" i]',
            'button[aria-label*="menu" i]',
            'button[aria-label*="more" i]',
        ]:
            try:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    return btn
            except Exception:
                continue

        # Strategy 2: find button whose SVG path contains the three-dot signature
        try:
            buttons = await frame.query_selector_all("button")
            for btn in buttons:
                try:
                    if not await btn.is_visible():
                        continue
                    svg = await btn.query_selector("svg")
                    if not svg:
                        continue
                    path_el = await svg.query_selector("path")
                    if not path_el:
                        continue
                    d_attr = await path_el.get_attribute("d") or ""
                    # The three-dot icon has pattern: M3 12...M10 12...M17 12
                    if "M3 12" in d_attr and "M10 12" in d_attr and "M17 12" in d_attr:
                        return btn
                except Exception:
                    continue
        except Exception:
            pass

        return None

    async def _enable_auto_apply_edits(self, frame):
        """Open settings and enable 'Apply edits automatically' if OFF."""
        try:
            logger.info("🔧 Checking 'Apply edits automatically' toggle...")

            # Find and click the three-dot settings button
            settings_btn = await self._find_settings_button(frame)
            if not settings_btn:
                # Also search across all frames
                for f in self.page.frames:
                    settings_btn = await self._find_settings_button(f)
                    if settings_btn:
                        break

            if not settings_btn:
                logger.warning(
                    "⚠️ Settings button not found — skipping auto-apply check"
                )
                return

            await settings_btn.click()
            await asyncio.sleep(1.0)

            # Find the toggle switch for "Apply edits automatically"
            # Strategy 1: find by label text, then the associated switch
            switch = None
            for f in [frame] + self.page.frames:
                try:
                    labels = await f.query_selector_all("label")
                    for label in labels:
                        try:
                            text = (await label.text_content() or "").strip()
                            if "Apply edits automatically" in text:
                                label_for = await label.get_attribute("for")
                                if label_for:
                                    switch = await f.query_selector(
                                        f'button[id="{label_for}"]'
                                    )
                                if not switch:
                                    # Try finding sibling/nearby switch
                                    switch = await f.query_selector(
                                        'button[role="switch"]'
                                    )
                                break
                        except Exception:
                            continue
                    if switch:
                        break
                except Exception:
                    continue

            # Strategy 2: just find any role="switch" if label approach fails
            if not switch:
                for f in [frame] + self.page.frames:
                    try:
                        switch = await f.query_selector('button[role="switch"]')
                        if switch and await switch.is_visible():
                            break
                        switch = None
                    except Exception:
                        continue

            if switch:
                state = await switch.get_attribute("data-state")
                aria_checked = await switch.get_attribute("aria-checked")

                if state == "unchecked" or aria_checked == "false":
                    await switch.click()
                    await asyncio.sleep(0.5)
                    logger.info("✅ Enabled 'Apply edits automatically'")
                else:
                    logger.info("✅ 'Apply edits automatically' is already ON")
            else:
                logger.warning("⚠️ 'Apply edits automatically' toggle not found")

            # Close settings panel
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        except Exception as e:
            logger.debug(f"Could not check auto-apply toggle: {e}")
            try:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Panel close
    # ------------------------------------------------------------------

    async def _close_panel(self) -> bool:
        """Close the ChatGPT add-in panel to allow fresh reopen."""
        try:
            close_selectors = [
                'button[aria-label="Close task pane"]',
                'button[aria-label="Close"]',
                'button[title="Close"]',
                '[data-automation-id="PanelCloseButton"]',
            ]
            for selector in close_selectors:
                for ctx in [self.page] + self.page.frames:
                    try:
                        el = await ctx.query_selector(selector)
                        if el and await el.is_visible():
                            await el.click()
                            logger.info(f"✅ Closed panel via: {selector}")
                            self._chatgpt_frame = None
                            self._setup_completed = False
                            await asyncio.sleep(3)
                            return True
                    except Exception:
                        continue

            # Fallback: toggle via Add-ins ribbon
            logger.info("Close button not found, toggling via Add-ins...")
            for ctx in [self.page] + self.page.frames:
                try:
                    await ctx.click("text=Add-ins", timeout=3000)
                    await asyncio.sleep(2)
                    break
                except Exception:
                    continue
            # Click add-in tile in the My Add-ins popup (uses JS evaluate
            # to handle name mismatches like "Claude by Anthropic for Excel")
            if await self._click_addon_in_layer(self.get_addon_name()):
                self._chatgpt_frame = None
                self._setup_completed = False
                await asyncio.sleep(3)
                return True

            return False
        except Exception as e:
            logger.debug(f"Error closing panel: {e}")
            return False

    # ------------------------------------------------------------------
    # Session health
    # ------------------------------------------------------------------

    async def verify_session_health(self) -> bool:
        """Verify ChatGPT iframe is responsive (not just present)."""
        try:
            self._chatgpt_frame = None
            frame = await self._get_chatgpt_frame()
            if not frame:
                logger.warning("❌ Health check: ChatGPT frame not found")
                return False

            textarea = await self._find_input_element(frame)
            if not textarea or not await textarea.is_visible():
                logger.warning("❌ Health check: input not visible")
                return False

            # Test interactivity
            await textarea.focus()
            await asyncio.sleep(0.3)

            logger.info("✅ Session health check passed")
            return True
        except Exception as e:
            logger.warning(f"❌ Health check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Prompt submission
    # ------------------------------------------------------------------

    async def submit_prompt(
        self, prompt: str, prompt_number: int, has_attachments: bool = False
    ) -> bool:
        """Submit a prompt to ChatGPT using the chat input."""
        try:
            # On first prompt (or file-only submission), ensure setup is done
            if prompt_number == 1 or (prompt_number == 0 and has_attachments):
                await self.handle_initial_setup()

            frame = await self._get_chatgpt_frame()
            if not frame:
                logger.error("❌ Could not find ChatGPT frame")
                return False

            # Special case: Empty prompt with attachments (file-only submission)
            if has_attachments and not prompt:
                logger.info("📎 Files already attached, submitting without text entry")
                textarea = await self._find_input_element(frame)
                if textarea:
                    await textarea.click()
                    await asyncio.sleep(0.3)
            else:
                # Normal prompt submission with text entry
                textarea = await self._find_input_element(frame)
                if not textarea:
                    logger.error("❌ Could not find chat-input element")
                    return False

                await textarea.click(force=True)
                await asyncio.sleep(0.3)
                # Clear first, then fill
                await textarea.fill("")
                await asyncio.sleep(0.1)
                await textarea.fill(prompt)
                logger.info("✅ Filled prompt in ChatGPT input")

            await asyncio.sleep(0.5)

            # Click send button
            send_btn = await self._find_send_button(frame)
            if send_btn:
                await send_btn.click(force=True)
                logger.info("✅ Clicked send button")
            else:
                # Fallback: press Enter
                if textarea:
                    await textarea.press("Enter")
                    logger.info("✅ Pressed Enter to send")
                else:
                    logger.error("❌ Could not find textarea or send button")
                    return False

            return True

        except Exception as e:
            logger.error(f"❌ Failed to submit prompt: {e}")
            return False

    async def _find_send_button(self, frame):
        """Find the send/submit button in the ChatGPT frame.

        The send button contains an SVG with an upward arrow path:
        M11.293 5.293a1 1 0 0 1 1.414 0l5 5a1...
        """
        # Strategy 1: standard selectors
        for sel in [
            '[data-testid="send-button"]',
            'button[aria-label="Send message"]',
            'button[aria-label="Send"]',
            'button[aria-label*="Send" i]',
        ]:
            try:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    return btn
            except Exception:
                continue

        # Strategy 2: find button with the arrow SVG path
        try:
            buttons = await frame.query_selector_all("button")
            for btn in buttons:
                try:
                    if not await btn.is_visible():
                        continue
                    svg = await btn.query_selector("svg")
                    if not svg:
                        continue
                    path_el = await svg.query_selector("path")
                    if not path_el:
                        continue
                    d_attr = await path_el.get_attribute("d") or ""
                    # The send arrow has "M11.293 5.293" in its path
                    if "11.293" in d_attr and "5.293" in d_attr:
                        return btn
                except Exception:
                    continue
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # File upload
    # ------------------------------------------------------------------

    async def upload_files(self, file_paths: str | list) -> bool:
        """Upload files to ChatGPT via the Plus (+) button."""
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        if not file_paths:
            return True

        MAX_SIZE = 1 * 1024 * 1024  # 1 MB

        logger.info(f"📎 Checking {len(file_paths)} file(s) for upload...")
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                logger.error(f"❌ File not found: {fp}")
                return False
            if p.stat().st_size > MAX_SIZE:
                logger.error(f"❌ File too large: {p.name}")
                return False
            logger.info(f"   📄 {p.name} ({p.stat().st_size / 1024 / 1024:.2f} MB)")

        try:
            frame = await self._get_chatgpt_frame()
            if not frame:
                logger.error("❌ Could not find ChatGPT frame for upload")
                return False

            for i, fp in enumerate(file_paths):
                p = Path(fp)
                logger.info(f"📤 Uploading {i + 1}/{len(file_paths)}: {p.name}")

                # Find the Plus (+) button
                plus_btn = await self._find_plus_button(frame)
                if not plus_btn:
                    logger.error("❌ Plus (+) button not found for file upload")
                    return False

                logger.info("✅ Found Plus (+) button — clicking...")
                async with self.page.expect_file_chooser(timeout=5000) as fc:
                    await plus_btn.click()
                chooser = await fc.value
                await chooser.set_files([fp])
                logger.info(f"   ✅ Selected: {p.name}")

                await asyncio.sleep(min(5 + p.stat().st_size / 1024 / 100, 30))

            logger.info(f"✅ All {len(file_paths)} file(s) uploaded!")
            return True

        except Exception as e:
            logger.error(f"❌ Upload failed: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False

    async def _find_plus_button(self, frame):
        """Find the Plus (+) file attachment button in the ChatGPT frame.

        The button contains an SVG with a plus/cross path:
        M12 3.59998...C12.5891 3.59998 13.0666 4.07754 13.0666 4.66664V10.9333H19.3333...
        """
        # Strategy 1: aria-label
        for sel in [
            'button[aria-label*="attach" i]',
            'button[aria-label*="upload" i]',
            'button[aria-label*="file" i]',
            'button[aria-label*="add" i]',
        ]:
            try:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    return btn
            except Exception:
                continue

        # Strategy 2: find button with plus SVG path signature
        try:
            buttons = await frame.query_selector_all("button")
            for btn in buttons:
                try:
                    if not await btn.is_visible():
                        continue
                    svg = await btn.query_selector("svg")
                    if not svg:
                        continue
                    path_el = await svg.query_selector("path")
                    if not path_el:
                        continue
                    d_attr = await path_el.get_attribute("d") or ""
                    # The plus icon has "M12 3.59998" in its path
                    if "M12 3.59998" in d_attr or "12 3.6" in d_attr:
                        return btn
                except Exception:
                    continue
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Completion detection
    # ------------------------------------------------------------------

    async def _has_stop_button(self, frame) -> bool:
        """Check if the Stop button is visible (ChatGPT is processing).

        The stop button contains a square SVG:
        M6 8C6 6.89543 6.89543 6 8 6H16C17.1046 6 18 6.89543 18 8V16...
        wrapped in <span class="_ButtonInner_1jdeq_4">
        """
        try:
            # Strategy 1: aria-label
            for sel in [
                'button[aria-label="Stop"]',
                'button[aria-label*="stop" i]',
                'button[data-testid="stop-button"]',
            ]:
                btn = await frame.query_selector(sel)
                if btn and await btn.is_visible():
                    return True

            # Strategy 2: find button with _ButtonInner span containing square SVG
            buttons = await frame.query_selector_all("button")
            for btn in buttons:
                try:
                    if not await btn.is_visible():
                        continue
                    # Check for the ButtonInner span pattern
                    inner = await btn.query_selector("span")
                    if not inner:
                        continue
                    svg = await inner.query_selector("svg")
                    if not svg:
                        continue
                    path_el = await svg.query_selector("path")
                    if not path_el:
                        continue
                    d_attr = await path_el.get_attribute("d") or ""
                    # The stop-square path starts with "M6 8C6 6.89543"
                    if "M6 8C6 6.89543" in d_attr:
                        return True
                except Exception:
                    continue

        except Exception:
            pass
        return False

    async def _get_response_count(self, frame) -> int:
        """Get current number of ChatGPT responses.

        ChatGPT responses may use data-message-author-role="assistant"
        or similar DOM structure. Falls back to generic detection.
        """
        try:
            # Try several selectors for ChatGPT response containers
            for sel in [
                '[data-message-author-role="assistant"]',
                "article",
                'div[data-testid*="conversation-turn"]',
            ]:
                responses = await frame.query_selector_all(sel)
                if responses:
                    return len(responses)
            return 0
        except Exception:
            return 0

    async def wait_for_completion(
        self, prompt_number: int, initial_counts: dict = None
    ) -> bool:
        """Wait for ChatGPT to complete by checking Stop button presence.

        Logic (mirrors Claude):
        1. Wait for Stop button to APPEAR (ChatGPT started processing)
        2. Wait for Stop button to DISAPPEAR (ChatGPT finished)
        3. Brief stabilization to ensure ChatGPT is fully done
        """
        agent_config = self.get_config_section()
        max_wait = agent_config.get("max_wait_per_prompt_seconds", 900)

        # Get initial response count for logging
        self._chatgpt_frame = None
        frame = await self._get_chatgpt_frame()
        initial_response_count = await self._get_response_count(frame) if frame else 0

        logger.info(
            f"⏳ Waiting for prompt #{prompt_number} "
            f"(starting with {initial_response_count} responses)..."
        )

        start_time = time.monotonic()
        check_interval = 1  # Check every second for responsiveness
        saw_stop_button = False

        while (time.monotonic() - start_time) < max_wait:
            if self.shutdown_event and self.shutdown_event.is_set():
                return False

            await asyncio.sleep(check_interval)
            elapsed = int(time.monotonic() - start_time)

            # Refresh frame reference
            self._chatgpt_frame = None
            frame = await self._get_chatgpt_frame()
            if not frame:
                continue

            # Scroll to bottom periodically
            if elapsed % 5 == 0:
                try:
                    await frame.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                except Exception:
                    pass

            try:
                stop_visible = await self._has_stop_button(frame)
                current_response_count = await self._get_response_count(frame)

                if stop_visible:
                    # ChatGPT is processing
                    saw_stop_button = True
                    if elapsed % 10 == 0:
                        logger.info(
                            f"   [{elapsed}s] ChatGPT processing... "
                            f"(responses: {current_response_count})"
                        )
                    continue

                # Stop button not visible
                if not saw_stop_button:
                    # Haven't seen ChatGPT start yet — keep waiting
                    if elapsed % 10 == 0:
                        logger.info(
                            f"   [{elapsed}s] Waiting for ChatGPT to start... "
                            f"(responses: {current_response_count})"
                        )
                    continue

                # Saw stop button, now it's gone — ChatGPT finished!
                logger.info(
                    f"   [{elapsed}s] Stop button gone, verifying completion..."
                )
                await asyncio.sleep(3)

                # Re-check that stop button is still gone
                self._chatgpt_frame = None
                frame = await self._get_chatgpt_frame()
                if frame:
                    still_stopped = not await self._has_stop_button(frame)
                    final_count = await self._get_response_count(frame)

                    if not still_stopped:
                        # Stop button reappeared — ChatGPT started again
                        logger.info(f"   [{elapsed}s] ChatGPT resumed processing...")
                        continue

                    # Stop button gone and stayed gone — ChatGPT finished.
                    # Trust the stop button signal. Response count selectors
                    # may not match the add-in iframe DOM, so don't gate on it.
                    logger.info(
                        f"✅ Prompt #{prompt_number} completed! "
                        f"(responses: {final_count}, was {initial_response_count})"
                    )
                    return True

            except Exception as e:
                logger.debug(f"   [{elapsed}s] Check error: {e}")

        logger.error(f"❌ Timeout waiting for prompt #{prompt_number}")
        return False

    # ------------------------------------------------------------------
    # Feedback buttons (for base class completion detection fallback)
    # ------------------------------------------------------------------

    async def get_button_count(self) -> dict:
        """Count ChatGPT's feedback buttons (thumbs up/down)."""
        try:
            frame = await self._get_chatgpt_frame()
            if not frame:
                return {"upvote": 0, "downvote": 0}

            upvote_count = 0
            downvote_count = 0

            up_selectors = [
                'button[aria-label*="thumbs up" i]',
                'button[aria-label*="like" i]',
                '[data-testid*="thumbs-up"]',
                '[data-testid*="upvote"]',
            ]
            down_selectors = [
                'button[aria-label*="thumbs down" i]',
                'button[aria-label*="dislike" i]',
                '[data-testid*="thumbs-down"]',
                '[data-testid*="downvote"]',
            ]

            for sel in up_selectors:
                try:
                    btns = await frame.query_selector_all(sel)
                    upvote_count += len(btns)
                except Exception:
                    continue

            for sel in down_selectors:
                try:
                    btns = await frame.query_selector_all(sel)
                    downvote_count += len(btns)
                except Exception:
                    continue

            return {"upvote": upvote_count, "downvote": downvote_count}

        except Exception as e:
            logger.debug(f"Error counting ChatGPT buttons: {e}")
            return {"upvote": 0, "downvote": 0}
