import json
import re
import string

import yaml

LETTERS = string.ascii_uppercase


def load_template(template_path: str) -> list[dict]:
    """Load a YAML prompt template and return the judge_prompt message list."""
    with open(template_path) as f:
        data = yaml.safe_load(f)
    return data["judge_prompt"]


def render_content(
    content: str, params: dict, nullable_keys: set[str] | None = None
) -> str:
    """Replace {{ var }} placeholders with values from params dict.

    Uses regex instead of str.format() because template content contains
    literal { } in JSON examples that would break Python's formatter.

    Keys in nullable_keys that are missing or None render as empty string.
    """
    if nullable_keys is None:
        nullable_keys = set()

    def replacer(match):
        key = match.group(1).strip()
        if key in nullable_keys and (key not in params or params[key] is None):
            return ""
        if key not in params:
            raise KeyError(f"Missing template parameter: {key}")
        return str(params[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replacer, content)


def _get_nullable_keys(entry: dict) -> set[str]:
    """Extract parameter names marked as nullable from an entry."""
    nullable = set()
    for p in entry.get("parameters", []):
        if isinstance(p, dict) and p.get("nullable", False):
            nullable.add(p["name"])
    return nullable


def _has_optional_data(entry: dict, kwargs: dict) -> bool:
    """Check whether an optional entry's required data is available in kwargs.

    Parameters can be dicts with {"name": str, "nullable": bool} or plain strings.
    An entry has its data if all non-nullable parameters are present and non-None.
    """
    params = entry.get("parameters", [])
    if not params:
        return False
    for p in params:
        if isinstance(p, dict):
            name = p["name"]
            nullable = p.get("nullable", False)
        else:
            name = p
            nullable = False
        if not nullable and (name not in kwargs or kwargs[name] is None):
            return False
    return True


def render_rubric_checks(rubric_path: str, category: str) -> str:
    """Render a rubric category's checks into prompt-ready text.

    Each check becomes:
        Check A: {description}
        Pass: {good}
        Fail: {bad}

    Checks are separated by a blank line.
    """
    with open(rubric_path) as f:
        rubric = json.load(f)
    items = rubric[category]
    blocks = []
    for i, item in enumerate(items):
        letter = LETTERS[i]
        blocks.append(
            f"Check {letter}: {item['description']}\n"
            f"Pass: {item['good']}\n"
            f"Fail: {item['bad']}"
        )
    return "\n\n".join(blocks)


def compile_prompt(template_path: str, **kwargs) -> list[list[dict]]:
    """Compile a YAML prompt template into staged message lists.

    Walks the template entries, renders msg content and injects placeholder
    messages. Splits at each 'response' entry to create stages.

    Returns a list of stages. Each stage is a list of message dicts
    ({"role": str, "content": str}) that should be appended to the
    conversation before making an LLM call.

    len(return_value) == number of LLM calls needed.

    Caller usage::

        stages = compile_prompt(template_path, **kwargs)
        all_messages = []
        responses = []
        for stage_messages in stages:
            all_messages.extend(stage_messages)
            response = my_llm_call(all_messages)
            all_messages.append({"role": "assistant", "content": response})
            responses.append(response)
    """
    template = load_template(template_path)
    stages: list[list[dict]] = []
    current_stage: list[dict] = []

    for entry in template:
        entry_type = entry.get("type")

        if entry_type == "msg":
            if entry.get("optional") and not _has_optional_data(entry, kwargs):
                continue
            nullable_keys = _get_nullable_keys(entry)
            content = render_content(entry["content"], kwargs, nullable_keys)
            current_stage.append({"role": entry["role"], "content": content})

        elif entry_type == "placeholder":
            name = entry["name"]
            if entry.get("optional") and name not in kwargs:
                continue
            current_stage.extend(kwargs[name])

        elif entry_type == "response":
            stages.append(current_stage)
            current_stage = []

    if current_stage:
        stages.append(current_stage)

    return stages


if __name__ == "__main__":
    # Quick test of compile_prompt
    print(f"*" * 84 + "Test compile_prompt" + f"*" * 84)
    TEST_KWARGS = dict(
        ai_attempt="student_attempt.xlsx",
        solution_sheet="golden_solution.xlsx",
        context="FY2024 revenue case",
        solution_messages=[
            {"role": "user", "content": "Sheet: Revenue\n[A1]Revenue 100"},
            {"role": "user", "content": "Sheet: Expenses\n[A1]Expenses 50"},
        ],
        ai_attempt_messages=[
            {"role": "user", "content": "Sheet: Revenue\n[A1]Revenue 95"},
        ],
        context_messages=[
            {"role": "user", "content": "Case brief: Build a revenue model"},
        ],
        accuracy_checks="Check A: Revenue matches\nCheck B: Expenses match",
        formula_checks="Check A: Formulas use cell refs",
        formatting_checks="Check A: Currency uses dollar sign",
    )
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))

    TEMPLATE_PATH = os.path.join(
        os.path.dirname(__file__), "..", "prompts", "judge_template_6_3.yaml"
    )

    stages = compile_prompt(TEMPLATE_PATH, **TEST_KWARGS)

    print(f"Compiled {len(stages)} stages:")
    for i, stage in enumerate(stages):
        print(f"\nStage {i} ({len(stage)} messages):")
        for idx, msg in enumerate(stage):
            print(f"[msg {idx}] {msg['role']}: {msg['content'][:100]}...")

    print(f"*" * 84 + "Test compile_prompt" + f"*" * 84)
    RUBRIC_PATH = os.path.join(
        os.path.dirname(__file__), "..", "prompts", "rubrics", "rubric_7.json"
    )
    checks_text = render_rubric_checks(RUBRIC_PATH, "Accuracy")
    print("\nRendered rubric checks for accuracy:\n")
    print(checks_text)

    checks_text = render_rubric_checks(RUBRIC_PATH, "Formula")
    print("\nRendered rubric checks for formula:\n")
    print(checks_text)

    checks_text = render_rubric_checks(RUBRIC_PATH, "Formatting")
    print("\nRendered rubric checks for formatting:\n")
    print(checks_text)
