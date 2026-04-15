import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import tools.calculator  # noqa: F401
import tools.string_utils  # noqa: F401
import tools.search  # noqa: F401
import tools.shell  # noqa: F401
import tools.list_tools  # noqa: F401
import tools.open_file  # noqa: F401
import tools.download_file  # noqa: F401
import tools.memory  # noqa: F401
import tools.image_search  # noqa: F401

from tools import get_all_tools, execute_tool
from strategies.minimal_style import MinimalStyleStrategy

LLAMA_URL = "http://localhost:8080/v1/chat/completions"
MODEL_ID = "unsloth/gemma-4-E4B-it-GGUF:Q4_K_S"

KEEPALIVE_INTERVAL = 4 * 60  # seconds between pings (must be < llama.cpp idle timeout)

app = FastAPI()
strategy = MinimalStyleStrategy()


async def _keepalive_loop():
    payload = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                await client.post(LLAMA_URL, json=payload)
            except Exception:
                pass  # server not up yet or transient error — will retry next interval


@app.on_event("startup")
async def startup():
    asyncio.create_task(_keepalive_loop())


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


MAX_TOOL_STEPS = 10


async def _llm_call(client: httpx.AsyncClient, model: str, messages: list[dict]) -> tuple[str, list[str]]:
    """Send messages to llama.cpp and return (full_text, token_chunks)."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.0,
        "max_tokens": 512,
    }
    chunks = []
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
            token = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if token:
                chunks.append(token)
    return "".join(chunks), chunks


async def _stream(model: str, messages: list[dict], tools: dict):
    async with httpx.AsyncClient(timeout=60) as client:
        for step in range(MAX_TOOL_STEPS):
            raw, chunks = await _llm_call(client, model, messages)
            parsed = strategy.parse_response(raw, tools)

            if parsed is None:
                # Final plain-text answer — stream it out token by token
                for token in chunks:
                    yield f"data: {json.dumps({'token': token})}\n\n"
                yield f"data: {json.dumps({'done': True, 'type': 'text'})}\n\n"
                return

            thought, func_name, kwargs = parsed
            if thought:
                yield f"data: {json.dumps({'thought': thought, 'step': step})}\n\n"
            yield f"data: {json.dumps({'tool_call': func_name, 'args': kwargs, 'step': step})}\n\n"

            try:
                result = await asyncio.to_thread(execute_tool, func_name, kwargs)
                result_str = str(result)
            except Exception as e:
                result_str = f"ERROR: {e}"

            yield f"data: {json.dumps({'tool_result': result_str, 'step': step})}\n\n"

            # Append the exchange to the message history so the model can reason over it
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Tool result: {result_str}"})

    # Hit the step limit without a final answer
    yield f"data: {json.dumps({'error': 'Tool step limit reached', 'done': True})}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)