#!/usr/bin/env python3
"""Build the Knowledge Assistant that ``query_plant_docs`` asks.

Uploads the plant-document corpus to a Unity Catalog volume, creates (or reuses)
a Knowledge Assistant with that volume as a ``files`` knowledge source, registers
the corpus examples, and asks for a sync so a re-run picks up edited documents.

Three decisions worth knowing before reading the code:

**REST, not the SDK.** ``databricks-sdk`` grew a ``knowledge_assistants`` client
after the version this project pins, so a client call here would make the whole
accelerator require a newer SDK than the App runtime ships. The REST surface
(``/api/2.1/knowledge-assistants``) is stable and version-independent, which is
the same reasoning already written down in ``agent_server/tools/genie_query.py``
for Genie.

**Look the assistant up by display name.** Display names are unique per
workspace, and this script runs on every deploy. Without the lookup, deploy two
either fails or quietly builds a second assistant with the same name and a
different endpoint — and the app keeps pointing at the first one, so new
documents appear to be ignored.

**Nothing here parses documents.** Knowledge Assistant ingestion runs
``ai_parse_document`` internally on the files in the volume. An accelerator that
also parsed them would be duplicating the platform and would have to keep the
chunking in step with it.

Usage:
  python3 scripts/provision_knowledge_assistant.py --catalog C --schema S \\
      --display-name "Plant documents" [--volume plant_docs] \\
      [--corpus data/plant_docs] [--grant-principal <app-sp-id>] \\
      [--profile P] [--dry-run]

The last two lines of a successful run are, by contract with ``deploy.sh``:
  knowledge_assistant_endpoint=<serving endpoint name>
  knowledge_assistant_id=<knowledge assistant id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

API = "/api/2.1/knowledge-assistants"
PERMISSIONS_API = "/api/2.0/permissions/knowledge-assistants"

# Knowledge Assistant volume ingestion reads these and skips everything else.
INGESTIBLE_SUFFIXES = {".txt", ".pdf", ".md", ".ppt", ".pptx", ".doc", ".docx"}

DEFAULT_VOLUME = "plant_docs"
DEFAULT_CORPUS = "data/plant_docs"
DEFAULT_DESCRIPTION = (
    "Answers questions from the plant's engineering documents: standard operating "
    "procedures, the line process narrative, deviation reports, certificates of "
    "analysis and maintenance manuals."
)
DEFAULT_INSTRUCTIONS = (
    "You answer questions for production line operators and engineers from the "
    "documents provided.\n"
    "1. Cite the document you took each statement from, by name, and the section "
    "or step number where the document has one.\n"
    "2. Quote setpoints, tolerances and torque values exactly as written. Do not "
    "round them and do not convert units.\n"
    "3. Where a procedure is ordered, keep the order and say so — a safety "
    "isolation sequence is wrong if it is reordered.\n"
    "4. If the documents do not answer the question, say that plainly. Do not "
    "fill the gap from general knowledge of packaging lines; an operator cannot "
    "tell the difference between a cited fact and a plausible one.\n"
    "5. Machine names are fixed identifiers such as FILLER, CAPPER, LABELLER and "
    "CONVEYOR_2. Use them exactly as the documents do."
)


# ── files ────────────────────────────────────────────────────────────────────

def corpus_files(corpus_dir: Path) -> list[Path]:
    """Documents that Knowledge Assistant will actually ingest, in a stable order.

    Files starting with ``_`` or ``.`` are excluded because ingestion skips them
    silently — uploading them would suggest a corpus larger than the one the
    assistant can see. ``README.md`` and ``examples.json`` are repository
    furniture, not plant documents.
    """
    skip_names = {"README.md", "examples.json"}
    return sorted(
        p for p in corpus_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in INGESTIBLE_SUFFIXES
        and p.name not in skip_names
        and not p.name.startswith(("_", "."))
    )


def corpus_examples(corpus_dir: Path) -> list[dict]:
    """Question / guideline pairs to register as assistant examples.

    The ``doc`` field in the corpus file is for the corpus test (it pins each
    example to the document that answers it); the API takes only the question and
    its guidelines.
    """
    path = corpus_dir / "examples.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"question": e["question"], "guidelines": list(e.get("guidelines") or [])}
        for e in raw
        if e.get("question")
    ]


# ── workspace plumbing ───────────────────────────────────────────────────────

def build_rest_client(profile: str | None):
    """A requests-like session bound to the workspace, plus the host.

    Authorisation comes from the SDK config so this honours the same profile /
    environment resolution as the rest of the deploy.
    """
    import requests
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    host = w.config.host.rstrip("/")

    class _Rest:
        """Thin adapter: paths in, parsed responses out, auth refreshed per call."""

        def _headers(self) -> dict:
            raw = w.config.authenticate()
            if isinstance(raw, dict):
                headers = dict(raw)
            else:
                headers = {"Authorization": f"Bearer {raw}"}
            headers.setdefault("Content-Type", "application/json")
            return headers

        def get(self, path: str, params: dict | None = None):
            return requests.get(f"{host}{path}", headers=self._headers(),
                                params=params, timeout=60)

        def post(self, path: str, body: dict | None = None):
            return requests.post(f"{host}{path}", headers=self._headers(),
                                 json=body or {}, timeout=120)

        def put(self, path: str, body: dict | None = None):
            return requests.put(f"{host}{path}", headers=self._headers(),
                                json=body or {}, timeout=60)

        def patch(self, path: str, body: dict | None = None):
            return requests.patch(f"{host}{path}", headers=self._headers(),
                                  json=body or {}, timeout=60)

    return _Rest(), host


def ensure_volume(profile: str | None, catalog: str, schema: str, volume: str,
                  dry_run: bool) -> str:
    """Create the managed volume if absent and return its ``/Volumes`` path."""
    path = f"/Volumes/{catalog}/{schema}/{volume}"
    if dry_run:
        print(f"  [dry-run] would ensure volume {catalog}.{schema}.{volume}")
        return path

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.catalog import VolumeType

    w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    existing = {v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema)}
    if volume in existing:
        print(f"  volume {catalog}.{schema}.{volume}: exists")
        return path

    w.volumes.create(catalog_name=catalog, schema_name=schema, name=volume,
                     volume_type=VolumeType.MANAGED,
                     comment="Plant documents for the Knowledge Assistant")
    print(f"  volume {catalog}.{schema}.{volume}: created")
    return path


def upload_corpus(profile: str | None, volume_path: str, corpus_dir: Path,
                  dry_run: bool) -> int:
    """Upload every ingestible document, overwriting. Returns the file count."""
    files = corpus_files(corpus_dir)
    if dry_run:
        print(f"  [dry-run] would upload {len(files)} document(s) to {volume_path}")
        return len(files)

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    for doc in files:
        with doc.open("rb") as handle:
            w.files.upload(f"{volume_path}/{doc.name}", handle, overwrite=True)
    print(f"  uploaded {len(files)} document(s) to {volume_path}")
    return len(files)


# ── assistant ────────────────────────────────────────────────────────────────

def _paged(rest, path: str, key: str, what: str) -> list[dict]:
    """Every item across every page of a list endpoint.

    The list APIs cap a page at 100 items and coerce anything larger, so a single
    request is not "all of them" — it is the first hundred. Stopping there is the
    difference between finding the assistant and building a second one beside it.
    """
    items: list[dict] = []
    token = None
    while True:
        params = {"page_size": 100}
        if token:
            params["page_token"] = token
        resp = rest.get(path, params=params)
        if not resp.ok:
            raise RuntimeError(f"listing {what} failed: {resp.text[:300]}")
        body = resp.json()
        items.extend(body.get(key) or [])
        token = body.get("next_page_token")
        if not token:
            return items


def _retry_while_provisioning(call, sleep=time.sleep, attempts: int = 2,
                              delay_seconds: int = 15):
    """Run ``call``, retrying only while the assistant's index is provisioning.

    Bounded hard, and measured rather than guessed: against a real workspace the
    index was still provisioning more than five minutes after the assistant was
    created, so there is no wait a deploy can afford that turns this into a
    success. The retry exists for the narrow race where the assistant has just
    become ACTIVE and its index is seconds behind. Anything that is not
    TEMPORARILY_UNAVAILABLE is raised on the first attempt.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except RuntimeError as e:
            if "TEMPORARILY_UNAVAILABLE" not in str(e) or attempt == attempts:
                raise
            print(f"  index still provisioning, retrying in {delay_seconds}s "
                  f"({attempt} of {attempts - 1})")
            sleep(delay_seconds)


