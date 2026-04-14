from pydantic import BaseModel, Field
from typing import Literal


class SearchArgs(BaseModel):
    query: str = Field(description="The search query string")
    max_results: int = Field(default=5, description="Maximum number of results to return")


class CalculatorArgs(BaseModel):
    expression: str = Field(description="Mathematical expression to evaluate")


class StringUtilsArgs(BaseModel):
    text: str = Field(description="Input text")
    operation: Literal["upper", "lower", "reverse", "count_words"] = Field(
        description="Operation to perform on the text"
    )
