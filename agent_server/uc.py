from __future__ import annotations

import os
from dataclasses import dataclass


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
    default_schema: str = "line_manager_ai_kit_preview",
) -> UcLocation:
    """
    Resolve UC catalog/schema from env, with safe fallback when bundle placeholders leak through.
    """
    raw_catalog = os.environ.get("UC_CATALOG", default_catalog)
    raw_schema = os.environ.get("UC_SCHEMA", default_schema)

    catalog = raw_catalog if raw_catalog and not _looks_like_placeholder(raw_catalog) else default_catalog
    schema = raw_schema if raw_schema and not _looks_like_placeholder(raw_schema) else default_schema

    return UcLocation(catalog=catalog, schema=schema)

