# ProdLine CoPilot: Architecture & Technical Reference

Deep-dive reference for ProdLine CoPilot. For *what it is* and *how to deploy*, start with the [README](../README.md).

> **Deployment model:** unlike the notebook-driven `RUNME.py` / `_resources`
> blueprint used by some Databricks industry-solutions accelerators, this project
> deploys via **Databricks Asset Bundles** (`databricks bundle` + `scripts/deploy.sh`).
> There is intentionally no `RUNME.py`. The `notebooks/00-07` files are a
> **step-by-step reference mirror** of what `deploy.sh` does (for reading/learning
> in the workspace), the single **executable** path is `./scripts/deploy.sh`, which
> is idempotent and self-provisioning.

## Architecture

Aligned with the Databricks reference architecture for Industrial AI, GenAI agents, and Databricks Apps, from factory-floor sources through medallion transform, serving, and the compound-AI application:

![ProdLine CoPilot reference architecture](media/architecture-high-level.png)

<details>
<summary>Text view (application runtime layers)</summary>

```
┌─────────────────────────────────────────────────────────────┐
│  React Frontend (AppKit)                                    │
│  KPIStrip · ProcessFlowSchematic · EventFeed                │
│  InventoryPanel · IncidentPanel · ChatPanel · ActionPanel   │
│  ←── SSE (~1 s, Lakebase playback) ──→                     │
├─────────────────────────────────────────────────────────────┤
│  Express Proxy (server.ts)                                  │
│  /api/serving/stream  /api/agent  /api/sim/*                │
├─────────────────────────────────────────────────────────────┤
│  FastAPI Agent Server (start_server.py)                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  LangGraph Agent (8 specialists)                      │  │
│  │  context_builder → live state (Lakebase ai_serving.*) │  │
│  │  tools: per-specialist (see below)                   │  │
│  │  memory: Lakebase conversations                      │  │
│  └───────────────────────────────────────────────────────┘  │
│  /serving/stream + /sim/* read/command Lakebase ai_serving.*│
├─────────────────────────────────────────────────────────────┤
│  Producer job (cascade.py · incidents.py · inventory)      │
│  → Zerobus → bronze Delta → streaming_quality_sync         │
│  → Lakebase ai_serving.*  (the sole writer of live state)  │
├─────────────────────────────────────────────────────────────┤
│  Databricks Platform                                        │
│  Unity Catalog · SQL Warehouse · Model Serving · MLflow     │
│  Lakebase · AI Search · Mosaic AI                       │
└─────────────────────────────────────────────────────────────┘
```

</details>

## How the Cascade Engine Works

The line is a sequence of machines connected by finite buffers. Each tick:

1. **Reset** derived statuses (starved/blocked → running)
2. **Attenuate** effective capacity based on buffer fill levels
3. **Forward pass**: compute production flow per machine
4. **Buffer update**: net in/out per buffer
5. **Status pass**: derive `speed_pct`, detect starved/blocked

When a machine stops, downstream machines keep running while their input buffers have headroom. As buffers drain below 60 %, machines slow proportionally. Recovery reverses the process as buffers refill.

The tick loop is **deterministic**: no randomness in `cascade_tick`. Sensor-level jitter (±1.5 % throughput, ±1.0 speed) is added only in `snapshot_to_dicts` at emission time so the cascade's flow accounting stays exact and unit-testable.

## Tech Stack

| Layer             | Technology                                                                                                        |
| ----------------- | ----------------------------------------------------------------------------------------------------------------- |
| **Frontend**      | React 19, TypeScript, ECharts, Databricks AppKit, SSE                                                             |
| **Proxy**         | Express, Node.js 22, AppKit server plugin                                                                         |
| **Agent Server**  | FastAPI, LangGraph, ChatDatabricks — one model per role across six vendors (see Specialists below)                |
| **Simulation**    | Producer job: cascade.py (pure, deterministic) → Zerobus → Lakebase                                               |
| **Data**          | Unity Catalog, Delta Lake, SQL Warehouse                                                                          |
| **Memory**        | Lakebase (PostgreSQL 17) for conversations                                                                        |
| **Observability** | MLflow tracing, agent evaluation                                                                                  |
| **Deployment**    | Databricks Asset Bundles (DABs), Databricks Apps                                                                  |

## GenAI Specialist Agents

The orchestrator (`agent_server/agent.py`) routes each query to one of eight LangGraph ReAct agents. When an incident is active, routing is deterministic (via `INCIDENT_TO_SPECIALIST`). Otherwise an LLM classification call with `max_tokens=50` picks the specialist. All eight are built with `langchain.agents.create_agent` and run with a `recursion_limit` of 16 (`SPECIALIST_RECURSION_LIMIT`, env-overridable). LangGraph counts super-steps and one ReAct round costs two, so 16 allows seven rounds — enough for a specialist to recover from a query that names a column which does not exist, which the previous value of 10 was not.

