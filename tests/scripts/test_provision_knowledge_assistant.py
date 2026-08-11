"""Tests for ``scripts/provision_knowledge_assistant.py``.

The script is run on every deploy once the feature is enabled, so the property
that matters most is that a second run does not build a second assistant.
Knowledge Assistant display names are unique per workspace but creation does not
fail loudly enough to rely on: this looks the assistant up by display name first
and reuses it.

The other two guards are ingestion rules that fail silently in the platform:
files starting with ``_`` or ``.`` are skipped during ingestion, and a corpus
directory that is not there produces an assistant with nothing in it.

Everything is faked — no workspace, no REST, no SDK client.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "provision_knowledge_assistant.py"


def load_module():
    spec = importlib.util.spec_from_file_location("provision_knowledge_assistant", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRest:
    """Records calls and replays queued responses per (method, path suffix).

    ``page_size`` bounds a page the way the real API does, so a test with more
    assistants than fit on one page exercises the paging rather than pretending
    every list call returns everything.
    """

    def __init__(self, assistants: list[dict] | None = None, page_size: int = 100,
                 sources: list[dict] | None = None):
        self.assistants = assistants or []
        self.sources = sources or []
        self.page_size = page_size
        self.calls: list[tuple[str, str, dict | None]] = []

    def _page(self, items: list[dict], key: str, params: dict | None) -> FakeResponse:
        start = int((params or {}).get("page_token") or 0)
        end = start + self.page_size
        body: dict = {key: items[start:end]}
        if end < len(items):
            body["next_page_token"] = str(end)
        return FakeResponse(body)

    def _by_id(self, assistant_id: str) -> dict | None:
        for a in self.assistants:
            if (a.get("name") or "").rsplit("/", 1)[-1] == assistant_id:
                return a
        return None

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path.endswith("/knowledge-assistants"):
            return self._page(self.assistants, "knowledge_assistants", params)
        if path.endswith("/knowledge-sources"):
            return self._page(self.sources, "knowledge_sources", params)
        if path.endswith("/examples"):
            return self._page([], "examples", params)
        # Single-resource read. The real API returns fields here that the list
        # view omits, notably `state`, which is why the script reads it at all.
        single = re.match(r"^/api/[\d.]+/knowledge-assistants/([^/]+)$", path)
        if single:
            found = self._by_id(single.group(1))
            return FakeResponse(found) if found else FakeResponse({}, status=404)
        # A serving endpoint is addressed by name here and by id in the
        # permissions api, which is why the script has to look it up.
        endpoint = re.match(r"^/api/2\.0/serving-endpoints/([^/]+)$", path)
        if endpoint:
            return FakeResponse({"name": endpoint.group(1), "id": "ep-id-123"})
        return FakeResponse({})

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path.endswith("/knowledge-assistants"):
            created = {
                "name": "knowledge-assistants/ka-new",
                "knowledge_assistant_id": "ka-new",
                "display_name": (body or {}).get("display_name"),
                "endpoint_name": "ka-new-endpoint",
            }
            self.assistants.append(created)
            return FakeResponse(created)
        return FakeResponse({"name": path + "/created"})

    def put(self, path, body=None):
        self.calls.append(("PUT", path, body))
        return FakeResponse({})

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body))
        return FakeResponse({})

    def methods(self) -> list[str]:
        return [f"{m} {p}" for m, p, _ in self.calls]


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    d = tmp_path / "plant_docs"
    d.mkdir()
    (d / "sop_one.md").write_text("# SOP one\nThe FILLER runs at 420 BPM.\n", encoding="utf-8")
    (d / "sop_two.md").write_text("# SOP two\nThe CAPPER torque is 22 Nm.\n", encoding="utf-8")
    (d / "_draft.md").write_text("not ingested", encoding="utf-8")
    (d / ".hidden.md").write_text("not ingested", encoding="utf-8")
    (d / "notes.xlsx").write_text("wrong type", encoding="utf-8")
    (d / "examples.json").write_text(
        json.dumps([{"question": "What torque?", "guidelines": ["22 Nm"], "doc": "sop_two.md"}]),
        encoding="utf-8",
    )
    return d


class TestCorpusSelection:
    def test_only_ingestible_files_are_uploaded(self, corpus: Path):
        names = [p.name for p in MODULE.corpus_files(corpus)]
        assert names == ["sop_one.md", "sop_two.md"]

    def test_underscore_and_dot_files_are_left_out(self, corpus: Path):
        names = [p.name for p in MODULE.corpus_files(corpus)]
        assert not any(n.startswith(("_", ".")) for n in names)

    def test_examples_are_read_from_the_corpus(self, corpus: Path):
        examples = MODULE.corpus_examples(corpus)
        assert examples == [{"question": "What torque?", "guidelines": ["22 Nm"]}]

    def test_a_missing_corpus_directory_is_an_error(self, tmp_path: Path):
        rc = MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Docs",
            "--corpus", str(tmp_path / "nope"), "--dry-run",
        ])
        assert rc != 0


class TestAssistantLookup:
    def test_an_existing_display_name_is_reused(self):
        rest = FakeRest([{"name": "knowledge-assistants/ka-old",
                          "display_name": "Plant documents",
                          "endpoint_name": "ka-old-endpoint"}])
        found = MODULE.find_assistant(rest, "Plant documents")
        assert found["name"] == "knowledge-assistants/ka-old"

    def test_lookup_is_exact_not_fuzzy(self):
        rest = FakeRest([{"name": "knowledge-assistants/ka-old",
                          "display_name": "Plant documents (old)"}])
        assert MODULE.find_assistant(rest, "Plant documents") is None

    def test_an_assistant_past_the_first_page_is_still_found(self):
        """The list API caps a page at 100 and returns a page token for the rest.

        Reading only the first page in a workspace with more assistants than that
        reports the assistant as absent, and the script's response to absent is
        to create one — a second assistant on the same name, with a different
        endpoint, while the app keeps querying the first.
        """
        others = [{"name": f"knowledge-assistants/ka-{i}", "display_name": f"Other {i}"}
                  for i in range(7)]
        wanted = {"name": "knowledge-assistants/ka-wanted",
                  "display_name": "Plant documents",
                  "endpoint_name": "wanted-endpoint"}
        rest = FakeRest(others + [wanted], page_size=3)

        found = MODULE.find_assistant(rest, "Plant documents")
        assert found is not None, "the assistant was on a later page and was missed"
        assert found["name"] == "knowledge-assistants/ka-wanted"
        assert len([c for c in rest.calls if c[0] == "GET"]) == 3, "should page through"

    def test_ensure_assistant_creates_only_when_absent(self):
        rest = FakeRest()
        first = MODULE.ensure_assistant(rest, "Plant documents", "desc", "instructions", False)
        creates = [c for c in rest.methods() if c.startswith("POST")]
        assert len(creates) == 1

        second = MODULE.ensure_assistant(rest, "Plant documents", "desc", "instructions", False)
        creates_after = [c for c in rest.methods() if c.startswith("POST")]
        assert len(creates_after) == 1, "a second run must not build a second assistant"
        assert second["name"] == first["name"]


class TestExamplesOnAFreshAssistant:
    """The run that creates the assistant is the run that cannot write examples.

    Observed against a real workspace: for the first minute or two every example
    call returns TEMPORARILY_UNAVAILABLE because the assistant's internal vector
    index is still provisioning. So the single run where no examples exist yet is
    the run guaranteed to fail to write them, unless it waits.
    """

    PROVISIONING = ('{"error_code":"TEMPORARILY_UNAVAILABLE","message":"The internal '
                    'vector search index is still under provisioning."}')

    def test_it_waits_out_a_provisioning_index(self):
        attempts = {"n": 0}
        slept: list[int] = []

        def call():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError(f"listing examples failed: {self.PROVISIONING}")
            return [{"question": "already there"}]

        got = MODULE._retry_while_provisioning(call, sleep=slept.append, attempts=3,
                                              delay_seconds=1)
        assert got == [{"question": "already there"}]
        assert attempts["n"] == 3
        assert slept == [1, 1], "should have waited between attempts"

    def test_the_default_wait_is_short(self):
        """The retry covers a seconds-long race, not a minutes-long build. A
        generous default would just be dead time in every deploy."""
        import inspect
        sig = inspect.signature(MODULE._retry_while_provisioning)
        attempts = sig.parameters["attempts"].default
        delay = sig.parameters["delay_seconds"].default
        assert (attempts - 1) * delay <= 30, "the deploy should not wait this long"

    def test_it_does_not_wait_on_a_permanent_error(self):
        slept: list[int] = []

        def call():
            raise RuntimeError("listing examples failed: PERMISSION_DENIED")

        with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
            MODULE._retry_while_provisioning(call, sleep=slept.append, delay_seconds=1)
        assert slept == [], "a permanent error must not cost the deploy any wait"

    def test_a_rate_limited_example_is_retried_not_dropped(self):
        """Nineteen examples back to back rate-limit the embedding endpoint.

        Three were lost that way on a real deploy: each example is embedded as it
        is written, the shared endpoint answered 429 REQUEST_LIMIT_EXCEEDED, and
        the loop moved on. The assistant kept working with less guidance on the
        questions people actually ask, and the only trace was three warnings in a
        600-line log.
        """
        posts: list[dict] = []
        slept: list[int] = []

        class Limited:
            def post(self, path, body=None):
                posts.append(body or {})
                ok = len(posts) > 2
                return SimpleNamespace(
                    ok=ok, status_code=200 if ok else 429,
                    text="" if ok else '{"error_code":"REQUEST_LIMIT_EXCEEDED"}',
                )

        resp = MODULE._post_example(Limited(), "knowledge-assistants/ka-1",
                                    {"question": "q", "answer": "a"},
                                    sleep=slept.append, delay_seconds=1)
        assert resp.ok, "a rate limit is not a permanent failure"
        assert len(posts) == 3
        assert slept, "it has to actually back off, or the retry hits the same limit"

    def test_it_gives_up_on_a_real_rejection(self):
        """Anything that is not a rate limit will not be fixed by waiting."""
        posts: list[dict] = []
        slept: list[int] = []

        class Rejects:
            def post(self, path, body=None):
                posts.append(body or {})
                return SimpleNamespace(ok=False, status_code=400,
                                       text='{"error_code":"INVALID_PARAMETER_VALUE"}')

        resp = MODULE._post_example(Rejects(), "knowledge-assistants/ka-1",
                                    {"question": "q"}, sleep=slept.append)
        assert not resp.ok
        assert len(posts) == 1 and slept == []

    def test_the_backoff_stays_bounded(self):
        import inspect
        sig = inspect.signature(MODULE._post_example)
        attempts = sig.parameters["attempts"].default
        delay = sig.parameters["delay_seconds"].default
        worst = sum(delay * n for n in range(1, attempts))
        assert worst <= 30, "per example, across a whole corpus — this cannot be generous"

    def test_a_provisioning_index_does_not_fail_the_run(self, corpus: Path, monkeypatch,
                                                       capsys):
        """Examples are worth a wait, not a failed deploy: the assistant answers
        without them, and the next run registers them."""
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)
        monkeypatch.setattr(MODULE, "_retry_while_provisioning",
                            lambda call, *a, **k: (_ for _ in ()).throw(
                                RuntimeError(f"listing examples failed: {self.PROVISIONING}")))

        rc = MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus),
        ])
        out = capsys.readouterr().out
        assert rc == 0, "a provisioning index must not fail the deploy"
        assert "the index is still building" in out, (
            "an expected, self-resolving condition should not read as a warning"
        )
        assert "will register on the next run" in out
        assert "knowledge_assistant_endpoint=ka-new-endpoint" in out

    def test_a_building_index_stops_the_loop_at_the_first_refusal(self):
        """The index refuses every example, not a particular one.

        Grinding through a 19-example corpus to be told the same thing 19 times
        costs the deploy a bounded retry each and buries the log in identical
        warnings. One refusal is the whole answer.
        """
        posts: list[dict] = []

        class Building:
            def post(self, path, body=None):
                posts.append(body or {})
                return SimpleNamespace(ok=False, status_code=503,
                                       text='{"error_code":"TEMPORARILY_UNAVAILABLE"}')

            def get(self, path, params=None):
                return SimpleNamespace(ok=True, json=lambda: {"examples": []})

        examples = [{"question": f"q{n}", "answer": "a"} for n in range(19)]
        added = MODULE.register_examples(Building(), "knowledge-assistants/ka-1",
                                         examples, dry_run=False, sleep=lambda s: None)
        assert added == 0
        assert len(posts) == 1, "one refusal answers for all of them"

    def test_a_re_sync_does_not_strand_the_examples_it_did_not_write(self, corpus: Path,
                                                                    monkeypatch, capsys):
        """This gate used to read the assistant's state, which was circular.

        Every run re-uploads the corpus and requests a sync, and a sync puts an
        assistant that has been serving for hours back into CREATING. So a run
        that registered 16 of 19 (a rate limit dropped three) was followed by runs
        that skipped registration entirely, and the three stayed missing for good.
        """
        rest = FakeRest([{
            "name": "knowledge-assistants/ka-serving",
            "display_name": "Plant documents",
            "state": "CREATING",  # a sync was requested; the endpoint still serves
            "endpoint_name": "ka-serving-endpoint",
        }])
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)

        rc = MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus),
        ])
        assert rc == 0
        assert [c for c in rest.methods() if "/examples" in c], (
            "a re-synced assistant must still get the examples it is missing"
        )
        assert "examples:" in capsys.readouterr().out


class TestResourceId:
    """A live workspace returns ``id``; the SDK documents
    ``knowledge_assistant_id`` and calls ``id`` deprecated. Both shapes exist in
    the wild, and an id read as ``None`` writes a broken settings key."""

    def test_the_deprecated_field_is_still_read(self):
        assert MODULE.resource_id({"id": "abc", "name": "knowledge-assistants/abc"}) == "abc"

    def test_the_documented_field_wins_when_both_are_present(self):
        assert MODULE.resource_id({"knowledge_assistant_id": "new", "id": "old"}) == "new"

    def test_the_resource_name_is_the_last_resort(self):
        assert MODULE.resource_id({"name": "knowledge-assistants/from-name"}) == "from-name"


class TestBuildState:
    """An assistant reports CREATING, ACTIVE or FAILED. Both of the first two
    look identical from the endpoint name alone — absent — and only one of them
    gets better by waiting, so the state has to be read rather than inferred."""

    def _run(self, corpus: Path, monkeypatch, assistant: dict):
        rest = FakeRest([assistant])
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)
        return MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus),
        ])

    def test_a_failed_assistant_fails_the_run_and_names_the_reason(
        self, corpus: Path, monkeypatch, capsys
    ):
        rc = self._run(corpus, monkeypatch, {
            "name": "knowledge-assistants/ka-bad",
            "knowledge_assistant_id": "ka-bad",
            "display_name": "Plant documents",
            "state": "FAILED",
            "error_info": "ingestion failed on 2 files",
        })
        out = capsys.readouterr().out
        assert rc != 0, "a FAILED assistant reported success"
        assert "ingestion failed on 2 files" in out
        assert "knowledge_assistant_endpoint=" not in out, (
            "a failed assistant must not be wired into the app"
        )

    def test_a_building_assistant_is_not_an_error(self, corpus: Path, monkeypatch, capsys):
        rc = self._run(corpus, monkeypatch, {
            "name": "knowledge-assistants/ka-new",
            "knowledge_assistant_id": "ka-new",
            "display_name": "Plant documents",
            "state": "CREATING",
        })
        out = capsys.readouterr().out
        assert rc == 0
        assert "CREATING" in out
        assert "knowledge_assistant_id=ka-new" in out
        assert "knowledge_assistant_endpoint=" not in out


class TestASecondSyncIsNotStackedOnAnIngest:
    """Measured on a cold deploy of the whole product.

    Pass one created the assistant and its source, which started ingesting 22
    documents. Pass two, seven minutes later, asked for another sync. The two
    ingests together exceeded the org's embedding quota, and
    REQUEST_LIMIT_EXCEEDED is not queued or retried by the platform — it failed
    the build. What was left ran the endpoint, billed for it, and answered every
    document question ungrounded.
    """

    VOLUME = "/Volumes/c/s/plant_docs"

    def _run(self, corpus: Path, monkeypatch, assistant: dict,
             sources: list[dict] | None = None) -> FakeRest:
        rest = FakeRest([assistant], sources=sources)
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: self.VOLUME)
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 22)
        MODULE.main(["--catalog", "c", "--schema", "s",
                     "--display-name", "Plant documents", "--corpus", str(corpus)])
        return rest

    def _synced(self, rest: FakeRest) -> bool:
        return any("sync" in c.lower() for c in rest.methods())

    def test_a_build_in_flight_is_left_alone(self, corpus: Path, monkeypatch, capsys):
        rest = self._run(corpus, monkeypatch, {
            "name": "knowledge-assistants/ka-building",
            "display_name": "Plant documents",
            "state": "CREATING",
            "endpoint_name": "ka-building-endpoint",
        }, sources=[{"files": {"path": self.VOLUME}}])
        assert not self._synced(rest), "a second ingest was stacked on a running one"
        assert "sync not requested" in capsys.readouterr().out

    def test_a_first_ingest_is_still_asked_for(self, corpus: Path, monkeypatch):
        """The run that attaches the source must sync, or a first deploy uploads
        documents that nothing ever reads."""
        rest = self._run(corpus, monkeypatch, {
            "name": "knowledge-assistants/ka-new",
            "display_name": "Plant documents",
            "state": "CREATING",
            "endpoint_name": "ka-new-endpoint",
        })
        assert self._synced(rest)

    def test_an_assistant_that_is_serving_picks_up_edited_documents(
        self, corpus: Path, monkeypatch
    ):
        rest = self._run(corpus, monkeypatch, {
            "name": "knowledge-assistants/ka-live",
            "display_name": "Plant documents",
            "state": "ACTIVE",
            "endpoint_name": "ka-live-endpoint",
        }, sources=[{"files": {"path": self.VOLUME}}])
        assert self._synced(rest)


class TestAQuotaFailureIsRecovered:
    """A build that ran out of embedding quota looks exactly like a build that
    failed on something permanent, and the remediation is opposite: one clears on
    a re-sync (measured — FAILED went back to CREATING and error_info emptied),
    the other wastes an hour rebuilding the same failure."""

    VOLUME = "/Volumes/c/s/plant_docs"

    def _run(self, corpus: Path, monkeypatch, failure: str):
        rest = FakeRest([{
            "name": "knowledge-assistants/ka-quota",
            "display_name": "Plant documents",
            "state": "FAILED",
            "error_info": failure,
            "endpoint_name": "ka-quota-endpoint",
        }], sources=[{"files": {"path": self.VOLUME}}])
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: self.VOLUME)
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 22)
        rc = MODULE.main(["--catalog", "c", "--schema", "s",
                          "--display-name", "Plant documents", "--corpus", str(corpus)])
        return rc, rest

    def test_the_quota_failure_is_re_synced_rather_than_thrown_away(
        self, corpus: Path, monkeypatch, capsys
    ):
        rc, rest = self._run(
            corpus, monkeypatch,
            "Knowledge source f5e2b596 failed: REQUEST_LIMIT_EXCEEDED: Your request "
            "was rejected since your organization has exceeded the rate limit.",
        )
        out = capsys.readouterr().out
        assert rc == 0, "a recoverable failure stopped the deploy"
        assert any("sync" in c.lower() for c in rest.methods()), "nothing was re-synced"
        assert "quota" in out

    def test_a_failure_in_the_corpus_still_stops_the_run(self, corpus: Path, monkeypatch):
        rc, rest = self._run(corpus, monkeypatch, "ingestion failed on 2 files")
        assert rc != 0
        assert not any("sync" in c.lower() for c in rest.methods()), (
            "re-syncing a permanent failure repeats it on every deploy"
        )

    def test_the_markers_are_read_case_insensitively(self):
        assert MODULE.recoverable("request_limit_exceeded")
        assert MODULE.recoverable("RESOURCE_EXHAUSTED on the embedding endpoint")
        assert not MODULE.recoverable("PERMISSION_DENIED on the volume")
        assert not MODULE.recoverable(None)


class TestTheStateTheDeployReports:
    """An endpoint name is not an answer: a first build reports one within seconds
    and then indexes for the best part of an hour. A deploy that says "serving"
    over that sends someone to ask the assistant a question and get nothing."""

    VOLUME = "/Volumes/c/s/plant_docs"

    def _state_line(self, corpus: Path, monkeypatch, capsys, assistant: dict) -> str:
        rest = FakeRest([assistant], sources=[{"files": {"path": self.VOLUME}}])
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: self.VOLUME)
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 22)
        MODULE.main(["--catalog", "c", "--schema", "s",
                     "--display-name", "Plant documents", "--corpus", str(corpus)])
        out = capsys.readouterr().out
        line = [ln for ln in out.splitlines() if ln.startswith("knowledge_assistant_state=")]
        assert line, "the deploy has no way to tell built from answering"
        return line[0].split("=", 1)[1]

    def test_a_building_assistant_reports_indexing(self, corpus: Path, monkeypatch, capsys):
        assert self._state_line(corpus, monkeypatch, capsys, {
            "name": "knowledge-assistants/ka-building",
            "display_name": "Plant documents",
            "state": "CREATING",
            "endpoint_name": "ka-building-endpoint",
        }) == "indexing"

    def test_a_built_assistant_reports_answering(self, corpus: Path, monkeypatch, capsys):
        assert self._state_line(corpus, monkeypatch, capsys, {
            "name": "knowledge-assistants/ka-live",
            "display_name": "Plant documents",
            "state": "ACTIVE",
            "endpoint_name": "ka-live-endpoint",
        }) == "answering"


class TestTheAppIsAllowedToQuery:
    """Two access control lists, and the app only cares about the second.

    Measured against a live workspace: granting CAN_QUERY on the assistant left
    the endpoint's acl holding only ``admins`` and the creator, so the deployed
    app got 403 on every document question while the deploy log said the grant
    succeeded. Worse, the specialist then reported the documents as unavailable —
    indistinguishable from the assistant simply not being configured.
    """

    def _run(self, corpus: Path, monkeypatch) -> FakeRest:
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)
        MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus), "--grant-principal", "app-sp-1",
        ])
        return rest

    def test_the_endpoint_the_app_queries_is_granted_too(self, corpus: Path, monkeypatch):
        rest = self._run(corpus, monkeypatch)
        grants = [(p, b) for m, p, b in rest.calls if m == "PATCH" and "permissions" in p]
        paths = [p for p, _ in grants]
        assert any("permissions/knowledge-assistants/" in p for p in paths)
        assert any("permissions/serving-endpoints/" in p for p in paths), (
            "only the assistant was granted, so the app still gets 403"
        )
        for _, body in grants:
            acl = body["access_control_list"][0]
            assert acl == {"service_principal_name": "app-sp-1",
                           "permission_level": "CAN_QUERY"}

    def test_granting_one_app_does_not_revoke_the_other(self, corpus: Path, monkeypatch):
        """Two environments can share one assistant, and PUT would make them
        fight over it: it replaces the list, so each deploy would take the
        endpoint away from the app the other one deployed. PATCH merges."""
        rest = self._run(corpus, monkeypatch)
        replaced = [p for m, p, _ in rest.calls if m == "PUT" and "permissions" in p]
        assert replaced == [], (
            "a grant that replaces the access list revokes the other environment's app"
        )

    def test_the_endpoint_is_addressed_by_id_not_by_name(self, corpus: Path, monkeypatch):
        """The permissions api rejects the name with "not a valid Inference
        Endpoint ID", so the id has to be looked up first."""
        rest = self._run(corpus, monkeypatch)
        granted = [p for m, p, _ in rest.calls
                   if m == "PATCH" and "permissions/serving-endpoints/" in p]
        assert granted == ["/api/2.0/permissions/serving-endpoints/ep-id-123"]

    def test_no_principal_means_no_grant(self, corpus: Path, monkeypatch):
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)
        MODULE.main(["--catalog", "c", "--schema", "s",
                     "--display-name", "Plant documents", "--corpus", str(corpus)])
        assert not [p for m, p, _ in rest.calls if m == "PUT" and "permissions" in p]


