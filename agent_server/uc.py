from __future__ import annotations

import os
from dataclasses import dataclass

# Where the app reads when UC_SCHEMA is missing. Every rendered app yaml sets it,
# so this only runs when something bypassed that — but it still has to name the
# schema the app actually installs into. Four modules used to fall back to
# "line_manager_ai_kit" and this one to "line_manager_ai_kit_preview", neither of
# which is where the data lives; the first is the seed schema an earlier version
# copied *from*, and it still exists with stale rows. So the fallback did not
# fail, it answered — from a snapshot of a different line, with no sign to the
# reader that the numbers were months old. A default that reads the wrong table
# is worse than one that reads nothing.
DEFAULT_UC_SCHEMA = "line_manager_streaming"


def _looks_like_placeholder(v: str) -> bool:
    return ("${" in v) or ("$" in v) or ("{var." in v)


@dataclass(frozen=True)
class UcLocation:
    catalog: str
    schema: str

    def table(self, name: str) -> str:
        return f"{self.catalog}.{self.schema}.{name}"


def get_uc_location(
    *,
    default_catalog: str = "main",
    default_schema: str = DEFAULT_UC_SCHEMA,
) -> UcLocation:
    """
    Resolve UC catalog/schema from env, with safe fallback when bundle placeholders leak through.
    """
    raw_catalog = os.environ.get("UC_CATALOG", default_catalog)
    raw_schema = os.environ.get("UC_SCHEMA", default_schema)

    catalog = raw_catalog if raw_catalog and not _looks_like_placeholder(raw_catalog) else default_catalog
    schema = raw_schema if raw_schema and not _looks_like_placeholder(raw_schema) else default_schema

    return UcLocation(catalog=catalog, schema=schema)