Each role runs on the model that suits its work rather than on one shared endpoint. All of them are Databricks Foundation Model APIs, so six vendors sit behind one API, one set of Unity Catalog permissions and one bill — swapping a vendor is a config line, not an integration. `agent_server/model_roster.py` holds the assignments and the reasoning; the About tab renders what is actually resolved in your workspace.

| Specialist              | Model                        | Vendor      | Tools                                                                                                                  |
| ----------------------- | ---------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------- |
| `downtime_analyst`      | Claude Sonnet 5              | Anthropic   | sql_query, governed_asset_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies, query_plant_docs |
| `quality_specialist`    | GLM-5.2                      | Zhipu AI    | sql_query, governed_quality_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies, quality_risk_assessment, query_plant_docs |
| `supply_chain_advisor`  | Llama 4 Maverick             | Meta        | sql_query, governed_inventory_query, load_plan_and_constraints, calculator, retrieve_similar_cases                     |
| `oee_coach`             | GPT-5.4                      | OpenAI      | sql_query, genie_query, governed_production_query, calculator, load_plan_and_constraints, oee_loss_analysis, production_forecast |
| `schedule_optimizer`    | GPT-OSS 120B                 | open weights| sql_query, governed_production_query, load_plan_and_constraints, calculator, schedule_recovery, production_forecast, stochastic_schedule_recovery, query_plant_docs |
| `maintenance_predictor` | Qwen 3.5 122B                | Alibaba     | sql_query, governed_asset_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies, estimate_rul, query_plant_docs |
| `strategic_advisor`     | **Claude Opus 5**            | Anthropic   | sql_query, genie_query, governed_production_query, calculator, load_plan_and_constraints, retrieve_similar_cases, multi_shift_plan |
| `shift_briefing`        | Claude Haiku 4.5             | Anthropic   | sql_query, genie_query, calculator, load_plan_and_constraints, retrieve_similar_cases                                  |
| intent router           | Gemma 3 12B                  | Google      | — (single-shot classification, no tools)                                                                               |
| general / synthesis     | GPT-5 mini                   | OpenAI      | — (no tools)                                                                                                           |

**One constraint shaped these picks, and it was found by testing rather than reading spec sheets.** GPT-5.5 and the GPT-5.6 family refuse function tools on chat-completions unless reasoning effort is set to `none`, and GPT-5.5-pro is Responses-API only — so the strongest *usable* OpenAI model here is GPT-5.4, not the newest one. That is a vendor capability decision no abstraction can paper over.

### Governed domains: one Genie space per schema

The governed manufacturing model is organised into four schemas, and each one is a separate curated Genie space. A specialist is handed the space for its own domain and no other:

| Governed domain | Entities | Genie tool | Specialists |
| --------------- | -------- | ---------- | ----------- |
| `asset` | equipment_register, location, pm_schedule, failure_record | `governed_asset_query` | Maintenance Predictor, Downtime Analyst |
| `quality` | inspection_result, ncr | `governed_quality_query` | Quality Specialist |
| `inventory` | material_master, stock_balance | `governed_inventory_query` | Supply Chain Advisor |
| `production` | production_line, work_center, schedule, run | `governed_production_query` | OEE Coach, Schedule Optimizer, Strategic Advisor |

The split follows the model's own schemas rather than the agent list, which matters for two reasons. Genie accuracy falls off as table count and instruction length grow, so a two-table Quality space with its own examples answers more reliably than a twelve-table space that also has to explain inventory reorder rules. And because the boundary is a Unity Catalog schema, per-domain grants are a real permission story — the Quality Specialist can be given quality without inventory.

`scripts/genie_space_config_mvm.py` is the single source of truth for all four governed spaces. `--domain <name>` filters the tables to that schema, keeps only the instruction facts and join rules whose every side is in scope, and drops any example SQL that reaches outside it. Cross-checked by tests: an example that joins a table the space cannot see would teach Genie broken SQL, and the tool's advertised entity list is asserted against the tables actually registered.

These tools **augment** `sql_query`; they do not replace it. Genie is poll-based and nondeterministic, so it earns its place on open-ended and governed-phrasing questions while the fixed analytical paths a specialist runs every shift stay on direct SQL. Shift Briefing has no governed domain at all — its subject is the shift that just happened, so it reads the **line ops** space.

Line ops is the curated Genie space over the live streaming tables, and it is deliberately the opposite shape from a governed domain: 14 tables, against two to four in each governed space and 12 across all four combined. That is not the narrow-scope argument being abandoned. The governed model splits along its own schemas because that is where grants live, so each split produces a boundary worth permissioning; the live tables are a single schema behind a free-form "ask anything about this shift" surface, where there is no boundary to split on and no separate grant to make. Naming it for its scope rather than calling it "live" also avoids implying that the other five specialists cannot see the current shift — all eight read the live tables, with direct SQL.

