"""The chat's "Full trace in MLflow" link.

Two things here are worth more than the URL formatting. First, a drift guard:
this feature's predecessor called ``mlflow.get_last_active_trace()``, which
MLflow 3 removed, and because the failure was swallowed by a bare ``except``
that logged at debug level, the trace panel rendered nothing for an entire
release and nobody noticed. Second, thread isolation: the lookup is
process-wide by default, and specialists run on a worker pool, so the default
hands one user a link to another user's trace.
"""

import os
import threading

import mlflow
import pytest

from agent_server import trace_link
from agent_server.trace_link import current_trace_url, trace_url, workspace_host

HOST = "DATABRICKS_HOST"
EXPERIMENT = "MLFLOW_EXPERIMENT_ID"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from an unconfigured environment, not the developer's."""
    monkeypatch.delenv(HOST, raising=False)
    monkeypatch.delenv(EXPERIMENT, raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(HOST, "https://example.cloud.databricks.com")
    monkeypatch.setenv(EXPERIMENT, "1234567890")


class TestTheUrl:
    def test_points_at_the_workspace_route_for_a_single_trace(self, configured):
        assert trace_url("tr-abc123") == (
            "https://example.cloud.databricks.com/ml/experiments/1234567890/traces/tr-abc123"
        )

    def test_a_bare_hostname_is_promoted_to_https(self, monkeypatch, configured):
        monkeypatch.setenv(HOST, "example.cloud.databricks.com")
        assert trace_url("tr-abc").startswith("https://example.cloud.databricks.com/ml/")

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch, configured):
        monkeypatch.setenv(HOST, "https://example.cloud.databricks.com/")
        assert "com//ml" not in trace_url("tr-abc")

    def test_http_hosts_are_left_alone(self, monkeypatch, configured):
        # Local and proxied workspaces are addressed over http; forcing https
        # would produce a link that cannot connect.
        monkeypatch.setenv(HOST, "http://localhost:8080")
        assert trace_url("tr-abc").startswith("http://localhost:8080/")


class TestUnityCatalogTraceIds:
    """UC-backed traces are identified by location, and the route is not.

    Switching the app to a UC trace location changed the id from ``tr-<hex>``
    to ``trace:/<catalog>.<schema>.<prefix>/<hex>``. Interpolated whole, its
    slashes split one route segment into three and the UI renders a 404 —
    while the server still answers 200, so nothing server-side reports it.
    """

    UC_ID = "trace:/main.mlflow_traces.prodline_traces/927f9737d4fcfdc18bdaed09cce01dae"

    def test_only_the_trailing_hex_reaches_the_url(self, configured):
        assert trace_url(self.UC_ID) == (
            "https://example.cloud.databricks.com/ml/experiments/1234567890"
            "/traces/927f9737d4fcfdc18bdaed09cce01dae"
        )

    def test_the_location_does_not_leak_into_the_path(self, configured):
        """The bug precisely: extra segments below the trace route."""
        url = trace_url(self.UC_ID)
        assert "trace:/" not in url
        assert url.count("/traces/") == 1
        assert url.rsplit("/traces/", 1)[1].count("/") == 0

    def test_classic_ids_are_untouched(self, configured):
        """Prod still uses a classic experiment, so both forms must work."""
        assert trace_url("tr-abc123").endswith("/traces/tr-abc123")

    def test_an_id_with_nothing_after_the_location_falls_back_to_the_tab(self, configured):
        """Better a valid list page than a link ending in a bare slash."""
        assert trace_url("trace:/main.schema.prefix/") == (
            "https://example.cloud.databricks.com/ml/experiments/1234567890/traces"
        )


