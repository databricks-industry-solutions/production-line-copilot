"""Per-role model assignment, operator overrides, and the availability fallback.

The fallback is the part that has to work: the roster names models from six
vendors, and no customer workspace is guaranteed to serve all of them. A
missing model must degrade that one role to its tier default rather than 404
the specialist on first deploy.
"""
import pytest

from agent_server import model_roster
from agent_server.model_roster import (
    ADVANCED_TIER_DEFAULT,
    ROSTER,
    SPECIALIST_TIER_DEFAULT,
    describe,
    resolve,
    resolved_roster,
    vendor_summary,
)

# Env vars any test here might set; cleared so one test cannot leak into another.
_MODEL_ENV = tuple(
    [f"MODEL_{a.role.upper()}" for a in ROSTER]
    + ["MODEL_SPECIALIST", "MODEL_ADVANCED", "MODEL_ORCHESTRATOR", "MODEL_MULTI_VENDOR"]
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _MODEL_ENV:
        monkeypatch.delenv(name, raising=False)
    model_roster.reset_cache()
    yield
    model_roster.reset_cache()


@pytest.fixture
def serving(monkeypatch):
    """Pin the set of endpoints the 'workspace' serves."""

    def _set(names):
        model_roster.reset_cache()
        monkeypatch.setattr(
            model_roster, "available_endpoints", lambda: None if names is None else frozenset(names)
        )

    return _set


class TestTheRosterItself:
    def test_every_specialist_and_orchestrator_role_is_assigned(self):
        # These are the routing targets in agent._classify_intent plus the
        # router itself; a role missing here silently falls to the tier default.
        expected = {
            "downtime_analyst", "quality_specialist", "supply_chain_advisor",
            "oee_coach", "schedule_optimizer", "maintenance_predictor",
            "strategic_advisor", "shift_briefing", "general", "intent_router",
        }
        assert {a.role for a in ROSTER} == expected

    def test_roles_are_unique(self):
        roles = [a.role for a in ROSTER]
        assert len(roles) == len(set(roles))

    def test_every_assignment_declares_a_valid_tier(self):
        assert all(a.tier in {"specialist", "advanced"} for a in ROSTER)

    def test_the_point_of_the_exercise_is_actually_multi_vendor(self):
        # If a future edit collapses this back onto one vendor, the accelerator
        # stops demonstrating the thing it claims to demonstrate.
        assert len({a.vendor for a in ROSTER}) >= 5

    def test_a_gemini_specialist_requires_the_compat_shim(self):
        """Gemini in a tool-loop seat is only safe while the shim exists.

        Gemini rejects the second turn of a tool loop unless its
        ``thoughtSignature`` is echoed back, and the Databricks LangChain
        integration drops that field. ``agent_server/vendor_compat.py`` restores
        it. If someone deletes the shim and leaves a Gemini specialist in place,
        the failure shows up only on the *second* turn — invisible to a
        single-shot smoke test, broken in production. Fail here instead.
        """
        from agent_server import vendor_compat

        tool_loop_gemini = [
            a for a in ROSTER if a.role != "intent_router" and "gemini" in a.endpoint
        ]
        if tool_loop_gemini:
            assert vendor_compat.apply() is True, (
                f"{[a.role for a in tool_loop_gemini]} run on Gemini, which needs "
                "vendor_compat to survive a multi-turn tool loop."
            )

    def test_no_openai_model_that_refuses_function_tools(self):
        # GPT-5.5 and the 5.6 family reject function tools on chat-completions
        # unless reasoning is disabled; GPT-5.5-pro is Responses-API only.
        banned = ("gpt-5-5", "gpt-5-6")
        offenders = [a.endpoint for a in ROSTER if any(b in a.endpoint for b in banned)]
        assert not offenders


class TestResolution:
    def test_preferred_model_is_used_when_served(self, serving):
        serving({"databricks-claude-sonnet-5"})
        assert resolve("downtime_analyst") == "databricks-claude-sonnet-5"

    def test_missing_model_falls_back_to_its_tier(self, serving):
        serving({"databricks-gpt-5-mini", "databricks-gpt-5"})
        # Sonnet is a specialist-tier role, Opus an advanced-tier one.
        assert resolve("downtime_analyst") == SPECIALIST_TIER_DEFAULT
        assert resolve("strategic_advisor") == ADVANCED_TIER_DEFAULT

    def test_fallback_honours_a_custom_tier_endpoint(self, serving, monkeypatch):
        # A workspace that serves the models under different names configures
        # the tiers; the fallback must land on those, not the hardcoded default.
        serving({"house-model"})
        monkeypatch.setenv("MODEL_SPECIALIST", "house-model")
        assert resolve("downtime_analyst") == "house-model"

    def test_unknown_availability_keeps_the_preferred_model(self, serving):
        # Failing to list endpoints must not silently downgrade all nine roles
        # to one vendor — that would quietly undo the whole feature.
        serving(None)
        assert resolve("downtime_analyst") == "databricks-claude-sonnet-5"

    def test_role_env_override_wins_over_everything(self, serving, monkeypatch):
        serving({"databricks-claude-sonnet-5"})
        monkeypatch.setenv("MODEL_DOWNTIME_ANALYST", "databricks-claude-opus-5")
        assert resolve("downtime_analyst") == "databricks-claude-opus-5"

    def test_override_is_used_even_when_not_listed_as_served(self, serving, monkeypatch):
        # An operator naming an endpoint explicitly knows something we don't
        # (a provisioned-throughput or external model, say). Don't overrule it.
        serving({"databricks-gpt-5-mini"})
        monkeypatch.setenv("MODEL_OEE_COACH", "my-custom-endpoint")
        assert resolve("oee_coach") == "my-custom-endpoint"

    def test_unknown_role_gets_the_specialist_tier_instead_of_raising(self, serving):
        serving(None)
        assert resolve("brand_new_specialist") == SPECIALIST_TIER_DEFAULT


class TestKillSwitch:
    def test_multi_vendor_off_restores_the_old_two_tier_behaviour(self, serving, monkeypatch):
        serving(None)
        monkeypatch.setenv("MODEL_MULTI_VENDOR", "0")
        assert resolve("downtime_analyst") == SPECIALIST_TIER_DEFAULT
        assert resolve("oee_coach") == ADVANCED_TIER_DEFAULT
        assert resolve("strategic_advisor") == ADVANCED_TIER_DEFAULT

    def test_a_role_override_still_applies_with_the_roster_off(self, serving, monkeypatch):
        serving(None)
        monkeypatch.setenv("MODEL_MULTI_VENDOR", "off")
        monkeypatch.setenv("MODEL_SHIFT_BRIEFING", "databricks-claude-haiku-4-5")
        assert resolve("shift_briefing") == "databricks-claude-haiku-4-5"


class TestLegacyOrchestratorPin:
    def test_it_still_pins_both_orchestrator_roles(self, serving, monkeypatch):
        # Deployments that set MODEL_ORCHESTRATOR before the roster existed
        # must keep the behaviour they configured.
        serving(None)
        monkeypatch.setenv("MODEL_ORCHESTRATOR", "databricks-gpt-5-mini")
        assert resolve("general") == "databricks-gpt-5-mini"
        assert resolve("intent_router") == "databricks-gpt-5-mini"

    def test_it_does_not_leak_into_specialists(self, serving, monkeypatch):
        serving(None)
        monkeypatch.setenv("MODEL_ORCHESTRATOR", "databricks-gpt-5-mini")
        assert resolve("downtime_analyst") == "databricks-claude-sonnet-5"

    def test_a_role_specific_override_beats_the_legacy_pin(self, serving, monkeypatch):
        serving(None)
        monkeypatch.setenv("MODEL_ORCHESTRATOR", "databricks-gpt-5-mini")
        monkeypatch.setenv("MODEL_INTENT_ROUTER", "databricks-gemma-3-12b")
        assert resolve("intent_router") == "databricks-gemma-3-12b"


class TestDescribe:
    @pytest.mark.parametrize(
        "endpoint,vendor",
        [
            ("databricks-claude-opus-5", "Anthropic"),
            ("databricks-gpt-5-4", "OpenAI"),
            ("databricks-gpt-oss-120b", "OpenAI (open weights)"),
            ("databricks-gemma-3-12b", "Google"),
            ("databricks-llama-4-maverick", "Meta"),
            ("databricks-qwen35-122b-a10b", "Alibaba"),
            ("databricks-glm-5-2", "Zhipu AI"),
        ],
    )
    def test_roster_endpoints_report_their_vendor(self, endpoint, vendor):
        assert describe(endpoint)[1] == vendor

    def test_uncatalogued_endpoints_are_identified_by_name(self):
        # Operators point at their own endpoints; the badge should still say
        # something truthful rather than "unknown".
        assert describe("databricks-claude-sonnet-4")[1] == "Anthropic"
        assert describe("my-llama-finetune")[1] == "Meta"

    def test_open_weights_gpt_is_not_mistaken_for_proprietary(self):
        # "gpt-oss" contains "gpt"; hint order decides this.
        assert describe("databricks-gpt-oss-20b")[1] == "OpenAI (open weights)"

    def test_a_genuinely_unrecognised_endpoint_admits_it(self):
        assert describe("some-internal-endpoint") == ("some-internal-endpoint", "unknown")

    def test_empty_endpoint_is_handled(self):
        assert describe("") == ("unknown", "unknown")


class TestResolvedRosterView:
    def test_it_reports_what_is_really_running_not_the_wish_list(self, serving):
        # A workspace without Anthropic should not have the About tab claiming
        # Claude answered — the substitution has to be visible.
        serving({"databricks-gpt-5-mini", "databricks-gpt-5"})
        entry = next(e for e in resolved_roster() if e["role"] == "downtime_analyst")
        assert entry["substituted"] is True
        assert entry["endpoint"] == SPECIALIST_TIER_DEFAULT
        assert entry["vendor"] == "OpenAI"
        assert entry["preferred_vendor"] == "Anthropic"

    def test_nothing_is_flagged_substituted_when_all_models_are_served(self, serving):
        serving({a.endpoint for a in ROSTER})
        assert not [e for e in resolved_roster() if e["substituted"]]

    def test_vendor_summary_collapses_to_one_when_everything_falls_back(self, serving):
        serving({"databricks-gpt-5-mini", "databricks-gpt-5"})
        assert vendor_summary() == ["OpenAI"]

    def test_vendor_summary_lists_the_full_roster_when_served(self, serving):
        serving({a.endpoint for a in ROSTER})
        assert len(vendor_summary()) >= 5
