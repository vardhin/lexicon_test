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


class ShellArgs(BaseModel):
    command: str = Field(description="Zsh shell command to execute. rm commands are not allowed.")


class ListToolsArgs(BaseModel):
    pass


class OpenFileArgs(BaseModel):
    path: str = Field(description="Absolute path to the file to open/read")


class DownloadFileArgs(BaseModel):
    url: str = Field(description="URL to download")
    destination: str = Field(description="Absolute local path to save the file to")


class MemoryGetArgs(BaseModel):
    key: str = Field(description="Key to retrieve. Pass '*' to list all keys and values.")


class MemorySetArgs(BaseModel):
    key: str = Field(description="Key to store (e.g. 'images_dir', 'downloads_dir')")
    value: str = Field(description="Value to store")


class MemoryQueryArgs(BaseModel):
    query: str = Field(description="Keyword, phrase, or free-form text to search in memory")
    top_k: int = Field(default=5, description="How many top-ranked items to return per level")


class MemoryClearArgs(BaseModel):
    scope: Literal["all", "graph", "kv"] = Field(
        default="all",
        description="What to clear: 'graph' for word/phrase/sentence graph, 'kv' for key-value memory, 'all' for both",
    )
    confirm: str = Field(
        default="",
        description="Safety token for clear operations. Must be exactly 'CONFIRM' to allow deletion.",
    )


class ImageSearchArgs(BaseModel):
    query: str = Field(description="Image search query")
    max_results: int = Field(default=3, description="Number of direct image URLs to return")
