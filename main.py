import asyncio
import json
import socket
import time

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
from tools.memory import auto_memory_ingest, build_memory_context, ensure_memory_daemon
from strategies.minimal_style import MinimalStyleStrategy

LLAMA_URL = "http://localhost:8080/v1/chat/completions"
MODEL_ID = "gemma-4-E4B-it-obliterated-Q4_K_M"

KEEPALIVE_INTERVAL = 4 * 60  # seconds between pings (must be < llama.cpp idle timeout)

app = FastAPI()
strategy = MinimalStyleStrategy()

MAX_CONVERSATION_TURNS = 3
_SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}


NET_PROBE_HOST = "1.1.1.1"
NET_PROBE_PORT = 443
NET_PROBE_TIMEOUT = 0.5


def _probe_network() -> str:
    """Return a short env string like 'online (42ms)' / 'slow (410ms)' / 'offline'."""
    start = time.monotonic()
    try:
        with socket.create_connection((NET_PROBE_HOST, NET_PROBE_PORT), timeout=NET_PROBE_TIMEOUT):
            pass
    except Exception:
        return "offline"
    ms = int((time.monotonic() - start) * 1000)
    label = "online" if ms < 200 else "slow"
    return f"{label} ({ms}ms)"


def _get_session_key(session_id: str | None) -> str:
    if session_id and session_id.strip():
        return session_id.strip()
    return "default"


def _get_session_history(session_key: str) -> list[dict[str, str]]:
    return list(_SESSION_HISTORY.get(session_key, []))


def _save_turn(session_key: str, user_prompt: str, assistant_reply: str) -> None:
    if not user_prompt.strip() and not assistant_reply.strip():
        return

    history = _SESSION_HISTORY.get(session_key, [])
    history.append({"role": "user", "content": user_prompt})
    history.append({"role": "assistant", "content": assistant_reply})

    max_messages = MAX_CONVERSATION_TURNS * 2
    if len(history) > max_messages:
        history = history[-max_messages:]

    _SESSION_HISTORY[session_key] = history


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
    await asyncio.to_thread(ensure_memory_daemon)


class ChatRequest(BaseModel):
    prompt: str
    model: str = MODEL_ID
    session_id: str | None = None


@app.post("/chat")
async def chat(req: ChatRequest):
    session_key = _get_session_key(req.session_id)
    prior_history = _get_session_history(session_key)

    memory_context = await asyncio.to_thread(build_memory_context, req.prompt)
    memory_block = memory_context if memory_context else "(none)"
    await asyncio.to_thread(auto_memory_ingest, req.prompt)

    tools = get_all_tools()
    system_prompt = strategy.build_system_prompt(tools)

    messages = [{"role": "system", "content": system_prompt}]

    net_status = await asyncio.to_thread(_probe_network)
    messages.append({"role": "system", "content": f"ENV: network={net_status}"})

    messages.append({"role": "system", "content": f"MEMORY: {memory_block}"})
    messages.extend(prior_history)

    messages.append({"role": "user", "content": req.prompt})

    return StreamingResponse(
        _stream(req.model, messages, tools, session_key, req.prompt),
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


async def _stream(
    model: str,
    messages: list[dict],
    tools: dict,
    session_key: str,
    initial_user_prompt: str,
):
    async with httpx.AsyncClient(timeout=60) as client:
        for step in range(MAX_TOOL_STEPS):
            raw, chunks = await _llm_call(client, model, messages)
            await asyncio.to_thread(auto_memory_ingest, raw)
            parsed = strategy.parse_response(raw, tools)

            if parsed is None:
                _save_turn(session_key, initial_user_prompt, raw)
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

            await asyncio.to_thread(auto_memory_ingest, result_str)

            yield f"data: {json.dumps({'tool_result': result_str, 'step': step})}\n\n"

            # Append the exchange to the message history so the model can reason over it
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Tool result: {result_str}"})

    # Hit the step limit without a final answer
    _save_turn(session_key, initial_user_prompt, "Tool step limit reached")
    yield f"data: {json.dumps({'error': 'Tool step limit reached', 'done': True})}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)