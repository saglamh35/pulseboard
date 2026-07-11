# AI weekly coach

An opt-in section in the weekly report: an LLM reads the report's numbers —
this week vs last, goals and streaks, sleep debt, training load, today's
scores and your 90-day correlations — and writes a short, motivating
coach's note with 2-3 gentle goals for next week.

> **Informational only, not medical advice.** The model is explicitly
> instructed never to diagnose and to say when the data is too sparse to
> conclude anything. Treat the output as a nudge, not a verdict.

Two ways to use it, combinable:

1. **A configured provider** writes the summary into the report itself
   (local Ollama by default, or a cloud API).
2. **The no-key phone path**: `GET /coach/prompt` returns a ready-made
   prompt, and the HTML report has "Ask Claude / Ask ChatGPT" links that
   open the chat app with the prompt prefilled — no API key, nothing leaves
   your machine until *you* paste or tap.

## Quick start with Ollama (local, recommended)

Health data never leaves your machine — the model runs on it.

```bash
# with the compose stack
docker compose --profile ai up -d
docker compose exec ollama ollama pull gemma3:4b

# or a host-installed Ollama
ollama pull gemma3:4b
```

Then set the provider (e.g. in a git-ignored `.env`, see `.env.example`):

```bash
PULSEBOARD_AI_PROVIDER=ollama
# host-installed Ollama instead of the compose service:
# PULSEBOARD_OLLAMA_URL=http://127.0.0.1:11434
```

That's it: the next weekly report (CLI, cron, compose reporter, Helm
CronJob) gains a **Coach (AI)** section. `gemma3:4b` is a light model — a
summary takes seconds to a minute on CPU.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PULSEBOARD_AI_PROVIDER` | unset = feature **off**, zero LLM calls | `ollama` \| `anthropic` \| `openai` \| `gemini` |
| `PULSEBOARD_AI_MODEL` | `gemma3:4b` / `claude-haiku-4-5` / `gpt-5-mini` / `gemini-2.5-flash` | model override for the active provider |
| `PULSEBOARD_OLLAMA_URL` | `http://127.0.0.1:11434` (`http://ollama:11434` in compose) | Ollama base URL |
| `PULSEBOARD_ANTHROPIC_API_KEY` | falls back to `ANTHROPIC_API_KEY` | Anthropic (Claude) key |
| `PULSEBOARD_OPENAI_API_KEY` | falls back to `OPENAI_API_KEY` | OpenAI (GPT) key |
| `PULSEBOARD_GEMINI_API_KEY` | falls back to `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Google (Gemini) key |

Where the coach runs:

- **CLI / cron / reporter / CronJob** — automatic when a provider is
  configured; `--no-coach` skips it.
- **`GET /report/weekly`** — opt-in per request with `?coach=1` (a local
  model can take a minute; the endpoint stays fast by default).
- On any provider error the report simply renders without the coach
  section — it never fails because the LLM did.

Kubernetes: put the AI variables in a Secret and set `report.aiSecret` in
the Helm values (kept separate from `notifySecret` so the two can be
rotated independently).

## The phone path (no API key)

- `GET /coach/prompt` returns the full big-picture prompt as plain text;
  `?format=json` adds prefill links. Paste it into any chat AI — Claude,
  ChatGPT, Gemini, whatever you use.
- The HTML weekly report's footer has **Ask Claude** / **Ask ChatGPT**
  links that open the app with the prompt prefilled. Gemini has no reliable
  prefill URL, so for Gemini copy the prompt from `/coach/prompt`.
- Privacy: these are plain local text until you tap or paste — sending the
  digest to the chat provider is your explicit action each time.

## Security — read this, the repo is public

- Keys are read from **environment variables only** — never from a config
  file in the repo, never a CLI flag. Use a `.env` file (git-ignored; see
  `.env.example`) or your shell/secret manager. **Never commit a real key.**
- Keys are sent only in request **headers** (the Gemini key deliberately
  goes in `x-goog-api-key`, not the `?key=` URL parameter — URLs end up in
  logs and error messages, headers don't).
- Logs mention the provider and model name only; no endpoint echoes
  configuration. `python -m pulseboard.doctor` reports key *presence*
  ("API key set"), never the value.
- Privacy trade-off, stated plainly: with `ollama` everything stays on your
  machine; with a cloud provider the report digest (your weekly numbers) is
  sent to that provider. Choose accordingly.
