"""Resolve each catalogued prompt to the text the app actually sends.

The registry is the place prompts are versioned; the Python constants are the
place they are guaranteed. This module prefers the former and falls back to the
latter, per prompt, so the worst case of a registry problem is that the app
behaves exactly as it did before the registry existed.

Three rules make that guarantee real, and each exists because breaking it
produces a *subtly* wrong prompt rather than an obvious failure:

1. **Resolved once, at startup.** A per-request read would let a registry blip
   change the agent's behaviour mid-shift, and would put a network round trip
   in front of every model call.
2. **Validated before use.** A loaded template must still contain every
   variable the call site fills. A registered version edited in the UI to drop
   ``{context}`` would otherwise send a specialist a prompt with no line data
   and no error anywhere.
3. **Never empty.** A blank system prompt is the failure that looks like a
   quality regression rather than an outage, and is correspondingly hard to
   diagnose from the answers alone.

Off by default. Turning the registry into the source of truth is a change to
what the model receives, so it is opt-in via ``PROMPTS_FROM_REGISTRY`` and
belongs on dev before prod.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from agent_server.prompts.catalog import CATALOG, PromptSpec, uc_name
from agent_server.uc import get_uc_location

logger = logging.getLogger(__name__)

ALIAS = os.environ.get("PROMPTS_REGISTRY_ALIAS", "production")


def registry_enabled() -> bool:
    """Whether to consult the registry at all. Read late so tests can set it."""
    return os.environ.get("PROMPTS_FROM_REGISTRY", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _usable(spec: PromptSpec, template: str | None) -> str | None:
    """``template`` as runtime text, or None if it cannot be trusted."""
    if not template or not template.strip():
        return None
    text = spec.runtime_text(template)
    missing = [var for var in spec.variables if "{" + var + "}" not in text]
    if missing:
        logger.warning(
            "registered prompt %s is missing variable(s) %s — using the code "
            "constant instead",
            spec.slug,
            ", ".join(missing),
        )
        return None
    return text


def _load_from_registry() -> dict[str, str]:
    """Registry text per slug. Silent about prompts it cannot resolve."""
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_registry_uri("databricks-uc")
    client = MlflowClient()
    loc = get_uc_location()

    loaded: dict[str, str] = {}
    for spec in CATALOG:
        name = uc_name(spec, loc.catalog, loc.schema)
        try:
            prompt = client.load_prompt(f"prompts:/{name}@{ALIAS}")
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            logger.warning("could not load prompt %s: %s", spec.slug, exc)
            continue
        text = _usable(spec, getattr(prompt, "template", None))
        if text is not None:
            loaded[spec.slug] = text
    return loaded


@lru_cache(maxsize=1)
def _resolved() -> dict[str, str]:
    """Every catalogued prompt, registry text where trustworthy."""
    texts = {spec.slug: spec.text for spec in CATALOG}
    if not registry_enabled():
        logger.info("prompts: %d from code constants (registry off)", len(texts))
        return texts

    try:
        loaded = _load_from_registry()
    except Exception as exc:  # noqa: BLE001 - never fail startup over prompts
        logger.warning("prompt registry unavailable, using code constants: %s", exc)
        return texts

    texts.update(loaded)
    fallbacks = [spec.slug for spec in CATALOG if spec.slug not in loaded]
    logger.info(
        "prompts: %d from registry @%s, %d from code constants%s",
        len(loaded),
        ALIAS,
        len(fallbacks),
        f" ({', '.join(fallbacks)})" if fallbacks else "",
    )
    return texts


def prompt_text(slug: str) -> str:
    """The text to send for ``slug``. Never empty, never raises on registry state."""
    try:
        return _resolved()[slug]
    except KeyError:
        raise KeyError(f"{slug!r} is not in the prompt catalogue") from None


def reset_cache() -> None:
    """Drop the resolved prompts. For tests, and for a future reload endpoint."""
    _resolved.cache_clear()
