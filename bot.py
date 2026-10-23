"""ScenarioPilot forecasting bot for the Metaculus FutureEval tournaments (MIT licence).

Built on the official Fall 2026 template in forecasting-tools (MIT). The template does the question loading, the
forecasts for every question type, the aggregation and the submission with a comment. We add one layer:

Structured angles. Before forecasting, the news research is followed by a second pass that writes down the base
rate (how often things of this kind happen), the status quo outcome if nothing changes, the main drivers for and
against, and the signposts that would show the outcome coming. The forecaster then reads both. These
are standard superforecasting habits, written down as a prompt.

Further layers are added only when the MiniBench results show they help; BOT_VARIANT=template runs the plain
template as the A/B control on a secondary bot account.

Run: python bot.py --mode tournament|minibench|test_questions [--dry-run]
Environment: METACULUS_TOKEN, OPENROUTER_API_KEY,
BOT_MODEL (an OpenRouter model id for litellm, e.g. "openrouter/openai/<model>"; required, no silent default),
BOT_VARIANT ("angles", the default; "template", the A/B control; "ensemble", which also needs BOT_MODELS: two or more
comma-separated model ids that the forecasts rotate over), BOT_RESEARCHER ("no_research", the default;
"scenariopilot", which also needs SP_SUPABASE_URL, SP_ANON_KEY, SP_EMAIL and SP_PASSWORD).
The --control run uses METACULUS_TOKEN_CONTROL, CONTROL_VARIANT and CONTROL_RESEARCHER.
"""
import argparse
import asyncio
import functools
import itertools
import json
import logging
import os
import urllib.request

from dotenv import load_dotenv
from forecasting_tools.ai_models.general_llm import GeneralLlm
from forecasting_tools.data_models.questions import MetaculusQuestion
from forecasting_tools.forecast_bots.official_bots.template_bot_2026_fall import FallTemplateBot2026
from forecasting_tools.helpers.metaculus_client import MetaculusClient
from forecasting_tools.util.misc import clean_indents

logger = logging.getLogger(__name__)

TEST_QUESTIONS = [
    "https://www.metaculus.com/questions/578/human-extinction-by-2100/",  # binary
    "https://www.metaculus.com/questions/14333/age-of-oldest-human-as-of-2100/",  # numeric
    "https://www.metaculus.com/questions/22427/number-of-new-leading-ai-labs/",  # multiple choice
]


def angles_prompt(question: MetaculusQuestion, news: str) -> str:
    return clean_indents(
        f"""
        You help a superforecaster. Do not give a forecast. Using the question, its resolution criteria and the news
        below, write four short sections:
        1. Base rate: how often do events of this kind happen in comparable cases? Name the reference class.
        2. Status quo: what is the outcome if nothing changes before the resolution date, and how much time is left?
        3. Drivers: the strongest reasons it could resolve differently from the status quo, for and against.
        4. Signposts: concrete events that, if seen, would make one outcome much more likely.

        Question: {question.question_text}
        Resolution criteria: {question.resolution_criteria}
        Fine print: {question.fine_print}

        News research:
        {news}
        """
    )


SCENARIOPILOT = "scenariopilot"  # BOT_RESEARCHER value: evidence from the ScenarioPilot service 


