import urllib.request
import json

BASE_URL = "http://localhost:8080"


def get_models():
    url = f"{BASE_URL}/v1/models"
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read())
    return [model["id"] for model in data["data"]]


def select_model(model_id):
    models = get_models()
    if model_id not in models:
        raise ValueError(f"Model '{model_id}' not found. Available: {models}")
    return model_id


def query(model_id, prompt):
    url = f"{BASE_URL}/v1/chat/completions"
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())
    return data["choices"][0]["message"]["content"]


def chat(model_id: str, messages: list[dict], **kwargs) -> dict:
    """Send a chat completion request and return the full response dict (including usage)."""
    url = f"{BASE_URL}/v1/chat/completions"
    payload = json.dumps({"model": model_id, "messages": messages, **kwargs}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())
