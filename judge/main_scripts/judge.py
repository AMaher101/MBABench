"""Run a local judge on specified attempt and solution files, using the prompt template and rubric."""

import json
import shutil
import time
import traceback
import uuid
from pathlib import Path

from openai import OpenAI
from utils.excel_utils import (
    calculate_message_size_for_files,
    copy_support_files,
    find_golden_solution_file,
    prepare_directory_files,
    process_case_files,
    shorten_attempt_csv_files,
    shorten_solution_csv_files,
)
from utils.llm_utils import calculate_cost, robust_send_message
from utils.logger import add_log_file, logger, remove_log_file
from utils.misc_utils import (
    get_absolute_path,
    load_env_var,
    load_project_configs,
    relative_path_from_project_root,
    str2bool,
)
from utils.prompt_utils import (
    add_file_confirmation,
    build_check_name_mapping,
    compile_prompt,
    encode_file_to_base64,
    format_file_section,
    render_rubric_checks,
)


class JudgeOutputError(Exception):
    """Raised when the judge model returns valid JSON but with an unexpected structure."""

    pass


### Obtain constants
load_project_configs()
JUDGE_MODEL = load_env_var("JUDGE_OPENROUTER_MODEL", required=True)
DEFAULT_SOLUTION_CONTEXT_CHAR_LIMIT = int(
    load_env_var("JUDGE_DEFAULT_SOLUTION_CONTEXT_CHAR_LIMIT", required=True)
)
DEFAULT_ATTEMPT_CONTEXT_CHAR_LIMIT = int(
    load_env_var("JUDGE_DEFAULT_ATTEMPT_CONTEXT_CHAR_LIMIT", required=True)
)
DEFAULT_TOTAL_CHARACTER_LIMIT = int(
    load_env_var("JUDGE_DEFAULT_TOTAL_CHARACTER_LIMIT", required=True)
)

### Main Judge Function