def sp_post(path, body, token):
    req = urllib.request.Request(os.environ["SP_SUPABASE_URL"].rstrip("/") + path, json.dumps(body).encode(), method="POST", headers={
        "apikey": os.environ["SP_ANON_KEY"], "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=170).read())  # the edge function stops at 150 s


@functools.cache  # one sign-in per run: a run ends within 25 minutes, the token lives an hour
def sp_login() -> str:
    body = {"email": os.environ["SP_EMAIL"], "password": os.environ["SP_PASSWORD"]}
    return sp_post("/auth/v1/token?grant_type=password", body, os.environ["SP_ANON_KEY"])["access_token"]


def scenariopilot_evidence(question_text: str) -> str:
    """The ScenarioPilot evidence base for a question: news, web and research items kept by its relevance judge,
    each tagged with how many independent sources report it. The service is called as a signed-in user
    (SP_EMAIL / SP_PASSWORD) of the ScenarioPilot Supabase project (SP_SUPABASE_URL, SP_ANON_KEY)."""
    try:
        out = sp_post("/functions/v1/foresight-cycle", {"theme": question_text[:4000], "mode": "evidence"}, sp_login())
    except (OSError, ValueError) as e:  # coverage beats purity: a question without research still scores
        logger.warning("ScenarioPilot evidence failed, forecasting without research: %s", e)
        return "No research: the evidence service was unavailable."
    items = out["data"]["evidence"]
    lines = ["ScenarioPilot evidence (%d items kept of %d considered):" % (len(items), out["data"]["considered"])]
    for e in items:
        n = e.get("corroboration") or 1
        tag = "confirmed by %d sources" % n if n >= 2 else "single source"
        date = (e.get("date") or {}).get("iso") or "undated"
        lines.append("[%d] (%s, %s, %s) %s %s" % (e["id"], e["source"], tag, date, e["title"], e.get("url") or ""))
        if e.get("snippet"):
            lines.append("    " + e["snippet"])
    return "\n".join(lines)


class ScenarioPilotBot(FallTemplateBot2026):
    @classmethod
    def _llm_config_defaults(cls):
        defaults = super()._llm_config_defaults()
        defaults["angles"] = defaults["default"]  # the angles pass always uses the primary model
        return defaults

    async def run_research(self, question: MetaculusQuestion) -> str:
        if self.get_llm("researcher") == SCENARIOPILOT:
            news = await asyncio.to_thread(scenariopilot_evidence, question.question_text)
        else:
            news = await super().run_research(question)
        angles = await self.get_llm("angles", "llm").invoke(angles_prompt(question, news))
        return f"{news}\n\n## Structured angles (base rate, status quo, drivers, signposts)\n{angles}"


class EnsembleBot(ScenarioPilotBot):
    """Angles, and the forecasts rotate over several models; the template takes the median of all of them.

    shortcut: the rotation is shared across concurrent questions, so one question's forecasts are spread over the
    models only on average; make it per question if the MiniBench results show the balance matters."""

    def __init__(self, *args, forecasters: list, **kwargs):
        self._forecasters = forecasters  # set first: the parent's __init__ already calls make_llm_dict()
        self._turn = itertools.count()
        super().__init__(*args, **kwargs)

    def get_llm(self, purpose="default", guarantee_type=None):
        if purpose != "default":
            return super().get_llm(purpose, guarantee_type)
        llm = self._forecasters[next(self._turn) % len(self._forecasters)]
        return llm.model if guarantee_type == "string_name" else llm

    def make_llm_dict(self):
        out = super().make_llm_dict()
        out["forecasters"] = [f.to_dict() for f in self._forecasters]  # the public comment names every model used
        return out


VARIANTS = {"angles": ScenarioPilotBot, "template": FallTemplateBot2026, "ensemble": EnsembleBot}  # template = A/B control


def make_bot(model: str, publish: bool, variant: str = "angles", ensemble_models: list | None = None) -> FallTemplateBot2026:
    def llm(name, temperature):
        return GeneralLlm(model=name, temperature=temperature, timeout=240, allowed_tries=2)  # one ensemble model took 181 s in the first test run

    extra = {}
    if variant == "ensemble":
        if not ensemble_models or len(ensemble_models) < 2:
            raise SystemExit("the ensemble variant needs BOT_MODELS with at least two comma-separated model ids")
        extra["forecasters"] = [llm(m, 0.3) for m in ensemble_models]
    researcher = os.environ.get("BOT_RESEARCHER") or "no_research"  # we have no AskNews access
    if researcher == SCENARIOPILOT and variant == "template":
        raise SystemExit("the plain template cannot use the ScenarioPilot researcher; set CONTROL_RESEARCHER")
    llms = {"default": llm(model, 0.3), "summarizer": llm(model, 0.0), "researcher": researcher, "parser": llm(model, 0.0)}
    if variant != "template":
        llms["angles"] = llm(model, 0.3)  # the plain template has no angles pass
    return VARIANTS[variant](
        research_reports_per_question=1,
        predictions_per_research_report=6 if variant == "ensemble" else 5,  # ensemble: two per model for three models
        use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=publish,
        folder_to_save_reports_to=None,
        skip_previously_forecasted_questions=publish,
        extra_metadata_in_explanation=True,
        llms=llms,
        **extra,
    )


def as_control(env, mode: str) -> str:
    """Switch the environment to the control bot: its own token and the control variant; returns the run mode."""
    if not env.get("METACULUS_TOKEN_CONTROL"):
        raise SystemExit("--control needs METACULUS_TOKEN_CONTROL")
    env["METACULUS_TOKEN"] = env["METACULUS_TOKEN_CONTROL"]
    env["BOT_VARIANT"] = env.get("CONTROL_VARIANT") or "template"
    env["BOT_RESEARCHER"] = env.get("CONTROL_RESEARCHER") or "no_research"  # the control never uses our evidence
    return "minibench" if mode == "tournament" else mode  # the control only needs MiniBench for the comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="ScenarioPilot Metaculus bot")
    parser.add_argument("--mode", choices=["tournament", "minibench", "test_questions"], default="tournament")
    parser.add_argument("--dry-run", action="store_true", help="forecast but do not submit to Metaculus")
    parser.add_argument("--control", action="store_true",
                        help="run as the A/B control bot: METACULUS_TOKEN_CONTROL, CONTROL_VARIANT (default template), MiniBench only")
    args = parser.parse_args()
    load_dotenv()  # local runs read .env; in GitHub Actions the variables come from secrets
    if args.control:
        args.mode = as_control(os.environ, args.mode)
    model = os.environ.get("BOT_MODEL")
    if not model:
        raise SystemExit("BOT_MODEL is not set (an OpenRouter model id for litellm, e.g. openrouter/openai/<model>)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    variant = os.environ.get("BOT_VARIANT") or "angles"
    if variant not in VARIANTS:
        raise SystemExit("BOT_VARIANT must be one of %s" % sorted(VARIANTS))
    publish = not args.dry_run and args.mode != "test_questions"
    models = [m.strip() for m in os.environ.get("BOT_MODELS", "").split(",") if m.strip()]
    bot = make_bot(model, publish, variant, models)
    client = MetaculusClient()
    if args.mode == "tournament":
        reports = asyncio.run(bot.forecast_on_tournament(client.CURRENT_AI_COMPETITION_ID, return_exceptions=True))
        reports += asyncio.run(bot.forecast_on_tournament(client.CURRENT_MINIBENCH_ID, return_exceptions=True))
    elif args.mode == "minibench":
        reports = asyncio.run(bot.forecast_on_tournament(client.CURRENT_MINIBENCH_ID, return_exceptions=True))
    else:
        questions = [client.get_question_by_url(u) for u in TEST_QUESTIONS]
        reports = asyncio.run(bot.forecast_questions(questions, return_exceptions=True))
    bot.log_report_summary(reports)


if __name__ == "__main__":
    main()
