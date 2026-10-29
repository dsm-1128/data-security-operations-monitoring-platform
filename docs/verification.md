# Demo verification

Date: 2026-10-17. Scope: offline local demo, not a production security evaluation.

## Automated

- Python 3.12 environment: `python -m pytest -q` -> **18 passed**.
- Two upstream test-client deprecation warnings (httpx/Starlette and anyio alias) remain; no failing tests.
- `python -m compileall -q app` -> passed.
- `node --check app/static/app.js` -> passed.
- `docker compose config --quiet` -> passed. Docker client emits a sandbox warning reading the user's config.
- PowerShell AST parsing of start.ps1 and install-to-target.ps1 -> passed.
- install-to-target.ps1 copied into a separate allowed empty test directory with -NoStart -> passed; source and compose.yaml present.

Tests cover lifecycle persistence, execution idempotency, role denial, stale versions, unknown events, incomplete evidence, blocked-but-malicious classification, authorized scans, approval expiry and parameter hash, verification failure, account actions, interrupted investigation recovery, fresh investigation after expired execution with archived history, session/origin checks and connection closure.

## Actual browser journeys

- Workbench and incident layout inspected at a desktop breakpoint (1440px) and narrow default viewport (about 376px).
- SQL injection incident: actual LangGraph tools -> evidence/report -> analyst review -> manager approval -> operator simulated action -> analyst verification -> closed incident and closed ticket.
- Two tabs: keep review modal open, investigate again in second tab, submit old first-tab review -> rejected with version conflict; reopening with current evidence succeeds.
- Missing application logs: report remains inconclusive; attempting false-positive review -> rejected with visible inline modal error; inconclusive review -> manual attention and open investigation ticket.
- Authorized scan: scope/time/source evidence -> analyst review -> verification -> closed without a remediation action.
- UI scenario ingestion created a new persisted event. Ticket, audit and connector pages inspected.
- Host process restarted after the browser journeys: 6 events, 2 closed, 3 tickets and 21 audit records remained available.

## Deployment boundaries

- Host preview: **http://127.0.0.1:18080**. Uses the same app code and a local SQLite file.
- Docker configuration: one non-root Python service, loopback host port 8080 by default, persistent named volume and HTTP healthcheck.
- Docker Engine access was denied at its named pipe in this session. **The image was not built and the container was not started or runtime-tested.** No permission or Docker settings were changed.
- Requested target D:/PythonProject/Agent_yizhiyun is outside the session's writable roots. Source was created under D:/PythonProject/Codex/2026-10-17/new-chat/Agent_yizhiyun. User-run install-to-target.ps1 copies to the requested empty target and starts Docker.

## Intentionally limited

- Simulated data sources/actions; optional model summary not required and not a replacement for evidence review.
- Demo role selection, not enterprise IAM; single-tenant local application.
- SQLite, single Uvicorn process, in-process background tasks; interrupted investigation is surfaced for manual retry rather than claiming automatic replay.
- Tool registry runs in-process; standalone MCP/A2A integrations, Redis/Milvus and external response products are not implemented.
