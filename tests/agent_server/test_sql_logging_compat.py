"""The two-filter interaction that silently disabled MLflow trace export.

MLflow and ``databricks-sql-connector`` both attach a redaction filter to the
shared ``urllib3.connectionpool`` logger. MLflow's nulls ``record.args`` after
redacting a presigned URL; the connector's then iterates them and raises
``TypeError``. A raising filter surfaces in the caller, and the caller here is
MLflow uploading span data — so traces were created and their spans never
arrived.

Both halves are pinned. The interaction test is the one that matters: an earlier
fix guarded the head of the filter chain and passed its own unit tests while
changing nothing in production, because the ``None`` is introduced further down.
"""

import logging

import pytest

from agent_server import sql_logging_compat
from agent_server.sql_logging_compat import TARGET_LOGGER, apply, is_applied, reset

PRESIGNED = "https://bucket.s3.amazonaws.com/spans?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA"


def retry_warning(url: str = PRESIGNED) -> logging.LogRecord:
    """The urllib3 warning that MLflow's span upload provokes on a retry."""
    return logging.LogRecord(
        name=TARGET_LOGGER,
        level=logging.WARNING,
        pathname=__file__,
        lineno=869,
        msg="Retrying (%r) after connection broken by '%r': %s",
        args=("Retry(total=4)", "ProtocolError()", url),
        exc_info=None,
    )


