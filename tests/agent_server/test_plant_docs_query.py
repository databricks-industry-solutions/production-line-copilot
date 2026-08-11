"""Tests for ``query_plant_docs`` — the Knowledge Assistant document tool.

The interesting behaviour here is not the happy path, it is the two ways this
tool can lie:

  * A Knowledge Assistant that is not configured must say so. Returning an empty
    answer would let a specialist narrate plant procedure it never read.
  * A failure must NOT fall back to ``retrieve_similar_cases``. That tool
    searches resolved *events*, not documents, and its results look correct, so a
    silent substitution would answer "what does the SOP say" with "here is what
    we did last Tuesday".

Answer and citation extraction is covered as pure functions because the agent
endpoint can reply in either of the two documented agent response shapes and we
parse both. All network is mocked.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mlflow")

from agent_server.tools import plant_docs_query as pdq  # noqa: E402
from agent_server.tools.plant_docs_query import (  # noqa: E402
    extract_answer,
    extract_citations,
    query_plant_docs,
)


def _invoke(tool_obj, **kwargs):
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(kwargs)
    return tool_obj(**kwargs)


# Recorded from a live Knowledge Assistant endpoint, not invented. The shape the
# agent documentation implies (``doc_uri``) is NOT what arrives: an annotation is
# a ``url_citation`` with ``url`` and ``title``, and the url carries a
# ``#:~:text=`` deep link to the sentence that was quoted. Parsing the documented
# names alone dropped every citation while still returning a confident answer.
_CITATION_URL = (
    "https://example.cloud.databricks.com/ajax-api/2.0/fs/files/Volumes/cat/sch/"
    "plant_docs/sop_capper_loto.md#:~:text=Isolate%20pneumatic%20supply"
)

RESPONSES_PAYLOAD = {
    "object": "response",
    "model": "kbqa_agent",
    "output": [
        {
            "type": "message",
            "id": "7143c0c5-65e7-45d1-8b79-f18d68a54bee",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Vent the pneumatic accumulator before opening the guard.",
                    "annotations": [
                        {"type": "url_citation", "url": _CITATION_URL,
                         "title": "sop_capper_loto.md"},
                        {"type": "url_citation", "url": _CITATION_URL,
                         "title": "sop_capper_loto.md"},
                    ],
                    "logprobs": [],
                }
            ],
        }
    ],
    "custom_outputs": {"sources_used": True},
}

# The spellings the documentation suggests. Kept because the endpoint is in beta
# and they cost nothing to read, but they are not what fires in practice.
DOCUMENTED_PAYLOAD = {
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Vent the pneumatic accumulator before opening the guard.",
                    "annotations": [
                        {"type": "citation",
                         "doc_uri": "/Volumes/c/s/plant_docs/sop_capper_loto.md",
                         "page": 2},
                    ],
                }
            ],
        }
    ]
}

# The chat-completions shape.
CHAT_PAYLOAD = {
    "choices": [
        {"message": {"role": "assistant", "content": "Glue temperature must be 165 to 175 °C."}}
    ],
    "citations": [{"doc_uri": "/Volumes/c/s/plant_docs/deviation_2026_03_labeller.md"}],
}


class TestExtractAnswer:
    def test_responses_shape(self):
        assert extract_answer(RESPONSES_PAYLOAD).startswith("Vent the pneumatic")

    def test_chat_shape(self):
        assert "165 to 175" in extract_answer(CHAT_PAYLOAD)

    def test_unknown_shape_is_empty_not_an_exception(self):
        assert extract_answer({"unexpected": True}) == ""


class TestExtractCitations:
    def test_the_live_url_citation_shape_is_read(self):
        cites = extract_citations(RESPONSES_PAYLOAD)
        assert cites == [{"doc": "sop_capper_loto.md", "uri": _CITATION_URL}], (
            "a url_citation annotation must produce a citation"
        )

    def test_annotations_are_deduplicated(self):
        assert len(extract_citations(RESPONSES_PAYLOAD)) == 1

    def test_one_document_cited_many_times_is_one_source(self):
        """Live behaviour: every cited sentence gets its own annotation with its
        own text fragment, so one specification arrived seven times."""
        payload = {"citations": [
            {"type": "url_citation", "url": f"{_CITATION_URL}%20one",
             "title": "spec_pasteurizer_pu_hold.md"},
            {"type": "url_citation", "url": f"{_CITATION_URL}%20two",
             "title": "spec_pasteurizer_pu_hold.md"},
            {"type": "url_citation", "url": f"{_CITATION_URL}%20three",
             "title": "bulletin_2026_05_pasteurizer_pu_window.md"},
        ]}
        assert [c["doc"] for c in extract_citations(payload)] == [
            "spec_pasteurizer_pu_hold.md",
            "bulletin_2026_05_pasteurizer_pu_window.md",
        ]

    def test_the_documented_spellings_still_work(self):
        cites = extract_citations(DOCUMENTED_PAYLOAD)
        assert cites == [{"doc": "sop_capper_loto.md",
                          "uri": "/Volumes/c/s/plant_docs/sop_capper_loto.md",
                          "page": 2}]

    def test_the_text_fragment_is_not_part_of_the_document_name(self):
        """Without a title, the name comes from the path — and the citation url
        ends in a ``#:~:text=`` quote, so a naive split returns the quote too."""
        payload = {"citations": [{"url": _CITATION_URL}]}
        assert extract_citations(payload)[0]["doc"] == "sop_capper_loto.md"

    def test_top_level_citations(self):
        assert extract_citations(CHAT_PAYLOAD)[0]["doc"] == "deviation_2026_03_labeller.md"

    def test_no_citations_is_empty_list(self):
        assert extract_citations({"output": []}) == []


