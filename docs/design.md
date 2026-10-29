# SecOps Demo design

Goal: an executable Chinese security operations workbench demonstrating the agreed investigation, review, approval, simulated response and verification lifecycle. User requested Docker Compose deployment and eventual location D:/PythonProject/Agent_yizhiyun.

Scope: FastAPI serves a responsive static frontend; LangGraph coordinates context, evidence collection and reporting. SQLite persists incidents, tool traces, tickets, approvals, audit events and demo sessions. All security data and remediation connectors are explicitly simulated. Optional OpenAI-compatible model only drafts an evidence-grounded narrative; the deterministic policy owns classification and state transitions. No external security system is modified.

Roles: analyst investigates/reviews/verifies; manager additionally approves/rejects; operator executes approved simulated actions. Demo role switching is not production authentication. Approval binds a hash of the concrete action, initiator and expiry. An incomplete or failed query is not an absence of threats. HTTP 200 does not prove exploitation. Authorized scans require scoped authorization. Repeated actions are idempotent.

UI: workbench with current incident metrics and queue; incident detail with evidence, LangGraph tool trace and report; tickets; audit log; connector inventory; scenario simulation and role selector. The UI marks all mock data and simulated operations.

Deployment: Python 3.12 container, FastAPI port 8080, loopback host binding, named volume /app/data, healthcheck, restart unless-stopped, non-root user. No API key or Node build required. Host start.ps1 and install-to-target.ps1 are also included. The current sandbox cannot write the requested directory or access Docker Engine; implement under the writable session workspace and provide the explicit user-run target installation script.

Verification: lifecycle, role boundaries, incomplete evidence, malicious-but-blocked distinction, approval integrity/expiry, idempotent execution, verification failure, persistence, API validation/session checks, frontend browser journey and Compose configuration validation. Docker image build/run requires accessible Docker Engine and must not be claimed when unavailable.
