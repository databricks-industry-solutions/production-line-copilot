# Agent evaluation and tracing

ProdLine CoPilot ships an MLflow GenAI evaluation harness (`evaluations/`) and
end-to-end MLflow tracing so you can measure agent quality and inspect every
run.

## What gets measured

The seed dataset lives in `evaluations/scenarios.py` (shared with the unit
tests) and is materialized to `evaluations/data/seed_v1.jsonl`:

- **Routing scenarios** (`INTENT_SCENARIOS`): one-or-more queries per specialist
  with the `expected_specialist` label. Scored deterministically.
- **Golden responses** (`GOLDEN_RESPONSES`): a query plus `must_contain_keywords`
  (semantic facts) and optional `must_not_contain` phrases.

Regenerate the JSONL after editing scenarios:

```bash
python -c "from evaluations.dataset import build_dataset; build_dataset(source='seed', output_path='evaluations/data/seed_v1.jsonl')"
```

## Scorers

| Scorer | Type | What it checks |
| --- | --- | --- |
| `Correctness` | built-in LLM | Answer matches `expected_facts` (semantic) |
| `RelevanceToQuery` | built-in LLM | Answer actually addresses the question (no ground truth needed) |
| `RetrievalGroundedness` | built-in LLM | Claims grounded in retrieved context |
| `Safety` | built-in LLM | No unsafe content |
| `ToolCallEfficiency` | built-in | Tool-call latency/efficiency signal |
| `recovery_respects_buffers` | custom LLM | Speed/setpoint advice cites buffer state |
| `optimizer_choice_appropriate` | custom LLM | Correct optimizer tool for the intent |
| `cites_live_evidence` | custom LLM | Per-machine claims trace to context/SQL/Genie spans |
| `incident_matches_specialist` | deterministic | Routed specialist equals the expected one |
| `response_avoids_forbidden` | deterministic | Response contains none of `must_not_contain` |

## Gate thresholds

`evaluations/run_eval.py` enforces a composite gate. Each threshold is an env
var so environments can tune independently; records that a scorer marks
`not_applicable` / `no_ground_truth` are excluded from that scorer's
denominator, so a partial slice can't trip an irrelevant check.

| Env var | Default | Meaning |
| --- | --- | --- |
| `EVAL_MIN_CORRECTNESS` | 0.75 | Mean correctness must be >= |
| `EVAL_MIN_INCIDENT_MATCH` | 0.90 | Routing accuracy must be >= |
| `EVAL_MAX_HALLUCINATION_RATE` | 0.10 | Hallucinated fraction must be <= |
| `EVAL_MAX_BUFFER_VIOLATION_RATE` | 0.05 | Buffer-violating advice fraction must be <= |
| `EVAL_MAX_FORBIDDEN_RATE` | 0.0 | Forbidden-phrase fraction must be <= |

## Running

```bash
# One-off run against the seed dataset (needs workspace model-serving access).
python -m evaluations.run_eval --source seed --limit 10 \
  --experiment /Shared/production-line-copilot-experiment

# CI / pre-deploy gate (exits non-zero if any threshold is breached).
python -m evaluations.run_eval --gate

# Pull recent real queries from an experiment (no ground truth; skips Correctness).
python -m evaluations.run_eval --source traces \
  --experiment /Shared/production-line-copilot-experiment
```

The deploy pipeline runs the gate when `RUN_EVAL_GATE=1 ./scripts/deploy.sh dev`
(see `scripts/run_eval_gate.sh`).

## Tracing

`mlflow.langchain.autolog()` captures every ReAct step, LLM call, and tool call.
On top of that, the agent emits named spans so a trace reads top-down:

- `classify_intent` — the routing decision (`agent_server/agent.py::_classify_intent`).
- `specialist.<name>` — a parent span per specialist run; the ReAct tool/LLM
  spans (`sql_query`, `genie_query`, `retrieve_similar_cases`, ...) nest under it.

Traces land in the experiment set by `MLFLOW_EXPERIMENT_PATH` (per env, so dev
and prod don't intermix). Open the experiment in MLflow to inspect a run, or use
the in-app reasoning-trace card which surfaces the same spans inline.

## Judge quality and alignment (known limitation)

The LLM-backed judges here are **unaligned** — they reflect the base judge
model's opinion, not calibrated line-manager judgment. That is fine for a demo
and for catching gross regressions, but for a production-grade, trustworthy
signal the judges should be **aligned to real human labels**.

The **ready-to-run workflow** for closing this gap ships in
[`evaluations/alignment/`](../evaluations/alignment/README.md) — it never
fabricates labels (aligning a judge to LLM-generated labels is circular and adds
no signal), so the alignment steps wait for real SME ratings:

1. **Managed UC dataset (runnable today, no labels):**
   `python -m evaluations.alignment.uc_dataset --uc-table <cat>.<schema>.prodline_eval_v1`
   migrates the checked-in JSONL to a versioned, trace-linked
   `mlflow.genai.datasets` dataset.
2. **SME labels via the Review App:** `evaluations/alignment/labeling.py` creates
   one categorical label schema per LLM judge — **named identically to the
   judge** (the MemAlign pairing key) — plus a labeling session line managers
   open to rate traces.
3. **Align + register:** `evaluations/alignment/align.py` runs MemAlign
   (`make_judge(...).align(...)`) on the labeled traces and updates the
   registered judge. Then `run_eval --use-aligned-judges` swaps aligned judges in
   automatically (falling back to base judges when none exist).

> Aligned judge scores may drop — that means the judge got *more accurate*, not
> that the agent regressed.

Until real labels exist, we rely on: deterministic scorers (routing + forbidden
phrases, 100% reliable), ground-truth `expected_facts` (hand-authored, not
synthetic), and `RelevanceToQuery` (no ground truth needed). See
`evaluations/alignment/README.md` for the full runbook and the roadmap/backlog
for the production-monitoring follow-up.

## Baselines

Record a baseline after each significant agent change so regressions are
visible. Fill this in from a `--gate` run's `breakdown` output:

| Date | Commit | correctness | incident_match | hallucination | buffer_violation | forbidden | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| _pending_ | _pending_ | | | | | | first v0.2.0 baseline |