class TestDryRun:
    def test_dry_run_writes_nothing(self, corpus: Path, monkeypatch):
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)

        rc = MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus), "--dry-run",
        ])
        assert rc == 0
        assert not [c for c in rest.methods() if c.startswith(("POST", "PUT"))]


class TestOutputContract:
    def test_it_prints_the_endpoint_and_id_for_the_deploy_to_capture(
        self, corpus: Path, monkeypatch, capsys
    ):
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)

        rc = MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus),
        ])
        out = capsys.readouterr().out
        assert rc == 0
        assert "knowledge_assistant_endpoint=ka-new-endpoint" in out
        assert "knowledge_assistant_id=ka-new" in out

    def test_examples_and_a_sync_are_requested_after_the_source_is_added(
        self, corpus: Path, monkeypatch
    ):
        rest = FakeRest()
        monkeypatch.setattr(MODULE, "build_rest_client", lambda profile: (rest, "https://host"))
        monkeypatch.setattr(MODULE, "ensure_volume", lambda *a, **k: "/Volumes/c/s/plant_docs")
        monkeypatch.setattr(MODULE, "upload_corpus", lambda *a, **k: 2)

        MODULE.main([
            "--catalog", "c", "--schema", "s", "--display-name", "Plant documents",
            "--corpus", str(corpus),
        ])
        calls = rest.methods()
        assert any("knowledge-sources" in c for c in calls)
        assert any("/examples" in c for c in calls)
        assert any("sync" in c.lower() for c in calls)
