"""Agent server package."""

# All applied here so every entry point gets them — the FastAPI server, the
# eval harness and any script that builds a specialist. Without the first,
# Gemini-backed roles fail on the second turn of a tool loop; without the
# second, Qwen- and Llama-backed roles fail on the first model call; without
# the third, no MLflow trace ever reaches the workspace. None of them raises;
# see the module docstrings for what they patch and why.
from agent_server import sql_logging_compat as _sql_logging_compat
from agent_server import tool_schema_compat as _tool_schema_compat
from agent_server import vendor_compat as _vendor_compat

_vendor_compat.apply()
_tool_schema_compat.apply()
_sql_logging_compat.apply()
