"""Every environment shares one Lakebase database.

dev and prod therefore poll the same ``ai_serving.scenario_commands`` table.
Unfiltered, whichever producer polls first claims the row: it applies the fault
to its own line state, emits the event into its own bronze schema, and flips the
shared row to 'active' — after which the environment the operator was actually
looking at never sees it as pending, and never will.

Observed in production: a trigger on DEPALLETIZER left the command 'active' with
the machine still running at ~299/min and no event anywhere in prod's bronze,
while dev's bronze carried ``EVT-78621928E5 shift_1_dev DEPALLETIZER
machine_breakdown`` stamped 400 ms after the prod command was created. The same
had happened to CASE_PACKER 40 minutes earlier. It presented as an intermittent
fault because it is a race — a trigger on LABELLER minutes earlier had worked
end to end, prod having won that one.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.streaming_sim.producer import CommandStore

PRODUCER_SRC = Path(__file__).resolve().parents[2] / "src" / "streaming_sim" / "producer.py"


def _executable_source() -> str:
    """Producer source with comments stripped.

    The comments describe the broken code these tests exist to keep out, so a
    plain substring search over the file matches the explanation and fails.
    """
    return "\n".join(
        line for line in PRODUCER_SRC.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


class _FakeCursor:
    def __init__(self, sink: list[tuple[str, tuple]]):
        self._sink = sink
        self.description = [("command_id",), ("shift_id",), ("scenario_id",),
                            ("target_machine_id",), ("severity",), ("status",),
                            ("created_at",), ("created_by",), ("params",),
                            ("resolved_at",), ("resolved_by",)]

    def execute(self, sql, args=()):
        self._sink.append((" ".join(sql.split()), args))

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)


@pytest.fixture
def store_and_sql(monkeypatch):
    def build(shift_id):
        monkeypatch.setenv("PGHOST", "h")
        monkeypatch.setenv("PGUSER", "u")
        store = CommandStore(shift_id=shift_id)
        sink: list[tuple[str, tuple]] = []
        monkeypatch.setattr(store, "_ensure_connection", lambda: _FakeConn(sink))
        return store, sink
    return build


class TestTheInboxIsScopedToOneShift:
    def test_pending_is_filtered_by_shift(self, store_and_sql):
        store, sql = store_and_sql("shift_1")
        store.fetch_pending()
        statement, args = sql[0]
        assert "shift_id = %s" in statement
        assert args == ("pending", "shift_1")

    def test_resolve_requests_are_filtered_too(self, store_and_sql):
        # Otherwise prod's producer clears a fault dev's operator asked about.
        store, sql = store_and_sql("shift_1")
        store.fetch_resolve_requested()
        statement, args = sql[0]
        assert "shift_id = %s" in statement
        assert args == ("resolve_requested", "shift_1")

    def test_dev_and_prod_do_not_share_an_inbox(self, store_and_sql):
        prod, prod_sql = store_and_sql("shift_1")
        dev, dev_sql = store_and_sql("shift_1_dev")
        prod.fetch_pending()
        dev.fetch_pending()
        assert prod_sql[0][1][1] == "shift_1"
        assert dev_sql[0][1][1] == "shift_1_dev"

    def test_an_unscoped_store_still_reads_everything(self, store_and_sql):
        # Single-environment and workshop deployments pass no shift.
        store, sql = store_and_sql(None)
        store.fetch_pending()
        statement, args = sql[0]
        assert "shift_id = %s" not in statement
        assert args == ("pending",)


class TestRehydrationAfterARestart:
    """The producer holds active incidents in memory. The block that reloads
    them addressed ``commands._cur`` — an attribute CommandStore has never had —
    and queried ``{_fqn}.scenario_commands`` where ``_fqn`` is already the
    qualified table. Both raise, a bare except logs at warning, so rehydration
    had never run: after any restart the machine returns to running while its
    command sits at 'active' forever.
    """

    def test_the_store_exposes_what_rehydration_needs(self):
        assert hasattr(CommandStore, "fetch_unresolved")

    def test_it_covers_both_non_terminal_states(self, store_and_sql):
        store, sql = store_and_sql("shift_1")
        store.fetch_unresolved()
        assert [args[0] for _s, args in sql] == ["active", "resolving"]

    def test_rehydration_is_shift_scoped_like_every_other_read(self, store_and_sql):
        store, sql = store_and_sql("shift_1")
        store.fetch_unresolved()
        assert all("shift_id = %s" in statement for statement, _a in sql)

    def test_the_producer_no_longer_reaches_for_a_private_cursor(self):
        assert "commands._cur" not in _executable_source()

    def test_it_does_not_double_the_table_name(self):
        assert "_fqn}.scenario_commands" not in _executable_source()

    def test_the_startup_path_calls_it(self):
        source = PRODUCER_SRC.read_text()
        assert "commands.fetch_unresolved()" in source


class TestTheStoreIsBuiltWithItsShift:
    def test_the_producer_passes_its_own_shift_id(self):
        source = PRODUCER_SRC.read_text()
        assert "CommandStore(shift_id=shift_id)" in source

    def test_shift_id_is_an_accepted_argument(self):
        assert "shift_id" in inspect.signature(CommandStore.__init__).parameters

    def test_the_constructor_takes_it_by_keyword(self):
        # Positional args on this constructor are the ignored historical
        # host/warehouse/catalog/schema, so a positional shift would land wrong.
        tree = ast.parse(PRODUCER_SRC.read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandStore"
        ]
        assert calls, "producer never constructs a CommandStore"
        for call in calls:
            assert not call.args
            assert {kw.arg for kw in call.keywords} == {"shift_id"}