def resource_id(assistant: dict) -> str:
    """The assistant's id, whichever field this workspace reports it in.

    The SDK marks ``id`` deprecated in favour of ``knowledge_assistant_id``, but a
    live workspace today returns ``id`` and no ``knowledge_assistant_id`` at all.
    Reading one field would work against one of them and silently produce
    ``None`` against the other, so read both and fall back to the resource name,
    which has carried the id all along.
    """
    return str(
        assistant.get("knowledge_assistant_id")
        or assistant.get("id")
        or (assistant.get("name") or "").rsplit("/", 1)[-1]
    )


def find_assistant(rest, display_name: str) -> dict | None:
    """The assistant with exactly this display name, or None.

    Exact match on purpose: "Plant documents" and "Plant documents (old)" are
    different assistants with different endpoints, and picking the wrong one
    points the app at a corpus nobody is updating.
    """
    for item in _paged(rest, API, "knowledge_assistants", "knowledge assistants"):
        if (item.get("display_name") or "") == display_name:
            return item
    return None


def get_assistant(rest, parent: str) -> dict:
    """Fetch one assistant, for the fields the list view leaves out.

    The list response carries no ``state`` — an assistant mid-build looks
    identical to a healthy one there — so the build-state check has to read the
    single-resource view or it can never fire on the path that matters, which is
    the second deploy finding an assistant that failed on the first.
    """
    resp = rest.get(f"{API.rsplit('/', 1)[0]}/{parent}")
    if not resp.ok:
        raise RuntimeError(f"reading {parent} failed: {resp.text[:300]}")
    return resp.json()


