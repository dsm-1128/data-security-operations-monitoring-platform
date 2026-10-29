# Execution ledger

Plan: docs/implementation-plan.md
Preflight: service APIs own all state transitions; workflow can update only investigating cases; UI consumes incident.version for mutations.
Ruling: user explicitly requested running the previously designed demo, so implement without another design approval round.
Ruling: target directory is outside writable roots; use this workspace and provide install-to-target.ps1. Docker Engine access is denied; validate configuration and run host app, without claiming container execution.

Task 1: complete. Domain tests first failed on absent implementation, then 10/10 passed.
Task 2: complete. API tests first failed on absent implementation, then full suite 14/14 passed. Compose config and JS syntax passed.
Final independent review: no critical issues; stale modal versions and expired execution recovery identified as important. SQLite connection ownership and unknown-event HTTP errors regraded as important for reliable Windows runtime/API behavior.
Final fixes: captured modal ID/version; verified old dialog rejection in two browser tabs. Added execution history and fresh investigation path after expiry/failure; explicit connection closing; typed missing-incident exception mapped to HTTP404. Three new regression tests observed RED then full suite 18/18 GREEN. Approval reopening also covered RED→GREEN.
Browser: SQL injection demo investigated, reviewed by analyst, approved by manager, simulated by operator, verified and closed by analyst. Sidebar counts and server state updated. Desktop 1440px and narrow 376px layouts visually inspected.
Ruling: this is a local single-service demo, with SQLite and simulated Python tools rather than independent MCP/A2A servers; accurately documented in README. Production adapters/IAM/queues remain future work.

Task 3: complete. Browser verified normal remediation, authorized activity closure, missing-evidence rejection/manual attention, scenario creation, tickets and audit. Restarted host service and confirmed 6 incidents, 2 resolved, 3 tickets and 21 audit events persisted. Fixed modal error visibility based on observed browser behavior. Final suite 18/18 passed; compile/JS syntax/Compose parsing passed. Source package: 29 files, excludes keys/database/logs/caches, ZIP integrity passed. Docker build/run and requested target write remain blocked by environment permissions; installer is ready for the user to run in their own PowerShell.
