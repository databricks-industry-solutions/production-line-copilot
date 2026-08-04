# Upstream issue draft: structured content is JSON-stringified, defeating `.text`

**Repo:** `databricks/databricks-ai-bridge` (the `databricks-langchain` package)
**Version observed:** `databricks-langchain` 0.16.1, `langchain-core` 1.4.9
**Impact:** Reasoning models return their private scratchpad to the caller as a
JSON string. `AIMessage.text` and `isinstance(content, str)` both fail to
protect against it, so the raw JSON reaches end users.

## Summary

`_convert_dict_to_message` (and `_convert_dict_to_message_chunk`) collapse
non-string content into a JSON string:

```python
content = _dict.get("content") or ""
if not isinstance(content, str):
    # for non-string content, serialize it into a string to maintain
    # compatibility with downstream consumers
    content = json.dumps(content)
```

`langchain-core` 1.x supports `content: str | list[str | dict]` natively and
provides `AIMessage.text` to extract displayable text while skipping
`reasoning` blocks. Stringifying first defeats that: `.text` sees a `str` and
returns the JSON verbatim.

## Reproduction

```python
from databricks_langchain import ChatDatabricks
r = ChatDatabricks(endpoint="databricks-gpt-oss-120b").invoke("What is 42 * 312?")
print(type(r.content))   # <class 'str'>
print(r.text[:80])
# [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "We need com
```

The underlying API returns the correct structure, and `langchain-openai`
pointed at the same endpoint handles it properly:

```python
# raw OpenAI-compatible client, same endpoint
msg.content   # -> list  [{'type': 'reasoning', ...}, {'type': 'text', 'text': '13104'}]

# langchain_openai.ChatOpenAI(base_url=f"{host}/serving-endpoints", model=EP)
type(r.content)   # <class 'list'>
r.text            # '13104'
```

A pydantic warning fires on every such response, which is the same bug seen
from the other side:

```
PydanticSerializationUnexpectedValue(Expected `str` - serialized value may not
be as expected [field_name='content', input_value=[{'type': 'reasoning', ...}],
input_type=list])
```

## Consequences beyond display

Because the value is a `str`, the usual guard passes and the payload flows
onward. In our agent this produced two failures:

1. Users saw the model's reasoning wrapped in JSON instead of an answer.
2. An intent classifier that compared the response against a fixed label set
   matched nothing, so every question fell through to the general path.

## Suggested fix

Leave structured content as the list `langchain-core` expects, and let `.text` /
`content_blocks` do the flattening. If backward compatibility for string-only
consumers is required, gate the stringification behind an opt-in flag rather
than making it the default, and note it in the release notes — silently
changing the *type* of `content` is more surprising than a list is.
