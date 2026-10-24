# Compass AI backend

An opportunity-intelligence API: admissions, scholarships, internships and jobs on one shared core
(profiles, a rules-based eligibility engine, matching, application tracking, reminders, and an
AI layer). FastAPI, PostgreSQL, SQLAlchemy, Alembic.

## Quick start

    cp .env.example .env          # Windows: copy .env.example .env
    docker compose up --build     # API on http://localhost:8000, docs at /docs
    docker compose run --rm api alembic upgrade head
    docker compose run --rm api python -m app.scripts.seed data/seed_sample.json --verify-demo

Create the test database once, then run the tests:

    docker compose exec db psql -U compass -c "CREATE DATABASE compass_test;"
    docker compose run --rm api pytest -v

Give a registered user a role: `python -m app.scripts.grant_role <email> data_manager`.

## Configuration

All settings are environment variables (see `.env.example`). In production
(`ENV=production`) the app refuses to start if a required setting is missing or unsafe.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL (a plain `postgresql://` string is accepted) |
| `REDIS_URL` | Rate limiting. If Redis is down, requests are allowed and a warning is logged |
| `JWT_SECRET` | 32+ random characters |
| `JOB_SECRET` | 16+ characters; protects `POST /internal/jobs/*` |
| `PUBLIC_BASE_URL`, `FRONTEND_CALLBACK_URL` | Used to build OAuth callbacks and redirects |
| `GOOGLE_*`, `GITHUB_*` | OAuth client IDs and secrets (optional) |
| `EMAIL_BACKEND` | `console` (dev), `smtp`, `resend`, `memory` (tests) |
| `AI_PROVIDER` | `mock` for now; see "AI layer" |
| `CORS_ORIGINS` | Comma-separated exact origins; empty disables CORS |
| `DOCS_ENABLED`, `ALLOW_OAUTH_PLACEHOLDER` | Demo conveniences; turn off for real use |

## Architecture

A modular monolith: one app, one database, modules in `app/modules/<name>` with `models`,
`schemas`, `service`, `router`. Modules call each other's services, never each other's tables.
Core logic is pure Python (no database) so it is fast to test: the eligibility engine
(`eligibility/engine.py`), the match scorer, reminder timing, the application status map,
skills-gap and career-path logic, and the AI output checks.

| Module | Purpose |
|---|---|
| auth | Sign-up and sign-in (email, Google, GitHub), tokens, lockout, audit trail, verification, reset |
| profiles | Profile, qualifications, skills, work experience |
| opportunities | Organizations, sources, admissions, scholarships, internships, jobs |
| eligibility | Rule trees and the deterministic engine (an AI can never change a result) |
| matching | Ranked, explained matches; eligibility caps the score |
| applications | Tracking, status map, append-only timeline, checklist |
| notifications | In-app notifications, deadline reminders, expiry, scheduled jobs |
| careers, goals, dashboard | Skills gaps, career paths, goals, one unified dashboard |
| strategy | Action plans: drafts the user accepts, edits or rejects |
| ai | Provider interface, orchestrator, seven agents, run tracking |

## Key rules

- Missing data is "unknown", never "fail". Unverifiable facts stay empty; nothing is invented.
- AI output is validated against the data it was given; a failing answer is replaced by a
  rules-based one. AI proposals (profile facts, checklist items, plans) need user approval.
- Users only ever see their own records (other people's ids return 404).
- Match scores are relevance rankings, not probabilities.

## AI layer

`AI_PROVIDER=mock` is a deterministic stand-in (keyword rules and templates), not a language model.
To add a real provider, implement a class with `name` and `complete(request)` in
`app/modules/ai/llm.py`, add it to `get_provider()` and its prices to `PRICES_PER_MILLION`.
User text (profile statements, drafts) is sent to the provider; no names, emails or account data.

## Jobs

`POST /internal/jobs/daily` (header `X-Job-Secret`) sends deadline reminders, expires lapsed
applications and closes stuck AI runs. A GitHub Actions workflow calls it daily. Jobs are
idempotent. To move to a worker later, change `enqueue` in `app/core/jobs.py`.

## Deployment (Not yet Implemented)

Free tier: Render (Docker, health path `/health/live`), Neon (Postgres), Upstash (Redis).
Migrations run on container start when `RUN_MIGRATIONS=true`. Free services sleep after
inactivity, so warm the service before demos. Free Render instances cannot use SMTP; use the
`resend` email backend.

## Adding an opportunity category

1. Add a details table and model, plus a relationship on `Opportunity`.
2. Update the `ck_opp_category` and `ck_goal_category` check constraints in a migration.
3. Schemas: `Category`, a details model, a create model in the union, `DETAIL_MODELS`, `OpportunityOut`.
4. Service: add it to `DETAIL_ORM`.
5. Matching: add weights and any new factor in `matching/scoring.py`.
6. Dashboard `CATEGORIES`, profile completeness, and tests.

## Known limitations

- No account deletion or deactivation endpoint yet, and no email change.
- Dependencies are not pinned to exact versions.
- Grades are self-reported; there is no reviewer workflow.
- Match refresh is manual; interest matching is keyword-based; locations need codes or city names.
- Free hosting is for demos, not real users.