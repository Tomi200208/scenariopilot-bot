"""Offline tests: no network, no keys. Run: python -m pytest -q (or python test_bot.py)."""
import asyncio
from types import SimpleNamespace

import bot


class FakeLlm:
    def __init__(self):
        self.prompts = []

    async def invoke(self, prompt):
        self.prompts.append(prompt)
        return "ANGLES"


def test_angles_prompt_carries_the_question_and_news():
    q = SimpleNamespace(question_text="Will X happen?", resolution_criteria="Yes if X by 2027.", fine_print="None.")
    p = bot.angles_prompt(q, "NEWS TEXT")
    assert "Will X happen?" in p and "Yes if X by 2027." in p and "NEWS TEXT" in p
    assert "Do not give a forecast" in p


def test_research_appends_angles_to_the_news(monkeypatch):
    async def fake_base(self, question):
        return "NEWS TEXT"

    monkeypatch.setattr(bot.FallTemplateBot2026, "run_research", fake_base)
    b = bot.make_bot("openrouter/test/model", publish=False)
    llm = FakeLlm()
    monkeypatch.setattr(b, "get_llm", lambda name, kind=None: llm)
    q = SimpleNamespace(question_text="Q?", resolution_criteria="R", fine_print="F")
    out = asyncio.run(b.run_research(q))
    assert out.startswith("NEWS TEXT") and out.endswith("ANGLES")
    assert "NEWS TEXT" in llm.prompts[0]


def test_dry_run_bot_does_not_publish():
    assert bot.make_bot("openrouter/test/model", publish=False).publish_reports_to_metaculus is False


def test_ensemble_rotates_forecasters_but_angles_stays_on_the_primary_model():
    b = bot.make_bot("openrouter/a/primary", publish=False, variant="ensemble",
                     ensemble_models=["openrouter/a/one", "openrouter/b/two", "openrouter/c/three"])
    picked = [b.get_llm("default", "llm").model for _ in range(6)]
    assert picked == ["openrouter/a/one", "openrouter/b/two", "openrouter/c/three"] * 2
    assert b.get_llm("angles", "llm").model == "openrouter/a/primary"
    assert b.predictions_per_research_report == 6
    assert [f["model"] for f in b.make_llm_dict()["forecasters"]] == ["openrouter/a/one", "openrouter/b/two", "openrouter/c/three"]


def test_ensemble_needs_two_models():
    import pytest
    with pytest.raises(SystemExit):
        bot.make_bot("openrouter/a/primary", publish=False, variant="ensemble", ensemble_models=["openrouter/a/one"])


def test_as_control_switches_token_variant_and_mode():
    env = {"METACULUS_TOKEN": "primary", "METACULUS_TOKEN_CONTROL": "control"}
    assert bot.as_control(env, "tournament") == "minibench"
    assert env["METACULUS_TOKEN"] == "control" and env["BOT_VARIANT"] == "template"
    assert bot.as_control({"METACULUS_TOKEN_CONTROL": "c", "CONTROL_VARIANT": "angles"}, "test_questions") == "test_questions"
    import pytest
    with pytest.raises(SystemExit):
        bot.as_control({"METACULUS_TOKEN": "primary"}, "tournament")


def test_control_variant_is_the_plain_template():
    control = bot.make_bot("openrouter/test/model", publish=False, variant="template")
    assert type(control) is bot.FallTemplateBot2026 and not isinstance(control, bot.ScenarioPilotBot)


def test_scenariopilot_evidence_is_formatted_and_failures_degrade(monkeypatch):
    item = {"id": 1, "source": "Reuters", "title": "X approved", "url": "https://r.example/x", "snippet": "Details.",
            "corroboration": 3, "date": {"iso": "2026-10-01"}}
    monkeypatch.setattr(bot, "sp_login", lambda: "token")
    monkeypatch.setattr(bot, "sp_post", lambda path, body, token: {"data": {"evidence": [item], "considered": 9}})
    out = bot.scenariopilot_evidence("Will X happen?")
    assert "1 items kept of 9" in out and "[1] (Reuters, confirmed by 3 sources, 2026-10-01) X approved" in out
    assert "    Details." in out

    def down(path, body, token):
        raise OSError("timed out")
    monkeypatch.setattr(bot, "sp_post", down)
    assert bot.scenariopilot_evidence("Will X happen?").startswith("No research")


def test_scenariopilot_researcher_replaces_the_news_and_the_control_keeps_the_baseline(monkeypatch):
    monkeypatch.setenv("BOT_RESEARCHER", "scenariopilot")
    b = bot.make_bot("openrouter/test/model", publish=False)
    monkeypatch.setattr(bot, "scenariopilot_evidence", lambda text: "SP EVIDENCE for " + text)
    llm = FakeLlm()
    real_get_llm = b.get_llm
    monkeypatch.setattr(b, "get_llm", lambda name, kind=None: llm if name == "angles" else real_get_llm(name, kind))
    q = SimpleNamespace(question_text="Q?", resolution_criteria="R", fine_print="F")
    assert asyncio.run(b.run_research(q)).startswith("SP EVIDENCE for Q?")
    env = {"METACULUS_TOKEN_CONTROL": "c", "BOT_RESEARCHER": "scenariopilot"}
    bot.as_control(env, "tournament")
    assert env["BOT_RESEARCHER"] == "no_research"
    import pytest
    with pytest.raises(SystemExit):
        bot.make_bot("openrouter/test/model", publish=False, variant="template")
