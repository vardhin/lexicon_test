from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from benchmark.cases import TestCase, match_args
from benchmark.metrics import ExperimentResult, RunResult
from strategies import ToolStrategy
from tools import get_all_tools, execute_tool
from utils.llama_cpp import chat


def run_experiment(
    model_id: str,
    strategy: ToolStrategy,
    test_cases: list[TestCase],
    runs_per_case: int = 3,
) -> ExperimentResult:
    tools = get_all_tools()
    system_prompt = strategy.build_system_prompt(tools)
    result = ExperimentResult(model_id=model_id, strategy=strategy.name)

    total = len(test_cases) * runs_per_case
    current = 0

    for case in test_cases:
        for run_idx in range(runs_per_case):
            current += 1
            print(f"  [{current}/{total}] {case.id} (run {run_idx + 1}/{runs_per_case})...", end=" ", flush=True)

            run = _run_single(model_id, strategy, system_prompt, case, tools)
            result.runs.append(run)

            status = "OK" if run.correct_tool else "MISS"
            if run.error:
                status = f"ERR: {run.error[:40]}"
            print(f"{status} ({run.latency_ms:.0f}ms, {run.total_tokens}tok)")

    return result


def _run_single(
    model_id: str,
    strategy: ToolStrategy,
    system_prompt: str,
    case: TestCase,
    tools: dict,
) -> RunResult:
    run = RunResult(case_id=case.id, strategy=strategy.name, model_id=model_id)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case.query},
    ]

    try:
        start = time.monotonic()
        response = chat(model_id, messages, temperature=0.0, max_tokens=512)
        elapsed = time.monotonic() - start
    except Exception as e:
        run.error = f"API error: {e}"
        return run

    run.latency_ms = elapsed * 1000

    # Extract token usage
    usage = response.get("usage", {})
    run.prompt_tokens = usage.get("prompt_tokens", 0)
    run.completion_tokens = usage.get("completion_tokens", 0)
    run.total_tokens = run.prompt_tokens + run.completion_tokens

    # Extract raw response text
    raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    run.raw_response = raw

    # Parse tool call
    try:
        parsed = strategy.parse_response(raw, tools)
    except Exception as e:
        run.tool_called = True
        run.error = f"Parse error: {e}"
        return run

    if parsed is None:
        # No tool call detected
        run.tool_called = False
        run.parse_success = True
        # Check if no tool was expected
        if case.expected_tool is None:
            run.correct_tool = True
            run.correct_args = True
        return run

    func_name, kwargs = parsed
    run.tool_called = True
    run.parse_success = True
    run.parsed_tool = func_name
    run.parsed_args = kwargs

    # Check correctness
    run.correct_tool = func_name == case.expected_tool
    run.correct_args = run.correct_tool and match_args(case.expected_args, kwargs)

    # Try execution
    if run.correct_tool:
        try:
            execute_tool(func_name, kwargs)
            run.execution_success = True
        except Exception as e:
            run.error = f"Execution error: {e}"

    return run


def save_result(result: ExperimentResult, output_dir: str = "results") -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{result.model_id}_{result.strategy}_{ts}.json"
    # Sanitize filename
    filename = filename.replace("/", "_").replace(" ", "_")
    path = os.path.join(output_dir, filename)
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path
