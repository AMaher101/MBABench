#!/usr/bin/env python3
"""
Excel Agent Engine - Automate AI agents in Excel Online

Modular automation engine using Playwright for browser control.
Supports multiple agent types (TabAI, Claude, ChatGPT).

Workflow:
1. Load config (with hierarchical overrides)
2. Launch persistent browser
3. Navigate to Excel file on OneDrive
4. Open AI agent and process prompts
5. Save and exit

Usage:
    # Standalone (interactive)
    uv run --active python engine.py --config config.yaml

    # Batch automation (recommended)
    python batch_automation_runner.py --tasks my_tasks.yaml --runner-config runner_configs/tabai.yaml

    # Standalone (non-interactive)
    uv run --active python engine.py --config config.yaml --no-hold

Config Hierarchy (merged in order):
    1. config.yaml (base defaults)
    2. Temp config from batch_automation_runner (task-specific)
    3. CLI flags (--no-hold, --max-runtime)
    4. Environment variables (ONEDRIVE_EMAIL, TABAI_EMAIL)
"""

import argparse
import asyncio
import logging
import os
import platform
import signal
import sys
from datetime import datetime
from pathlib import Path

# Import core modules
from core import (
    BrowserManager,
    ChatGPTCore,
    ClaudeCore,
    ConfigLoader,
    ExcelOperations,
    FileManager,
    Navigation,
    TabAICore,
    kill_all_browser_processes,
    setup_logging,
)
from core.logging_setup import SafeStreamHandler
from core.completion_logger import CompletionLogger
from core.file_organizer import (
    AGENT_STATUSES,
    FileOrganizer,
    TaskStatus,
    ValidationResult,
    get_run_folder_label,
)
from dotenv import load_dotenv

# Import utilities that stayed standalone
from playwright.async_api import async_playwright

# Initialize logger (will be configured after loading config)
logger = logging.getLogger(__name__)

# Global shutdown event for graceful termination
shutdown_event = asyncio.Event()


def _handle_signal(signum, frame):
    """Handle shutdown signals gracefully."""
    try:
        shutdown_event.set()
    except Exception:
        pass
    # Restore default handler so second Ctrl+C force quits
    signal.signal(signal.SIGINT, signal.SIG_DFL)


