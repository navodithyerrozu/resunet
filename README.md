# ResuNet — AI Career Assistant

ResuNet turns rough achievement notes into tailored, XYZ-formatted resume
bullets and a compiled PDF, without inventing numbers. You write what you
did; the agent drafts the bullet, checks it deterministically, asks you for
a missing metric instead of making one up, and only then lets it into a
render.

## What it does

- **Conversational generation** — a LangGraph agent mines your notes into
  achievements, drafts one XYZ bullet per achievement (action / metric /
  method), and validates each draft in code before it is saved.
- **Clarify before drafting** — an achievement with no number in the notes
  is never drafted. The agent interrupts and asks you for the metric first.
  The same rule applies to the method: a draft whose tools do not appear in
  your notes is sent back for repair, never silently accepted.
- **Deterministic validation** — the XYZ check is pure Python (a number must
  be present in the metric; the method's tools must appear in the notes),
  not an LLM judging itself. It gates persistence and rendering.
- **Assign, edit, approve, render** — generated bullets land in an
  Unassigned pile; you assign them to a section, edit them inline if
  needed (an edit resets the bullet to draft and it must be re-approved),
  and render a PDF only when every bullet in the resume is approved.
- **Job matching and ATS scoring** — jobs are ingested from public
  Greenhouse/Lever boards and matched against your verified skills; ATS
  scores are computed deterministically from the rendered text.

## Stack

- **API** — Python 3.12, FastAPI, Pydantic, Motor (MongoDB), python-jose
  JWT auth, LangGraph / LangChain for the agent, ARQ + Redis for queues
- **Web** — Next.js 15, React 19, TypeScript, Tailwind CSS 4
- **Render worker** — Jinja2 + pdflatex (TeX Live), three ATS templates
- **Automation worker** — Playwright (form automation; code and tests only,
  no live portal submission has been verified)
- **Data** — MongoDB 7 (single-node replica set), Redis 7
- **LLM** — Anthropic by default, or a local model via Ollama
  (`llama3.1:8b`); see below
- **Infra** — Docker Compose, six services: mongo, redis, api,
  render-worker, automation-worker, web

## Run it locally

Prerequisites: Docker and Docker Compose, plus Ollama if you want the local
model (see below).

```bash
cp .env.example .env        # then fill in secrets; never commit .env
docker compose up -d --build
```

- Web: http://localhost:3000
- API: http://localhost:8000 (liveness: `/health/live`)

### Local model (Ollama)

The default `LLM_PROVIDER` is `anthropic` and needs an `ANTHROPIC_API_KEY`.
To run entirely on your own machine instead:

```bash
ollama pull llama3.1:8b
```

Then in `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

Restart the API after changing `LLM_PROVIDER` — the setting is read at
startup. The compose file already points the API container at the host's
Ollama (`http://host.docker.internal:11434`). The model is about 4.9 GB;
on a 4 GB-VRAM GPU it runs partly on the GPU and partly on the CPU, and a
cold first generation turn can take a couple of minutes — the frontend
allows 210 seconds for a generation turn for exactly that reason.

## The generation loop

```
notes → mine → clarify (only if a metric is missing) → draft
      → validate → accept / repair / flag → assign → render PDF
```

1. **Mine** — notes are split into candidate achievements, each tagged
   with whether it already carries a metric. A fragment with no digit
   cannot carry a metric, whatever the model claims.
2. **Clarify** — a metric-less achievement interrupts the run and asks you
   one question. Drafting never happens first.
3. **Draft** — the model produces X, Y, Z as separate fields via structured
   output; the sentence is assembled in code, never by the model.
4. **Validate** — deterministic checks: metric contains a number,
   method tools appear in the notes, strong past-tense verb, no
   first person, under 240 characters. Failures go to repair (bounded
   retries) or are flagged for review. Nothing non-compliant is accepted.
5. **Assign** — bullets are saved against the generation thread and shown
   as Unassigned until you place them in a section.
6. **Render** — rendering refuses while any section bullet is not
   approved. A version is snapshotted immutably, then compiled to PDF by
   the render worker.

## Development

```bash
make typecheck          # mypy (API) + tsc --noEmit (web, shared)
pnpm gen:types          # regenerate shared TS types from the OpenAPI schema
```

Tests:

```bash
cd apps/api && .venv/bin/python -m pytest tests -q
cd apps/render-worker && .venv-render/bin/python -m pytest tests -q
```

## Honest limits

- **Local-only.** There is no production deployment and no public demo;
  the app runs on your machine behind Docker Compose.
- **No live auto-apply.** The automation worker exists as code and tests;
  submitting a real application through a portal has not been verified,
  and the resume must not claim it has.
- **The discovery digest is not in this repo's main branch.** A daily
  job-discovery digest was built locally only; it is not pushed, not
  scheduled, and not part of the product until its board list, schedule,
  and delivery surface are decided.
- **Method provenance is token-based.** The method gate checks that the
  method's substantive words appear in your notes. A method restated in
  completely different words can be flagged and need a repair or a manual
  edit; that is the safe error, by design.
- **Manual edits are yours.** A bullet you edit by hand is checked for XYZ
  shape only, not against source notes — you are the author there, not
  the agent.
```

## Repo layout

```
apps/api              FastAPI backend, LangGraph agent, MongoDB models
apps/web              Next.js frontend
apps/render-worker    Jinja2 + pdflatex PDF compilation
apps/automation-worker  Playwright form automation
packages/shared       TypeScript types generated from the API's OpenAPI schema
```
