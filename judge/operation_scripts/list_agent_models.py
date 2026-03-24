"""
List all unique agent_model_name values from the task_attempts table.

Usage:
    python judge/operation_scripts/list_agent_models.py
"""

import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.misc_utils import load_project_configs

load_project_configs()


def get_db_connection():
    db_url = os.environ.get("BIZBENCHJUDGE_KEYS_DATABASE_URL")
    if not db_url:
        print("Error: BIZBENCHJUDGE_KEYS_DATABASE_URL not set.")
        sys.exit(1)
    return psycopg2.connect(db_url)


def main():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent_model_name, COUNT(*) as attempt_count
                FROM task_attempts
                GROUP BY agent_model_name
                ORDER BY agent_model_name
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No attempts found.")
        return

    print(f"{'Agent Model Name':<50} {'Attempts':>10}")
    print("=" * 62)
    for model_name, count in rows:
        print(f"{model_name:<50} {count:>10}")
    print(f"\nTotal unique models: {len(rows)}")


if __name__ == "__main__":
    main()
