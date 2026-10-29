# SecOps Demo Implementation Plan

> For agentic workers: execute inline using superpowers:executing-plans; use one fresh reviewer after implementation.

**Goal:** run a full-stack Chinese SecOps demo with one-command Docker deployment.
**Architecture:** FastAPI plus static browser client; LangGraph tool workflow; SQLite persistence; simulated connectors and optional narrative model.
**Tech Stack:** Python 3.12, FastAPI 0.135.3, Uvicorn 0.42.0, LangGraph 1.1.6, httpx 0.28.1, vanilla JavaScript/CSS.
**Spec:** docs/design.md

## Global Constraints
- Simulated sources/actions must be visible to users; no external remediation.
- Enforce roles, state versions, evidence completeness, approval hash/expiry and idempotency on the server.
- Bind to loopback; no key required; durable data outside image.
- Work in the permitted session directory; deliver a script for the requested target.

## Review Focus
- Duplicate mutations and stale browser actions must not duplicate tickets/actions.
- Missing evidence must not become a safe conclusion.
- Unknown or malicious input must not become HTML or SQL.
- Container restart must preserve completed work and surface interrupted investigations.
- Model errors must fall back without changing security policy.

## Task 1: workflow and persistence
- [ ] Write tests for lifecycle, unauthorized actions, incomplete evidence, authorization scope, expiry/hash and restart persistence.
- [ ] Verify RED with missing domain implementation.
- [ ] Implement app/storage.py, app/seed.py, app/tools.py, app/workflow.py, app/service.py.
- [ ] Verify the domain test suite GREEN.

## Task 2: API, frontend and deployment
- [ ] Write API tests for session enforcement, workflow mutation, validation and origin restrictions; verify RED.
- [ ] Implement app/main.py, app/static/{index.html,styles.css,app.js}, requirements.txt, Dockerfile, compose.yaml and start/install scripts.
- [ ] Run full tests, compile Python, check JavaScript, and validate Compose configuration.

## Task 3: running demonstration and handoff
- [ ] Start the app; inspect real browser pages; perform full review/approval/execution/verification journey and verify persistence.
- [ ] Get one independent code review, fix important issues with regression tests, and run the final suite.
- [ ] Package source, README and target installation script; report actual runtime URL and Docker/path permission limitations.
