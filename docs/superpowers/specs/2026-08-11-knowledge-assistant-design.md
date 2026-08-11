# ProdLine CoPilot — Knowledge Assistant (Chapter 5) Design

Status: approved for implementation planning  
Date: 2026-08-11  
Owner: mohammad.khelghati@databricks.com

## Problem

Deep-dive Chapter 5 (“engineering documents”) describes a Knowledge Assistant
over plant docs (P&IDs, SOPs, deviations, CoAs). Today that chapter is honest
about a gap: there is no document corpus and no KA wired into the accelerator.
Event retrieval (`retrieve_similar_cases` / optional Vector Search) is shipped;
document Q&A is not.

## Goal

Ship an **opt-in, KA-first** path that:

1. Provisions a synthetic plant-doc corpus in a UC volume and an Agent Bricks
   Knowledge Assistant against that volume.
2. Exposes a thin LangChain tool so the running copilot can answer doc
   questions with citations from chat.
3. Leaves Multi-Agent Supervisor (MAS) composition for a later slice.

Showcase KA. Do **not** build an explicit `ai_parse_document` / `ai_extract`
pipeline in the accelerator. KA volume ingestion uses parsing internally;
callers only need PDFs in a volume.

## Non-goals

- Explicit SQL/notebooks calling `ai_parse_document` or `ai_extract`
- Supervisor Agent / MAS wiring Genie + KA
- Dedicated “Ask docs” UI panel (Genie Ask-Line style)
- Replacing or merging with `retrieve_similar_cases` (historical events ≠ plant docs)
- Always-on KA for every deploy

## Approach

**Opt-in deploy + thin tool** (mirrors `vector_search_endpoint`):

```text
config (opt-in)
  → deploy: volume + checked-in synthetic PDFs (+ JSON examples)
  → create/update Knowledge Assistant
  → write tile id / endpoint back to settings
  → render app env
  → LangChain query_plant_docs
  → specialists: maintenance_predictor, quality_specialist, downtime_analyst
```

MAS remains roadmap: Genie and KA stay siblings; composition one level up later.

## Architecture

### Corpus

- ~6–10 synthetic plant documents aligned to demo line / machine names already
  used in the accelerator (SOP, P&ID-style narrative, deviation, CoA,
  changeover, LOTO, etc.).
- **Markdown, not PDF.** KA accepts `txt, pdf, md, ppt/pptx, doc/docx`, so the
  corpus ships as reviewable, diffable `.md` files rather than binaries in git.
  A real deployment points the same config at its own volume of PDFs.
- A single `examples.json` carrying `question` / `guidelines` pairs, registered
  as KA examples at provision time.
- **Checked-in fixtures** under `data/plant_docs/`, uploaded to
  `/Volumes/<catalog>/<schema>/<docs_volume>/` at provision time for
  reproducible deploys.

### Config (`config/project-settings(.local).yaml`, per env)

| Key | Role |
|-----|------|
| `knowledge_assistant_name` | Stable KA display name; empty → feature off |
| `knowledge_assistant_id` | Written back on create (`knowledge-assistants/{id}`) |
| `knowledge_assistant_endpoint` | Agent serving endpoint name, written back on create |
| `knowledge_docs_volume` | Volume name under project catalog/schema |

The REST resource is `knowledge-assistants/{knowledge_assistant_id}`, so the id
key uses that name rather than the Agent Bricks MCP wording “tile id”.

Unset / empty → skip provision; runtime tool reports not configured (same
spirit as empty `vector_search_endpoint`).

### Provision (`scripts/provision_knowledge_assistant.py`)

1. Ensure UC volume exists under the deploy catalog/schema.
2. Sync checked-in corpus into the volume if missing or stale (idempotent).
3. Create or update KA with `volume_path` pointing at the plant docs folder.
4. Wait for a usable endpoint state where practical; if still provisioning,
   log clearly and allow a later re-run / sync.
5. Write `knowledge_assistant_tile_id` (and endpoint name if captured) back into
   local settings.
6. Grant the app service principal whatever is required to query the endpoint.

### Deploy hook (`scripts/deploy.sh`)

- If KA opted in → run provision script.
- Else → one-line skip: how to enable via settings.

### Runtime

- `scripts/render_app_yaml.py` maps settings → app env (e.g. `KA_ENDPOINT`,
  `KA_TILE_ID`). Exact env names chosen to match serving client needs.
- New tool `query_plant_docs(question)` →
  `{ answer, citations: [{ doc, page? }], retrieval: "knowledge_assistant" }`.
- Auth via app workspace client (same pattern as `genie_query`), not end-user
  OBO.
- Attach tool only to: `maintenance_predictor`, `quality_specialist`,
  `downtime_analyst`.
- If env unset: structured “docs assistant not configured” result — do not
  throw in a way that looks like a model failure.

### Chat data flow

1. User asks a docs-only question (LOTO, CoA limits, SOP steps, …).
2. Orchestrator routes to a specialist that has `query_plant_docs`.
3. Tool calls the KA serving endpoint.
4. Specialist grounds the reply on returned citations.

### Error handling

| Case | Behavior |
|------|----------|
| KA not configured | Structured not-configured result; no invented doc content |
| Endpoint provisioning / offline | Clear retry message; no silent empty success |
| Auth / grant failure | Log + structured error |
| No relevant chunks | Pass through KA “not in corpus” style answer |

**Do not** fall through to `retrieve_similar_cases` on KA failure. That corpus
is historical events; a silent fallback would look correct and be wrong.

## Docs / film

Once shipped:

- Update Chapter 5 copy (film VO / deep dive UI) so it is no longer “the one
  thing not built.”
- Note in `docs/ARCHITECTURE.md` and deploy docs: opt-in KA, distinct from
  event Vector Search; KA ≠ Genie knowledge store; MAS is the composition layer
  later.

## Testing

- Unit: unset env, malformed KA response, citation parsing.
- Provision: idempotent create/update / “already exists” path (mirror VS
  provision tests).
- Eval: 2–3 seed questions with citation guidelines; enable
  RetrievalRelevance / RetrievalSufficiency scorers only after the tool emits
  the retrieval span shape judges expect (`evaluations/judges.py` already
  notes this as “after step 4”).
- Manual smoke: one plant-doc question in chat after opt-in deploy.

## Acceptance criteria

1. Opt-in deploy creates volume + KA and writes ids back to settings.
2. With KA on, chat returns a cited plant-doc answer via a specialist tool.
3. With KA off, chat still works; tool reports not configured.
4. KA failures never silently use event-case retrieval.
5. Chapter 5 / architecture docs no longer claim the feature is unbuilt.

## Follow-ups (explicitly later)

- Thin `ai_extract` tables for structured fields Genie/SQL can query.
- Supervisor Agent composing Genie + KA.
- Optional dedicated Ask-docs UI surface.
- Broader specialist attachment (e.g. `strategic_advisor`) if routing needs it.