A domain whose space is unprovisioned yields **no tool**, and the specialist degrades to `sql_query`. Handing a model a tool that always errors burns a turn per call; `GET /ops/topology` reports what is actually wired so the System Map draws the deployment rather than the design.

#### Cross-domain questions go to SQL, not to a second Genie space

A specialist gets one domain, never two. That looks restrictive until you notice that Genie spaces **cannot join across each other**, while the four governed schemas share a single catalog — so `sql_query` can join them in one query and Genie cannot join them at all. Adding a second space would buy two round trips and manual stitching where one SQL statement does the job.

The route only exists if the prompt says the schemas are there, which is what `GOVERNED_MODEL_NOTE` in `agent_server/prompts/_shared.py` does: it names all four schemas, tells the specialist to fully qualify them, and carries the honesty caveats (curated column subset, incident facts empty at steady state, prefer live tables for real-time). Every specialist that owns a governed domain carries it, with one deliberate exception:

- **Shift Briefing** owns no governed domain and gets no note. Its subject is the shift that just happened; audit-grade rows would cost it the freshness that is its entire value.
- **Schedule Optimizer** owns the production domain but is still excluded. It is the specialist a user reaches mid-disruption, its prompt caps it at three tool calls, and its constraints already arrive deterministically — `load_plan_and_constraints` reads `line_constraints`, and the `schedule_recovery` MILP auto-loads speed limits and CIP windows from that same table. Four more schemas would buy exploration on the one specialist whose value is answering fast.

Both the inclusion and the exclusion are pinned by tests, so neither can drift into the other by accident.

#### A Genie space is an agent, so which layer owns what

