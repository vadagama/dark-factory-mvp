# Dark Factory Console

Operator web console of the Software Dark Factory (task T036, delivery per
[ADR-021](../docs/adr/ADR-021-console-mvp-delivery.md)). Five screens over the
T035 API: changes list, change card, gates/approvals, budgets/limits,
settings/profiles.

Stack: **React 19 + TypeScript (strict) + Vite 8**, Radix primitives, plain CSS
design tokens, `react-router` v7. No Tailwind, no react-query/axios (see
"Deliberate simplifications").

## Run

Requires Node 22 (see `.nvmrc`; `nvm use` or any Node >= 22).

```bash
npm ci
npm run dev          # dev server with /api proxy → VITE_API_TARGET (default http://127.0.0.1:8000)
```

Build and preview the production bundle:

```bash
npm run build        # outputs dist/
npm run preview      # serves dist/ on http://localhost:4173
```

## Tests

```bash
npm run test         # vitest: unit (api client, hooks, meta) + component tests
npm run e2e          # builds dist/, serves it via vite preview and runs the Playwright smoke suite
```

The Playwright suite intercepts every `api/v1` request with `page.route` and
answers from synthetic fixtures (`e2e/fixtures/api.ts`) — hermetic, no running
API or database needed. First run needs a browser:
`npx playwright install chromium`.

## Screens

| Route | Screen | Data |
|---|---|---|
| `/` | Changes list + intake form | `GET /changes`, `GET /runs`; intake: `POST /changes` |
| `/changes/:id` | Change card: runs, stages, evidence, usage, blockers, stage chain (SC-007) | `GET /changes/{id}`, `/changes/{id}/trace`, `/runs/{id}`, `/runs/{id}/evidence`, `/runs/{id}/findings` |
| `/changes/:id/gates` | Gates, open blocker findings, approvals history, version-bound approval form | `GET /runs/{id}/gates`, `/runs/{id}/findings`, `/changes/{id}/approvals`; `POST /changes/{id}/approvals` |
| `/budgets` | Configured limits vs actual usage | limits from `src/generated/meta.json`; usage from `GET /runs` + `GET /runs/{id}` |
| `/settings` | Operator token, API base URL, factory mode, role profiles | localStorage + `meta.json` |

## Architecture

```
console/
├── e2e/                  # Playwright smoke suite + synthetic API fixtures
├── src/
│   ├── api/              # thin typed fetch client (client.ts), wire types (types.ts),
│   │                     # token/localStorage store (token.ts), base URL (settings.ts), useAsync hook
│   ├── components/       # Section/badges/Layout, Radix token dialog, intake form
│   ├── generated/        # meta.json — COMMITTED snapshot, generated from Python sources
│   ├── lib/              # formatting, meta accessors, id helpers
│   ├── pages/            # five screens (react-router)
│   └── test/             # vitest setup + shared fixtures / fetch stub
└── tools/
    └── export_meta.py    # regenerates src/generated/meta.json (see below)
```

**API client** (`src/api/client.ts`): plain `fetch`, no client libraries.
Reads (GET) never carry the token; writes (POST) attach
`Authorization: Bearer <token>` and an `Idempotency-Key` (UUID v4). Errors are
RFC 7807-like bodies surfaced as `ApiError` with `isUnauthorized` (401),
`isForbidden` (403) and `isStateRevisionConflict` (409) helpers. Wire types in
`src/api/types.ts` mirror the domain pydantic models of `src/dark_factory/`
(`docs/descriptions/api.md` is the contract): datetimes are ISO strings, `cost`
is a decimal string.

**Optimistic locking**: the API does not expose a change's `state_revision` on
reads. The console derives `expected_state_revision = 1 + decisions_count`
(a change row starts at 1, each recorded approval bumps it). On 409 the
console re-reads the card, re-derives the revision and retries once with the
same Idempotency-Key (a 409 means nothing was written).

**Token model** (ADR-021 p.4): the operator token lives only in
`localStorage`, entered on the settings screen (masked input, masked display —
only the last 4 chars are shown). It is never logged, never put in URLs, and
attached only to POST requests. Losing the cache is acceptable by design
(SC-008): the only loss is the token itself.

**Factory mode / profiles**: rendered from the committed `meta.json` snapshot.
The mode display ("С согласованиями" vs "Автономно до MR") is informational —
derived from human gates and auto-merge risk classes; there is no API to
switch modes in the MVP.

## meta.json snapshot

`src/generated/meta.json` is generated from the Python sources of truth
(`src/dark_factory/rules`, `src/dark_factory/agents/profiles`):

```bash
uv run python console/tools/export_meta.py
```

The file is deterministic (fixed key order); a drift test
(`tests/test_console_meta_snapshot.py`) fails the pytest suite when the
committed snapshot no longer matches the Python sources — regenerate and
commit it after changing limits or profiles. The generator runs without node
and the test runs in the plain pytest CI job.

## Deliberate simplifications (MVP, per ADR-021)

- **No Tailwind.** ADR-014 names Radix/shadcn as the direction, but shadcn on
  Tailwind would pull a styling toolchain into the MVP. The console uses
  Radix primitives (`@radix-ui/react-dialog`, `@radix-ui/react-label`) plus
  plain CSS with design tokens (`src/index.css`). This is a deliberate
  simplification of the shadcn layer: the component files follow the shadcn
  shape (Radix primitive + styled wrapper) and can graduate to a real UI kit
  without API changes. The console does **not** define the product UI kit
  (ADR-014 p.4).
- **No react-query/axios.** Five read-mostly screens; a `useAsync` hook over
  the typed client is enough. Cache invalidation is an explicit `reload()`.
- **react-router v7** in declarative mode with plain hooks — data loaders /
  framework mode were skipped to keep the toolchain minimal; `useAsync` covers
  fetching, `Link`/`useParams` cover navigation.
- **`meta.json` snapshot** is a generated copy of Python state, refreshed
  manually (guarded by the drift test). Acceptable tech debt until a live
  config endpoint exists.
- **Intake is minimal**: title, provider/slug, risk class, description; the
  change id is generated client-side (`chg_<hex>`), `source` is always
  `console`, `change_request`/`external_ref` are out of scope.
- **Approval form resets on card reload** (the `expected_state_revision` field
  is re-derived from the fresh card via a component key).
- **E2E fixtures are inline TypeScript** (`e2e/fixtures/api.ts`) instead of
  JSON files — the same synthetic data, but type-checked against the wire
  types.
