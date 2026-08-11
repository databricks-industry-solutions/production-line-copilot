# Knowledge Assistant (Chapter 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in Agent Bricks Knowledge Assistant over a plant-document corpus, callable from chat by three specialists, with citations.

**Architecture:** Corpus of markdown plant docs ships in the repo, is uploaded to a Unity Catalog volume at deploy time, and becomes a `files` knowledge source on a Knowledge Assistant created through the REST API (`/api/2.1/knowledge-assistants`). Provisioning writes the resulting agent endpoint name back into settings; `render_app_yaml.py` turns that into `DOCS_KA_ENDPOINT`, which a new `query_plant_docs` LangChain tool queries. Everything is off when the config keys are empty.

**Tech Stack:** Python 3.11, Databricks SDK 0.94 (`WorkspaceClient` for auth + volume upload), `requests` for the Knowledge Assistants REST surface, LangChain `@tool`, pytest.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-08-11-knowledge-assistant-design.md`.
- No `ai_parse_document` / `ai_extract` code in the accelerator. KA ingestion parses internally.
- No Supervisor Agent (MAS) work in this plan.
- Opt-in: empty config → provisioning skipped, tool inert, deploy prints how to enable.
- KA failure must never fall through to `retrieve_similar_cases` (different corpus).
- Corpus is markdown (`.md` is a supported KA file type) — no binary files added to git.
- Installed `databricks-sdk` (0.94.0) has no `knowledge_assistants` client, so use REST directly, matching the reasoning already documented in `agent_server/tools/genie_query.py`.
- Config key names: `knowledge_assistant_name`, `knowledge_assistant_id`, `knowledge_assistant_endpoint`, `knowledge_docs_volume`. (`knowledge_assistant_id` replaces the spec's `knowledge_assistant_tile_id`: "tile id" is Agent Bricks MCP wording, the REST resource is `knowledge-assistants/{knowledge_assistant_id}`.)
- Env var names: `DOCS_KA_ENDPOINT` (agent serving endpoint), `DOCS_KA_ID` (assistant id, provenance only).
- Repo test style: descriptive module names under `tests/`, all network mocked.
- Run `.venv/bin/python -m pytest` (project venv), `.venv/bin/python -m ruff check` before each commit.

---

### Task 1: Plant document corpus

**Files:**
- Create: `data/plant_docs/README.md`, `data/plant_docs/sop_filler_changeover.md`, `data/plant_docs/sop_capper_loto.md`, `data/plant_docs/pid_filler_line_narrative.md`, `data/plant_docs/deviation_2026_03_labeler.md`, `data/plant_docs/coa_pet_preform_lot.md`, `data/plant_docs/maintenance_manual_conveyor.md`
- Create: `data/plant_docs/examples.json`
- Test: `tests/data/test_the_plant_doc_corpus_is_answerable.py`

**Interfaces:**
- Produces: corpus directory path `data/plant_docs/`, example file schema
  `[{"question": str, "guidelines": [str], "doc": str}]` where `doc` is a filename in the same directory.

- [x] **Step 1: Write the failing test** asserting every `.md` doc is non-empty, mentions at least one machine id used by the demo line, no file name starts with `_` or `.` (KA skips those), and every `examples.json` entry references an existing doc file.
- [x] **Step 2: Run it** — `.venv/bin/python -m pytest tests/data/test_the_plant_doc_corpus_is_answerable.py -v`; expect collection failure / missing directory.
- [x] **Step 3: Write the corpus** — six markdown docs written against the machines the accelerator simulates, each with headed sections and concrete values (setpoints, torque, tolerances, steps) so answers can cite something specific.
- [x] **Step 4: Run tests** — expect PASS.
- [x] **Step 5: Commit.**

---

### Task 2: `query_plant_docs` tool

**Files:**
- Create: `agent_server/tools/plant_docs_query.py`
- Test: `tests/agent_server/test_plant_docs_query.py`

**Interfaces:**
- Produces:
  - `docs_assistant_configured() -> bool`
  - `run_plant_docs_query(question: str) -> dict` → `{"answer": str, "citations": list[dict], "retrieval": "knowledge_assistant"}` or `{"error": str}`
  - `query_plant_docs` (LangChain `@tool`, same return shape)
  - pure helpers `extract_answer(payload: dict) -> str`, `extract_citations(payload: dict) -> list[dict]`
- Consumes: `DOCS_KA_ENDPOINT`, `agent_server.tools.workspace_client.app_workspace_client`.

- [x] **Step 1: Write failing tests** — unset endpoint returns an `error` mentioning `DOCS_KA_ENDPOINT`; question shorter than 5 chars rejected; `extract_answer` handles both the ResponsesAgent (`output[].content[].text`) and chat-completions (`choices[0].message.content`) shapes; `extract_citations` pulls doc/page from annotations and de-duplicates; a mocked happy path returns `retrieval == "knowledge_assistant"`.
- [x] **Step 2: Run** — expect import failure.
- [x] **Step 3: Implement** with fresh OAuth headers per call (Genie pattern), POST to `/serving-endpoints/{name}/invocations` using `{"input": [...]}` and one documented retry with `{"messages": [...]}` when the endpoint rejects that schema, defensive parse of both response shapes, no fallback to any other retriever.
- [x] **Step 4: Run tests** — expect PASS. Then `ruff check agent_server/tools/plant_docs_query.py`.
- [x] **Step 5: Commit.**

---

### Task 3: Wire the tool into the three specialists

**Files:**
- Modify: `agent_server/specialists/maintenance_predictor.py`, `agent_server/specialists/quality_specialist.py`, `agent_server/specialists/downtime_analyst.py`
- Test: `tests/agent_server/test_the_specialists_that_read_plant_docs.py`

**Interfaces:**
- Consumes: `query_plant_docs` from Task 2.

- [x] **Step 1: Write failing test** — the three factories expose `query_plant_docs`; the other specialists (`oee_coach`, `schedule_optimizer`, `supply_chain_advisor`, `strategic_advisor`, `shift_briefing`) do not. Introspect built agents using the `_tool_names` helper pattern from `tests/agent_server/test_specialist_domain_wiring.py`.
- [x] **Step 2: Run** — expect FAIL (tool absent).
- [x] **Step 3: Add the import and tool entry** to the three factories only.
- [x] **Step 4: Run tests** — expect PASS.
- [x] **Step 5: Commit.**

---

### Task 4: Provisioning script

**Files:**
- Create: `scripts/provision_knowledge_assistant.py`
- Test: `tests/scripts/test_provision_knowledge_assistant.py`

**Interfaces:**
- Produces (importable for tests): `find_assistant_by_display_name(session, host, display_name) -> dict | None`, `ensure_assistant(...) -> dict`, `ensure_files_source(...) -> dict`, `upload_corpus(w, volume_dir, corpus_dir, dry_run) -> int`, `main() -> int`.
- CLI: `--catalog --schema --display-name [--volume plant_docs] [--corpus data/plant_docs] [--instructions-file] [--grant-principal] [--profile] [--dry-run]`.
- Prints `knowledge_assistant_endpoint=<name>` and `knowledge_assistant_id=<id>` on the last lines so deploy can capture them.

- [x] **Step 1: Write failing tests** — `--dry-run` performs no writes; an existing assistant with the same display name is reused rather than duplicated; corpus upload skips files starting with `_`/`.`; a missing corpus directory returns non-zero. REST mocked with a fake session.
- [x] **Step 2: Run** — expect import failure.
- [x] **Step 3: Implement** — ensure volume (`CREATE VOLUME IF NOT EXISTS` via SDK `volumes` API), upload corpus with `w.files.upload`, `GET/POST /api/2.1/knowledge-assistants`, create the `files` knowledge source pointing at the volume directory, create examples from `examples.json`, `POST` the sync call, grant `CAN_QUERY` to the app SP via `PUT /api/2.0/permissions/knowledge-assistants/{id}` when `--grant-principal` is given.
- [x] **Step 4: Run tests + ruff** — expect PASS.
- [x] **Step 5: Commit.**

---

### Task 5: Config + app env plumbing

**Files:**
- Modify: `config/project-settings.yaml` (both profiles), `scripts/render_app_yaml.py`, `app.yaml` (blank prod template entry if the file lists env keys)
- Test: `tests/scripts/test_render_app_yaml.py` (extend), `tests/agent_server/test_code_defaults_agree_with_the_shipped_config.py` (extend if it enumerates keys)

**Interfaces:**
- Consumes: config keys from Global Constraints.
- Produces: `DOCS_KA_ENDPOINT`, `DOCS_KA_ID` in the rendered app yaml; empty when unset.

- [x] **Step 1: Write failing test** — rendering a profile with `knowledge_assistant_endpoint` set produces `DOCS_KA_ENDPOINT`; rendering without it leaves the value empty rather than omitting the key.
- [x] **Step 2: Run** — expect FAIL.
- [x] **Step 3: Add the mapping and the commented config block** explaining the opt-in cost (an agent endpoint bills while it is online) and that the corpus is a demo stand-in for a real document volume.
- [x] **Step 4: Run tests** — expect PASS.
- [x] **Step 5: Commit.**

---

### Task 6: Deploy hook

**Files:**
- Modify: `scripts/deploy.sh` (immediately after the Vector Search block)
- Test: `tests/scripts/test_the_deploy_provisions_the_plant_docs_assistant.py`

**Interfaces:**
- Consumes: Task 4 CLI, Task 5 config keys.

- [x] **Step 1: Write failing test** — `deploy.sh` invokes `scripts/provision_knowledge_assistant.py`, guards on the assistant name being set, prints an enable hint when it is not, and never aborts the deploy on failure (warn only, like the Vector Search step).
- [x] **Step 2: Run** — expect FAIL.
- [x] **Step 3: Add the block**, capturing the printed endpoint/id and writing them back into the local settings profile with the existing write-back helper style.
- [x] **Step 4: Run tests** — expect PASS.
- [x] **Step 5: Commit.**

---

### Task 7: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `docs/solution-accelerator/DEPLOY.md`, `CHANGELOG.md`
- Test: existing docs tests (`tests/docs/`) must stay green.

**Interfaces:** none.

- [x] **Step 1: Write the architecture section** — what the KA is, why it is opt-in, why it is not `retrieve_similar_cases`, why no `ai_parse_document` code exists here, and that MAS is the later composition layer.
- [x] **Step 2: Add the deploy note** — the two config keys, what the first run does, and how to point it at a real document volume instead of the shipped corpus.
- [x] **Step 3: Add the CHANGELOG entry** in the repo's existing voice.
- [x] **Step 4: Run** `.venv/bin/python -m pytest tests/docs -q` and the full suite; expect PASS.
- [x] **Step 5: Commit.**

---

## Deliberately not in this plan

- **Film Chapter 5 copy.** `film/`, `docs/media/prodline_copilot_deep_dive*` and the VO manifest are being edited in a parallel session (the video work). Flipping the "not built yet" copy there needs regenerated audio, so it belongs to that session once this lands.
- **Eval cases.** `evaluations/judges.py` gates `RetrievalRelevance` / `RetrievalSufficiency` on a retrieval span shape we still do not emit; adding seed questions is a follow-up once the tool has run live.
- **Live smoke.** Creating the assistant in a workspace bills an agent endpoint, so it needs an explicit go-ahead.