def ensure_assistant(rest, display_name: str, description: str, instructions: str,
                     dry_run: bool) -> dict | None:
    """Reuse the assistant with this display name, or create it."""
    existing = find_assistant(rest, display_name)
    if existing:
        print(f"  assistant {display_name!r}: exists ({existing.get('name')})")
        return existing

    if dry_run:
        print(f"  [dry-run] would create assistant {display_name!r}")
        return None

    resp = rest.post(API, {
        "display_name": display_name,
        "description": description,
        "instructions": instructions,
    })
    if not resp.ok:
        raise RuntimeError(f"creating the knowledge assistant failed: {resp.text[:300]}")
    created = resp.json()
    print(f"  assistant {display_name!r}: created ({created.get('name')})")
    return created


def ensure_files_source(rest, parent: str, volume_path: str, dry_run: bool) -> bool:
    """Attach the volume as a ``files`` knowledge source, once.

    Returns whether this run created it, which decides whether a sync is needed:
    attaching a source starts an ingest by itself.
    """
    if dry_run:
        print(f"  [dry-run] would add {volume_path} as a knowledge source")
        return False

    # Raises rather than falling through to create: a listing we cannot read is
    # not evidence the source is absent, and attaching a second source over the
    # same volume ingests the corpus twice and cites it twice.
    for source in _paged(rest, f"{API.rsplit('/', 1)[0]}/{parent}/knowledge-sources",
                         "knowledge_sources", "knowledge sources"):
        if ((source.get("files") or {}).get("path") or "") == volume_path:
            print(f"  knowledge source for {volume_path}: exists")
            return False

    resp = rest.post(f"{API.rsplit('/', 1)[0]}/{parent}/knowledge-sources", {
        "display_name": "Plant documents",
        "description": (
            "Standard operating procedures, the line process narrative, deviation "
            "reports, certificates of analysis and maintenance manuals for line 1."
        ),
        "source_type": "files",
        "files": {"path": volume_path},
    })
    if not resp.ok:
        raise RuntimeError(f"adding the knowledge source failed: {resp.text[:300]}")
    print(f"  knowledge source for {volume_path}: created")
    return True


