from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TestCase:
    id: str
    query: str
    expected_tool: str | None
    expected_args: dict[str, Any] | None
    category: str


def match_args(expected: dict[str, Any] | None, actual: dict[str, Any] | None) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    for key, expected_val in expected.items():
        if key not in actual:
            return False
        actual_val = actual[key]
        if isinstance(expected_val, str) and isinstance(actual_val, str):
            # Fuzzy match for strings: all expected words must appear in actual
            expected_words = set(expected_val.lower().split())
            actual_lower = actual_val.lower()
            if not all(w in actual_lower for w in expected_words):
                return False
        else:
            if str(expected_val) != str(actual_val):
                return False
    return True


DEFAULT_CASES: list[TestCase] = [
    # Calculator - direct
    TestCase(
        id="calc_simple",
        query="What is 2 + 2?",
        expected_tool="calculator",
        expected_args={"expression": "2 + 2"},
        category="calculator",
    ),
    TestCase(
        id="calc_complex",
        query="Calculate 15 * 7 + 3",
        expected_tool="calculator",
        expected_args={"expression": "15 * 7 + 3"},
        category="calculator",
    ),
    TestCase(
        id="calc_power",
        query="What is 2 to the power of 10?",
        expected_tool="calculator",
        expected_args={"expression": "2 ** 10"},
        category="calculator",
    ),
    # String utils - direct
    TestCase(
        id="str_upper",
        query="Convert 'hello world' to uppercase",
        expected_tool="string_utils",
        expected_args={"text": "hello world", "operation": "upper"},
        category="string",
    ),
    TestCase(
        id="str_reverse",
        query="Reverse the string 'abcdef'",
        expected_tool="string_utils",
        expected_args={"text": "abcdef", "operation": "reverse"},
        category="string",
    ),
    TestCase(
        id="str_count",
        query="How many words are in 'the quick brown fox jumps'?",
        expected_tool="string_utils",
        expected_args={"text": "the quick brown fox jumps", "operation": "count_words"},
        category="string",
    ),
    # Search
    TestCase(
        id="search_weather",
        query="What is the current weather in Tokyo?",
        expected_tool="web_search",
        expected_args={"query": "weather Tokyo"},
        category="search",
    ),
    TestCase(
        id="search_news",
        query="Search for recent news about artificial intelligence",
        expected_tool="web_search",
        expected_args={"query": "artificial intelligence"},
        category="search",
    ),
    # No tool needed
    TestCase(
        id="no_tool_capital",
        query="What is the capital of France?",
        expected_tool=None,
        expected_args=None,
        category="no_tool",
    ),
    TestCase(
        id="no_tool_greeting",
        query="Hello, how are you?",
        expected_tool=None,
        expected_args=None,
        category="no_tool",
    ),
]
