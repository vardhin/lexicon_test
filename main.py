import argparse
import json
import sys

# Import tools to trigger registration
import tools.calculator  # noqa: F401
import tools.string_utils  # noqa: F401
import tools.search  # noqa: F401

from tools import get_all_tools
from utils.llama_cpp import get_models
from strategies.c_style import CStyleStrategy
from strategies.json_style import JsonStyleStrategy
from strategies.minimal_style import MinimalStyleStrategy
from benchmark.cases import DEFAULT_CASES
from benchmark.runner import run_experiment, save_result
from benchmark.metrics import compare


STRATEGIES = {
    "c_style": CStyleStrategy,
    "json_style": JsonStyleStrategy,
    "minimal_style": MinimalStyleStrategy,
}


def cmd_list_tools(args):
    tools = get_all_tools()
    if not tools:
        print("No tools registered.")
        return
    for name, spec in tools.items():
        fields = spec.param_model.model_fields
        params = ", ".join(f"{k}: {v.annotation.__name__ if hasattr(v.annotation, '__name__') else v.annotation}" for k, v in fields.items())
        print(f"  {name}({params}) -> {spec.return_type}")
        print(f"    {spec.description}")
        print()


def cmd_list_models(args):
    try:
        models = get_models()
        print("Available models:")
        for m in models:
            print(f"  - {m}")
    except Exception as e:
        print(f"Error connecting to llama.cpp server: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args):
    model_id = args.model
    strategies_to_run = []

    if args.strategy == "both":
        strategies_to_run = [CStyleStrategy(), JsonStyleStrategy(), MinimalStyleStrategy()]
    else:
        strategies_to_run = [STRATEGIES[args.strategy]()]

    results = []
    for strategy in strategies_to_run:
        print(f"\n{'='*65}")
        print(f"Running: {strategy.name} | Model: {model_id} | Runs/case: {args.runs}")
        print(f"{'='*65}")

        result = run_experiment(model_id, strategy, DEFAULT_CASES, runs_per_case=args.runs)
        path = save_result(result)
        results.append(result)

        print(f"\nResults saved to: {path}")
        print(f"  E2E Accuracy: {result.end_to_end_accuracy:.1%}")
        print(f"  Avg Tokens:   {result.avg_prompt_tokens:.0f} prompt + {result.avg_completion_tokens:.0f} completion")
        print(f"  Avg Latency:  {result.avg_latency_ms:.0f}ms")

    if len(results) == 2:
        print(f"\n{'='*65}")
        print("COMPARISON")
        print(f"{'='*65}")
        print(compare(results[0], results[1]))


def cmd_compare(args):
    results = []
    for path in args.files:
        with open(path) as f:
            data = json.load(f)
        from benchmark.metrics import ExperimentResult, RunResult
        er = ExperimentResult(model_id=data["model_id"], strategy=data["strategy"])
        for r in data["runs"]:
            er.runs.append(RunResult(**{k: v for k, v in r.items()}))
        results.append(er)

    if len(results) != 2:
        print("Provide exactly 2 result files to compare.", file=sys.stderr)
        sys.exit(1)

    print(compare(results[0], results[1]))


def cmd_show_prompt(args):
    strategy = STRATEGIES[args.strategy]()
    tools = get_all_tools()
    print(strategy.build_system_prompt(tools))


def main():
    parser = argparse.ArgumentParser(description="Tool-calling strategy benchmark for local LLMs")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-tools", help="List registered tools")
    sub.add_parser("list-models", help="List models from llama.cpp server")

    run_p = sub.add_parser("run", help="Run benchmark experiment")
    run_p.add_argument("--model", required=True, help="Model ID from llama.cpp server")
    run_p.add_argument("--strategy", choices=["c_style", "json_style", "minimal_style", "both"], default="both")
    run_p.add_argument("--runs", type=int, default=3, help="Runs per test case (default: 3)")

    cmp_p = sub.add_parser("compare", help="Compare two result files")
    cmp_p.add_argument("files", nargs=2, help="Two JSON result files to compare")

    prompt_p = sub.add_parser("show-prompt", help="Show the system prompt for a strategy")
    prompt_p.add_argument("--strategy", choices=["c_style", "json_style", "minimal_style"], required=True)

    args = parser.parse_args()

    commands = {
        "list-tools": cmd_list_tools,
        "list-models": cmd_list_models,
        "run": cmd_run,
        "compare": cmd_compare,
        "show-prompt": cmd_show_prompt,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