def _post_example(rest, parent: str, example: dict, sleep=time.sleep,
                  attempts: int = 3, delay_seconds: int = 5):
    """Register one example, backing off while the embedding endpoint says slow down.

    Each example is embedded as it is written, and nineteen of them back to back
    is enough to earn REQUEST_LIMIT_EXCEEDED (429) from the shared embedding
    endpoint — three were dropped that way on a real deploy. The cost is quiet:
    the assistant keeps working, with less guidance on the questions people
    actually ask, and the only trace is three warnings in a 600-line log.

    A rate limit is not a permanent error, so a few seconds of backoff recovers
    it. Bounded, because the deploy still has to finish.
    """
    path = f"{API.rsplit('/', 1)[0]}/{parent}/examples"
    resp = rest.post(path, example)
    for attempt in range(1, attempts):
        if resp.ok:
            return resp
        rate_limited = getattr(resp, "status_code", None) == 429 or \
            "REQUEST_LIMIT_EXCEEDED" in (resp.text or "")
        if not rate_limited:
            return resp
        sleep(delay_seconds * attempt)
        resp = rest.post(path, example)
    return resp


def register_examples(rest, parent: str, examples: list[dict], dry_run: bool,
                      sleep=time.sleep) -> int:
    """Register example questions. Existing ones are left alone.

    An example that fails to register is not worth failing the deploy over: the
    assistant answers without it, slightly less reliably.

    Two failures are worth telling apart, because they want opposite handling.
    TEMPORARILY_UNAVAILABLE means the internal index is still building, which is
    true of every example equally — so the first one stops the loop and reports
    how many are left. REQUEST_LIMIT_EXCEEDED is per call: the shared embedding
    endpoint rate-limiting a corpus written back to back, which a few seconds of
    backoff fixes (see ``_post_example``).
    """
    if dry_run:
        print(f"  [dry-run] would register {len(examples)} example(s)")
        return len(examples)

    try:
        known = {(e.get("question") or "").strip()
                 for e in _retry_while_provisioning(
                     lambda: _paged(rest, f"{API.rsplit('/', 1)[0]}/{parent}/examples",
                                    "examples", "examples"), sleep)}
    except RuntimeError as e:
        if "TEMPORARILY_UNAVAILABLE" in str(e):
            # Expected on the run that creates the assistant: the list endpoint
            # goes unavailable with the index it reads. Not a warning.
            print(f"  examples: the index is still building — {len(examples)} will "
                  f"register on the next run")
            return 0
        # Not fatal, but say so: an unreadable list means every example below is
        # treated as new, and re-registering them is how duplicates accumulate.
        print(f"  WARN: could not list existing examples ({e}); skipping example registration")
        return 0

    added = 0
    pending = 0
    for index, example in enumerate(examples):
        if example["question"].strip() in known:
            continue
        resp = _post_example(rest, parent, example, sleep)
        if resp.ok:
            added += 1
        elif "TEMPORARILY_UNAVAILABLE" in (resp.text or ""):
            # A building index rejects every example, not this one, so trying the
            # rest costs the deploy a bounded retry per example and prints the
            # same error twenty times. Stop and say when they will land.
            pending = len(examples) - index
            print(f"  examples: the index is still building — {pending} will register "
                  f"on the next run")
            break
        else:
            print(f"  WARN: example not registered: {resp.text[:160]}")
    if not pending:
        print(f"  examples: {added} added, {len(known)} already present")
    return added


RECOVERABLE_INGEST_FAILURES = (
    "REQUEST_LIMIT_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "TEMPORARILY_UNAVAILABLE",
)


def recoverable(error_info: str | None) -> bool:
    """Whether a FAILED build is worth re-syncing instead of deleting.

    An ingest that ran out of the org's embedding quota fails the whole build,
    and the state it leaves behind is indistinguishable from a build that failed
    on something permanent. The difference matters: a re-sync clears the quota
    failure (measured — FAILED went back to CREATING and the error cleared),
    while deleting and re-creating costs the whole build for nothing.
    """
    return any(marker in (error_info or "").upper()
               for marker in RECOVERABLE_INGEST_FAILURES)


def sync_sources(rest, parent: str, dry_run: bool) -> None:
    """Ask for a re-index.

    A files-based source does not notice edited files on its own, and syncing is
    incremental, so asking on every deploy costs little and is the only way an
    updated document reaches the assistant. Index-based sources would not need
    this; this corpus is files.
    """
    if dry_run:
        print("  [dry-run] would trigger a knowledge source sync")
        return

    resp = rest.post(f"{API.rsplit('/', 1)[0]}/{parent}/knowledge-sources:sync")
    if resp.ok:
        print("  sync requested (indexing runs in the background)")
    else:
        print(f"  WARN: sync not requested: {resp.text[:160]}")


