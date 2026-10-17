# RAINY TRANSPARENCY — Mongolia open data transparency platform

**Status: runnable foundation / local MVP, not production-complete.** Based on `RAINY_Transparency_Architecture_MN.docx` (2026-10-17). It never represents synthetic data as actual tenders and never infers guilt from risk scores.

## Architecture

Next.js 15 (Mongolian responsive light UI) → FastAPI → PostgreSQL. A separate import worker reads explicitly supplied authorized JSON exports. Raw records keep SHA-256, source URL and fetch time. Identity resolution never merges by company name alone. R01 one-bid signal requires analyst review. API source status signals missing data.

## Local run

```bash
cp .env.example .env
# edit both secrets in .env
docker compose up --build -d
```

Open http://localhost:3000, API docs http://localhost:8000/docs. Dashboard starts EMPTY intentionally: no unverified statistics.

## Import already-authorized normalized records

The sample dataset is **illustrative and NOT real tender information**. For local demonstration ONLY:

```bash
# From project root on host with python dependencies installed:
cd backend
DATABASE_URL=sqlite:///./transparency.db python -m app.worker --file ../samples/EXAMPLE_ONLY_tenders.json
```

To import into the Docker Postgres DB, first place an authorized JSON export in the backend directory (e.g. `backend/authorized_tenders.json`; do not commit sensitive data), then:

```bash
docker compose cp backend/authorized_tenders.json api:/tmp/authorized_tenders.json
docker compose exec api python -m app.worker --file /tmp/authorized_tenders.json
```

For a single authorized normalized record use `POST /api/v1/admin/import/tender`, requiring header `x-api-key: <ADMIN_API_KEY>`. Execute `POST /api/v1/admin/evaluate` afterward to calculate **R01 only**. Other rules R02–R10 are scoped, not implemented. Search and read endpoints are public; import, calculation and review are protected by admin key. Production must implement login/RBAC, rate limiting, migrations, audit coverage for all mutations, monitoring, source-specific validation and tests before external release.

## Upstream integrations are deliberately not faked

`https://opendata.tender.gov.mn/` requires API access according to supplied architecture. This package **does not have its bearer token or verified endpoint response schema**, and therefore does **not** pretend to connect automatically. Implement a source adapter only after verifying official access terms, exact URL/fields, pagination and permissions. The same applies to Shilendans, Audit, Courts, IAAC, SOE and legal person data. No login circumvention or automated scraping is included.

## Implemented API

- `GET /health`, `/api/v1/stats`, `/api/v1/source-status`
- `GET /api/v1/search?q=`, `/api/v1/tenders`, `/api/v1/tenders/{id}`
- `GET /api/v1/companies/{id}`, `/api/v1/companies/{id}/tenders`, `/api/v1/organizations/{id}`
- `GET /api/v1/entities/{id}/relationships`, `/api/v1/graph?entity_id=`, `/api/v1/risks`, `/api/v1/evidence/{id}`
- `POST /api/v1/admin/import/tender`, `/api/v1/admin/evaluate`, `/api/v1/reviews/{id}` (admin key)

## Priority to reach production

1. Validate official tender API credentials and canonical field mapping + source-specific data licenses.
2. Alembic migrations, robust transactional import, data freshness and quality checks.
3. RBAC + authenticated analyst/reviewer accounts + comprehensive audit logs.
4. R02–R10 implemented only where comparable, verified facts exist; build reviewer workflow.
5. Authorized integrations for budgets, contracts, audit with cited documents; public correction process.
6. Graph rendering, safe text extraction, independent security/privacy review and deployment monitoring.

**Legal/privacy:** Restrict personally identifying information, document approvals and correction requests; keep risk severity separate from evidence confidence and require human review. This is an informational platform, not a finding of wrongdoing.
