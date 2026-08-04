"""Unit tests for the industry-data-model installer's pure logic.

Only the network-free, Spark-free helpers are exercised (SQL splitting,
phase categorization, catalog-token rewrite, plan building, and the
transient/deterministic error classifiers). The apply loop and GitHub fetch
are integration concerns validated live by notebook 06.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "..", "scripts", "install_industry_data_model.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("install_idm", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


idm = _load_module()


class TestSplitSql:
    def test_simple_split(self):
        assert idm.split_sql("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_trailing_statement_without_semicolon(self):
        assert idm.split_sql("SELECT 1;\nSELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_semicolon_in_single_quote_is_not_a_split(self):
        stmts = idm.split_sql("INSERT INTO t VALUES ('a;b'); SELECT 1;")
        assert stmts == ["INSERT INTO t VALUES ('a;b')", "SELECT 1"]

    def test_semicolon_in_line_comment_is_ignored(self):
        # The ';' inside the line comment must NOT create a third statement, and
        # the comment TEXT is dropped (consistent with block-comment handling) so
        # categorize()'s startswith() checks see the real DDL, not "-- header".
        stmts = idm.split_sql("SELECT 1; -- a; b\nSELECT 2;")
        assert len(stmts) == 2
        assert stmts[0] == "SELECT 1"
        assert stmts[1] == "SELECT 2"

    def test_leading_line_comment_stripped_from_statement(self):
        # Regression: upstream model files prefix each statement with
        # ``-- ===== DATABASE =====`` / ``-- ===== TABLES =====`` headers. If the
        # comment text were retained, the statement would start with the header
        # and categorize() would misfile CREATE DATABASE / CREATE TABLE into the
        # ``other`` phase (which runs LAST) → every dependent DDL fails.
        sql = (
            "-- ========= DATABASE =========\n"
            "CREATE DATABASE IF NOT EXISTS `c`.`asset`;\n"
            "-- ========= TABLES =========\n"
            "CREATE OR REPLACE TABLE `c`.`asset`.`t` (id BIGINT);"
        )
        stmts = idm.split_sql(sql)
        assert idm.categorize(stmts[0]) == "schema"
        assert idm.categorize(stmts[1]) == "table"

    def test_semicolon_in_block_comment_is_ignored(self):
        stmts = idm.split_sql("SELECT 1 /* ; still one */ ; SELECT 2;")
        # block comment content is stripped, statement stays whole
        assert stmts[0].startswith("SELECT 1")
        assert stmts[1] == "SELECT 2"

    def test_backtick_identifier_with_semicolon(self):
        stmts = idm.split_sql("CREATE TABLE `a;b` (x INT); SELECT 1;")
        assert stmts == ["CREATE TABLE `a;b` (x INT)", "SELECT 1"]

    def test_empty_statements_dropped(self):
        assert idm.split_sql(";; ;") == []


class TestCategorize:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("CREATE CATALOG IF NOT EXISTS `c`", "catalog"),
            ("CREATE SCHEMA IF NOT EXISTS `c`.`s`", "schema"),
            ("CREATE DATABASE `c`.`s`", "schema"),
            ("CREATE TABLE `c`.`s`.`t` (x INT)", "table"),
            ("CREATE OR REPLACE TABLE `c`.`s`.`t` (x INT)", "table"),
            ("ALTER TABLE `c`.`s`.`t` ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES `c`.`s`.`u`", "fk"),
            ("ALTER TABLE `c`.`s`.`t` SET TAGS ('k' = 'v')", "tag"),
            ("CREATE OR REPLACE VIEW `c`.`s`.`v` AS SELECT 1", "metric"),
            ("CREATE MATERIALIZED VIEW `c`.`s`.`mv` AS SELECT 1", "metric"),
            ("INSERT INTO `c`.`s`.`t` VALUES (1)", "other"),
        ],
    )
    def test_categorize(self, sql, expected):
        assert idm.categorize(sql) == expected


class TestCatalogRewrite:
    def test_detect_catalog_token(self):
        sql = "CREATE CATALOG IF NOT EXISTS `vibe_manufacturing_v1`;"
        assert idm.detect_catalog_token(sql) == "vibe_manufacturing_v1"

    def test_detect_catalog_token_none(self):
        assert idm.detect_catalog_token("CREATE SCHEMA `x`.`y`") is None

    def test_rewrite_catalog_replaces_token(self):
        text = "CREATE TABLE `vibe_manufacturing_v1`.`production`.`run` (x INT)"
        out = idm.rewrite_catalog(text, "vibe_manufacturing_v1", "line_manager_silver_mvm")
        assert "`line_manager_silver_mvm`.`production`.`run`" in out
        assert "vibe_manufacturing_v1" not in out

    def test_rewrite_catalog_noop_when_same(self):
        text = "`c`.`s`.`t`"
        assert idm.rewrite_catalog(text, "c", "c") == text

    def test_rewrite_catalog_noop_when_src_none(self):
        text = "`c`.`s`.`t`"
        assert idm.rewrite_catalog(text, None, "x") == text


class TestBuildPlan:
    def _model(self):
        return {
            "manufacturing_catalogs_v2_mvm.sql":
                "CREATE CATALOG IF NOT EXISTS `vibe_manufacturing_v1`;",
            "manufacturing_production_schema_v2_mvm.sql": (
                "CREATE SCHEMA IF NOT EXISTS `vibe_manufacturing_v1`.`production`;\n"
                "CREATE TABLE `vibe_manufacturing_v1`.`production`.`run` (run_id BIGINT);\n"
                "ALTER TABLE `vibe_manufacturing_v1`.`production`.`run` "
                "SET TAGS ('domain' = 'production');\n"
            ),
            "manufacturing_cross_domain_foreign_keys_v2_mvm.sql": (
                "ALTER TABLE `vibe_manufacturing_v1`.`production`.`run` "
                "ADD CONSTRAINT fk1 FOREIGN KEY (sku_master_id) "
                "REFERENCES `vibe_manufacturing_v1`.`product`.`sku_master`;\n"
            ),
            "manufacturing_production_metrics_v2_mvm.sql": (
                "CREATE OR REPLACE VIEW `vibe_manufacturing_v1`.`_metrics`.`oee` "
                "AS SELECT 1;\n"
            ),
        }

    def test_plan_buckets_and_rewrites(self):
        plan = idm.build_plan(self._model(), "line_manager_silver_mvm", include_metrics=True)
        # CREATE CATALOG statements are dropped (created explicitly by install()).
        assert plan["catalog"] == []
        assert len(plan["schema"]) == 1
        assert len(plan["table"]) == 1
        assert len(plan["fk"]) == 1
        assert len(plan["tag"]) == 1
        assert len(plan["metric"]) == 1
        assert plan["_src_token"] == "vibe_manufacturing_v1"
        # Everything rewritten to the target catalog.
        for phase in ("schema", "table", "fk", "tag", "metric"):
            for stmt in plan[phase]:
                assert "vibe_manufacturing_v1" not in stmt
                assert "line_manager_silver_mvm" in stmt

    def test_plan_excludes_metrics_when_disabled(self):
        plan = idm.build_plan(self._model(), "cat", include_metrics=False)
        assert plan["metric"] == []
        # structural phases still present
        assert plan["table"] and plan["fk"]


class TestErrorClassifiers:
    def test_ignorable(self):
        assert idm._ignorable("Table already exists")
        assert not idm._ignorable("some other error")

    def test_transient(self):
        assert idm._transient("UC_CLIENT_EXCEPTION: 503 service unavailable")
        assert idm._transient("Too many requests (429)")
        assert not idm._transient("PARSE_SYNTAX_ERROR near '('")

    def test_deterministic(self):
        assert idm._deterministic("UNRESOLVED_COLUMN `foo`")
        assert idm._deterministic("PARSE_SYNTAX_ERROR")
        assert not idm._deterministic("503 temporarily unavailable")


class TestExecWithBackoff:
    def test_success_first_try(self):
        calls = []
        idm._exec_with_backoff(lambda s: calls.append(s), "SELECT 1")
        assert calls == ["SELECT 1"]

    def test_non_transient_fails_immediately(self):
        attempts = {"n": 0}

        def boom(_s):
            attempts["n"] += 1
            raise RuntimeError("PARSE_SYNTAX_ERROR")

        ok, err = idm._exec_with_backoff(boom, "bad sql")
        assert ok is False
        assert "PARSE_SYNTAX_ERROR" in err
        assert attempts["n"] == 1  # not retried

    def test_ignorable_returns_false_without_retry(self):
        attempts = {"n": 0}

        def exists(_s):
            attempts["n"] += 1
            raise RuntimeError("Table already exists")

        ok, err = idm._exec_with_backoff(exists, "create")
        assert ok is False and "already exists" in err
        assert attempts["n"] == 1


class TestRunPhase:
    def test_collects_only_real_failures(self):
        def exec_one(sql):
            if "BAD" in sql:
                raise RuntimeError("PARSE_SYNTAX_ERROR")
            if "EXISTS" in sql:
                raise RuntimeError("already exists")
            return None

        failures = idm.run_phase("table", ["GOOD", "BAD", "EXISTS"], exec_one, workers=2)
        assert len(failures) == 1
        assert failures[0][0] == "BAD"

    def test_empty_phase_is_noop(self):
        assert idm.run_phase("table", [], lambda s: None, workers=4) == []
