"""
File management utilities for the Excel Agent Engine.

Handles file discovery, path resolution, and task file management.

Supports two modes:
  - **Explicit** (v2): caller provides `upload_files` and `template_file` directly.
  - **Legacy** (v1): auto-discovers files from `main_tasks/{source}/{task}/Task/`.
"""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class FileManager:
    """Manages file discovery and path operations."""

    # ------------------------------------------------------------------
    # Explicit file resolution (v2 config)
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_upload_files(
        upload_files: List[str],
        local_files_base: Optional[str] = None,
    ) -> List[str]:
        """
        Resolve upload_files paths to absolute paths.

        Args:
            upload_files: Relative (or absolute) file paths from the task YAML.
            local_files_base: Base directory for resolving relative paths.
                              Defaults to CWD if not set.

        Returns:
            List of absolute file paths (only those that exist on disk).
        """
        base = Path(local_files_base) if local_files_base else Path.cwd()
        resolved = []
        for fp in upload_files:
            p = Path(fp)
            if not p.is_absolute():
                p = base / p
            p = p.resolve()
            if p.exists():
                resolved.append(str(p))
                logger.info(f"  📤 Upload file: {p.name}")
            else:
                logger.warning(f"  ⚠️ Upload file not found, skipping: {p}")
        logger.info(f"📤 Resolved {len(resolved)}/{len(upload_files)} upload files")
        return resolved

    # ------------------------------------------------------------------
    # Legacy helpers (v1 config)
    # ------------------------------------------------------------------

    @staticmethod
    def get_local_task_files(task_name: str, task_source: str = "fmwc") -> List[str]:
        """
        Get all files from local main_tasks/{task_source}/{task_name}/Task/ directory.

        Args:
            task_name: Name of the task folder
            task_source: Source of tasks ("fmwc", "modeloff", "wallstreetprep")

        Returns:
            List of absolute file paths
        """
        try:
            logger.info(
                f"🔍 Searching for local files for task: '{task_name}' (source: {task_source})"
            )

            # Map task_source to folder name (wallstreetprep -> wsp)
            folder_name = "wsp" if task_source == "wallstreetprep" else task_source

            # Construct path relative to current working directory:
            #   ../main_tasks/{folder_name}/{task_name}/Task
            task_path = Path("..") / "main_tasks" / folder_name / task_name / "Task"
            logger.info(f"   Trying path 1 (relative to cwd): {task_path.absolute()}")

            # Fallback: resolve relative to this file's location, assuming the
            # repo sits alongside a sibling main_tasks/ directory.
            if not task_path.exists():
                script_dir = Path(__file__).parent.parent  # core -> excel_agent
                task_path = (
                    script_dir.parent.parent
                    / "main_tasks"
                    / folder_name
                    / task_name
                    / "Task"
                )
                logger.info(
                    f"   Trying path 2 (relative to script): {task_path.absolute()}"
                )

            if not task_path.exists():
                logger.error("❌ Local task directory not found at either location!")
                logger.error(f"   Task name: '{task_name}'")
                logger.error(f"   Task source: '{task_source}'")
                logger.error(f"   Last tried: {task_path.absolute()}")
                return []

            logger.info(f"✅ Found local task directory: {task_path.absolute()}")

            # Get all files in the directory, ignoring macOS system files
            all_files = []
            # macOS system files to ignore
            ignored_patterns = [
                ".DS_Store",
                "._*",  # macOS resource fork files
                ".AppleDouble",
                ".LSOverride",
                "Thumbs.db",  # Windows thumbnail cache
                "desktop.ini",  # Windows folder settings
            ]

            for file_path in task_path.iterdir():
                if file_path.is_file():
                    file_name = file_path.name
                    # Skip macOS system files and hidden files starting with .
                    if file_name.startswith(".") or file_name in ignored_patterns:
                        continue
                    # Skip macOS resource fork files (._filename)
                    if file_name.startswith("._"):
                        continue
                    all_files.append(str(file_path.absolute()))

            logger.info(f"📁 Found {len(all_files)} local files:")
            for file_path in all_files:
                logger.info(f"     📄 {Path(file_path).name}")

            return all_files

        except Exception as e:
            logger.error(f"❌ Error getting local task files: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return []

    @staticmethod
    def find_workbook_file(
        file_list: List[str], task_source: str = "fmwc"
    ) -> Optional[str]:
        """
        Find pre-existing workbook based on task source conventions.

        Args:
            file_list: List of file paths
            task_source: "fmwc", "modeloff", or "wallstreetprep"

        Returns:
            Path to workbook file, or None if not found
        """
        logger.info(
            f"🔍 Searching for workbook file in {len(file_list)} files (source: {task_source})..."
        )

        for f in file_list:
            filename = Path(f).name
            filename_lower = filename.lower()

            # Skip files with "solution" in the name (those are outputs, not inputs)
            if "solution" in filename_lower:
                continue

            if task_source == "fmwc":
                # FMWC: Look for files ending with 'model.xlsx'
                if filename_lower.endswith("model.xlsx"):
                    logger.info(f"✅ Found FMWC model file: {filename}")
                    return f

            elif task_source == "modeloff":
                # ModelOff: Any .xlsx or .xlsm file (exclude PDFs and solution files)
                if filename_lower.endswith((".xlsx", ".xlsm")):
                    logger.info(f"✅ Found ModelOff workbook file: {filename}")
                    return f

            elif task_source == "wallstreetprep":
                # WallStreetPrep: Files ending with '-Before.xlsx'
                if filename_lower.endswith("-before.xlsx"):
                    logger.info(f"✅ Found WallStreetPrep Before file: {filename}")
                    return f

        logger.warning(f"❌ No workbook file found for source '{task_source}'")
        return None

    @staticmethod
    def get_files_to_upload(
        local_file_paths: List[str], workbook_file: Optional[str]
    ) -> List[str]:
        """
        Determine which local file paths to upload based on workbook presence.

        Args:
            local_file_paths: All local file paths
            workbook_file: Path to workbook file if found (.xlsx or .xlsm)

        Returns:
            List of files to upload
        """
        if workbook_file:
            # Upload all files EXCEPT the workbook file
            workbook_name = Path(workbook_file).name
            files_to_upload = [
                f for f in local_file_paths if Path(f).name != workbook_name
            ]
            logger.info(
                f"📤 Workbook exists locally ({workbook_name}) - will upload {len(files_to_upload)} other files"
            )
        else:
            # Upload ALL files
            files_to_upload = local_file_paths
            logger.info(
                f"📤 No workbook found locally - will upload all {len(files_to_upload)} files"
            )

        for file_path in files_to_upload:
            logger.info(f"  📤 Will upload: {Path(file_path).name}")

        return files_to_upload
