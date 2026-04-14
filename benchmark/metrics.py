from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunResult:
    case_id: str
    strategy: str
    model_id: str

    # Timing
    latency_ms: float = 0.0

    # Tokens
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Correctness
    tool_called: bool = False
    parse_success: bool = False
    correct_tool: bool = False
    correct_args: bool = False
    execution_success: bool = False

    # Raw data
    raw_response: str = ""
    parsed_tool: str | None = None
    parsed_args: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "strategy": self.strategy,
            "model_id": self.model_id,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tool_called": self.tool_called,
            "parse_success": self.parse_success,
            "correct_tool": self.correct_tool,
            "correct_args": self.correct_args,
            "execution_success": self.execution_success,
            "raw_response": self.raw_response,
            "parsed_tool": self.parsed_tool,
            "parsed_args": self.parsed_args,
            "error": self.error,
        }


@dataclass
class ExperimentResult:
    model_id: str
    strategy: str
    runs: list[RunResult] = field(default_factory=list)

    def _count(self, attr: str) -> int:
        return sum(1 for r in self.runs if getattr(r, attr))

    @property
    def avg_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.runs) / max(len(self.runs), 1)

    @property
    def avg_prompt_tokens(self) -> float:
        return sum(r.prompt_tokens for r in self.runs) / max(len(self.runs), 1)

    @property
    def avg_completion_tokens(self) -> float:
        return sum(r.completion_tokens for r in self.runs) / max(len(self.runs), 1)

    @property
    def tool_call_rate(self) -> float:
        return self._count("tool_called") / max(len(self.runs), 1)

    @property
    def parse_success_rate(self) -> float:
        return self._count("parse_success") / max(len(self.runs), 1)

    @property
    def tool_accuracy(self) -> float:
        return self._count("correct_tool") / max(len(self.runs), 1)

    @property
    def arg_accuracy(self) -> float:
        return self._count("correct_args") / max(len(self.runs), 1)

    @property
    def end_to_end_accuracy(self) -> float:
        return sum(1 for r in self.runs if r.correct_tool and r.correct_args) / max(len(self.runs), 1)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "strategy": self.strategy,
            "summary": {
                "total_runs": len(self.runs),
                "avg_latency_ms": round(self.avg_latency_ms, 1),
                "avg_prompt_tokens": round(self.avg_prompt_tokens, 1),
                "avg_completion_tokens": round(self.avg_completion_tokens, 1),
                "tool_call_rate": round(self.tool_call_rate, 3),
                "parse_success_rate": round(self.parse_success_rate, 3),
                "tool_accuracy": round(self.tool_accuracy, 3),
                "arg_accuracy": round(self.arg_accuracy, 3),
                "end_to_end_accuracy": round(self.end_to_end_accuracy, 3),
            },
            "runs": [r.to_dict() for r in self.runs],
        }


def compare(a: ExperimentResult, b: ExperimentResult) -> str:
    header = f"{'Metric':<25} {'(' + a.strategy + ')':<20} {'(' + b.strategy + ')':<20}"
    sep = "-" * 65
    rows = [
        ("Avg Latency (ms)", f"{a.avg_latency_ms:.1f}", f"{b.avg_latency_ms:.1f}"),
        ("Avg Prompt Tokens", f"{a.avg_prompt_tokens:.1f}", f"{b.avg_prompt_tokens:.1f}"),
        ("Avg Completion Tokens", f"{a.avg_completion_tokens:.1f}", f"{b.avg_completion_tokens:.1f}"),
        ("Tool Call Rate", f"{a.tool_call_rate:.1%}", f"{b.tool_call_rate:.1%}"),
        ("Parse Success Rate", f"{a.parse_success_rate:.1%}", f"{b.parse_success_rate:.1%}"),
        ("Tool Accuracy", f"{a.tool_accuracy:.1%}", f"{b.tool_accuracy:.1%}"),
        ("Arg Accuracy", f"{a.arg_accuracy:.1%}", f"{b.arg_accuracy:.1%}"),
        ("E2E Accuracy", f"{a.end_to_end_accuracy:.1%}", f"{b.end_to_end_accuracy:.1%}"),
    ]
    lines = [header, sep]
    for label, va, vb in rows:
        lines.append(f"{label:<25} {va:<20} {vb:<20}")
    return "\n".join(lines)