def judge_case(
    task_folder: str,
    client: OpenAI,
    rubric_path: str,
    template_path: str,
    model: str = JUDGE_MODEL,
    no_file_check: bool = True,
    nocall: bool = False,
    noupload: bool = False,
    use_existing: bool = True,
    solution_context_char_limit: int = DEFAULT_SOLUTION_CONTEXT_CHAR_LIMIT,
    attempt_context_char_limit: int = DEFAULT_ATTEMPT_CONTEXT_CHAR_LIMIT,
    total_character_limit: int = DEFAULT_TOTAL_CHARACTER_LIMIT,
    attempt_model: str = None,
    run_calculation: bool = False,
):
    """Execute the complete judging workflow for a case using OpenRouter.

    Args:
        task_folder: Path to the task folder containing Excel files.
        client: Configured OpenRouter client.
        rubric_path: Path to the rubric JSON file.
        template_path: Path to the prompt template YAML file.
        model: Model identifier to use for API calls (the grader model).
        no_file_check: If True, skip file confirmation step (default: True).
        nocall: If True, skip API calls (for testing).
        noupload: If True, skip file preparation (for testing).
        use_existing: If True, skip regenerating files if they already exist.
        solution_context_char_limit: Character limit for golden solution context.
        attempt_context_char_limit: Character limit for AI attempt context.
        total_character_limit: Total character limit for combined solution + attempt.
        attempt_model: Name of the AI model that generated the attempt being judged.
        run_calculation: If True, run Excel formula calculations before extracting CSVs.

    Returns:
        dict: Dictionary with paths to ai_judgement.json and output_dir.
    """
    # Set up logging to a cache directory first; copy to output_dir on completion
    cache_dir = (
        Path(load_env_var("PATHS_SCRATCH_PATH", default="scratch"))
        / "judge_cache"
        / f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_log_path = str(cache_dir / "judge.log")
    add_log_file(cache_log_path)

    logger.info(f"Writing logs to temporary cache path: {cache_log_path}")

    task_folder_name = Path(task_folder).name
    PROMPT_VERSION = load_env_var("JUDGE_PROMPT_VERSION", required=True)
    RUBRIC_VERSION = load_env_var("JUDGE_RUBRIC_VERSION", required=True)
    CHECK_ORDER = load_env_var(
        "JUDGE_CHECK_ORDER", default="Accuracy,Formula,Formatting"
    ).split(",")

    start_time = time.time()
    logger.info("=" * 80)
    logger.info("OpenRouter Judge Evaluation Workflow")
    logger.info("=" * 80)
    logger.info(
        f"Grading task: {task_folder_name}, prompt: {PROMPT_VERSION}, "
        f"rubric: {RUBRIC_VERSION}, model: {model}"
    )
    logger.info("=" * 80)

    # STEP 1: Process case files
    logger.info("\n[Step 1] Processing case files...")
    task_path = Path(task_folder)
    try:
        golden_solution_path = find_golden_solution_file(task_path)
        logger.info(f" Golden solution file: {golden_solution_path.name}")
    except Exception as e:
        logger.info(f" Error finding golden solution file: {e}")
        raise

    ai_attempt_path = task_path / "ai_attempt.xlsx"

    file_paths = [str(ai_attempt_path), str(golden_solution_path)]
    result = process_case_files(
        file_paths,
        task_folder,
        use_existing=use_existing,
        run_calculation=run_calculation,
    )
    output_dir = result["output_dir"]
    logger.info(f" Files processed and saved to: {output_dir}")

    copied_files = copy_support_files(
        task_path,
        output_dir,
        default_rubric_path=rubric_path,
    )
    logger.info(f" Copied {len(copied_files)} support files to output directory")

    if noupload:
        logger.info("\n--noupload flag set. Skipping file preparation.")
        remove_log_file(cache_log_path)
        shutil.copy(cache_log_path, str(output_dir / "judge.log"))
        return

    # STEP 2: Prepare files for OpenRouter
    logger.info("\n[Step 2] Preparing files for OpenRouter...")

    workbook_dirs = result.get("workbook_dirs", {})
    ai_attempt_dir = workbook_dirs.get("ai_attempt")

    golden_solution_stem = golden_solution_path.stem
    golden_solution_dir = workbook_dirs.get(golden_solution_stem)

    context_pdf = output_dir / "context.pdf"
    context_txt = output_dir / "context.txt"

    context_file_path = None
    if context_pdf.exists():
        context_file_path = context_pdf
    elif context_txt.exists():
        context_file_path = context_txt

    golden_solution_files = {}
    ai_attempt_files = {}

    # Check if golden solution needs shortening based on character count
    shortening_result = None
    solution_context_reduced = False
    attempt_context_reduced = False
    context_reduced_details = None
    effective_golden_solution_dir = golden_solution_dir
    final_solution_chars = 0
    if golden_solution_dir and Path(golden_solution_dir).exists():
        gs_dir = Path(golden_solution_dir)

        size_info = calculate_message_size_for_files(gs_dir)
        total_chars = size_info["total"]

        logger.info(
            f"\n[Step 2b] Golden solution size: {total_chars:,} chars "
            f"(limit: {solution_context_char_limit:,})"
        )

        if (
            solution_context_char_limit
            and solution_context_char_limit > 0
            and total_chars > solution_context_char_limit
        ):
            logger.info(
                f"  Exceeds limit by {total_chars - solution_context_char_limit:,} chars. "
                f"Applying shortening..."
            )

            shortened_dir = output_dir / f"{golden_solution_stem}_shortened"
            shortened_dir.mkdir(parents=True, exist_ok=True)

            for src_file in gs_dir.glob("*_full.csv"):
                shutil.copy(str(src_file), str(shortened_dir / src_file.name))
            for src_file in gs_dir.glob("*_additional_format.txt"):
                shutil.copy(str(src_file), str(shortened_dir / src_file.name))

            shortening_result = shorten_solution_csv_files(
                directory_path=shortened_dir,
                target_chars=solution_context_char_limit,
            )

            size_info_after = calculate_message_size_for_files(shortened_dir)
            total_chars_after = size_info_after["total"]
            per_file_after = size_info_after["per_file"]

            logger.info(
                f"  Shortened: {shortening_result['total_original']:,} -> "
                f"{shortening_result['total_shortened']:,} chars "
                f"(saved {shortening_result['total_original'] - shortening_result['total_shortened']:,}, "
                f"{shortening_result['steps_executed']} steps)"
            )
            logger.info("  Per-file character counts after shortening:")
            max_fname_len = max(len(fname) for fname in per_file_after.keys())
            for fname, fchars in per_file_after.items():
                logger.info(
                    f"    {fname:<{max_fname_len}}: {fchars['chars']:>12,} chars"
                )

            solution_context_reduced = True

            def _format_summary_entry(old_chars: int, new_chars: int) -> str:
                if old_chars == new_chars:
                    return f"{old_chars} (no change)"
                return f"{old_chars}->{new_chars}"

            context_reduced_details = {
                "summary": {
                    "solution": {
                        fname: _format_summary_entry(
                            size_info["per_file"].get(fname, {}).get("chars", 0),
                            finfo["chars"],
                        )
                        for fname, finfo in per_file_after.items()
                    }
                },
                "solution": {
                    "before": {
                        "total_chars": size_info["total"],
                        "per_file": size_info["per_file"],
                    },
                    "after": {
                        "total_chars": total_chars_after,
                        "per_file": per_file_after,
                    },
                    "shortening_info": {
                        "steps_executed": shortening_result["steps_executed"],
                        "chars_saved": shortening_result["total_original"]
                        - shortening_result["total_shortened"],
                    },
                },
            }

            context_reduction_path = output_dir / "_context_reduction.json"
            with open(context_reduction_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "solution_context_reduced": solution_context_reduced,
                        "attempt_context_reduced": attempt_context_reduced,
                        "context_reduced_details": context_reduced_details,
                    },
                    f,
                    indent=2,
                )
            logger.info(f"  Context reduction info saved to: {context_reduction_path}")

            effective_golden_solution_dir = str(shortened_dir)
            final_solution_chars = total_chars_after
        else:
            logger.info("  Within limit, no shortening needed.")
            final_solution_chars = total_chars

        golden_solution_files = prepare_directory_files(effective_golden_solution_dir)

    # Check if AI attempt needs shortening based on character count
    effective_ai_attempt_dir = ai_attempt_dir
    if ai_attempt_dir and Path(ai_attempt_dir).exists():
        ai_dir = Path(ai_attempt_dir)

        ai_size_info = calculate_message_size_for_files(ai_dir)
        ai_total_chars = ai_size_info["total"]

        # Calculate effective attempt limit dynamically
        effective_attempt_limit = attempt_context_char_limit
        if total_character_limit and total_character_limit > 0:
            remaining_room = total_character_limit - final_solution_chars
            if remaining_room > attempt_context_char_limit:
                effective_attempt_limit = remaining_room
                logger.info(
                    f"\n[Step 2c] Dynamic attempt limit: solution used {final_solution_chars:,} chars, "
                    f"remaining room from total limit ({total_character_limit:,}) = {remaining_room:,} chars"
                )
                logger.info(
                    f"  Effective attempt limit increased: {attempt_context_char_limit:,} -> "
                    f"{effective_attempt_limit:,} chars"
                )

        logger.info(
            f"\n[Step 2c] AI attempt size: {ai_total_chars:,} chars "
            f"(limit: {effective_attempt_limit:,})"
        )

        if (
            effective_attempt_limit
            and effective_attempt_limit > 0
            and ai_total_chars > effective_attempt_limit
        ):
            logger.info(
                f"  Exceeds limit by {ai_total_chars - effective_attempt_limit:,} chars. "
                f"Applying shortening..."
            )

            ai_shortened_dir = output_dir / "ai_attempt_shortened"
            ai_shortened_dir.mkdir(parents=True, exist_ok=True)

            for src_file in ai_dir.glob("*_full.csv"):
                shutil.copy(str(src_file), str(ai_shortened_dir / src_file.name))
            for src_file in ai_dir.glob("*_additional_format.txt"):
                shutil.copy(str(src_file), str(ai_shortened_dir / src_file.name))

            ai_shortening_result = shorten_attempt_csv_files(
                directory_path=ai_shortened_dir,
                target_chars=effective_attempt_limit,
            )

            ai_size_info_after = calculate_message_size_for_files(ai_shortened_dir)
            ai_total_chars_after = ai_size_info_after["total"]
            ai_per_file_after = ai_size_info_after["per_file"]

            logger.info(
                f"  Shortened: {ai_shortening_result['total_original']:,} -> "
                f"{ai_shortening_result['total_shortened']:,} chars "
                f"(saved {ai_shortening_result['total_original'] - ai_shortening_result['total_shortened']:,}, "
                f"{ai_shortening_result['steps_executed']} steps)"
            )
            logger.info("  Per-file character counts after shortening:")
            if ai_per_file_after:
                ai_max_fname_len = max(len(fname) for fname in ai_per_file_after.keys())
                for fname, fchars in ai_per_file_after.items():
                    logger.info(
                        f"    {fname:<{ai_max_fname_len}}: {fchars['chars']:>12,} chars"
                    )

            def _format_summary_entry(old_chars: int, new_chars: int) -> str:
                if old_chars == new_chars:
                    return f"{old_chars} (no change)"
                return f"{old_chars}->{new_chars}"

            if not context_reduced_details:
                context_reduced_details = {"summary": {}}
            if "summary" not in context_reduced_details:
                context_reduced_details["summary"] = {}
            attempt_context_reduced = True
            context_reduced_details["summary"]["attempt"] = {
                fname: _format_summary_entry(
                    ai_size_info["per_file"].get(fname, {}).get("chars", 0),
                    finfo["chars"],
                )
                for fname, finfo in ai_per_file_after.items()
            }
            context_reduced_details["attempt"] = {
                "before": {
                    "total_chars": ai_size_info["total"],
                    "per_file": ai_size_info["per_file"],
                },
                "after": {
                    "total_chars": ai_total_chars_after,
                    "per_file": ai_per_file_after,
                },
                "shortening_info": {
                    "steps_executed": ai_shortening_result["steps_executed"],
                    "chars_saved": ai_shortening_result["total_original"]
                    - ai_shortening_result["total_shortened"],
                },
            }

            context_reduction_path = output_dir / "_context_reduction.json"
            with open(context_reduction_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "solution_context_reduced": solution_context_reduced,
                        "attempt_context_reduced": attempt_context_reduced,
                        "context_reduced_details": context_reduced_details,
                    },
                    f,
                    indent=2,
                )
            logger.info(f"  Context reduction info saved to: {context_reduction_path}")

            effective_ai_attempt_dir = str(ai_shortened_dir)
        else:
            logger.info("  Within limit, no shortening needed.")

        ai_attempt_files = prepare_directory_files(effective_ai_attempt_dir)

    # STEP 3: Build file messages and rubric checks
    logger.info("\n[Step 3] Building conversation via compile_prompt...")

    solution_messages, solution_prompt, solution_file_sizes = format_file_section(
        "Golden solution",
        golden_solution_files,
        add_confirmation=not no_file_check,
    )

    ai_attempt_messages, ai_attempt_prompt, ai_attempt_file_sizes = format_file_section(
        "AI attempt", ai_attempt_files, add_confirmation=not no_file_check
    )

    # Build context messages
    context_messages = []
    context_prompt = ""
    context_file_sizes = {}
    context_display_name = ""
    if context_file_path:
        context_display_name = context_file_path.name
        context_ext = context_file_path.suffix.lower()

        if context_ext == ".txt":
            try:
                with open(context_file_path, "r", encoding="utf-8") as f:
                    context_content = f.read()
                context_text = f"Context:\n{context_content}"
                context_messages = [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": context_text}],
                    }
                ]
                context_file_sizes[context_file_path.name] = len(context_text)
            except UnicodeDecodeError:
                base64_content, mime_type = encode_file_to_base64(context_file_path)
                context_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Context:"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_content}"
                                },
                            },
                        ],
                    }
                ]
                context_file_sizes[context_file_path.name] = len("Context:") + len(
                    base64_content
                )
        else:
            base64_content, mime_type = encode_file_to_base64(context_file_path)
            context_messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Context:"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_content}"
                            },
                        },
                    ],
                }
            ]
            context_file_sizes[context_file_path.name] = len("Context:") + len(
                base64_content
            )
        context_prompt = f"Context: {context_file_path.name}\n"
    else:
        context_prompt = "No context file provided.\n"

    if not no_file_check and context_messages:
        context_messages, context_prompt = add_file_confirmation(
            context_messages, header="context_files", prompt=context_prompt
        )

    # Render rubric checks for each category
    rubric_json_path = output_dir / "rubric.json"
    accuracy_checks = render_rubric_checks(str(rubric_json_path), "Accuracy")
    formula_checks = render_rubric_checks(str(rubric_json_path), "Formula")
    formatting_checks = render_rubric_checks(str(rubric_json_path), "Formatting")

    # Compile the prompt template into staged message lists
    compile_kwargs = dict(
        ai_attempt="ai_attempt",
        solution_sheet=golden_solution_stem,
        context=context_display_name or None,
        solution_messages=solution_messages,
        ai_attempt_messages=ai_attempt_messages,
        accuracy_checks=accuracy_checks,
        formula_checks=formula_checks,
        formatting_checks=formatting_checks,
    )
    if context_messages:
        compile_kwargs["context_messages"] = context_messages

    stages = compile_prompt(template_path, **compile_kwargs)
    logger.info(f" Compiled {len(stages)} evaluation stages from template")

    # Build check name mapping for enriching responses
    check_name_mapping = build_check_name_mapping(str(rubric_json_path))

    # STEP 4: Save file prompt for logging
    logger.info("\n[Step 4] Saving file prompt...")
    prompt = solution_prompt + ai_attempt_prompt + context_prompt
    fileprompt_path = output_dir / "fileprompt.txt"
    with open(fileprompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    logger.info(f" File prompt saved to: {fileprompt_path}")

    if nocall:
        logger.info("\n--nocall flag set. Skipping API calls.")
        remove_log_file(cache_log_path)
        shutil.copy(cache_log_path, str(output_dir / "judge.log"))
        return

    # STEP 5: Make sequential OpenRouter API calls via staged conversation
    logger.info("\n[Step 5] Evaluating with OpenRouter API...")

    conversation_messages = []
    token_tracking = {
        "evaluations": {},
        "total_message_size": 0,
        "total_message_size_with_images": 0,
        "total_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost": 0.0,
        "file_sizes": {
            "golden_solution": solution_file_sizes,
            "ai_attempt": ai_attempt_file_sizes,
            "context": context_file_sizes,
        },
    }

    all_responses = {}
    parse_failures = {}

    for stage_idx, stage_messages in enumerate(stages):
        category = (
            CHECK_ORDER[stage_idx]
            if stage_idx < len(CHECK_ORDER)
            else f"stage_{stage_idx}"
        )

        logger.info(f"  Evaluating {category} (stage {stage_idx})...")

        conversation_messages.extend(stage_messages)

        # Retry loop for API call + JSON parsing
        max_json_attempts = 5
        parse_success = False
        response_text = None
        failed_responses = []
        cumulative_metrics = {
            "message_size": 0,
            "message_size_with_images": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        for json_attempt in range(max_json_attempts):
            response, metrics = robust_send_message(
                client,
                conversation_messages,
                model,
                response_format={"type": "json_object"},
            )

            response_text = response.choices[0].message.content

            cumulative_metrics["message_size"] += metrics["message_size"]
            cumulative_metrics["message_size_with_images"] += metrics[
                "message_size_with_images"
            ]
            cumulative_metrics["prompt_tokens"] += metrics["prompt_tokens"]
            cumulative_metrics["completion_tokens"] += metrics["completion_tokens"]
            cumulative_metrics["total_tokens"] += metrics["total_tokens"]

            # Try to parse JSON
            try:
                parsed_response = json.loads(response_text)
                if category in parsed_response:
                    category_data = parsed_response[category]
                else:
                    category_data = parsed_response

                # Format check category_data. It must be a list of check items with 'check', 'decision', 'summary', and 'mistakes' fields.
                # 'check', 'decision', and 'summary' are strings. 'mistakes' is a list of dictionaries. If any field is missing, enforce a retry
                if isinstance(category_data, list):
                    for item in category_data:
                        if not isinstance(item, dict):
                            raise JudgeOutputError(
                                f"Check item is not a dictionary: {item}"
                            )
                        if (
                            "check" not in item
                            or "decision" not in item
                            or "summary" not in item
                            or "mistakes" not in item
                        ):
                            missing_fields = [
                                field
                                for field in [
                                    "check",
                                    "decision",
                                    "summary",
                                    "mistakes",
                                ]
                                if field not in item
                            ]
                            raise JudgeOutputError(
                                f"Check item missing required fields: {missing_fields}. Item: {item}"
                            )
                        if (
                            not isinstance(item["check"], str)
                            or not isinstance(item["decision"], str)
                            or not isinstance(item["summary"], str)
                            or not isinstance(item["mistakes"], list)
                        ):
                            raise JudgeOutputError(
                                f"Check item has incorrect field types: {item}"
                            )
                        for mistake in item["mistakes"]:
                            if not isinstance(mistake, dict):
                                raise JudgeOutputError(
                                    f"Mistake item is not a dictionary: {mistake}"
                                )
                else:
                    raise JudgeOutputError(
                        f"Category data is not a list: {category_data}"
                    )

                # Enrich each check item with its name if available
                if isinstance(category_data, list):
                    for item in category_data:
                        if isinstance(item, dict) and "check" in item:
                            check_letter = item["check"]
                            name = check_name_mapping.get((category, check_letter))
                            if name:
                                item["name"] = name

                all_responses[category] = category_data
                parse_success = True
                break
            except (json.JSONDecodeError, JudgeOutputError) as e:
                traceback.print_exc()
                failed_responses.append(response_text)
                parse_failures[category] = {
                    "success": True,
                    "count": len(failed_responses),
                    "responses": failed_responses,
                }
                if json_attempt < max_json_attempts - 1:
                    logger.info(
                        f"   Parse/validation attempt {json_attempt + 1}/{max_json_attempts} "
                        f"failed for {category}: {e}. Response: {response_text[:200]}... retrying..."
                    )
                    time.sleep(2)

        if not parse_success:
            logger.info(
                f"   WARNING: Failed to parse JSON for {category} after "
                f"{max_json_attempts} attempts. Raw response: {response_text[:200]}..."
            )
            parse_failures[category]["success"] = False
            all_responses[category] = {"raw_response": response_text}

        # Track cumulative metrics
        token_tracking["evaluations"][category] = cumulative_metrics
        token_tracking["evaluations"][category]["chars_per_token"] = (
            round(
                cumulative_metrics["message_size"]
                / cumulative_metrics["prompt_tokens"],
                2,
            )
            if cumulative_metrics["prompt_tokens"] > 0
            else 0
        )
        cost_info = calculate_cost(
            model,
            cumulative_metrics["prompt_tokens"],
            cumulative_metrics["completion_tokens"],
        )
        token_tracking["evaluations"][category]["cost"] = cost_info["total_cost"]
        token_tracking["total_message_size"] += cumulative_metrics["message_size"]
        token_tracking["total_message_size_with_images"] += cumulative_metrics[
            "message_size_with_images"
        ]
        token_tracking["total_tokens"] += cumulative_metrics["total_tokens"]
        token_tracking["total_prompt_tokens"] += cumulative_metrics["prompt_tokens"]
        token_tracking["total_completion_tokens"] += cumulative_metrics[
            "completion_tokens"
        ]
        token_tracking["total_cost"] += cost_info["total_cost"]

        logger.info(
            f"   Message size: {cumulative_metrics['message_size']:,} chars "
            f"(with images: {cumulative_metrics['message_size_with_images']:,}) | "
            f"Tokens: {cumulative_metrics['prompt_tokens']:,} prompt + "
            f"{cumulative_metrics['completion_tokens']} completion = "
            f"{cumulative_metrics['total_tokens']:,} total | "
            f"Cost: ${cost_info['total_cost']:.6f}"
        )

        conversation_messages.append({"role": "assistant", "content": response_text})

        logger.info(f"   {category} evaluation completed")
        time.sleep(2)

    # Save conversation messages for reference
    conversation_path = output_dir / "conversation_messages.json"
    with open(conversation_path, "w", encoding="utf-8") as f:
        json.dump(conversation_messages, f, indent=2)
    logger.info(f" Conversation messages saved to: {conversation_path}")

    # STEP 6: Save ai_judgement as JSON
    logger.info("\n[Step 6] Saving AI judgement...")
    ai_judgement_path = output_dir / "ai_judgement.json"
    with open(ai_judgement_path, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, indent=2)
    logger.info(f" AI judgement saved to: {ai_judgement_path}")

    # Add model info to token tracking
    token_tracking["model"] = model

    # Save token tracking data
    token_tracking_path = output_dir / "token_tracking.json"
    with open(token_tracking_path, "w", encoding="utf-8") as f:
        json.dump(token_tracking, f, indent=2)
    logger.info(f" Token tracking saved to: {token_tracking_path}")

    # Create/update _metadata.json with cost and timing information
    elapsed_time = time.time() - start_time
    metadata_path = output_dir / "_metadata.json"
    metadata_dict = {
        "task_folder": task_folder_name,
        "grader_model": model,
        "attempt_model": attempt_model,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "total_prompt_tokens": token_tracking["total_prompt_tokens"],
        "total_completion_tokens": token_tracking["total_completion_tokens"],
        "total_tokens": token_tracking["total_tokens"],
        "total_cost": round(token_tracking["total_cost"], 6),
        "elapsed_time_seconds": round(elapsed_time, 2),
        "files_considered": {
            "golden_solution": sorted(golden_solution_files.keys()),
            "ai_attempt": sorted(ai_attempt_files.keys()),
            "context": context_file_path.name if context_file_path else None,
        },
    }

    if metadata_path.exists():
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                existing_metadata = json.load(f)
            if isinstance(existing_metadata, dict):
                existing_metadata.update(metadata_dict)
                metadata_dict = existing_metadata
        except (json.JSONDecodeError, IOError):
            pass

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata_dict, f, indent=2)
    logger.info(f" Metadata saved to: {metadata_path}")

    logger.info("\n" + "=" * 80)
    logger.info("EVALUATION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"\nToken Usage & Cost Summary:")
    logger.info(
        f"  Total message size: {token_tracking['total_message_size']:,} characters "
        f"(with images: {token_tracking['total_message_size_with_images']:,})"
    )
    logger.info(f"  Total tokens used: {token_tracking['total_tokens']:,}")
    logger.info(f"    - Prompt tokens: {token_tracking['total_prompt_tokens']:,}")
    logger.info(
        f"    - Completion tokens: {token_tracking['total_completion_tokens']:,}"
    )
    if token_tracking["total_tokens"] > 0:
        logger.info(
            f"  Average ratio: "
            f"{token_tracking['total_message_size'] / token_tracking['total_tokens']:.2f} chars/token"
        )
    logger.info(f"  Total cost: ${token_tracking['total_cost']:.6f}")
    if token_tracking["evaluations"]:
        logger.info(f"\n  Evaluations:")
        for cat, data in token_tracking["evaluations"].items():
            logger.info(
                f"    {cat}: {data['message_size']:,} chars "
                f"(with images: {data.get('message_size_with_images', 0):,}) -> "
                f"{data['total_tokens']:,} tokens "
                f"({data.get('chars_per_token', 0):.2f} chars/token) | "
                f"${data.get('cost', 0):.6f}"
            )
    logger.info("=" * 80)

    result = {
        "ai_judgement": str(ai_judgement_path),
        "output_dir": str(output_dir),
        "solution_context_reduced": solution_context_reduced,
        "attempt_context_reduced": attempt_context_reduced,
        "context_reduced_details": context_reduced_details,
    }
    if parse_failures:
        result["parse_failures"] = parse_failures
        logger.info("\nJSON Parse Failures Summary:")
        for category, info in parse_failures.items():
            logger.info(
                f"  {category}: {info['count']} failed parse attempts recorded."
            )
            if info["count"] >= 1:
                logger.info(
                    f"    Sample failed response: {info['responses'][0][:500]}..."
                )
    remove_log_file(cache_log_path)
    shutil.copy(cache_log_path, str(output_dir / "judge.log"))
    return result


# ============================================================================
# CLI Entry Point
# ============================================================================


def main(args):
    """Main entry point that wires CLI args to judge_case."""
    load_project_configs(verbose=True)

    # Resolve paths from config
    rubric_path = str(
        relative_path_from_project_root(
            load_env_var("JUDGE_RUBRIC", default="./prompts/rubrics/rubric_7.json")
        )
    )
    template_path = str(
        relative_path_from_project_root(
            load_env_var(
                "JUDGE_PROMPT_TEMPLATE", default="./prompts/judge_template_6_3.yaml"
            )
        )
    )

    # Initialize OpenRouter client
    api_key = load_env_var("KEYS_OPEN_ROUTER_API_KEY", required=True)
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    judge_case(
        task_folder=args.folder_to_grade,
        client=client,
        rubric_path=rubric_path,
        template_path=template_path,
        model=args.model,
        nocall=args.nocall,
        noupload=args.noupload,
        use_existing=not args.no_use_existing,
        run_calculation=args.run_calculation,
        solution_context_char_limit=args.solution_char_limit,
        attempt_context_char_limit=args.attempt_char_limit,
        total_character_limit=args.total_char_limit,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run local judge on specified attempt and solution files."
    )
    parser.add_argument(
        "-f",
        "--folder-to-grade",
        required=True,
        help="Path to folder containing student attempt, solution files, and possibly context files. "
        "Relative paths are interpreted from the project root directory.",
    )
    parser.add_argument(
        "-o",
        "--output-folder",
        required=False,
        default=None,
        help="Path to folder where feedback and scores will be written. "
        "Defaults to a 'judge_results' subfolder within the folder to grade.",
    )
    parser.add_argument(
        "--model",
        default=JUDGE_MODEL,
        help=f"OpenRouter model to use for grading (default: {JUDGE_MODEL})",
    )
    parser.add_argument(
        "--nocall",
        action="store_true",
        help="Skip API calls (for testing file preparation only)",
    )
    parser.add_argument(
        "--noupload",
        action="store_true",
        help="Skip file preparation (for testing file discovery only)",
    )
    parser.add_argument(
        "--no-use-existing",
        default=True,
        type=str2bool,
        help="Force re-extraction of CSV files even if they already exist",
    )
    parser.add_argument(
        "--run-calculation",
        action="store_true",
        help="Run Excel formula calculations via LibreOffice before extracting CSVs",
    )
    parser.add_argument(
        "--solution-char-limit",
        type=int,
        default=DEFAULT_SOLUTION_CONTEXT_CHAR_LIMIT,
        help=f"Character limit for golden solution context (default: {DEFAULT_SOLUTION_CONTEXT_CHAR_LIMIT:,})",
    )
    parser.add_argument(
        "--attempt-char-limit",
        type=int,
        default=DEFAULT_ATTEMPT_CONTEXT_CHAR_LIMIT,
        help=f"Character limit for AI attempt context (default: {DEFAULT_ATTEMPT_CONTEXT_CHAR_LIMIT:,})",
    )
    parser.add_argument(
        "--total-char-limit",
        type=int,
        default=DEFAULT_TOTAL_CHARACTER_LIMIT,
        help=f"Total character limit for combined solution + attempt (default: {DEFAULT_TOTAL_CHARACTER_LIMIT:,})",
    )

    # Args preprocessing
    args = parser.parse_args()

    args.folder_to_grade = get_absolute_path(args.folder_to_grade)
    if args.output_folder is not None:
        args.output_folder = get_absolute_path(args.output_folder)
    else:
        args.output_folder = f"{args.folder_to_grade}/judge_results"

    logger.info(
        f"Running local judge with parameters: {json.dumps(vars(args), indent=2)}"
    )

    main(args)