Genie is no longer a text-to-SQL surface. A Genie Agent plans with chain-of-thought and then chooses between generating SQL, running a **parameterized verified query**, and calling a **Unity Catalog SQL function** — the last two are *trusted assets*, and an answer produced from one carries a "verified" tag. Genie invokes a registered function by matching the question against the function's comment; it [cannot see or modify the function's SQL](https://docs.databricks.com/aws/en/genie-agents/tune-quality). Genie also keeps a **knowledge store** of table descriptions, join relationships and SQL expressions (200 snippets per agent), which sharpens its grasp of the schema.

Two things this does *not* mean:

- **A Knowledge Assistant cannot be attached to a Genie space.** They are siblings, and they compose one level up: the Agent Bricks [Supervisor Agent](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor) takes Genie Agents, KA endpoints, UC functions and MCP servers as sub-agents. Genie's *knowledge store* is schema metadata, not document retrieval, and the similar name is easy to trip over.
- **Genie does not become cross-modal.** It gets better at structured data. Unstructured knowledge stays a separate agent.

Because a Genie space plans, this app runs two planners in series: a specialist's LangGraph tool loop picks among `sql_query`, its optimizers and its governed Genie space, and Genie then picks how to answer. That is workable, but only if each layer owns a distinct kind of logic. The boundary:

| Layer | Owns | Because |
| --- | --- | --- |
| Unity Catalog function (Genie trusted asset) | Deterministic business definitions expressible in SQL that must be identical everywhere — OEE formula, stock cover days, PM adherence | One definition serves both Genie and `sql_query`; two copies drift |
| App optimizer (`agent_server/optimizers/`) | Solver and simulation work that is not SQL — MILP recovery, Monte Carlo forecast, Bayesian CPT quality risk | No SQL expression exists for it |
| App orchestrator + specialists | Cross-domain routing, live streaming data, operator-facing action proposals (copy-to-handoff; approval and write-back are unimplemented) | No equivalent inside a Genie space |

Today the four domain spaces carry instructions, example SQL and tables, and **no trusted assets** — so the overlap is theoretical rather than live. Before adopting them, note one integration cost: the chat's evidence trail extracts Genie's generated SQL, and a function-answered response deliberately withholds it, surfacing the function's comment instead. `agent_server/tool_evidence.py` would then report a row count with no query, weakening exactly the surface it exists to provide.

### Why the chat shows evidence, not reasoning

The chat's "How this decision was made" expander shows what each tool *did*: the governed domain that answered, the tables touched, the row count, and the query itself — Genie's generated SQL, or the SQL the model wrote for `sql_query`. `agent_server/tool_evidence.py` lifts this out of the LangGraph message stream, where it was previously discarded at the agent boundary even though the tools already returned it.

It would have been easier to show the model's own reasoning instead — some models return it, and `llm_output` already has to parse those blocks out of the answer. We don't, for three reasons:

- **It isn't checkable.** Chain-of-thought is [frequently unfaithful](https://www.anthropic.com/research/reasoning-models-dont-say-think) to the computation that produced the answer; Anthropic measured Claude 3.7 Sonnet acknowledging a hint it demonstrably used only 25% of the time. A plausible rationale that doesn't track what the model did manufactures confidence. A query a line manager can re-run earns it.
- **The vendor says not to.** OpenAI's [gpt-oss guidance](https://developers.openai.com/cookbook/articles/gpt-oss/handle-raw-cot) is explicit that raw CoT should not be shown to end users: it is deliberately left unsupervised, can contain hallucinated content, and can leak instructions the model was told to keep out of its output — which for us means the governed-domain routing rules in the specialist prompts.
- **It wouldn't cover the app.** Only two of the ten roster models emit reasoning blocks. A confidence surface that is blank for eight specialists reads as broken, and in a multi-vendor demo it implies the other vendors are less transparent when they simply don't ship the blocks. The evidence trail reads the same whichever vendor answered.

Row counts are reported honestly. Both tools truncate — Genie keeps 20 rows, `sql_query` appends `LIMIT 500` when the model omits one — so a count landing exactly on the cap renders as `20+` rather than being presented as the size of the result set.

### What streams, and what is only a draft

`/invocations/stream` emits five kinds of frame: a `snapshot` within about a
second so the user sees something immediately, a `status` naming the specialist
once routing decides, `tool_update` per tool call, `token` fragments as the
model writes, and finally a single `answer` carrying the authoritative text
plus the tool trace, reasoning steps and trace URL.

For a long time only the middle of that list was true. The specialist's output
was accumulated in the worker thread and released whole, so what streamed was
the tool trace: chips animated, then a finished answer appeared in one jump.
The ReAct loop is now consumed in both `updates` and `messages` mode — the
first builds the tool trace, the second carries tokens — which adds no latency,
because those tokens were already crossing the wire and were simply being held.

Three details are easy to get wrong and each was verified against a live
endpoint rather than assumed:

- **The tools node emits on the same stream.** Forwarding everything types raw
  tool JSON into the user's answer.
- **Tool-call arguments arrive as fragments**, on chunks with empty content and
  a populated `tool_call_chunks`. They are not prose.
- **A preamble before a tool call is not the answer.** ReAct models think aloud,
  call a tool, then write. That preamble is superseded server-side, so the
  stream sends a `token_reset` when a tool result lands and the client drops
  the draft; otherwise the visible text would be the model's thinking with the
  real answer glued on the end.

The streamed text stays a **draft** on the client, rendered as plain text in the
thinking indicator and discarded when `answer` arrives. It has to be: a later
reasoning round can replace it outright, and the synthesis pass rewrites short
specialist answers into the sectioned format the message bubble parses. Parsing
a half-written document would show sections appearing and rearranging as tokens
land.

A silent run gets a keep-alive. A question that calls no tools produces no
frames at all between `status` and `answer` — one model call, potentially the
full specialist timeout (`SPECIALIST_TIMEOUT_SECONDS`, 150 s by default) — and
the app sits behind a proxy entitled to
drop an idle connection, which surfaces to the user as a spinner that never
resolves. The drain loop already woke once a second, so it now emits an SSE
comment on each idle tick. Comments are skipped by the client's reader and are
excluded from the token accounting, along with `token` frames, whose text
arrives a second time in `answer`.

### The integration layer, and what it costs

Swapping vendors is a config line because every role is built the same way — `ChatDatabricks(endpoint=..., max_tokens=...)` into `create_agent`, with no vendor branching anywhere in the app. Two defects in `databricks-langchain` leak through that interface, and both are worked around here rather than in the specialists:

| Defect | Effect | Where it's handled |
|---|---|---|
| `_convert_dict_to_message` runs `json.dumps` on non-string content | Reasoning models return their scratchpad as a JSON string, so `isinstance(content, str)` is `True` and `.text` returns the raw JSON | `agent_server/llm_output.py`, on read |
| `_get_tool_calls_from_ai_message` rebuilds tool calls from a fixed `id`/`type`/`function` shape | Gemini's `thoughtSignature` is dropped, so turn two of any tool loop is rejected | `agent_server/vendor_compat.py`, on write |

Both are confirmed against the live API: raw OpenAI-compatible calls return structured content as a `list` and preserve the signature, so the transport is fine and the adapter is not. With the shim in place both Gemini endpoints complete multi-tool ReAct loops correctly, which is why Gemini is eligible for any seat via `model_roles`. Each workaround is pinned by tests that fail if upstream changes shape *or* fixes the bug, so they can be deleted when that happens.

**A third leak is nobody's defect — the endpoints disagree about tool schemas.** `calculator` takes an open `params` dict because it reads different keys per operation, and pydantic renders that as `additionalProperties: true`. Qwen 3.5 122B and Llama 4 Maverick reject any request carrying it (`the "additionalProperties" keyword must be False or not specified`); the other eight endpoints in the roster accept it. Because `calculator` is bound by almost every specialist, this failed *before the first token* on both those roles, and the app fell through to the general handler with "encountered an error" — a vendor dark, with nothing on the surface to say why. `agent_server/tool_schema_compat.py` strips the keyword at bind time, which is a no-op in JSON Schema terms since an absent `additionalProperties` already means the same thing. A literal `false`, and a schema-valued one, are left alone. The guard test asserts that no shipped tool reaches a model with an open dict, because the original failure was invisible from outside the logs.

**Gemini was then tried for a specialist seat and did not get one.** `scripts/dev/compare_shift_briefing_models.py` scores a model against the shift_briefing prompt's format contract over repeated runs. Averaged compliance was a tie (Haiku 4.5 88%, Gemini 3.5 Flash 89%), but the failures differ in kind: Gemini broke the exact `##` headings the UI parses on in 3 of 8 runs, where Haiku broke none and erred only cosmetically (unbolded bullet lead-ins), at roughly half the latency. An average hides that; a briefing that renders wrong 37% of the time is worse than one that renders plainly. Google therefore keeps the router seat on merit rather than by necessity — the constraint is gone, the evidence still points the same way.

**Not every workspace serves all six vendors.** Each role declares a fallback tier (`model_specialist_endpoint` / `model_advanced_endpoint`), and a role whose preferred model is missing degrades to it and logs the substitution instead of failing. Every deploy prints a coverage table naming any role that fell back; run it on its own with:

```bash
python3 scripts/preflight.py dev --models
```

To choose differently, set `model_roles` in `config/project-settings.yaml` — a role you leave out keeps its roster default, and an unknown role name fails the render rather than silently doing nothing:

```yaml
model_roles:
  downtime_analyst: 'databricks-claude-sonnet-5'
  oee_coach: 'databricks-gpt-5-4'
```

`model_multi_vendor: false` disables the roster entirely and runs every role on the two tiers.

Action chips shown alongside each agent response are a static lookup keyed on specialist name (`_extract_actions` in `agent_server/agent.py`), they are not generated by the LLM.

### Prompts are versioned in Unity Catalog

Every prompt the app sends is published to the MLflow Prompt Registry as `<catalog>.<schema>.prompt_<slug>`, so a wording change gets a version, an author and a commit message rather than living only in a Python diff. That is what makes it possible to read a shift in answer quality against the wording that was live at the time. `scripts/deploy.sh` publishes on every deploy; run it standalone with:

```bash
python3 scripts/register_prompts.py --catalog <cat> --schema <schema> [--dry-run]
```

Three things about it are worth knowing before you trust what you see in the registry:

- **Whether the registry controls anything is a switch.** Every call site reads `prompt_text(slug)` from `agent_server/prompts/loader.py`, but the loader only consults the registry when `PROMPTS_FROM_REGISTRY` is set; otherwise it returns the constant in `agent_server/prompts/`. Turning it on changes what the model receives, so it belongs on dev first. Resolution happens once at startup — a registry blip cannot change behaviour mid-shift, and a prompt change needs a restart to take effect.
- **A loaded prompt has to earn its place.** The loader falls back to the constant, per prompt, when the registry is unreachable, the version is blank, or the text no longer contains a variable its call site fills. That last check is the important one: the registry stores Jinja `{{var}}` and the app formats with `{var}`, and `str.format` renders an unconverted `{{var}}` as the literal text `{var}` instead of raising — a prompt that is quietly wrong rather than loudly broken.
- **A registered specialist prompt is not the whole system prompt.** Specialists that own a governed domain get `governed_domain_prompt(<domain>)` appended at agent-construction time — 734–792 characters, about 15% of what the model receives — and it is empty when the domain has no Genie space provisioned. The suffix is registered on its own as `prompt_governed_domain_suffix`, and each governed specialist's version carries `governed_domain` and `runtime_suffix` tags, so the full prompt can be reassembled from the registry rather than appearing to be missing from it.
- **Brace syntax differs on each side.** `agent_server/prompts/catalog.py` converts in both directions, but only for variables a prompt declares — a blanket regex would promote the literal `{"shift_id": ...}` JSON examples inside three system prompts into variables no call site fills.

The catalogue is the guard against prompts going unversioned, in two directions. A prompt constant absent from it would never be registered, and a system prompt built from string literals anywhere outside `agent_server/prompts/` is a prompt no registry can see; `tests/agent_server/test_prompt_catalog.py` fails on either.

### Similar-case retrieval runs on AI Search, which the deploy starts and does not wait for

`retrieve_similar_cases` answers "have we seen this before" by embedding the description an operator gave and searching past resolved events. It is wired into six of the eight specialists, and it is the one tool whose infrastructure takes longer to build than the rest of the deploy:

```bash
python3 scripts/provision_vector_search.py --catalog <cat> --schema <schema> \
    --warehouse-id <id> --wait [--dry-run]
```

You do not normally run it by hand. `vector_search_endpoint` ships set, and `scripts/deploy.sh` provisions the endpoint and index, then re-syncs the case library on every later deploy. What is worth knowing is the timing: **creating an endpoint takes 15–40 minutes** on Databricks' side, against seconds for everything else in the deploy. So the deploy starts the endpoint and moves on, and the index is built by the next deploy — by which time the endpoint is online and that run costs seconds. Until the index exists the tool falls back to keyword matching over the same corpus, which is a real answer rather than an error. `VS_WAIT=1` blocks for the endpoint instead, for the deploy that has to come out of it complete; the script says which kind of run it is before it starts waiting, because a silent 40-minute wait looks like a hang and gets killed.

The endpoint bills by the hour whether or not anything queries it, so it is one of the two capabilities `MINIMAL=1` skips and `scripts/cleanup.sh` removes. `./scripts/deploy.sh <env> --capabilities` prints the whole inventory — what is on, what each one costs, what the app does without it — without deploying anything.

Everything above describes infrastructure. Whether the app can *use* it is a separate question, and for a long time the answer was no, invisibly. Two independent defects had to be fixed before a single semantic query ran in the deployed app:

- **The client could not authenticate.** `VectorSearchClient()` with no arguments resolves credentials through MLflow, which wants `DATABRICKS_TOKEN` — a personal access token. A Databricks App is never issued one; it gets OAuth client credentials. Every call raised, and the `except` turned it into a keyword fallback. The app now passes its service principal explicitly, the same identity every other client here uses.
- **The response parser read a key that does not exist.** Column names arrive under `manifest.columns`; `result` carries only `row_count` and `data_array`. Reading them from `result` raised `KeyError` on every response — again caught, again silently downgraded to keyword search. The unit test had encoded the invented shape, so the suite reported the semantic path as covered and passing.

Both failures were survivable-looking by construction: the fallback returns sensible, well-formed results, so nothing downstream looked wrong while the endpoint sat online and billed. That is the argument for the `retrieval` field on every returned row — `vector_search` or `keyword` — and for the warning the fallback now logs.

The shape follows current Databricks AI Search guidance (renamed from Vector Search in June 2026; the `vector_search()` SQL function, the old API prefix and our own `VS_ENDPOINT` / `vector_search_endpoint` identifiers all still carry the former name). A **standard** endpoint is correct here — the guidance points to storage-optimized only at 10M+ vectors, where 250–500ms query latency is an acceptable trade; a standard endpoint answers in 20–50ms and this corpus is 27 rows. A **Delta Sync index with managed embeddings** is the recommended option whenever the corpus already lives in a Delta table, which is why the fallback to a Direct Access index does not apply: Direct Access cannot use managed embeddings at all and would put embedding on us at both write and query time. Queries run as `query_type="HYBRID"` rather than pure ANN, because the guidance calls for hybrid "where domain-specific keywords are important" and an operator typing "capper misfeed" is exactly that case. Only the columns the tool reads are synced, and sync is triggered rather than continuous.

Two details make the index buildable at all:

- **It indexes a purpose-made table, not the event log.** The tool's whole value is `resolution_minutes`, `outcome_oee_impact` and `outcome_description` — what fixed it, how long it took, what it cost. Those are written to `event_log_bronze` and dropped by the quality gate before they reach `gold_history_event_log`, so an index over gold, the obvious source, fails on the column list. `event_case_library` joins the gated event back to its outcome fields, is written by `MERGE` so Change Data Feed stays coherent, and carries a `case_text` column concatenating description, root cause and corrective action — a Delta Sync index embeds exactly one column, and relevance lives across all three.
- **The column list is a contract split across two files.** AI Search fails a query outright if you request a column the index does not carry; there is no partial result. `tests/scripts/test_provision_vector_search.py` asserts the tool's list is a subset of what the provisioner syncs.

### Document retrieval is a Knowledge Assistant, and it is the other half of memory

`query_plant_docs` answers "what does the procedure say" against the plant's engineering documents — SOPs, specifications and lookup tables, the line process narrative, a deviation report, a certificate of analysis, a batch record, a maintenance manual extract. It is wired into the four specialists whose answers get quoted back at a supervisor: `downtime_analyst`, `quality_specialist`, `maintenance_predictor` and `schedule_optimizer`.

The chat panel offers three document questions as suggestion chips, but only when `/api/ops/topology` reports `plant_docs` true — the assistant can be switched off, and in a deployment without one those chips would answer every click with "the documents could not be read" and read as a bug rather than as a feature nobody enabled. The flag is read from the app's own env at request time, so provisioning or tearing the assistant down changes the chips without rebuilding the client.

`schedule_optimizer` was added after the deployed app was measured getting it wrong. Asked how long a 0.5 L to 1.0 L changeover takes, it answered "≈20 min (per matrix)" at HIGH confidence; the matrix says 45 minutes for the filler and 55 for the line critical path, and the role had no way to read it. Changeover minutes, cleaning-group crossings and planned maintenance windows are written down and are nowhere in telemetry, so a role that schedules around them needs the documents even though the rest of its work is arithmetic.

It exists as a separate tool because it is a separate corpus. `retrieve_similar_cases` searches resolved *events*; this searches *documents*. That distinction is load-bearing enough that the tool returns an error when the assistant is unavailable rather than falling back to anything: the event library answers in fluent, plausible prose, so a silent substitution would read as a correct answer to a question about an isolation sequence. Both tool docstring and error text tell the model to say the documents could not be read instead of answering from memory.

Retrieval itself is not ours. `scripts/provision_knowledge_assistant.py` creates an [Agent Bricks Knowledge Assistant](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/knowledge-assistant), points it at a Unity Catalog volume of documents, and Agent Bricks owns parsing, chunking, embedding, retrieval, grounding and citations behind one agent serving endpoint. That is why **you will find no `ai_parse_document` call in this repo** — the parse happens inside the managed service, and adding our own would mean maintaining a second, worse copy of it. The app's whole share of the work is one POST to `/serving-endpoints/{name}/invocations`, which is also why the tool is 200 lines rather than a pipeline.

Three things about the setup are worth knowing:

- **It ships on, and it bills.** `knowledge_assistant_name` ships set and `scripts/deploy.sh` provisions the assistant (`knowledge_docs_volume` names the volume under `uc_catalog.uc_schema`, default `plant_docs`); blank the name and the tool reports itself unconfigured. The name differs between dev and prod by default, because the assistant is looked up by display name and one name is one assistant; giving both profiles the same name *and* the same `knowledge_docs_catalog` / `knowledge_docs_schema` is the supported way to run a single shared one, which is a second endpoint's hourly bill saved and prod's teardown made everyone's problem — `scripts/cleanup.sh` will not delete an assistant another profile names without `--purge-shared`. An assistant serves from an agent endpoint that bills while it is online, so it is one of the two capabilities `MINIMAL=1` skips, and `scripts/cleanup.sh` deletes it and clears both written-back keys — a teardown that dropped the corpus volume and left the assistant running would leave a billing endpoint answering from an empty knowledge base.
- **The corpus is in Git, not in the workspace.** `data/plant_docs/*.md` is checked in and uploaded to the volume on every provision run, so a demo's answers are reproducible from a clone and a document change is reviewable as a diff — markdown rather than PDF for the same reason. Editing a document is not enough on its own: a files-based knowledge source does not notice changed files, which is why the script asks for a sync every run. `tests/data/test_the_plant_doc_corpus_is_answerable.py` holds the corpus to the machine vocabulary the rest of the app uses, because a document naming a machine the line does not have yields a confidently cited answer about equipment that does not exist. To answer from real documents instead, pass `--corpus <dir>` at your own document directory; the script refuses to run against one with nothing ingestible in it rather than building an assistant that knows nothing.
- **The endpoint answers before it knows anything, which is the state to plan for.** Measured against a live workspace: the assistant returns an `endpoint_name` and serves requests within seconds of creation, while its `state` is still `CREATING` and its index is still building — for well over five minutes. So the app gets wired up on the first deploy and, until indexing finishes, every question comes back as a fluent "I have no documents to answer from". That is the right answer and it is the corpus instructions doing their job, but it is not a working demo, so check `state` is `ACTIVE` before showing anyone. `provision_knowledge_assistant.py` prints the endpoint and assistant id, deploy writes them back to the local settings profile as `knowledge_assistant_endpoint` / `knowledge_assistant_id`, and `render_app_yaml.py` turns them into `DOCS_KA_ENDPOINT` / `DOCS_KA_ID`. The app was already deployed by that point, so the deploy re-renders and redeploys when the endpoint *changes* — the same second pass the Genie space ids need.

  The same delay is why the example questions are registered on a *later* run. While the state is `CREATING`, every call to the examples API is rejected with `TEMPORARILY_UNAVAILABLE` because the internal index is not there yet, so the script does not ask: it says how many examples are waiting and registers them once the assistant is active. Retrying inside the deploy was tried and measured as pure dead time.

**Multi-Agent Supervisor is the composition layer above this, later.** Agent Bricks MAS would let the KA sit alongside the Genie spaces under a managed supervisor instead of under our orchestrator. Nothing here blocks that; the KA is deliberately reachable as an endpoint, so it is already a component a supervisor could be handed.

## Incident Scenarios

The following scenario IDs are recognised by the routing layer (`INCIDENT_TO_SPECIALIST` in `agent_server/agent.py`). Each is deterministically routed to the correct specialist without an LLM classification call.

| Scenario              | Routed to             |
| --------------------- | --------------------- |
| `machine_breakdown`   | downtime_analyst      |
| `sensor_failure`      | downtime_analyst      |
| `safety_incident`     | downtime_analyst      |
| `zone_lockout`        | downtime_analyst      |
| `speed_loss`          | oee_coach             |
| `minor_stops`         | oee_coach             |
| `quality_deviation`   | quality_specialist    |
| `contamination`       | quality_specialist    |
| `material_shortage`   | supply_chain_advisor  |
| `supply_delay`        | supply_chain_advisor  |
| `vibration_anomaly`   | maintenance_predictor |
| `maintenance_overdue` | maintenance_predictor |
| `changeover_delay`    | schedule_optimizer    |
| `rush_order`          | schedule_optimizer    |

## Project Structure

```
├── agent_server/            # FastAPI agent server
│   ├── agent.py             # Orchestrator + streaming
│   ├── start_server.py      # FastAPI app, SSE endpoints, sim control
│   ├── context_builder.py   # Parallel SQL context builder
│   ├── sim/                 # Incident command store (Lakebase) + recovery lookup
│   │   ├── commands.py      # scenario_commands CRUD (ai_serving.*)
│   │   └── recovery.py      # map machine_id → active command_ids
│   ├── specialists/         # 8 LangGraph ReAct agents
│   ├── prompts/             # Specialist system prompts
│   ├── tools/               # sql_query, calculator, anomaly_detector, and more
│   └── memory/              # Lakebase conversation store
├── src/streaming_sim/       # Core simulation logic
│   ├── cascade.py           # Buffer-based cascade engine (pure)
│   └── incidents.py         # Incident → fault mapping
├── client/                  # React frontend
│   └── src/
│       ├── App.tsx           # Main app, KPI calculation, sim controls
│       ├── components/       # UI panels with data source badges
│       └── lib/              # useSSELiveData, useIncidentApi hooks
├── server/                  # Express proxy (server.ts)
├── config/queries/          # AppKit parameterised SQL (see note below)
├── scripts/                 # Deploy, bootstrap, init scripts
│   └── deploy.sh                 # ./scripts/deploy.sh dev|prod
├── tests/                   # Unit + integration tests
│   └── streaming_sim/       # Cascade and incident tests
├── docs/solution-accelerator/  # Deployment + getting-started guides
├── databricks.yml           # Asset Bundle (includes config/bundle-targets.yaml)
├── app.yaml                 # Prod app env (line_manager_prod)
└── app.dev.yaml             # Dev app env (line_manager_dev), swapped over app.yaml by deploy.sh
```

## Additional Server Endpoints

Beyond the agent and simulation endpoints, `agent_server/start_server.py` exposes:

- **Ops control plane** (`/ops/cost-summary`, `/ops/cost-live`, `/ops/dashboard-info`, `/ops/control/state`, `/ops/control/action`), pauses, resumes, and restarts the Databricks producer job and **`streaming_quality_sync`** job; surfaces live token/DBU cost metrics.
- **`/ops/quality-metrics`**: the six quality-gate aggregates the Data Integrity tab draws — pass rate, per-table health, hourly throughput, status mix, reject reasons, QC failures. The tab embedded the AI/BI dashboard for these until an iframe proved to be the most fragile part of the app: it needed the dashboard published with `embed_credentials` (running its queries as the *publisher*, not the viewer), CAN_READ for the app SP on the dashboard and its parent folder, and a browser willing to load a cross-origin frame with cookies. The SQL is the dashboard's own, copied verbatim from `dashboards/data_quality.lvdash.json` and pinned to it by `tests/agent_server/test_quality_metrics.py`, so the two surfaces cannot answer the same question differently. The dashboard is still deployed and linked from the tab for exploring; `scripts/ensure_dashboard_access.py` publishes it on the viewer's own credentials and grants CAN_READ.
- **`/sim/warmup`**: phased startup probe that sequentially checks SQL connectivity, `scenario_commands` table availability, Lakebase health, and engine tick status. Useful for canary validation after a deploy.
- **`/ops/models`** and **`/ops/topology`** (proxied as `/api/ops/models` and `/api/ops/topology`): what actually answers here. The first reports the resolved model per role including any fallback; the second reports which governed domain Genie spaces are provisioned and which specialists own them. Both are read by the About tab's System Map and the Multi-AI tab, so those views describe the deployment rather than the design.

## OEE Calculation

OEE uses industry baselines for a top-quartile F&B line:

| Factor          | Baseline  | Rationale                                            |
| --------------- | --------- | ---------------------------------------------------- |
| Availability    | 88 %      | Changeovers ~4 %, CIP ~3 %, planned maintenance ~2 % |
| Performance     | 95 %      | Minor stops ~3 %, reduced speed ~2 %                 |
| Quality         | 98.5 %    | Start-up rejects ~1 %, rework ~0.5 %                 |
| **Healthy OEE** | **~82 %** | Top-quartile benchmark for F&B packaging             |

Incident faults reduce availability and performance in real time; quality is held constant for the demo.