class TestWhenThereIsNoLinkToGive:
    """None is the normal answer off-platform, not an error path."""

    def test_no_trace_id(self, configured):
        assert trace_url(None) is None
        assert trace_url("") is None

    def test_no_host(self, monkeypatch, configured):
        monkeypatch.delenv(HOST)
        assert trace_url("tr-abc") is None

    def test_no_experiment(self, monkeypatch, configured):
        monkeypatch.delenv(EXPERIMENT)
        assert trace_url("tr-abc") is None

    @pytest.mark.parametrize("placeholder", ["SET_ME", "<experiment-id>", "", "   "])
    def test_unsubstituted_template_values_are_not_links(self, monkeypatch, configured, placeholder):
        # app.yaml ships placeholders before deploy fills them in. A link built
        # from one is worse than no link: it looks clickable and 404s.
        monkeypatch.setenv(EXPERIMENT, placeholder)
        assert trace_url("tr-abc") is None

    def test_a_placeholder_host_is_not_a_host(self, monkeypatch, configured):
        monkeypatch.setenv(HOST, "SET_ME")
        assert workspace_host() == ""


class TestTheMlflowCallItself:
    """The part that silently broke last time."""

    def test_the_api_this_depends_on_still_exists(self):
        # If MLflow renames or removes this again, fail here — loudly, in CI —
        # rather than in a debug log nobody reads.
        assert hasattr(mlflow, "get_last_active_trace_id"), (
            "MLflow moved the trace-id lookup. agent_server/trace_link.py needs updating; "
            "the last time this happened the feature was dead for a release."
        )

    def test_the_lookup_is_asked_for_this_thread_only(self, monkeypatch, configured):
        # Process-wide is the MLflow default and is wrong here: with a pool of
        # specialist workers it returns whichever request finished last, so one
        # user's answer links to another user's trace.
        seen = {}

        def _spy(**kwargs):
            seen.update(kwargs)
            return "tr-spy"

        monkeypatch.setattr(trace_link.mlflow, "get_last_active_trace_id", _spy)
        assert current_trace_url().endswith("/traces/tr-spy")
        assert seen == {"thread_local": True}

    def test_mlflow_blowing_up_costs_the_link_and_nothing_else(self, monkeypatch, configured):
        def _boom(**_):
            raise RuntimeError("no tracing backend")

        monkeypatch.setattr(trace_link.mlflow, "get_last_active_trace_id", _boom)
        assert current_trace_url() is None

    def test_no_trace_open_yields_no_link(self, monkeypatch, configured):
        monkeypatch.setattr(trace_link.mlflow, "get_last_active_trace_id", lambda **_: None)
        assert current_trace_url() is None

    def test_the_env_var_still_tracks_the_active_experiment(self, monkeypatch, tmp_path):
        """The link's experiment id comes from the environment, not the trace.

        That is only correct because MLflow rewrites the variable whenever the
        active experiment changes. If a release stopped doing so, links would
        keep pointing at a stale experiment and quietly 404. Reading it back off
        the trace instead would mean fetching the trace, which means blocking on
        the export queue on every answer.
        """
        monkeypatch.setenv(EXPERIMENT, "an-id-mlflow-should-replace")
        previous_uri = mlflow.get_tracking_uri()
        try:
            mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
            mlflow.set_experiment("trace-link-probe")
            assert os.environ[EXPERIMENT] != "an-id-mlflow-should-replace"
        finally:
            mlflow.set_tracking_uri(previous_uri)


class TestConcurrency:
    def test_two_threads_get_their_own_links(self, monkeypatch, configured):
        """Each worker's link must describe that worker's run.

        Stubbed rather than driven through real MLflow so the assertion is about
        our call being thread-scoped, and does not depend on a tracking backend
        being reachable from the test suite.
        """
        per_thread = threading.local()

        def _thread_scoped(*, thread_local=False):
            return getattr(per_thread, "trace_id", None) if thread_local else "tr-whoever-finished-last"

        monkeypatch.setattr(trace_link.mlflow, "get_last_active_trace_id", _thread_scoped)

        started = threading.Barrier(2)
        urls: list[str | None] = [None, None]

        def worker(index: int) -> None:
            per_thread.trace_id = f"tr-worker-{index}"
            started.wait(timeout=5)  # both threads in flight at once
            urls[index] = current_trace_url()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert urls[0].endswith("/traces/tr-worker-0")
        assert urls[1].endswith("/traces/tr-worker-1")
