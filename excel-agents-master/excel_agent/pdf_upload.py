#!/usr/bin/env python3
"""
PDF Upload Module for TabAI Integration

This module handles PDF file uploads through the TabAI interface within
Microsoft Excel Online. It provides functionality to attach PDF documents
to TabAI prompts for processing alongside Excel data.
"""

import asyncio
import logging

# Initialize logger
logger = logging.getLogger(__name__)


async def log_all_buttons(page):
    """Log basic info about every visible button element on the page and its iframes.

    Helps discover the selector for the attachment/upload control.

    Args:
        page: Playwright page object
    """
    try:
        containers = [("main page", page)] + [
            (f"frame[{i}] {frame.url}", frame) for i, frame in enumerate(page.frames)
        ]
        for container_name, container in containers:
            try:
                btns = await container.query_selector_all(
                    'button, [role="button"], input[type="button"], div[role="button"]'
                )
                logger.info(
                    f"🔍 {container_name}: Found {len(btns)} button-like elements"
                )
                for idx, btn in enumerate(btns):
                    try:
                        raw_text = await btn.inner_text()
                        txt = raw_text.strip() if raw_text else ""
                    except Exception:
                        txt = ""
                    try:
                        aria = (await btn.get_attribute("aria-label")) or ""
                    except Exception:
                        aria = ""
                    try:
                        tag = (await btn.evaluate("e => e.tagName")) or "?"
                    except Exception:
                        tag = "?"
                    try:
                        classes = (await btn.get_attribute("class")) or ""
                    except Exception:
                        classes = ""
                    # capture outerHTML snippet to help identify icons
                    try:
                        outer_html = await btn.evaluate("e => e.outerHTML")
                        outer_html_snip = outer_html[:120].replace("\n", " ")
                    except Exception:
                        outer_html_snip = ""
                    logger.info(
                        f"   [{idx}] <{tag.lower()}> text='{txt[:60]}' aria='{aria}' class='{classes[:60]}' html='{outer_html_snip}'"
                    )
            except Exception as e:
                logger.debug(f"Failed to inspect {container_name}: {e}")
    except Exception as e:
        logger.error(f"log_all_buttons failed: {e}")


async def upload_pdf_via_tabai(page, file_paths):
    """Upload files using the TabAI attachments button.

    This function:
    1. Validates file paths and size limits
    2. Locates the Excel frame and TabAI iframe
    3. Finds the attachments button
    4. Handles file upload through browser file chooser

    Args:
        page: Playwright page object
        file_paths: List of file paths to upload, or a single file path string

    Returns:
        bool: True on successful upload, False otherwise
    """
    # Normalize to list
    if isinstance(file_paths, str):
        file_paths = [file_paths]
    elif not isinstance(file_paths, list):
        logger.error("❌ file_paths must be a list or string")
        return False

    if not file_paths:
        logger.info("ℹ️ No files to upload; skipping")
        return True  # Not an error

    # Check file sizes (1 MB limit)
    MAX_FILE_SIZE_MB = 1
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    logger.info(f"📎 Checking {len(file_paths)} file(s) for upload...")
    total_size_bytes = 0
    for file_path in file_paths:
        from pathlib import Path

        fp = Path(file_path)

        if not fp.exists():
            logger.error(f"❌ File does not exist: {file_path}")
            return False

        file_size_bytes = fp.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        total_size_bytes += file_size_bytes

        if file_size_bytes > MAX_FILE_SIZE_BYTES:
            logger.error(f"❌ File exceeds {MAX_FILE_SIZE_MB} MB limit: {fp.name}")
            logger.error(f"   File size: {file_size_mb:.2f} MB")
            logger.error("   Aborting task to prevent upload failure")
            return False

        logger.info(f"   📄 {fp.name} ({file_size_mb:.2f} MB)")

    # Locate Excel frame
    excel_frame = page.frame(name="WacFrame_Excel_0")
    if not excel_frame:
        logger.error(
            "❌ Excel frame 'WacFrame_Excel_0' not found – cannot upload files"
        )
        return False

    # Locate TabAI iframe that contains the attachments button
    tabai_frame = None
    search_start = asyncio.get_event_loop().time()
    while (
        asyncio.get_event_loop().time() - search_start < 30
    ):  # Increased from 15 to 30 seconds
        for f in excel_frame.child_frames:
            try:
                if await f.query_selector('[data-testid="attachments-button"]'):
                    tabai_frame = f
                    logger.info("✅ Found TabAI frame with attachments button")
                    break
            except Exception:
                continue
        if tabai_frame:
            break
        await asyncio.sleep(2)  # Slowed down for slow browser

    if not tabai_frame:
        logger.error(
            "❌ TabAI iframe with attachments-button not found – cannot upload files"
        )
        return False

    try:
        # Click attachments button and wait for file chooser
        # Use query_selector instead of get_by_test_id to avoid visibility checks
        attachments_btn = await tabai_frame.query_selector(
            '[data-testid="attachments-button"]'
        )
        if not attachments_btn:
            logger.error("❌ Attachments button not found")
            return False

        async with page.expect_file_chooser() as fc_info:
            await attachments_btn.click()
            logger.info("✅ Clicked attachments button")

        file_chooser = await fc_info.value
        await file_chooser.set_files(file_paths)
        logger.info("📤 Files selected, waiting for upload...")

        # Adaptive wait based on file size: 8 sec base + 2 sec per 100 KB, capped at 120 sec
        total_size_kb = total_size_bytes / 1024
        size_based_wait = 2 * (total_size_kb / 100)  # 2 seconds per 100 KB
        upload_wait_time = min(
            8 + size_based_wait, 120
        )  # Base 8s + size-based, max 120s

        logger.info(
            f"⏳ Waiting {upload_wait_time:.1f}s for upload (based on {total_size_kb:.1f} KB total size)..."
        )
        await asyncio.sleep(upload_wait_time)
        logger.info(f"✅ Upload completed - {len(file_paths)} file(s)")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to upload files via attachments-button: {e}")
        import traceback

        logger.debug(traceback.format_exc())
        return False
