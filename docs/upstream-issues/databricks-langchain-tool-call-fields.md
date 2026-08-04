# Upstream issue draft: tool-call fields dropped when replaying an assistant turn

**Repo:** `databricks/databricks-ai-bridge` (the `databricks-langchain` package)
**Version observed:** `databricks-langchain` 0.16.1, `langchain-core` 1.4.9
**Impact:** Gemini models served through Databricks cannot complete a multi-turn
tool loop (agents, ReAct, LangGraph) — every second turn is rejected.

## Summary

`_convert_message_to_dict` sends an assistant turn back to the endpoint using
`_get_tool_calls_from_ai_message`, which **rebuilds** each tool call rather than
forwarding the one that was received:

```python
# databricks_langchain/chat_models.py
def _get_tool_calls_from_ai_message(message: AIMessage) -> List[Dict]:
    tool_calls = [
        {
            "type": "function",
            "id": tc["id"],
            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
        }
        for tc in message.tool_calls
    ]
```

That is lossless for OpenAI and Anthropic, whose tool calls carry nothing beyond
those fields. It is lossy for Google: Gemini attaches a `thoughtSignature` to
each function call and requires it back on the following turn.

The field is not lost on the way in — `_convert_dict_to_message` already stores
the untouched provider payload in `additional_kwargs["tool_calls"]`. It is
discarded only on the way out.

## Reproduction

```python
from databricks_langchain import ChatDatabricks
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

@tool
def get_downtime_minutes(machine_id: str) -> str:
    """Downtime minutes for a machine today."""
    return "47"

llm = ChatDatabricks(endpoint="databricks-gemini-3-5-flash").bind_tools([get_downtime_minutes])
msgs = [HumanMessage("How many downtime minutes did FILLER-01 have? Use the tool.")]
a1 = llm.invoke(msgs)

print(a1.additional_kwargs["tool_calls"][0].keys())
# dict_keys(['id', 'function', 'type', 'thoughtSignature'])   <- present

msgs += [a1, ToolMessage(content="47", tool_call_id=a1.tool_calls[0]["id"])]
llm.invoke(msgs)
```

Result:

```
BadRequestError: Error code: 400 - {'error_code': 'BAD_REQUEST', 'message':
'Function call is missing a thought_signature in functionCall parts.
This is required for tools to work correctly...'}
```

**The same exchange succeeds through the raw OpenAI-compatible client**, which
confirms the serving API is fine and the loss is in this adapter:

```python
client = WorkspaceClient().serving_endpoints.get_open_ai_client()
r1 = client.chat.completions.create(model=EP, messages=msgs, tools=TOOLS)
raw = json.loads(r1.model_dump_json())["choices"][0]["message"]
# raw["tool_calls"][0] has keys: id, function, type, thoughtSignature
msgs += [raw, {"role": "tool", "tool_call_id": ..., "content": "47"}]
client.chat.completions.create(model=EP, messages=msgs, tools=TOOLS)   # 200 OK
```

## Suggested fix

Carry over any key on the original payload that the rebuild does not produce,
matched by tool-call id — a passthrough rather than a Gemini special case, so
future vendor fields work without another change:

```python
def _get_tool_calls_from_ai_message(message: AIMessage) -> List[Dict]:
    tool_calls = [...]  # unchanged
    raw_by_id = {
        raw.get("id"): raw
        for raw in message.additional_kwargs.get("tool_calls", [])
        if isinstance(raw, dict)
    }
    for call in tool_calls:
        raw = raw_by_id.get(call["id"], {})
        for key, value in raw.items():
            if key not in {"id", "type", "function"} and key not in call:
                call[key] = value
    return tool_calls
```

Verified locally as a monkeypatch: `databricks-gemini-3-5-flash` and
`databricks-gemini-3-1-flash-lite` both complete two-tool ReAct loops with
correct results on 4/4 runs, and output for OpenAI/Anthropic endpoints is
byte-identical.