def argless_record(msg: str = "a message with no args") -> logging.LogRecord:
    return logging.LogRecord(
        name=TARGET_LOGGER,
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


@pytest.fixture
def urllib3_logger():
    """Drive the real filter chain without printing to the test run."""
    import databricks.sql  # noqa: F401  (installs the connector's)

    import agent_server  # noqa: F401  (installs MLflow's filter via its imports)

    log = logging.getLogger(TARGET_LOGGER)
    handlers, propagate, disabled = log.handlers[:], log.propagate, log.disabled
    log.handlers = [logging.NullHandler()]
    log.propagate = False
    log.disabled = False
    try:
        yield log
    finally:
        log.handlers, log.propagate, log.disabled = handlers, propagate, disabled


@pytest.fixture(autouse=True)
def _restore():
    """Leave the process as we found it — the repair is applied on import."""
    was_applied = is_applied()
    yield
    reset()
    if was_applied:
        apply()


class TestTheInteraction:
    """What actually happens in the deployed app."""

    def test_a_presigned_url_retry_warning_survives_the_chain(self, urllib3_logger):
        reset()
        assert apply() is True
        urllib3_logger.handle(retry_warning())  # must not raise

    def test_and_would_not_without_the_repair(self, urllib3_logger):
        # The regression this exists to prevent. If this stops raising, either
        # library has been fixed and the shim can go.
        reset()
        with pytest.raises(TypeError, match="not iterable"):
            urllib3_logger.handle(retry_warning())

    def test_the_credentials_are_still_redacted(self, urllib3_logger):
        reset()
        apply()
        record = retry_warning()
        urllib3_logger.handle(record)
        rendered = record.getMessage()
        assert "deadbeef" not in rendered
        assert "AKIA" not in rendered

    def test_an_ordinary_warning_is_unaffected(self, urllib3_logger):
        reset()
        apply()
        record = retry_warning(url="https://example.com/nothing-secret")
        urllib3_logger.handle(record)
        assert "nothing-secret" in record.getMessage()


class TestTheUpstreamHalves:
    """Delete this shim when either of these starts failing."""

    def test_mlflow_still_nulls_the_args_it_consumes(self):
        # Legal, and the trigger. If MLflow stops, the connector's bug goes
        # back to being latent.
        from mlflow.utils.logging_utils import SensitiveQueryParamFilter

        record = retry_warning()
        SensitiveQueryParamFilter().filter(record)
        assert record.args is None, (
            "MLflow no longer clears record.args when redacting. The connector's "
            "filter may no longer be reachable; re-check whether this shim is needed."
        )

    def test_the_connector_still_filters_a_logger_it_does_not_own(self, urllib3_logger):
        names = {type(f).__name__ for f in urllib3_logger.filters}
        assert "RedactUrlQueryParamsFilter" in names, (
            "databricks-sql-connector no longer filters urllib3.connectionpool. "
            "If so, agent_server/sql_logging_compat.py can be deleted."
        )

    def test_the_connector_filter_still_breaks_on_argless_records(self):
        from databricks.sql import RedactUrlQueryParamsFilter

        reset()
        with pytest.raises(TypeError, match="not iterable"):
            RedactUrlQueryParamsFilter().filter(argless_record())

    def test_its_dict_branch_is_broken_too(self):
        # Latent rather than live: nothing logs urllib3 with mapping args. It is
        # in the upstream report because `record.arg[k]` is a typo for
        # `record.args[k]`, so the branch cannot ever have run.
        from databricks.sql import RedactUrlQueryParamsFilter

        record = argless_record()
        record.args = {"url": "http://x?a=b"}
        with pytest.raises(AttributeError):
            RedactUrlQueryParamsFilter().filter(record)


class TestTheRepair:
    def test_it_covers_instances_created_before_it_ran(self):
        # The connector instantiates its filter at import, long before this. The
        # repair is on the class for exactly that reason.
        from databricks.sql import RedactUrlQueryParamsFilter

        instance = RedactUrlQueryParamsFilter()
        reset()
        apply()
        assert instance.filter(argless_record()) is True

    def test_none_becomes_the_empty_tuple(self):
        from databricks.sql import RedactUrlQueryParamsFilter

        reset()
        apply()
        record = argless_record()
        RedactUrlQueryParamsFilter().filter(record)
        assert record.args == ()

    def test_an_empty_tuple_renders_the_same_as_none(self):
        # The substitution is only safe because these are indistinguishable.
        plain = "no substitutions here"
        with_none = argless_record(plain)
        with_empty = argless_record(plain)
        with_empty.args = ()
        assert with_none.getMessage() == with_empty.getMessage() == plain

    def test_applying_twice_does_not_stack_wrappers(self):
        from databricks.sql import RedactUrlQueryParamsFilter

        reset()
        apply()
        once = RedactUrlQueryParamsFilter.filter
        apply()
        assert RedactUrlQueryParamsFilter.filter is once

    def test_reset_puts_the_original_back(self):
        from databricks.sql import RedactUrlQueryParamsFilter

        reset()
        before = RedactUrlQueryParamsFilter.filter
        apply()
        assert RedactUrlQueryParamsFilter.filter is not before
        reset()
        assert RedactUrlQueryParamsFilter.filter is before


class TestInstallation:
    def test_it_can_be_switched_off(self, monkeypatch):
        from databricks.sql import RedactUrlQueryParamsFilter

        reset()
        monkeypatch.setenv("SQL_LOGGING_COMPAT", "0")
        untouched = RedactUrlQueryParamsFilter.filter
        assert apply() is False
        assert RedactUrlQueryParamsFilter.filter is untouched

    def test_it_is_on_by_default_for_every_entry_point(self):
        # Applied from agent_server/__init__, so the eval harness and any script
        # that builds a specialist get it, not just the server.
        import agent_server  # noqa: F401

        assert sql_logging_compat.is_applied()

    def test_a_missing_connector_costs_the_repair_and_nothing_else(self, monkeypatch):
        import builtins

        reset()
        real_import = builtins.__import__

        def refuse_databricks_sql(name, *args, **kwargs):
            if name == "databricks" and args and args[2] and "sql" in args[2]:
                raise ImportError("no connector here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse_databricks_sql)
        assert apply() is False

    def test_a_renamed_filter_class_is_survivable(self, monkeypatch):
        from databricks import sql as databricks_sql

        reset()
        monkeypatch.delattr(databricks_sql, "RedactUrlQueryParamsFilter")
        assert apply() is False
