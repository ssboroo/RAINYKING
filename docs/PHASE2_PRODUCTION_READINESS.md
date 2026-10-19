# RAINYKING Phase 2 — Production Readiness Audit

Audit date: 2026-10-19
Repository: https://github.com/ssboroo/RAINYKING
Branch inspected: `feature/evidence-ownership-discovery`
Inspected head: `04ac332b556c203e22247aea3df454da55ecb152`
Default branch at audit time: `main`

## Status legend

- **PASS** — directly confirmed by repository configuration or a completed CI run.
- **FAIL** — the inspected code/configuration does not provide the claimed capability.
- **NOT VERIFIED** — requires runtime credentials, deployed-environment access, production data, or a test not present in the repository/CI.

This report is a repository/configuration audit, not a claim that the live production environment was accessed.

## Evidence-based findings

| Area | Status | Evidence / limitation |
|---|---|---|
| Repository accessible; main and feature branches exist | PASS | GitHub repository metadata and branch refs were readable. |
| Backend compile + test CI | PASS | GitHub Actions run [39191545775](https://github.com/ssboroo/RAINYKING/actions/runs/39191545775): backend job completed successfully, including `python -m compileall -q backend/app` and `pytest -q`. |
| Frontend production build CI | PASS | The same run's frontend job completed successfully, including `npm install` and `npm run build`. |
| Dedicated lint and static type-check CI | NOT VERIFIED | The checked-in `.github/workflows/ci.yml` does not run a dedicated linter or static type checker. A successful build is not equivalent to either check. |
| Schema migration framework and versioned migrations | FAIL | The inspected code calls `Base.metadata.create_all(engine)` during application startup and in the worker. The repository tree inspected does not contain an Alembic migration directory/config. This creates tables but is not a versioned production migration strategy. |
| Backup restore and production rollback | NOT VERIFIED | No successful backup/restore run or live deployment rollback was available in the inspected repository evidence. |
| Registered-user authentication and role-based access | FAIL | The inspected backend has a `require_admin` dependency based on a shared `X-API-Key`; no registered-user login/session/OIDC flow was found in the inspected backend entrypoint. This is not a complete user authentication and RBAC system. Public read endpoints may intentionally remain public. |
| Admin route guard | PASS (limited) | Admin source discovery uses `Depends(require_admin)`; the guard denies requests when the configured admin key is missing or mismatched. This does not prove every privileged route is guarded. |
| Durable job queue / failed-job recovery | FAIL / NOT VERIFIED | The repository includes an optional periodic worker and sync-run records, but the inspected dependencies/configuration do not establish a durable queue with persisted retries, backoff, dead-letter handling, and replay. Runtime recovery is not verified. |
| Source provenance and content versioning | PASS (implemented; completeness NOT VERIFIED) | `SourceRecord` stores source ID, external ID, raw payload, SHA-256, source URL, parser version, and fetched timestamp; uniqueness is scoped to source, external ID, and content hash. Full live-source history/retention behavior is not verified. |
| Evidence-backed relationship schema | PASS (implemented; correctness NOT VERIFIED) | Relationship and Evidence models include source record references, verification status/confidence and document evidence fields. Referential semantics and all API response paths need further tests. |
| Search normalization | PASS (partial) | Mongolian/English aliases and Unicode NFKC normalization exist. Transliteration equivalence and production search relevance/latency are not established by the inspected configuration. |
| SSRF and public-source fetch controls | PASS (partial) | The inspected crawler enforces source-domain scope, HTTPS/public URL validation, robots rules, redirect limits and byte limits. Full DNS rebinding/redirect edge-case security testing is NOT VERIFIED. |
| Audit logging coverage | NOT VERIFIED | An audit-log model exists; complete logging of every privileged mutation and tamper-resistance were not proven. |
| Load/concurrency tests | NOT VERIFIED | The inspected CI workflow does not run load, concurrency, or performance benchmarks. |
| Live external source sync | NOT VERIFIED | Code and connector definitions exist, but no current authenticated source credentials or production sync run were independently verified in this audit. |
| Production secrets and environment | NOT VERIFIED | Repository templates are not evidence that Railway/Cloud Run production environment variables are configured safely. No secret values were requested or inspected. |

## Highest-priority remediation order

1. **Migration safety:** add a versioned migration framework and a safe adoption/baseline procedure. Do not run a first migration against a live database until existing schemas are inventoried and a restorable backup is confirmed.
2. **Authorization coverage:** enumerate every mutating/admin route and test that missing or invalid credentials are rejected. Decide explicitly whether registered-user accounts are required; do not describe the shared admin key as full user authentication.
3. **Durable sync execution:** define persisted job states, bounded retries with exponential backoff, timeouts, idempotency, and failed-job replay. Add a queue only if supported by the deployment architecture.
4. **Search/data quality:** add regression tests for Unicode normalization, aliases, registration matching, amount/date parsing, duplicate imports, and provenance.
5. **Operational verification:** add explicit lint/type-check steps if selected tools are configured, plus migration, security, concurrency, backup/restore, staging smoke and rollback tests.
6. **Deployment evidence:** record environment, commit SHA, test report, backup timestamp, restore result, and smoke-test result for each release.

## Safety boundaries

- Do not merge or deploy to production as a consequence of this audit.
- Do not alter live data or run destructive migrations.
- Do not classify a person or organization as corrupt based on an inferred relationship or a risk signal alone.
- Every relationship and material claim must retain a traceable source and a clear verification status.
- Keep credentials out of commits and audit logs.

## Current conclusion

The inspected feature branch has a successful GitHub CI run for backend compilation/tests and frontend build. It is **not yet proven production-ready** by this evidence. Versioned migrations, full user authentication/RBAC, durable job recovery, dedicated lint/type checking, load testing, and live backup/restore/deployment verification remain gaps or unverified items.
