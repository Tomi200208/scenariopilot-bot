# ScenarioPilot Metaculus bot

An open-source (MIT) forecasting bot for the [Metaculus FutureEval](https://www.metaculus.com/futureeval/) bot
tournaments, run by [ScenarioPilot](https://scenariopilot.com).

## How it forecasts

It is built on the official Fall 2026 template bot in
[forecasting-tools](https://github.com/Metaculus/forecasting-tools) (MIT), which loads the questions, writes the
forecasts for every question type, takes the median of five forecasts and submits them with a comment.

On top of that, every question gets a **structured-angles** pass after the news research: the base rate of
comparable events, the status-quo outcome, the main drivers for and against, and the signposts that would show the
outcome coming. The forecaster reads both. These are standard superforecasting habits, written down as a prompt.

We add further layers only when the MiniBench results show that they help. To measure that, a second bot account
(scored by Metaculus, not ranked for prizes) runs another variant:

| `BOT_VARIANT` | What it does |
|---|---|
| `template` | the plain template (the control) |
| `angles` | the template plus the structured-angles pass (default) |
| `ensemble` | angles, and the six forecasts rotate over the models in `BOT_MODELS` (two or more, comma-separated); the median of all is submitted |

In GitHub Actions the repository variables `PRIMARY_VARIANT` (default `angles`) and `CONTROL_VARIANT` (default
`template`) choose the two variants. The primary forecasts on the tournament and MiniBench; the control on MiniBench
only, which gives enough questions for the comparison at a fraction of the cost.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                              # fill in the keys
python bot.py --mode test_questions               # forecasts three example questions, submits nothing
python bot.py --mode minibench --dry-run          # forecasts the open MiniBench questions, submits nothing
python bot.py --mode tournament                   # the tournament and MiniBench, submits
python -m pytest -q                               # offline tests, no keys needed
```

In GitHub Actions, `.github/workflows/run_bot.yaml` runs the bot every 30 minutes once the repository variable
`BOT_LIVE` is `true`; before that only manual runs start, and a manual `test_questions` run publishes nothing for
either bot. It needs the repository secrets
`METACULUS_TOKEN` and `OPENROUTER_API_KEY`, and the repository variable `BOT_MODEL` (an OpenRouter model id in
litellm form, e.g. `openrouter/openai/<model>`). Research is off by default (`BOT_RESEARCHER=no_research`); with
`BOT_RESEARCHER=scenariopilot` the bot also needs the variables `SP_SUPABASE_URL` and `SP_ANON_KEY` and the secrets
`SP_EMAIL` and `SP_PASSWORD`.

## Licence

MIT, see [LICENSE](LICENSE).