def grant_query(rest, assistant_id: str, principal: str, dry_run: bool,
                endpoint: str | None = None) -> None:
    """Let the app service principal query the assistant.

    Without this the app owns nothing on the assistant and every tool call 403s,
    which surfaces to an operator as the assistant having no answer.

    Two grants, because the assistant and the endpoint it serves from keep
    SEPARATE access control lists and the app talks to the endpoint. Granting
    only the assistant was measured to leave the endpoint acl untouched, so a
    deploy that printed "granted CAN_QUERY" still produced 403 on every question
    — and did it convincingly, since the specialist then reported the documents
    as unavailable exactly as it would for an unconfigured assistant.

    PATCH, not PUT: one assistant can serve both environments (see
    ``knowledge_docs_schema`` in the settings), and PUT *replaces* the list, so
    each deploy would hand access to its own app by taking it from the other's.
    PATCH merges, and re-granting a principal that already has CAN_QUERY is a
    no-op — which is what a deploy run twice needs.
    """
    if dry_run:
        print(f"  [dry-run] would grant CAN_QUERY to {principal}")
        if endpoint:
            print(f"  [dry-run] would grant CAN_QUERY on endpoint {endpoint}")
        return

    acl = {"access_control_list": [
        {"service_principal_name": principal, "permission_level": "CAN_QUERY"}
    ]}
    resp = rest.patch(f"{PERMISSIONS_API}/{assistant_id}", acl)
    if resp.ok:
        print(f"  granted CAN_QUERY to {principal}")
    else:
        print(f"  WARN: grant failed for {principal}: {resp.text[:160]}")

    if not endpoint:
        return
    # The permissions api addresses a serving endpoint by its id, not the name
    # everything else here uses, and answers "not a valid Inference Endpoint ID"
    # for the name.
    ep = rest.get(f"/api/2.0/serving-endpoints/{endpoint}")
    endpoint_id = ep.json().get("id") if ep.ok else None
    if not endpoint_id:
        print(f"  WARN: could not resolve endpoint {endpoint} to grant on it — "
              f"the app will get 403 when it asks about documents.")
        return
    resp = rest.patch(f"/api/2.0/permissions/serving-endpoints/{endpoint_id}", acl)
    if resp.ok:
        print(f"  granted CAN_QUERY on endpoint {endpoint} to {principal}")
    else:
        print(f"  WARN: endpoint grant failed for {principal}: {resp.text[:160]}")


# ── entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", default=os.environ.get("UC_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("UC_SCHEMA"))
    ap.add_argument("--display-name", default=os.environ.get("KNOWLEDGE_ASSISTANT_NAME"))
    ap.add_argument("--volume", default=DEFAULT_VOLUME)
    ap.add_argument("--corpus", default=str(REPO_ROOT / DEFAULT_CORPUS))
    ap.add_argument("--description", default=DEFAULT_DESCRIPTION)
    ap.add_argument("--instructions-file", default=None,
                    help="override the built-in answering instructions")
    ap.add_argument("--grant-principal", default=None,
                    help="app service principal to grant CAN_QUERY")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    missing = [n for n in ("catalog", "schema", "display_name") if not getattr(args, n)]
    if missing:
        print(f"error: missing required argument(s): {', '.join(missing)}")
        return 2

    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_dir():
        print(f"error: corpus directory {corpus_dir} does not exist")
        return 2
    if not corpus_files(corpus_dir):
        print(f"error: {corpus_dir} holds no ingestible documents "
              f"({', '.join(sorted(INGESTIBLE_SUFFIXES))})")
        return 2

    instructions = DEFAULT_INSTRUCTIONS
    if args.instructions_file:
        instructions = Path(args.instructions_file).read_text(encoding="utf-8")

    print(f"Knowledge Assistant provisioning for {args.catalog}.{args.schema}")

    volume_path = ensure_volume(args.profile, args.catalog, args.schema, args.volume,
                                args.dry_run)
    upload_corpus(args.profile, volume_path, corpus_dir, args.dry_run)

    rest, _host = build_rest_client(args.profile)
    assistant = ensure_assistant(rest, args.display_name, args.description, instructions,
                                 args.dry_run)
    if assistant is None:
        # Only reachable under --dry-run, where there is nothing to attach to.
        print("\n[dry-run] nothing was created.")
        return 0

    assistant_id = resource_id(assistant)
    parent = assistant.get("name") or f"knowledge-assistants/{assistant_id}"
    assistant = get_assistant(rest, parent)

    # The assistant reports its own build state, so read it rather than inferring
    # from a missing endpoint name: CREATING and FAILED both present as "no
    # endpoint", and only one of them gets better by waiting. Checked before the
    # source and examples because syncing a corpus into an assistant that will
    # never serve only hides the failure behind a longer run.
    state = (assistant.get("state") or "").upper()
    failure = assistant.get("error_info") or ""
    if state == "FAILED" and not recoverable(failure):
        print("\nThe assistant is in FAILED state, so it will never serve an endpoint.")
        print(f"  reason: {failure or 'none reported'}")
        print("Delete it in the Agents UI (or with ./scripts/cleanup.sh) and re-run;")
        print("re-running against a failed assistant reuses the failure.")
        print(f"knowledge_assistant_id={assistant_id}")
        return 1
    if state == "FAILED":
        print(f"  build failed on a quota, not on the corpus: {failure[:140]}")
        print("  re-syncing, which clears it — the documents index again in the background")

    created_source = ensure_files_source(rest, parent, volume_path, args.dry_run)
    # Gated on the state being CREATING once, which turned out to be circular:
    # every run re-uploads the corpus and requests a sync, and a sync puts an
    # assistant that has been serving for hours back into CREATING. So the run
    # after a partial registration skipped registration, and the three examples a
    # rate limit had dropped stayed missing for good.
    #
    # The condition worth reacting to is the index actually refusing, which
    # register_examples now detects and reports — on the first refusal, since it
    # applies to every example equally.
    register_examples(rest, parent, corpus_examples(corpus_dir), args.dry_run)

    # Why a sync is not asked for unconditionally: the second pass of a first
    # deploy asked for one seven minutes into the first ingest, and the two
    # together exceeded the org's embedding quota. That does not queue and it does
    # not retry — REQUEST_LIMIT_EXCEEDED failed the whole build, leaving an
    # assistant that reported an endpoint, billed for it and answered nothing.
    #
    # A brand-new source is still synced: that is the run that has been seen to
    # index a corpus successfully, and skipping it risks a first deploy that
    # uploads documents nothing ever reads.
    if created_source or state != "CREATING":
        sync_sources(rest, parent, args.dry_run)
    else:
        print("  sync not requested — an ingest is already running; edited documents")
        print("    index on the next deploy, once this one finishes")
    indexing = state != "ACTIVE"

    endpoint = assistant.get("endpoint_name") or ""

    # After the endpoint name is known, so both access control lists can be set
    # in one run — the app queries the endpoint, not the assistant.
    if args.grant_principal:
        grant_query(rest, assistant_id, args.grant_principal, args.dry_run,
                    endpoint=endpoint or None)

    if not endpoint:
        # Rare in practice: a live workspace returns the endpoint name within
        # seconds of creation, long before the index is built. Kept because the
        # field is optional in the API and an assistant without one cannot be
        # wired to the app. Not a failure — a later run picks the name up.
        print(f"\nThe assistant has no endpoint name yet (state: {state or 'unreported'}).")
        print("A first build takes minutes to a couple of hours. Re-run this")
        print("script (or deploy) once it reports one to wire the app up.")
        print(f"knowledge_assistant_id={assistant_id}")
        return 0

    print("\nThe app reads these two values; deploy.sh writes them back to settings.")
    print(f"knowledge_assistant_endpoint={endpoint}")
    print(f"knowledge_assistant_id={assistant_id}")
    # An endpoint name is not an answer. A first build reports one within seconds
    # and then indexes for the best part of an hour, during which every question
    # comes back ungrounded — so the deploy summary needs to know the difference
    # between built and answering, and only this script has read the state.
    print(f"knowledge_assistant_state={'indexing' if indexing else 'answering'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
