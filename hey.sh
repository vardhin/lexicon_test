#!/usr/bin/env bash
prompt="$*"

curl -sN -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": $(echo -n "$prompt" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
| while IFS= read -r line; do
  [[ "$line" != data:* ]] && continue
  data="${line#data: }"
  type=$(echo "$data" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(list(d.keys())[0])')
  case "$type" in
    token)
      echo "$data" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"], end="", flush=True)'
      ;;
    tool_call)
      echo ""
      echo "$data" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("[" + d["tool_call"] + "(" + str(d["args"]) + ")]", flush=True)'
      ;;
    tool_result)
      echo "$data" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tool_result"], flush=True)'
      ;;
    error)
      echo "$data" | python3 -c 'import json,sys; print("Error:", json.load(sys.stdin)["error"], flush=True)'
      ;;
  esac
done
echo ""
