import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import tools.calculator  # noqa: F401
import tools.string_utils  # noqa: F401
import tools.search  # noqa: F401

from tools import get_all_tools, execute_tool
from strategies.minimal_style import MinimalStyleStrategy

LLAMA_URL = "http://localhost:8080/v1/chat/completions"
MODEL_ID = "unsloth/gemma-4-E4B-it-GGUF:Q4_K_S"

app = FastAPI()
strategy = MinimalStyleStrategy()


class ChatRequest(BaseModel):
    prompt: str
    model: str = MODEL_ID


@app.post("/chat")
async def chat(req: ChatRequest):
    tools = get_all_tools()
    system_prompt = strategy.build_system_prompt(tools)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.prompt},
    ]

    return StreamingResponse(
        _stream(req.model, messages, tools),
        media_type="text/event-stream",
    )


async def _stream(model: str, messages: list[dict], tools: dict):
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.0,
        "max_tokens": 512,
    }

    raw_chunks = []

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", LLAMA_URL, json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    raw_chunks.append(token)

    # Decide after full response whether it's a tool call or plain text
    raw = "".join(raw_chunks)
    parsed = strategy.parse_response(raw, tools)

    if parsed is None:
        # Plain text — stream tokens now
        for token in raw_chunks:
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'done': True, 'type': 'text'})}\n\n"
        return

    func_name, kwargs = parsed
    yield f"data: {json.dumps({'tool_call': func_name, 'args': kwargs})}\n\n"

    try:
        result = await asyncio.to_thread(execute_tool, func_name, kwargs)
        yield f"data: {json.dumps({'tool_result': str(result), 'done': True, 'type': 'tool'})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
