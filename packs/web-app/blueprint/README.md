# example-product

Blueprint skeleton of a pilot product repository (web-app engineering pack,
T-070). Copy this tree into a new product repository as-is, then rename the
placeholders. Stack (fixed by the pack, ADR-014/ADR-015/ADR-019):
FastAPI + SQLAlchemy async + PostgreSQL on the backend, React + Vite +
TypeScript with a `@small/ui` workspace stub on the frontend, Helm chart for
deployment.

## Layout

```
.github/workflows/ci.yml   CI: gates + trusted image builds (sha-<sha>, main-only publish)
backend/                   FastAPI app (src/app), Alembic migrations, pytest suite
frontend/                  React+Vite SPA; packages/ui = @small/ui workspace stub
deploy/Dockerfile.backend  backend OCI image (digest-pinned, non-root, uv.lock-frozen)
deploy/Dockerfile.frontend frontend OCI image (nginx-unprivileged, serves dist/)
deploy/chart/              Helm chart: backend + frontend + in-chart PostgreSQL
```

## Run locally

Backend (Python 3.12, uv):

```sh
cd backend
cp .env.example .env          # set DATABASE_URL (secret - never commit .env)
uv sync                       # creates .venv from uv.lock
uv run alembic upgrade head   # apply migrations
uv run uvicorn app.main:app --reload
```

Endpoints: `GET /` - liveness (no database); `GET /api/healthz` - readiness
(pings PostgreSQL, 503 while it is down).

Frontend (Node >= 22, npm workspaces):

```sh
cd frontend
npm ci          # installs the workspace + @small/ui
npm run dev     # vite dev server; /api is proxied to http://127.0.0.1:8000
```

Tests:

```sh
cd backend  && uv run pytest                       # unit always; integration needs APP_TEST_DATABASE_URL
cd frontend && npm run lint && npm run typecheck && npm run test
```

## Deployment

1. CI on `main` publishes immutable images `sha-<sha>` to ghcr.io (backend and
   frontend) and uploads scan evidence. Non-main branches build but never
   publish.
2. A GitOps MR replaces the `sha256:__..._IMAGE_DIGEST__` placeholders in the
   chart values with the real digests (pattern:
   `deploy/argocd/gitops-seed/examples/gitops-mr-digest-change.md` of the
   factory repository). Floating tags are forbidden.
3. Argo CD applies the release into the pilot namespace:

   ```sh
   helm upgrade --install example-product deploy/chart \
     -n apps-dev -f deploy/chart/values-apps-dev.yaml
   ```

4. Migrations run automatically as a pre-install/pre-upgrade hook Job
   (`alembic upgrade head`) before the backend rolls out.

The database secret (`example-product-db`, keys `DATABASE_URL` and
`POSTGRES_PASSWORD`) is created outside git:

```sh
kubectl -n apps-dev create secret generic example-product-db \
  --from-literal=POSTGRES_PASSWORD='<password>' \
  --from-literal=DATABASE_URL='postgresql+psycopg://postgres:<password>@example-product-postgres:5432/example_product'
```

## Placeholders to rename

| Placeholder | Where |
|---|---|
| `example-product` / `example_product` | package names, DB name, secret name, chart release |
| `example-org` | image repositories (chart values, CI env) |
| `@small/ui` stub | `frontend/packages/ui` - replaced by the real Small UIKit (T-071) |
| `sha256:__*_IMAGE_DIGEST__` | chart values - replaced by the GitOps MR |
