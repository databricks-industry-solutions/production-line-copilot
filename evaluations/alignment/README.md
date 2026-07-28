# Judge Alignment Runbook

Turns the unaligned LLM judges into **SME-aligned** judges, and the checked-in
JSONL into a **managed Unity Catalog dataset**. This is the credible,
best-practice path for a trustworthy eval signal.

**Honesty guardrail:** we never fabricate labels. Aligning a judge to
LLM-generated labels is circular and adds no signal. The alignment steps below
do nothing until *real* line-manager / SME ratings exist. What ships today is
the ready-to-run workflow + the managed dataset (which needs no labels).

## Prerequisites

- A Databricks workspace + an MLflow experiment (the eval harness already logs
  traces there).
- `pip install dspy` in the runtime that runs `align.py` (MemAlign's backend).
  Not needed for the dataset or labeling steps.

## Steps

### 1. Managed UC dataset (runnable now — no labels needed)

Migrate `evaluations/data/seed_v1.jsonl` to a versioned, trace-linked UC
dataset. Idempotent (`merge_records` upserts).

```bash
python -m evaluations.alignment.uc_dataset \
    --uc-table <catalog>.<schema>.prodline_eval_v1
```

### 2. Generate traces to review

Run the eval so the experiment has traces for SMEs to rate, then tag the ones
you want reviewed:

```bash
python -m evaluations.run_eval --experiment <experiment> --source seed
```

```python
import mlflow
ok = results.result_df.loc[results.result_df["state"] == "OK", "trace_id"]
for tid in ok:
    mlflow.set_trace_tag(trace_id=tid, key="eval", value="complete")
```

### 3. Create a labeling session for SMEs

Creates one categorical label schema per judge — **named identically to the
judge** (`recovery_respects_buffers`, `optimizer_choice_appropriate`,
`cites_live_evidence`), which is how MemAlign pairs SME ratings with judge
scores — and a Review App session.

```bash
python -m evaluations.alignment.labeling \
    --experiment-id <id> \
    --users alice@co.com bob@co.com \
    --dataset-name <catalog>.<schema>.prodline_eval_v1
```

Share the printed URL. SMEs rate each response using the same categories the
judge emits.

### 4. Align the judge (after SMEs finish labeling)

```bash
python -m evaluations.alignment.align \
    --experiment-id <id> \
    --judge recovery_respects_buffers \
    --labels-filter "tag.eval = 'complete'"
```

Repeat per judge. MemAlign distills the SME feedback into the judge's
instructions and updates the registered judge in-place (kept off live traffic,
`sample_rate=0`).

### 5. Re-evaluate with aligned judges

```bash
python -m evaluations.run_eval --experiment <experiment> --use-aligned-judges
```

`resolve_scorers` swaps in any aligned judge registered to the experiment and
falls back to the base judge when none exists — always safe to leave on.

> **Expect aligned scores to move (often down).** A lower score from a judge
> that now reflects expert standards is a *better* signal than a high score
> from a generic judge. That is the point.

## Files

| File | Purpose | Needs labels? |
|---|---|---|
| `uc_dataset.py` | JSONL → managed UC dataset | No |
| `base_judges.py` | Alignable judges + matching label schemas (name invariant) | No |
| `labeling.py` | Review App labeling session for SMEs | No (produces the ask) |
| `align.py` | MemAlign + register aligned judge | **Yes** |

## Invariant enforced in code

`base_judges.label_schema_specs()` derives each schema's **name and categories
from the judge object itself**, so the label schema can never drift from what
the judge emits. `tests/agent_server/test_alignment.py` pins this.
