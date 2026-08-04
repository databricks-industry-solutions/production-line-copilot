"""The past-tense labels the chat shows when a tool finishes.

These are on the primary UI path — a line manager sees one per tool call, on
essentially every turn. They were built by string replacement
(``label.replace("ing ", "ed ").replace("Running ", "Ran ")``) which produced
"Runned anomaly detection" and "Queryed production database" on ten of the
seventeen labels, because the first substitution consumed the "Running " the
second was looking for. "Estimating remaining useful life" came out as
"Estimated remained useful life", a blanket replace having no way to tell a
verb from the middle of a noun phrase.

The point of the sweep below is that a new label cannot reintroduce that: any
verb the conjugation does not know fails here rather than in front of an
audience.
"""

import pytest

from agent_server.agent import _PAST_TENSE, TOOL_DISPLAY_NAMES, completed_label


class TestEveryShippedLabel:
    @pytest.mark.parametrize("label", sorted(set(TOOL_DISPLAY_NAMES.values())))
    def test_has_a_past_tense_we_know(self, label):
        verb = label.split(" ")[0]
        assert verb in _PAST_TENSE, (
            f"{label!r} opens with {verb!r}, which agent._PAST_TENSE cannot conjugate. "
            "Add it there — the fallback leaves the label in the present tense, which "
            "reads as unfinished next to a ✓."
        )

    @pytest.mark.parametrize("label", sorted(set(TOOL_DISPLAY_NAMES.values())))
    def test_reads_as_english(self, label):
        done = completed_label(label)
        assert done != label, f"{label!r} was not conjugated at all"
        # The specific wrecks the old implementation produced.
        for wreck in ("Runned", "Queryed", "Asked the ... ing", "remained useful"):
            assert wreck not in done, f"{label!r} -> {done!r}"

    @pytest.mark.parametrize("label", sorted(set(TOOL_DISPLAY_NAMES.values())))
    def test_only_the_verb_changes(self, label):
        # "Estimating remaining useful life" must keep "remaining": the noun
        # phrase is not the verb, and the old blanket replace could not tell.
        _, _, rest = label.partition(" ")
        assert completed_label(label).endswith(rest)


class TestTheConjugation:
    @pytest.mark.parametrize(
        ("running", "done"),
        [
            ("Running anomaly detection", "Ran anomaly detection"),
            ("Querying production database", "Queried production database"),
            ("Estimating remaining useful life", "Estimated remaining useful life"),
            ("Asking the governed asset domain", "Asked the governed asset domain"),
            ("Loading plan & constraints", "Loaded plan & constraints"),
            ("Searching historical cases", "Searched historical cases"),
        ],
    )
    def test_known_verbs(self, running, done):
        assert completed_label(running) == done

    def test_the_unnamed_tool_fallback_conjugates_too(self):
        # TOOL_DISPLAY_NAMES.get(name, f"Using {name}") — a tool with no display
        # name of its own still reaches this function.
        assert completed_label("Using some_new_tool") == "Used some_new_tool"

    def test_an_unknown_verb_is_left_alone_rather_than_mangled(self):
        # Present tense reads as slightly off; "Computeing" reads as broken. The
        # sweep above is what stops this shipping.
        assert completed_label("Computing something") == "Computing something"

    def test_a_single_word_label_does_not_lose_its_tail(self):
        assert completed_label("Running") == "Ran"

    def test_an_empty_label_is_survivable(self):
        assert completed_label("") == ""
