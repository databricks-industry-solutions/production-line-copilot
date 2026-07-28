"""MLflow 3 GenAI evaluation harness for the ProdLine CoPilot agent.

Three-file layout:
    dataset.py     — build a JSONL eval dataset from the reusable seed
                     (tests/agent_server/test_agent_evaluation.py) or from live
                     MLflow traces via ``mlflow.search_traces``.
    judges.py      — built-in scorers (Correctness, ToolCallCorrectness,
                     RetrievalGroundedness, Safety) plus four project-specific
                     scorers: three LLM judges via ``make_judge`` and one
                     deterministic ``@scorer`` for incident→specialist routing.
    run_eval.py    — CLI + programmatic entry. Wraps ``agent.invoke`` as the
                     ``predict_fn``, runs ``mlflow.genai.evaluate``, and exits
                     non-zero when configured thresholds are breached.

The predict function reuses ``agent_server.agent.invoke`` unchanged. Because
``mlflow.langchain.autolog()`` is already active in the agent module and the
wrapper is ``@mlflow.trace``-decorated, each eval record produces exactly one
parent trace with the ReAct/tool spans nested underneath — which is what the
built-in ``ToolCallCorrectness`` and the trace-based custom judges rely on.
"""
