"""
Check which agent models have completed valid attempts for tasks.

Valid attempts: agent_failed = false AND deprecated = false.

Usage:
    source judge/project_configs.sh

    # Single task - list unique agents with valid attempts
    python judge/operation_scripts/check_attempt_completion.py --task-id 42

    # Multiple tasks - completion matrix across all agents
    python judge/operation_scripts/check_attempt_completion.py --task-ids 42 43 44

    # Restrict to specific agent models
    python judge/operation_scripts/check_attempt_completion.py --task-ids 42 43 --models "gpt-4o" "claude-sonnet-4-20250514"

TODO: Add default filtering for specific models' prompt version. For example, only consider GPT-4 attempts with prompt_version = "v2".
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

# Add judge/ to path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.misc_utils import load_project_configs

# Load configs into env vars
load_project_configs()

# ---------------------------------------------------------------------------
# Optional: hard-code a default model list here. When non-empty, only these
# models are considered unless overridden by --models / --all-models.
# ---------------------------------------------------------------------------
DEFAULT_MODELS: list[str] = [
    "GPT-5.2",
    "claude_excel_agent",
    "claude_web",
    "openpyxl_openai/gpt-5.3-codex",
]

# Optional: per-model prompt_version filter. When a model appears here, only
# attempts whose prompt_version matches the specified value are considered valid.
# Models not listed here are not filtered by prompt_version.
# Example: {"GPT-4": 2} means only GPT-4 attempts with prompt_version=2 are kept.
DEFAULT_MODELS_PROMPT_VERSION: dict[str, int] = {
    "openpyxl_openai/gpt-5.3-codex": 1004,
}


def get_db_connection():
    db_url = os.environ.get("BIZBENCHJUDGE_KEYS_DATABASE_URL")
    if not db_url:
        print("Error: BIZBENCHJUDGE_KEYS_DATABASE_URL not set.")
        sys.exit(1)
    return psycopg2.connect(db_url)


def fetch_valid_attempts(conn, task_ids, models=None, prompt_versions=None):
    """Return valid attempts for the given task ids, optionally filtered by models.

    Args:
        prompt_versions: dict mapping model name -> required prompt_version.
            Attempts for listed models are kept only if their prompt_version matches.
    """
    query = """
        SELECT ta.id AS attempt_id,
               ta.task_id,
               ta.agent_model_name,
               ta.prompt_version,
               ta.agent_model_type,
               ta.time_taken_min,
               ta.cost,
               ta.start_time,
               ta.end_time
        FROM task_attempts ta
        WHERE ta.agent_failed = false
          AND ta.deprecated = false
          AND ta.attempt_files IS NOT NULL
          AND ta.attempt_files::text NOT IN ('null', '[]', '{}')
          AND ta.task_id = ANY(%s)
    """
    params: list = [task_ids]

    if models:
        query += " AND ta.agent_model_name = ANY(%s)"
        params.append(models)

    query += " ORDER BY ta.task_id, ta.agent_model_name, ta.id"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    if prompt_versions:
        rows = [
            r
            for r in rows
            if r["agent_model_name"] not in prompt_versions
            or r["prompt_version"] == prompt_versions[r["agent_model_name"]]
        ]

    return rows


def fetch_all_agent_models(conn, models=None):
    """Return all unique agent model names (from valid attempts)."""
    query = """
        SELECT DISTINCT agent_model_name
        FROM task_attempts
        WHERE agent_failed = false AND deprecated = false
    """
    params: list = []
    if models:
        query += " AND agent_model_name = ANY(%s)"
        params.append(models)
    query += " ORDER BY agent_model_name"

    with conn.cursor() as cur:
        cur.execute(query, params)
        return [row[0] for row in cur.fetchall()]


def fetch_task_names(conn, task_ids):
    """Return a mapping of task_id -> task_name."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, task_name FROM tasks WHERE id = ANY(%s) ORDER BY id",
            [task_ids],
        )
        return {row["id"]: row["task_name"] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def format_attempt_detail(row):
    """Format versioning and metadata for a single attempt row."""
    parts = [f"id={row['attempt_id']}"]
    if row.get("prompt_version") is not None:
        parts.append(f"prompt_v={row['prompt_version']}")
    if row.get("agent_model_type"):
        parts.append(f"type={row['agent_model_type']}")
    if row.get("time_taken_min") is not None:
        parts.append(f"time={row['time_taken_min']:.1f}min")
    if row.get("cost") is not None:
        parts.append(f"cost=${row['cost']:.2f}")
    if row.get("start_time"):
        parts.append(f"date={row['start_time'].strftime('%Y-%m-%d')}")
    return ", ".join(parts)


def print_single_task(task_id, task_names, attempts):
    """Print details for a single task."""
    name = task_names.get(task_id, "Unknown")
    print(f"\nTask {task_id}: {name}")
    print("-" * 60)

    # Group by model
    by_model: dict[str, list[dict]] = {}
    for row in attempts:
        by_model.setdefault(row["agent_model_name"], []).append(row)

    if not by_model:
        print("  No valid attempts found.")
        return

    for model, rows in sorted(by_model.items()):
        print(f"  {model}  ({len(rows)} attempt{'s' if len(rows) != 1 else ''})")
        for row in rows:
            print(f"    - {format_attempt_detail(row)}")

    print(f"\nTotal unique models: {len(by_model)}")


def print_multi_task(task_ids, task_names, attempts, all_models):
    """Print a completion matrix for multiple tasks."""
    # Build lookup: (task_id, model) -> [attempt rows]
    lookup: dict[tuple[int, str], list[dict]] = {}
    for row in attempts:
        key = (row["task_id"], row["agent_model_name"])
        lookup.setdefault(key, []).append(row)

    # Header
    print(f"\nCompletion matrix ({len(task_ids)} tasks x {len(all_models)} models)")
    print("=" * 80)

    for model in all_models:
        completed = []
        missing = []
        for tid in task_ids:
            if (tid, model) in lookup:
                completed.append(tid)
            else:
                missing.append(tid)

        rate = len(completed) / len(task_ids) * 100
        print(f"\n  {model}  —  {len(completed)}/{len(task_ids)} ({rate:.0f}%)")

        if completed:
            print(f"    Completed tasks:")
            for tid in completed:
                name = task_names.get(tid, "")
                rows = lookup[(tid, model)]
                print(f"      Task {tid} ({name}):")
                for row in rows:
                    print(f"        - {format_attempt_detail(row)}")

        if missing:
            missing_str = ", ".join(str(t) for t in missing)
            print(f"    Missing tasks: [{missing_str}]")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def resolve_models(args_models, args_all_models):
    """Determine the model filter list and per-model prompt_version filter."""
    if args_all_models:
        return None, None
    if args_models:
        # Explicit --models: no default prompt_version filtering
        return args_models, None
    if DEFAULT_MODELS:
        pv = DEFAULT_MODELS_PROMPT_VERSION if DEFAULT_MODELS_PROMPT_VERSION else None
        return DEFAULT_MODELS, pv
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Check valid attempt completion per agent model."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--task-id",
        type=int,
        help="Single task ID to inspect.",
    )
    group.add_argument(
        "--task-ids",
        type=int,
        nargs="+",
        help="List of task IDs to check completion across.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Only consider these agent model names.",
    )
    parser.add_argument(
        "--all-models",
        action="store_true",
        default=False,
        help="Ignore DEFAULT_MODELS and consider every model in the database.",
    )
    args = parser.parse_args()

    models, prompt_versions = resolve_models(args.models, args.all_models)

    task_ids = [args.task_id] if args.task_id else args.task_ids

    conn = get_db_connection()
    try:
        task_names = fetch_task_names(conn, task_ids)

        # Verify all requested task ids exist
        missing = [t for t in task_ids if t not in task_names]
        if missing:
            print(f"Warning: task IDs not found in database: {missing}")

        attempts = fetch_valid_attempts(conn, task_ids, models, prompt_versions)

        if args.task_id:
            print_single_task(args.task_id, task_names, attempts)
        else:
            all_models = fetch_all_agent_models(conn, models)
            print_multi_task(task_ids, task_names, attempts, all_models)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