async def run_automation(config: dict) -> str:
    """
    Main automation entry point — single attempt.

    Called once per task attempt. The batch runner handles retries
    by launching fresh subprocesses (fresh event loop, no zombies).

    Args:
        config: Configuration dictionary

    Returns:
        "success" — task completed and validated
        "agent_failure" — agent ran but failed (prompt_failed, timeout)
        "pipeline_failure" — infrastructure failure (nav_failed, excel_failed, etc.)
    """
    # Get no_hold from CLI args (set by batch runner or user)
    import sys

    no_hold = "--no-hold" in sys.argv

    # Get agent type to determine which config section to use
    agent_type = config.get(
        "agent_type", "tabai"
    )  # Default to tabai for backward compat

    # Get timeout setting (simplified - only one timeout source)
    max_sec_per_task = config.get(agent_type, {}).get("max_sec_per_task", 0)
    if max_sec_per_task and max_sec_per_task > 0:
        effective_timeout = max_sec_per_task
        logger.info(
            f"⏱️  Task timeout enabled: {effective_timeout}s ({effective_timeout//60}m)"
        )
    else:
        # No timeout
        effective_timeout = 0
        logger.info("⏱️  No timeout (unlimited runtime)")

    # Runtime/Task guard task (created per-attempt inside retry loop)
    async def _timeout_guard():
        if effective_timeout and effective_timeout > 0:
            await asyncio.sleep(effective_timeout)
            logger.error(f"⏱️  TIMEOUT: Task exceeded {effective_timeout}s limit")
            shutdown_event.set()

    guard_task = None

    try:
        # Optional: Kill all browsers if requested (manual cleanup mode)
        if (
            config.get(agent_type, {})
            .get("runtime", {})
            .get("kill_all_browsers", False)
        ):
            logger.warning(
                "⚠️  Killing all browser processes (--kill-all-browsers enabled)"
            )
            kill_all_browser_processes()
            await asyncio.sleep(2)  # Slowed down for slow browser
        else:
            await asyncio.sleep(0.8)  # Slowed down for slow browser

        # Initialize managers
        browser_mgr = BrowserManager(config)
        file_mgr = FileManager()

        # Get task name and source from config (set by batch automation runner)
        task_name = config["task_name"]
        task_source = config.get("task_source", "")
        direct_url = config.get("direct_url")
        onedrive_path = config.get("onedrive_path")  # explicit OneDrive path
        template_file = config.get("template_file")  # explicit workbook
        explicit_upload_files = config.get("upload_files")  # explicit uploads
        solution_name = config.get("solution_name")  # explicit output name
        local_files_base = config.get("local_files_base")  # base dir for uploads

        # Use agent_type directly as agent_name (no mapping needed)
        agent_name = agent_type

        # --- File resolution ---
        # Explicit format: caller listed the upload files in the task config
        if explicit_upload_files is not None:
            logger.info("📤 Using explicit upload_files from task config")
            files_to_upload = file_mgr.resolve_upload_files(
                explicit_upload_files, local_files_base
            )
            # template_file (when present) names a workbook on OneDrive to open
            if template_file and template_file.lower() != "blank":
                workbook_file = template_file  # name of file on OneDrive
                logger.info(f"📊 Will open template workbook: {template_file}")
            else:
                workbook_file = None
                logger.info("📝 Will create new blank workbook")
        else:
            # Task-source shorthand: auto-discover from local main_tasks/ folder.
            logger.info(f"📦 Task source: {task_source or '(none)'}")
            logger.info(f"🔍 Searching for workbook in {task_source}/{task_name}/Task/")

            local_files = file_mgr.get_local_task_files(task_name, task_source)
            logger.info(
                f"📂 Found {len(local_files)} local files for task '{task_name}'"
            )

            workbook_file = file_mgr.find_workbook_file(local_files, task_source)
            if workbook_file:
                logger.info(f"✅ Workbook exists locally: {workbook_file}")
                logger.info("   Will OPEN existing workbook (not create new)")
            else:
                logger.info("📝 No workbook found locally")
                logger.info("   Will CREATE new Solution workbook")

            # Determine files to upload
            skip_upload = config.get(agent_type, {}).get("skip_file_upload", False)
            if skip_upload:
                files_to_upload = []
                logger.info("⏭️  File upload skipped (skip_file_upload=true)")
            else:
                files_to_upload = file_mgr.get_files_to_upload(
                    local_files, workbook_file
                )

        prompt_version = config.get("prompt_version")
        # Accept attempt_number from batch runner (default 0 for standalone)
        attempt_number = config.get("attempt_number", 0)
        current_log_file_path = config.get("_log_file_path")

        # Launch browser with persistent context
        async with async_playwright() as playwright:
            # Launch browser
            browser, context = await browser_mgr.launch_browser(playwright)

            # Handle page creation based on browser mode
            if browser_mgr.is_cdp_mode():
                # CDP mode: Always create a fresh page, never touch existing tabs
                page = await context.new_page()
                logger.info(
                    f"✅ Created fresh page (CDP mode, {len(context.pages)} total tabs)"
                )
            else:
                # Classic mode: Get or create page, then navigate immediately
                num_tabs = len(context.pages)
                logger.info(f"🧹 Browser state: {num_tabs} existing tab(s)")

                if num_tabs == 0:
                    # No pages exist - create one and navigate immediately
                    page = await context.new_page()
                    logger.info("✅ Created new page")
                elif num_tabs == 1:
                    page = context.pages[0]
                    logger.info("✅ Using existing page")
                else:
                    # Multiple pages - keep first, close extras
                    page = context.pages[0]
                    for i, old_page in enumerate(context.pages[1:], start=1):
                        try:
                            await old_page.close()
                        except Exception:
                            pass

            # ================================================================
            # Create date-organized run directory
            # ================================================================
            today = datetime.now().strftime("%Y%m%d")
            folder_label = get_run_folder_label(agent_type)
            run_dir = Path.cwd() / f"{today}_{folder_label}"
            run_dir.mkdir(parents=True, exist_ok=True)
            json_logs_dir = run_dir / "json_logs"
            json_logs_dir.mkdir(parents=True, exist_ok=True)

            final_task_status = None  # None = no failure yet
            task_success = False
            excel_page = None
            task_page = None
            completion_logger = None

            # Helper to close all task pages (defined before loop so finally always has it)
            async def _close_task_pages(skip_save=False):
                nonlocal excel_page, task_page
                CLOSE_TIMEOUT = 30

                async def _do_close():
                    if not skip_save:
                        try:
                            if excel_page and not excel_page.is_closed():
                                save_key = (
                                    "Meta+S"
                                    if platform.system() == "Darwin"
                                    else "Control+S"
                                )
                                logger.info("💾 Saving Excel file before closing...")
                                await excel_page.keyboard.press(save_key)
                                await asyncio.sleep(2)
                                await asyncio.sleep(5)
                                logger.info("✅ Save complete, closing pages...")
                        except Exception as e:
                            logger.debug(f"Save attempt: {e}")
                    else:
                        logger.info("⏩ Skipping save (task failed), closing pages...")

                    try:
                        if excel_page and not excel_page.is_closed():
                            await excel_page.close()
                    except Exception:
                        pass
                    try:
                        if (
                            task_page
                            and task_page != page
                            and not task_page.is_closed()
                        ):
                            await task_page.close()
                    except Exception:
                        pass

                try:
                    await asyncio.wait_for(_do_close(), timeout=CLOSE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⚠️ _close_task_pages timed out after {CLOSE_TIMEOUT}s "
                        "— force-closing pages"
                    )
                    for pg in [excel_page, task_page]:
                        try:
                            if pg and not pg.is_closed():
                                if pg == page:
                                    continue
                                await pg.close()
                        except Exception:
                            pass

            # Build task identifier for this attempt
            retry_suffix = f"_retry{attempt_number}" if attempt_number > 0 else ""
            attempt_task_id = f"{task_name}{retry_suffix}"

            # Create CompletionLogger for this attempt
            completion_logger = CompletionLogger(
                log_dir=str(json_logs_dir),
                task_identifier=attempt_task_id,
                agent_name=agent_name,
                prompt_version=prompt_version,
                task_source=task_source,
                max_sec_per_task=effective_timeout,
            )

            # Start timeout guard
            guard_task = asyncio.create_task(_timeout_guard())

            try:
                if shutdown_event.is_set():
                    logger.warning("⏱️ Shutdown event set — skipping")
                    final_task_status = TaskStatus.TIMEOUT

                # ==============================================================
                # PIPELINE PHASE: navigate → create workbook → open panel
                # ==============================================================

                # --- Navigate to OneDrive ---
                if final_task_status is None:
                    logger.info("🌐 Navigating to OneDrive...")
                    try:
                        await page.goto(
                            "https://onedrive.live.com",
                            wait_until="load",
                            timeout=60000,
                        )
                        await asyncio.sleep(2)
                        logger.info(f"✅ OneDrive loaded: {page.url}")
                    except Exception as e:
                        logger.error(f"❌ Failed to load OneDrive: {e}")
                        final_task_status = TaskStatus.NAV_FAILED

                # --- Navigate to task folder with retries ---
                if final_task_status is None:
                    logger.info(
                        f"📁 Navigating to task folder (source: {task_source or 'custom'})"
                    )
                    nav = Navigation()
                    task_page = None
                    NAV_TIMEOUT = 120

                    async def _nav_with_retries():
                        nonlocal task_page
                        for nav_attempt in range(3):
                            if nav_attempt > 0:
                                logger.warning(
                                    f"⚠️ Navigation attempt {nav_attempt + 1}/3..."
                                )
                                try:
                                    await page.goto(
                                        "https://onedrive.live.com",
                                        wait_until="load",
                                        timeout=60000,
                                    )
                                    await asyncio.sleep(2)
                                except Exception as e:
                                    logger.error(f"❌ Could not reload OneDrive: {e}")
                                    continue

                            task_page = await nav.navigate_to_task_folder(
                                page,
                                task_name,
                                task_source,
                                base_path=config.get("file_path"),
                                direct_url=direct_url,
                                onedrive_path=onedrive_path,
                            )
                            if task_page:
                                break

                    try:
                        await asyncio.wait_for(_nav_with_retries(), timeout=NAV_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.error(
                            f"❌ Navigation phase timed out after {NAV_TIMEOUT}s"
                        )
                        task_page = None

                    if not task_page:
                        logger.error(
                            "❌ Failed to navigate to task folder after 3 attempts"
                        )
                        final_task_status = TaskStatus.NAV_FAILED

                # --- Handle Excel file ---
                if final_task_status is None:
                    excel_ops = ExcelOperations()
                    if solution_name:
                        base_name = f"{solution_name}_{agent_name}"
                    else:
                        # Drop the source segment when no task_source is set
                        # so custom-source projects don't get a stray underscore.
                        source_segment = f"_{task_source}" if task_source else ""
                        base_name = (
                            f"{task_name}_Solution{source_segment}_{agent_name}_Model"
                        )

                    if workbook_file and attempt_number == 0:
                        logger.info("=" * 60)
                        logger.info("📊 OPENING EXISTING WORKBOOK AND CREATING COPY")
                        logger.info("=" * 60)
                        # In the explicit format, template_file is already a
                        # plain filename. In the shorthand format, workbook_file
                        # is a full local path — extract just the name.
                        workbook_filename = (
                            workbook_file
                            if explicit_upload_files is not None
                            else Path(workbook_file).name
                        )
                        logger.info(f"   Opening file: {workbook_filename}")
                        excel_page = await nav.open_specific_file(
                            task_page, workbook_filename
                        )
                    else:
                        logger.info("=" * 60)
                        logger.info("📊 CREATING NEW WORKBOOK")
                        logger.info("=" * 60)
                        excel_page = await excel_ops.create_solution_model(task_page)

                    if not excel_page:
                        logger.error("❌ Failed to open/create Excel file")
                        final_task_status = TaskStatus.EXCEL_FAILED

                # --- Create standardized copy with incremental naming ---
                if final_task_status is None:
                    RENAME_TIMEOUT_S = 240
                    logger.info(
                        f"✏️ Creating copy: N_{base_name}.xlsx "
                        f"(hard timeout: {RENAME_TIMEOUT_S}s)"
                    )
                    copy_success = False
                    next_number = 0

                    async def _rename_phase():
                        nonlocal copy_success, next_number
                        for attempt in range(4):
                            if attempt > 0:
                                logger.info(
                                    f"⚠️ Attempt {attempt} failed, retrying "
                                    f"(attempt {attempt + 1}/4)..."
                                )
                                try:
                                    await excel_page.keyboard.press("Escape")
                                    await asyncio.sleep(1.0)
                                    await excel_page.keyboard.press("Escape")
                                    await asyncio.sleep(1.0)
                                except Exception:
                                    pass
                                wait_time = (2**attempt) * 2
                                logger.info(f"   Waiting {wait_time}s before retry...")
                                await asyncio.sleep(wait_time)
                            else:
                                await asyncio.sleep(3)

                            copy_success, next_number = await excel_ops.rename_workbook(
                                excel_page,
                                base_name,
                                retry=(attempt > 0),
                                start_number=next_number,
                            )
                            if copy_success:
                                break

                    try:
                        await asyncio.wait_for(
                            _rename_phase(), timeout=RENAME_TIMEOUT_S
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"⏱️ RENAME TIMEOUT: rename phase exceeded "
                            f"{RENAME_TIMEOUT_S}s — aborting"
                        )
                        copy_success = False

                    if not copy_success:
                        logger.error("❌ Could not create copy after 4 attempts")
                        logger.error(
                            "   Aborting task to avoid modifying " "original model file"
                        )
                        final_task_status = TaskStatus.EXCEL_FAILED

                # --- Close dialogs + Initialize AI Agent ---
                if final_task_status is None:
                    await asyncio.sleep(3)
                    await excel_page.keyboard.press("Escape")
                    await asyncio.sleep(1.0)

                    agent_type = config.get("agent_type", "tabai")

                    if agent_type == "claude_excel_agent":
                        ai_agent = ClaudeCore(
                            excel_page,
                            config,
                            shutdown_event,
                            completion_logger,
                        )
                        logger.info("🤖 Using Claude by Anthropic")
                    elif agent_type == "chatgpt_excel_agent":
                        ai_agent = ChatGPTCore(
                            excel_page,
                            config,
                            shutdown_event,
                            completion_logger,
                        )
                        logger.info("🤖 Using ChatGPT")
                    elif agent_type == "tabai":
                        ai_agent = TabAICore(
                            excel_page,
                            config,
                            shutdown_event,
                            completion_logger,
                        )
                        logger.info("🤖 Using TabAI")
                    else:
                        logger.error(f"❌ Unknown agent_type: {agent_type}")
                        final_task_status = TaskStatus.UNKNOWN

                # --- Find and click AI agent panel ---
                if final_task_status is None:
                    PANEL_OPEN_TIMEOUT_S = 180
                    logger.info(
                        f"🔍 Opening {ai_agent.get_addon_name()} panel... "
                        f"(hard timeout: {PANEL_OPEN_TIMEOUT_S}s)"
                    )
                    agent_opened = False

                    CLOSE_PANEL_TIMEOUT = 15  # cap _close_panel() calls

                    async def _panel_open_phase():
                        nonlocal agent_opened
                        for agent_attempt in range(3):
                            if agent_attempt > 0:
                                if hasattr(ai_agent, "_close_panel"):
                                    logger.info(
                                        "🔄 Closing stale panel before retry..."
                                    )
                                    try:
                                        await asyncio.wait_for(
                                            ai_agent._close_panel(),
                                            timeout=CLOSE_PANEL_TIMEOUT,
                                        )
                                    except asyncio.TimeoutError:
                                        logger.warning(
                                            f"⚠️ _close_panel() timed out "
                                            f"after {CLOSE_PANEL_TIMEOUT}s"
                                        )
                                logger.warning(
                                    f"⚠️ {ai_agent.get_addon_name()} open "
                                    f"attempt {agent_attempt + 1}/3..."
                                )
                                await asyncio.sleep(3)
                                if agent_attempt >= 1:
                                    try:
                                        logger.info("🔄 Refreshing Excel page...")
                                        await excel_page.reload(
                                            wait_until="load", timeout=30000
                                        )
                                        await asyncio.sleep(8)
                                    except Exception as e:
                                        logger.error(f"❌ Could not reload page: {e}")

                            if await ai_agent.find_and_click(max_seconds=15):
                                if await ai_agent.verify_session_health():
                                    agent_opened = True
                                    break
                                else:
                                    logger.warning(
                                        "⚠️ Panel opened but session "
                                        "health check failed"
                                    )
                                    if hasattr(ai_agent, "_close_panel"):
                                        try:
                                            await asyncio.wait_for(
                                                ai_agent._close_panel(),
                                                timeout=CLOSE_PANEL_TIMEOUT,
                                            )
                                        except asyncio.TimeoutError:
                                            logger.warning(
                                                f"⚠️ _close_panel() timed out "
                                                f"after {CLOSE_PANEL_TIMEOUT}s"
                                            )
                                    continue

                    try:
                        await asyncio.wait_for(
                            _panel_open_phase(),
                            timeout=PANEL_OPEN_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"⏱️ PANEL TIMEOUT: panel-open phase exceeded "
                            f"{PANEL_OPEN_TIMEOUT_S}s — aborting"
                        )
                        agent_opened = False

                    # Initial setup runs OUTSIDE the hard timeout — if the
                    # panel is open+verified, setup shouldn't be killed by it.
                    if agent_opened:
                        if not await ai_agent.handle_initial_setup():
                            logger.error(
                                f"❌ {ai_agent.get_addon_name()} "
                                "initial setup failed"
                            )
                            agent_opened = False

                    if not agent_opened:
                        logger.error(
                            f"❌ Could not open "
                            f"{ai_agent.get_addon_name()} panel "
                            "after 3 attempts"
                        )
                        final_task_status = TaskStatus.PANEL_FAILED

                # ======================================================
                # AGENT PHASE: panel is open → start JSON, run prompts
                # ======================================================
                if final_task_status is None:
                    completion_logger.start_task(
                        attempt_task_id,
                        attempt_number=attempt_number + 1,
                    )

                    # --- Process all prompts ---
                    logger.info("🚀 Starting prompt processing...")
                    prompt_success = False
                    try:
                        if not await ai_agent.process_all_prompts(
                            files_to_upload=files_to_upload
                        ):
                            logger.error("❌ Prompt processing failed")
                            if shutdown_event.is_set():
                                completion_logger.end_task(
                                    task_status=TaskStatus.TIMEOUT,
                                    error_msg="Task timed out",
                                )
                                final_task_status = TaskStatus.TIMEOUT
                            else:
                                completion_logger.end_task(
                                    task_status=TaskStatus.PROMPT_FAILED,
                                    error_msg="Prompt processing failed",
                                )
                                final_task_status = TaskStatus.PROMPT_FAILED
                        else:
                            logger.info("✅ All prompts completed successfully!")
                            prompt_success = True

                            # Save URL to file if configured
                            finished_works_file = config.get("finished_works_file")
                            if finished_works_file:
                                try:
                                    finished_works_path = Path(finished_works_file)
                                    if not finished_works_path.is_absolute():
                                        finished_works_path = (
                                            Path.cwd() / finished_works_file
                                        ).resolve()

                                    current_url = (
                                        excel_page.url
                                        if not excel_page.is_closed()
                                        else "URL unavailable (page closed)"
                                    )
                                    timestamp = datetime.now().strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                    finished_works_path.parent.mkdir(
                                        parents=True, exist_ok=True
                                    )
                                    with open(finished_works_path, "a") as f:
                                        f.write(
                                            f"[{timestamp}] {task_name}: "
                                            f"{current_url}\n"
                                        )
                                    logger.info(
                                        f"📝 Saved URL to {finished_works_path}"
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"⚠️ Could not save URL to "
                                        f"{finished_works_file}: {e}"
                                    )

                        # --- Download + validate ---
                        DOWNLOAD_TIMEOUT = 600
                        try:
                            logger.info(
                                "📁 Organizing task files "
                                "(downloading Excel + copying logs)..."
                            )
                            dl_start = datetime.now()
                            try:
                                file_results = await asyncio.wait_for(
                                    FileOrganizer.organize_task_files(
                                        excel_page,
                                        task_name,
                                        current_log_file_path,
                                        str(completion_logger.session_file),
                                        agent_name=agent_name,
                                        task_source=task_source,
                                    ),
                                    timeout=DOWNLOAD_TIMEOUT,
                                )
                            except asyncio.TimeoutError:
                                dl_elapsed = (datetime.now() - dl_start).total_seconds()
                                logger.warning(
                                    f"⚠️ Download timed out after "
                                    f"{dl_elapsed:.0f}s "
                                    f"(limit: {DOWNLOAD_TIMEOUT}s)"
                                )
                                file_results = {
                                    "excel": None,
                                    "validation": ValidationResult(
                                        is_valid=False,
                                        status=TaskStatus.DOWNLOAD_FAILED,
                                        message=(
                                            f"Download timed out after "
                                            f"{dl_elapsed:.0f}s"
                                        ),
                                    ),
                                    "log": None,
                                    "json": None,
                                }
                            else:
                                dl_elapsed = (datetime.now() - dl_start).total_seconds()
                                logger.info(
                                    f"📁 File organization completed in "
                                    f"{dl_elapsed:.0f}s"
                                )

                            if not prompt_success:
                                # Prompt failed — download for archival only
                                validation = file_results.get("validation")
                                if validation and validation.is_valid:
                                    logger.info(
                                        "📁 Archival download OK "
                                        "(prompt still failed)"
                                    )
                                elif validation:
                                    logger.info(
                                        "📁 Archival download: "
                                        f"{validation.status.value}"
                                    )

                            else:
                                # Prompts succeeded — full validation
                                validation = file_results.get("validation")

                                if validation and validation.is_valid:
                                    # SUCCESS
                                    final_task_status = TaskStatus.SUCCESS
                                    task_success = True
                                    completion_logger.end_task(
                                        task_status=TaskStatus.SUCCESS
                                    )
                                    logger.info("✅ Post-task validation PASSED")

                                else:
                                    # Validation failed
                                    if validation:
                                        logger.warning(
                                            "⚠️ Post-task validation "
                                            f"FAILED: "
                                            f"{validation.status.value}"
                                            f" — {validation.message}"
                                        )
                                    else:
                                        logger.warning(
                                            "⚠️ Post-task validation " "returned None"
                                        )

                                    # Try re-download for corruption
                                    redownload_success = False
                                    if validation and validation.status in (
                                        TaskStatus.DOWNLOAD_FAILED,
                                        TaskStatus.FILE_CORRUPTED,
                                    ):
                                        logger.info("🔄 Attempting re-download...")
                                        if (
                                            validation.file_path
                                            and Path(validation.file_path).exists()
                                        ):
                                            try:
                                                Path(validation.file_path).unlink()
                                                logger.info(
                                                    "🗑️ Deleted bad file: "
                                                    f"{validation.file_path}"
                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    "⚠️ Could not delete "
                                                    f"bad file: {e}"
                                                )

                                        file_results2 = await asyncio.wait_for(
                                            FileOrganizer.organize_task_files(
                                                excel_page,
                                                task_name,
                                                current_log_file_path,
                                                str(completion_logger.session_file),
                                                agent_name=agent_name,
                                                task_source=task_source,
                                            ),
                                            timeout=DOWNLOAD_TIMEOUT,
                                        )
                                        validation2 = file_results2.get("validation")
                                        if validation2 and validation2.is_valid:
                                            final_task_status = TaskStatus.SUCCESS
                                            task_success = True
                                            completion_logger.end_task(
                                                task_status=TaskStatus.SUCCESS
                                            )
                                            logger.info(
                                                "✅ Re-download validation " "PASSED"
                                            )
                                            redownload_success = True
                                        else:
                                            logger.warning(
                                                "⚠️ Re-download also "
                                                "failed validation"
                                            )

                                    # Classify the validation failure
                                    if not redownload_success:
                                        val_status = (
                                            validation.status
                                            if validation
                                            else TaskStatus.UNKNOWN
                                        )
                                        completion_logger.end_task(
                                            task_status=val_status,
                                            error_msg=(
                                                validation.message
                                                if validation
                                                else None
                                            ),
                                        )
                                        final_task_status = val_status

                        except Exception as e:
                            logger.warning(
                                f"⚠️ File organization failed: "
                                f"{type(e).__name__}: {e!r}"
                            )
                            if prompt_success:
                                final_task_status = TaskStatus.DOWNLOAD_FAILED
                                completion_logger.end_task(
                                    task_status=TaskStatus.DOWNLOAD_FAILED,
                                    error_msg=(
                                        f"File organization error: "
                                        f"{type(e).__name__}: {e!r}"
                                    ),
                                )

                    finally:
                        # Safety net: ensure task entry is closed
                        if completion_logger and completion_logger.current_task:
                            completion_logger.end_task(
                                task_status=(final_task_status or TaskStatus.UNKNOWN),
                                error_msg=("Task not completed (unexpected exit)"),
                            )

            finally:
                await _close_task_pages(skip_save=not task_success)
                if guard_task is not None:
                    guard_task.cancel()

            # ================================================================
            # Determine final result
            # ================================================================
            if shutdown_event.is_set() and not task_success:
                if final_task_status is None:
                    final_task_status = TaskStatus.TIMEOUT

            if task_success:
                result = "success"
                logger.info("✅ Task completed successfully!")
            elif final_task_status is not None and final_task_status in AGENT_STATUSES:
                result = "agent_failure"
                logger.error(f"❌ Agent failure: {final_task_status.value}")
            else:
                result = "pipeline_failure"
                status_val = final_task_status.value if final_task_status else "unknown"
                logger.error(f"❌ Pipeline failure: {status_val}")

            # Hold browser open unless --no-hold
            if not no_hold:
                logger.info("⏸️  Browser staying open for inspection...")
                logger.info("   Press Ctrl+C to exit")
                while not shutdown_event.is_set():
                    await asyncio.sleep(1.0)

            # Graceful cleanup
            logger.info("🧹 Script ending - cleaning up...")

            # Close the main navigation page in CDP mode
            if browser_mgr.is_cdp_mode() and page and not page.is_closed():
                try:
                    if len(context.pages) > 1:
                        await page.close()
                        logger.info("✅ Closed main navigation page (CDP cleanup)")
                    else:
                        logger.info(
                            "⚠️ Skipped closing last remaining tab "
                            "(keeps Chrome alive)"
                        )
                except Exception as e:
                    logger.debug(f"Could not close main page: {e}")

            await browser_mgr.close_browser(context, browser)

            return result

    except Exception as e:
        logger.error(f"❌ Automation error: {e}")
        logger.debug(f"Traceback: {e.__traceback__}")
        return "pipeline_failure"

    finally:
        if guard_task is not None:
            guard_task.cancel()


def main():
    """Main entry point."""
    # Configure stdout/stderr for Windows Unicode compatibility (before any print statements)
    from core.logging_setup import configure_safe_stdout

    configure_safe_stdout()

    # ============================================================================
    # CLI Arguments
    # ============================================================================
    parser = argparse.ArgumentParser(
        description="Excel Agent Engine - Automate AI agents in Excel Online",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (browser stays open)
  python engine.py --config config.yaml

  # Batch mode (exits after completion)
  python engine.py --config config.yaml --no-hold

  # With timeout
  python engine.py --config config.yaml --no-hold --max-runtime 3600

  # Reset browser session
  python engine.py --reset-session
        """,
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Reset persistent browser session and exit",
    )
    parser.add_argument(
        "--no-hold",
        action="store_true",
        help="Exit immediately after completion (for batch automation)",
    )
    parser.add_argument(
        "--max-runtime",
        type=int,
        default=0,
        help="Maximum runtime in seconds (0 = unlimited)",
    )
    parser.add_argument(
        "--kill-all-browsers",
        action="store_true",
        help="[DANGEROUS] Kill all browser processes before starting",
    )
    args = parser.parse_args()

    # ============================================================================
    # Signal Handlers
    # ============================================================================
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        # ============================================================================
        # Special Commands (handle before config loading)
        # ============================================================================

        # Handle session reset
        if args.reset_session:
            print("🔄 Resetting browser session...")
            from shutil import rmtree

            # Reset browser sessions in agentic_workflow/ directory
            # This ensures consistent location regardless of where script is run from
            agentic_workflow_dir = Path(__file__).parent.parent
            session_dirs = [
                "browser_session_firefox",
                "browser_session_chromium",
                "browser_session_webkit",
            ]
            removed_count = 0
            for session_dir in session_dirs:
                session_path = agentic_workflow_dir / session_dir
                if session_path.exists():
                    try:
                        rmtree(session_path)
                        print(f"✅ Removed: {session_path}")
                        removed_count += 1
                    except Exception as e:
                        print(f"⚠️  Could not remove {session_path}: {e}")
                        print("   (This is normal if browser is still running)")

            # Also remove auth state file
            auth_state_path = agentic_workflow_dir / "browser_auth_state.json"
            if auth_state_path.exists():
                try:
                    auth_state_path.unlink()
                    print(f"✅ Removed: {auth_state_path}")
                    removed_count += 1
                except Exception as e:
                    print(f"⚠️  Could not remove {auth_state_path}: {e}")

            if removed_count > 0:
                print(f"✅ Session reset complete. Removed {removed_count} item(s).")
            else:
                print("✅ No sessions found to reset.")
            print("   Run ./scripts/setup_firefox.sh to set up again.")
            sys.exit(0)

        # ============================================================================
        # Configuration Loading
        # ============================================================================

        # Resolve config path - prioritize intended location over current working directory
        config_path = Path(args.config)
        if not config_path.is_absolute():
            # For direct execution, look in current working directory first (user's intended location)
            # For batch automation, the runner provides absolute paths
            current_dir_config = Path(os.getcwd()) / config_path
            if current_dir_config.exists():
                config_path = current_dir_config
            else:
                # Fall back to agentic_workflow root if not found in current directory
                agentic_workflow_dir = Path(__file__).parent.parent
                agentic_workflow_config = agentic_workflow_dir / config_path
                if agentic_workflow_config.exists():
                    config_path = agentic_workflow_config

        if not config_path.exists():
            print(f"❌ Config file not found: {config_path}")
            sys.exit(1)

        # ============================================================================
        # Configuration & Logging Setup
        # ============================================================================

        # Create temporary logger for initial messages (SafeStreamHandler for Windows compatibility)
        logger_temp = logging.getLogger(__name__)
        logger_temp.setLevel(logging.INFO)
        console_handler = SafeStreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logger_temp.addHandler(console_handler)

        # Load .env file if present
        env_path = Path(__file__).parent / ".env"
        try:
            if env_path.exists():
                load_dotenv(env_path)
                logger_temp.info(f"✅ Loaded .env file from: {env_path}")
        except Exception as e:
            logger_temp.warning(f"⚠️ Could not load .env file: {e}")

        # Load config with hierarchical overrides:
        # 1. config.yaml (base)
        # 2. CLI args
        # 3. Environment variables
        config = ConfigLoader.load_config(str(config_path))
        ConfigLoader.apply_runtime_overrides(config, args)
        ConfigLoader.apply_env_overrides(config)

        # Validate required config sections.
        # `file_path` is only required by the task-source shorthand format —
        # tasks that provide `onedrive_path` or `direct_url` don't need it.
        if "prompts" not in config or not config["prompts"]:
            print("❌ Config validation failed. Missing required section: prompts")
            print("   Make sure your config file has a 'prompts' section with values.")
            sys.exit(1)

        # Setup proper logging with task name
        global logger
        task_name = config.get("task_name", "unknown_task")
        agent_name = config.get("agent_type", "tabai")
        logger, log_file_path = setup_logging(
            config, __name__, task_name=task_name, agent_name=agent_name
        )
        # Store log file path in config for use in run_automation
        config["_log_file_path"] = log_file_path

        # ============================================================================
        # Run Automation
        # ============================================================================

        logger.info("=" * 80)
        logger.info("🚀 Excel Agent Engine Starting")
        logger.info("=" * 80)
        logger.info(f"📋 Task: {task_name}")
        logger.info(f"📋 Config: {config_path}")
        logger.info(
            f"🔧 Runtime: no_hold={args.no_hold}, max_runtime={args.max_runtime}s"
        )

        result = asyncio.run(run_automation(config))

        if result == "success":
            print("\n🎉 SUCCESS")
            sys.exit(0)
        elif result == "agent_failure":
            print("\n❌ AGENT FAILURE")
            sys.exit(1)
        else:
            print("\n❌ PIPELINE FAILURE")
            sys.exit(2)

    except KeyboardInterrupt:
        logger.info("👋 Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Script failed: {e}")
        import traceback

        logger.debug(traceback.format_exc())
        sys.exit(2)


if __name__ == "__main__":
    main()
