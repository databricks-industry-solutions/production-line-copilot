"""System prompt for the no-tool "general" route.

`_run_specialist` falls back to this route when the router picks no specialist
or names one that is not registered — in practice the broad openers, "give me
a status", "what's going on". It is a single chat call with no tools, so the
context block below is the only line data the model ever sees on this path.

The text lived inline in `agent.py` until it became the one prompt the app
sends that the registry knew nothing about. Registering it costs nothing and
removes the case where a reader reconciling traces against the registry finds
a system prompt with no version behind it.

It is deliberately far thinner than the specialist prompts: no citation rules,
no response structure, no governed-domain guidance. That is worth revisiting
on quality grounds, but it is a behaviour change and does not belong in a
change whose purpose is to stop prompts going unversioned.
"""

GENERAL_SYSTEM_TEMPLATE = """You are a helpful Line Manager AI assistant. Here is the current context:
{context}"""