class TestGrounding:
    """An ungrounded answer about a procedure is the failure this tool exists to
    prevent, and it looks like any other answer."""

    def test_a_cited_answer_is_grounded(self):
        assert pdq.answered_from_documents(RESPONSES_PAYLOAD) is True
        assert pdq.answered_from_documents(DOCUMENTED_PAYLOAD) is True

    def test_an_uncited_answer_is_not_grounded(self):
        assert pdq.answered_from_documents({"output": []}) is False

    def test_sources_used_alone_does_not_make_an_answer_grounded(self):
        """Measured live: a question the corpus does not cover comes back with
        ``sources_used`` true, no citations, and a correct refusal. Trusting the
        flag would hand a specialist a refusal to narrate as procedure."""
        payload = {"output": [{"content": [{"text": "The documents do not cover this."}]}],
                   "custom_outputs": {"sources_used": True}}
        assert pdq.answered_from_documents(payload) is False

    def test_an_answer_that_skipped_the_sources_is_flagged(self):
        payload = {"output": [{"content": [{"text": "I have no documents."}]}],
                   "custom_outputs": {"sources_used": False}}
        assert pdq.answered_from_documents(payload) is False

    def test_the_tool_passes_the_flag_through(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", lambda q: RESPONSES_PAYLOAD)
        r = _invoke(query_plant_docs, question="What is the CAPPER isolation sequence?")
        assert r["grounded"] is True


class TestConfiguration:
    def test_unconfigured_reports_itself(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "")
        assert pdq.docs_assistant_configured() is False
        r = _invoke(query_plant_docs, question="What is the CAPPER torque window?")
        assert "DOCS_KA_ENDPOINT" in r["error"]
        assert "answer" not in r

    def test_configured(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        assert pdq.docs_assistant_configured() is True

    def test_a_trivial_question_is_refused_before_the_call(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", lambda *a, **k: pytest.fail("should not call out"))
        r = _invoke(query_plant_docs, question="hi")
        assert "error" in r


class TestQuerying:
    def test_happy_path_reports_how_it_answered(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", lambda payload: RESPONSES_PAYLOAD)
        r = _invoke(query_plant_docs, question="What is the CAPPER isolation sequence?")
        assert r["retrieval"] == "knowledge_assistant"
        assert r["citations"][0]["doc"] == "sop_capper_loto.md"
        assert "accumulator" in r["answer"]

    def test_an_answer_with_no_text_is_an_error_not_an_empty_answer(self, monkeypatch):
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", lambda payload: {"output": []})
        r = _invoke(query_plant_docs, question="What is the CAPPER torque window?")
        assert "error" in r and "answer" not in r

    def test_transport_failure_surfaces_as_an_error(self, monkeypatch):
        def boom(payload):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", boom)
        r = _invoke(query_plant_docs, question="What is the CAPPER torque window?")
        assert "error" in r
        assert "connection reset" in r["error"]

    def test_failure_does_not_fall_back_to_event_retrieval(self, monkeypatch):
        """A document question must never be answered from the event case library."""
        monkeypatch.setattr(pdq, "_ENDPOINT", "ka-plant-docs")
        monkeypatch.setattr(pdq, "_post", lambda payload: {"output": []})
        r = _invoke(query_plant_docs, question="What is the CAPPER torque window?")
        assert r.get("retrieval") != "keyword"
        assert "root_cause" not in r
        assert "corrective_action" not in r
