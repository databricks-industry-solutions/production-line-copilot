"""Evidence has to reach the chat attached to the right step.

Covers the seam between the tool stream and the answer payload: which step a
piece of evidence lands on, and that it survives into reasoning_steps. Also
guards the tool-label map against drift, since a governed tool missing from it
shows the user a raw function name mid-answer.
"""

from agent_server.agent import TOOL_DISPLAY_NAMES, _attach_evidence, _tool_steps
from agent_server.tools.governed_domain_query import DOMAIN_ENV


def _trace(*labels):
    return [{"step": i + 1, "action": label, "status": "done"} for i, label in enumerate(labels)]


class TestAttachEvidence:
    def test_evidence_lands_on_the_step_with_that_label(self):
        trace = _trace("Querying production database", "Asking Genie data analyst")
        _attach_evidence(trace, "Asking Genie data analyst", {"rows": 3})
        assert "evidence" not in trace[0]
        assert trace[1]["evidence"] == {"rows": 3}

    def test_repeated_calls_fill_their_own_steps_in_order(self):
        """Two calls of one tool must not pile onto the same step.

        Attaching to the newest match put both results on step 2: step 1 kept
        its label and lost its query, and step 2 showed one call's SQL beside
        another call's row count. A trail that exists to be checked is worse
        wrong than absent.
        """
        trace = _trace("Querying production database", "Querying production database")
        _attach_evidence(trace, "Querying production database", {"sql": "SELECT 1", "rows": 7})
        _attach_evidence(trace, "Querying production database", {"sql": "SELECT 2", "rows": 9})
        assert trace[0]["evidence"] == {"sql": "SELECT 1", "rows": 7}
        assert trace[1]["evidence"] == {"sql": "SELECT 2", "rows": 9}

    def test_evidence_beyond_the_known_calls_lands_on_the_newest(self):
        # Nowhere better to put it: every step with that label is accounted for.
        trace = _trace("Querying production database", "Querying production database")
        for ev in ({"sql": "a"}, {"sql": "b"}, {"rows": 3}):
            _attach_evidence(trace, "Querying production database", ev)
        assert trace[0]["evidence"] == {"sql": "a"}
        assert trace[1]["evidence"] == {"sql": "b", "rows": 3}

    def test_call_and_result_evidence_merge_onto_one_step(self):
        # sql_query contributes its SQL at call time and its row count on return.
        trace = _trace("Querying production database")
        _attach_evidence(trace, "Querying production database", {"sql": "SELECT 1"})
        _attach_evidence(trace, "Querying production database", {"rows": 1, "rows_capped": False})
        assert trace[0]["evidence"] == {"sql": "SELECT 1", "rows": 1, "rows_capped": False}

    def test_an_unknown_label_is_dropped_rather_than_inventing_a_step(self):
        trace = _trace("Querying production database")
        _attach_evidence(trace, "Asking Genie data analyst", {"rows": 3})
        assert trace == _trace("Querying production database")

    def test_an_empty_trace_is_survivable(self):
        trace: list[dict] = []
        _attach_evidence(trace, "Anything", {"rows": 1})
        assert trace == []


class TestToolSteps:
    def test_evidence_survives_into_the_reasoning_steps(self):
        trace = _trace("Asking the governed quality domain")
        trace[0]["evidence"] = {"domain": "quality", "rows": 0}
        steps = _tool_steps(trace)
        assert steps[0]["label"] == "Asking the governed quality domain"
        assert steps[0]["evidence"] == {"domain": "quality", "rows": 0}

    def test_a_step_without_evidence_carries_no_empty_key(self):
        # The UI treats presence as "there is something to show".
        steps = _tool_steps(_trace("Running calculations"))
        assert "evidence" not in steps[0]

    def test_step_order_follows_the_trace(self):
        steps = _tool_steps(_trace("First", "Second", "Third"))
        assert [s["label"] for s in steps] == ["First", "Second", "Third"]


class TestToolLabels:
    def test_every_governed_domain_has_a_human_label(self):
        for domain in DOMAIN_ENV:
            tool = f"governed_{domain}_query"
            assert tool in TOOL_DISPLAY_NAMES, (
                f"{tool} would render as 'Using {tool}' in the live trace"
            )

    def test_governed_labels_name_the_domain_they_query(self):
        for domain in DOMAIN_ENV:
            assert domain in TOOL_DISPLAY_NAMES[f"governed_{domain}_query"]

    def test_labels_are_prose_not_function_names(self):
        for label in TOOL_DISPLAY_NAMES.values():
            assert "_" not in label, f"{label!r} looks like a function name"
