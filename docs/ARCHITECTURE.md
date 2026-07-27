# ProdLine CoPilot: Architecture & Technical Reference

Deep-dive reference for ProdLine CoPilot. For *what it is* and *how to deploy*, start with the [README](../README.md).

> **Deployment model:** unlike the notebook-driven `RUNME.py` / `_resources`
> blueprint used by some Databricks industry-solutions accelerators, this project
> deploys via **Databricks Asset Bundles** (`databricks bundle` + `scripts/deploy.sh`).
> There is intentionally no `RUNME.py`. The `notebooks/00-05` files are a
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
│  KPIStrip · LineSchematic · EventFeed · InventoryPanel      │
│  IncidentPanel · ChatPanel · ActionPanel                    │
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
│  Lakebase · Vector Search · Mosaic AI                       │
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
| **Agent Server**  | FastAPI, LangGraph, ChatDatabricks (GPT-5-mini for 6 specialists; full GPT-5 for oee_coach and strategic_advisor) |
| **Simulation**    | Producer job: cascade.py (pure, deterministic) → Zerobus → Lakebase                                               |
| **Data**          | Unity Catalog, Delta Lake, SQL Warehouse                                                                          |
| **Memory**        | Lakebase (PostgreSQL 17) for conversations                                                                        |
| **Observability** | MLflow tracing, agent evaluation                                                                                  |
| **Deployment**    | Databricks Asset Bundles (DABs), Databricks Apps                                                                  |

## GenAI Specialist Agents

The orchestrator (`agent_server/agent.py`) routes each query to one of eight LangGraph ReAct agents. When an incident is active, routing is deterministic (via `INCIDENT_TO_SPECIALIST`). Otherwise an LLM classification call with `max_tokens=50` picks the specialist. All agents use `langgraph.prebuilt.create_react_agent` with a `recursion_limit` of 10.

| Specialist              | Model      | Tools                                                                                                                  |
| ----------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| `downtime_analyst`      | GPT-5-mini | sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies                             |
| `quality_specialist`    | GPT-5-mini | sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies, quality_risk_assessment    |
| `supply_chain_advisor`  | GPT-5-mini | sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases                                               |
| `oee_coach`             | **GPT-5**  | sql_query, genie_query, calculator, load_plan_and_constraints, oee_loss_analysis, production_forecast                  |
| `schedule_optimizer`    | GPT-5-mini | sql_query, load_plan_and_constraints, calculator, schedule_recovery, production_forecast, stochastic_schedule_recovery |
| `maintenance_predictor` | GPT-5-mini | sql_query, load_plan_and_constraints, calculator, retrieve_similar_cases, detect_anomalies, estimate_rul               |
| `strategic_advisor`     | **GPT-5**  | sql_query, genie_query, calculator, load_plan_and_constraints, retrieve_similar_cases, multi_shift_plan                |
| `shift_briefing`        | GPT-5-mini | sql_query, genie_query, calculator, load_plan_and_constraints, retrieve_similar_cases                                  |

Action chips shown alongside each agent response are a static lookup keyed on specialist name (`_extract_actions` in `agent_server/agent.py`), they are not generated by the LLM.

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
│   ├── hitl/                # Human-in-the-loop action tracking
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
├── config/queries/          # SQL queries for AppKit analytics
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
- **`/sim/warmup`**: phased startup probe that sequentially checks SQL connectivity, `scenario_commands` table availability, Lakebase health, and engine tick status. Useful for canary validation after a deploy.

## OEE Calculation

OEE uses industry baselines for a top-quartile F&B line:

| Factor          | Baseline  | Rationale                                            |
| --------------- | --------- | ---------------------------------------------------- |
| Availability    | 88 %      | Changeovers ~4 %, CIP ~3 %, planned maintenance ~2 % |
| Performance     | 95 %      | Minor stops ~3 %, reduced speed ~2 %                 |
| Quality         | 98.5 %    | Start-up rejects ~1 %, rework ~0.5 %                 |
| **Healthy OEE** | **~82 %** | Top-quartile benchmark for F&B packaging             |

Incident faults reduce availability and performance in real time; quality is held constant for the demo.
