# Dark Factory Console

Operator web console of the Software Dark Factory (task T036, delivery per
[ADR-021](../docs/adr/ADR-021-console-mvp-delivery.md); information
architecture per ADR-037: milestone M1 — tasks T075/T076, milestone M2 — the
ChangeSet workspace, the requirements phase and the markdown editor, tasks
T088–T090; milestone M3 — the architecture and interface phases and the UI
gate, tasks T095–T097). Products are the index; each product and change shows the
server-computed «Следующий шаг» (ADR-033); a new change is created from the
product through an agent-assisted intake and then lives in the ChangeSet
workspace (`/changes/:id`). The T036 screens (change card at `/changes/:id/card`,
gates/approvals, budgets, CI stages, settings) are kept and the service ones
moved under `/service`.

Stack: **React 19 + TypeScript (strict) + Vite 8**, Radix primitives, plain CSS
design tokens, `react-router` v7, `react-markdown` + `remark-gfm` for the
rendered document modes of the editor, `mermaid` (lazy chunk) for the diagrams
of the design overview. No Tailwind, no react-query/axios (see "Deliberate
simplifications").

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

Global navigation (ADR-037): `Продукты · Требует внимания · Активность · Служебное`.

| Route | Screen | Data |
|---|---|---|
| `/` | Products: readiness badge, repository, number of changes, «Добавить продукт» form | `GET /products`, `GET /changes` (grouped by `product_id`); `POST /products` |
| `/products/:id` | Product page: header, **NextStep**, «Обзор», «Изменения», «База», «Доставки»; «Новая фича», «Проверить репозиторий» | `GET /products/{id}`, `/products/{id}/guidance`, `/changes?product_id=`; `POST /products/{id}/validate` |
| `/products/:id/new-change` | Intake (T076): free text → «Помоги сформулировать» → brief fields, scenario, spend limit, risk class → «Создать задачу» | `GET /products/{id}`; `POST /briefs/formulate`; `POST /changes` |
| `/changes/:id` | **ChangeSet workspace** (T088, ADR-037 p.4): top panel (title, id, current phase + state, budget fact/limit/forecast, blockers), left column of phases Ф0–Ф7 from the server projection (state, open counts, iteration), centre tabs `Результат · Изменения · Проверки · История` (or the markdown editor of an opened artifact), collapsible right context panel (fragment discussion, comments, rework orders), bottom decision panel = **NextStep** with one CTA + the approval form | `GET /changes/{id}`, `/guidance`, `/approvals`, `/runs/{id}`, `/changes/{id}/phases`, `/questions`, `/comments`, `/rework-orders`, `/phase-gate?phase=`, `/decisions`, `/ui`, `/artifacts`, `/artifacts/{path}`; `POST /changes/{id}/approvals`, `/questions/{qid}/answer`, `/comments`, `/comments/{cid}/close|reopen`, `/rework-orders`, `/decisions/{did}/alternative`, `/artifact-views/{path}`; `PUT /artifacts/{path}`, `PUT|DELETE /artifact-drafts/{path}`; `GET /artifact-versions/{path}`, `/artifact-diff/{path}` |
| `/changes/:id/card` | M1 change card: **NextStep**, scenario/limit/product line, «Бриф» with inline editor, runs, stages, evidence, usage, blockers, stage chain (SC-007) | `GET /changes/{id}`, `/changes/{id}/guidance`, `/changes/{id}/trace`, `/runs/{id}`, `/runs/{id}/evidence`, `/runs/{id}/findings`; `PUT /changes/{id}/brief` |
| `/changes/:id/gates` | Gates, open blocker findings, approvals history, version-bound approval form | `GET /runs/{id}/gates`, `/runs/{id}/findings`, `/changes/{id}/approvals`; `POST /changes/{id}/approvals` |
| `/changes` | Legacy flat changes list + minimal intake (no longer the index) | `GET /changes`, `GET /runs`; `POST /changes` |
| `/attention` | Placeholder: «Inbox „Требует внимания“ появится в M5 (T116)» | — |
| `/activity` | Placeholder: «Лента активности вне объёма MVP» | — |
| `/service` | Service landing with links to the three screens below | — |
| `/service/budgets` | Configured limits vs actual usage | limits from `src/generated/meta.json`; usage from `GET /runs` + `GET /runs/{id}` |
| `/service/ci` | Этапы CI: on/off switches for the factory CI stages (T058/ADR-026) | `GET /ci/stages`; `PUT /ci/stages/{job}` |
| `/service/settings` | Operator token, API base URL, factory mode, role profiles | localStorage + `meta.json` |

`/budgets`, `/ci` and `/settings` redirect (`<Navigate replace>`) to their
`/service/*` counterparts; unknown paths render the products page.

### ChangeSet workspace (T088, ADR-037)

`src/pages/ChangeSetPage.tsx`. The selected phase defaults to the phase of the
server `Guidance` (`done` → the last column); the left column renders `GET
/changes/{id}/phases` (T098) as is — the server state of every phase
(`pending` / `active` / `needs_decision` / `approved` / `waived` /
`not_required` / `stale` / `done` / `blocked`, labelled in
`lib/artifacts.ts::PHASE_VIEW_STATE_LABELS`), the `state_reason` as the
tooltip, the open questions/comments counts and the `iteration`. No phase
state is computed in the Console; when the projection cannot be read the
column says so instead of inventing one. Budget
«факт» is the sum of the recorded run costs, «лимит» the intake limit,
«прогноз» is honestly `—` until execution data exists (M4). Blockers are the
`Guidance.blockers` count. No percentage of completion is rendered anywhere
(ADR-037 p.6; the unit and e2e tests assert it).

Phases: **Ф0 Инициатива** shows the brief (the M1 `BriefSection`), **Ф1
Требования** is the T089 phase, **Ф2 Архитектура** and **Ф3 Интерфейс** are
the M3 phases (below); Ф4–Ф7 are placeholders naming their milestone
(«появится в M4/M5»). Tabs: «Результат» — the phase content; «Изменения» —
the artifact tree (`GET /changes/{id}/artifacts`; `revision === null` = no
branch yet, distinct from an empty branch; 503 = the contour has no
repository, shown as the server detail); «Проверки» — the `PhaseGate`
(available/closed with every reason and its unblocking action, counts, rework
rounds used of max, the phase `checks[]` — `planned` is «запланировано на
исполнении» in the warning tone and never green — the `ui_requirement` with
«Подтвердить пропуск UI», and every approval with `current` / `stale` /
`unbound` and its phase; a waiver travels without `subject_revision` when the
phase has no revision — it is about the phase, not a document, and an
`unbound` waiver is in effect); «История» — decisions with their phase, rework
orders (with the decisions they are about), runs.

The bottom decision panel renders **only** `Guidance` through `NextStep`.
`lib/guidance.ts` maps the new server `api` strings: `POST
/changes/{id}/approvals` → the approval form (version-bound to
`phase_gate.current_revision`, outcome `approved` or an explicit `waived` with
a mandatory reason; 409 shows the server detail and offers a reload), `POST
/changes/{id}/rework-orders` → the rework form in the context panel, `GET
/changes/{id}/questions?status=open` → scroll to the questions, `GET
/changes/{id}/artifacts` → the «Изменения» tab. M3: the same approval
endpoint is refined by the server's own hints — `cli` `approve … --phase
architecture|interface` fixes the phase of the approval (`ApprovalRequest.phase`
is always sent, since the `specification` gate serves two phases), the label
«Подтвердить пропуск UI» becomes `waive_phase` (the form opens prefilled with
`waived` and the architect's `ui_requirement.reason`, gate `ui`, phase
`interface`), `POST /changes/{id}/decisions/{did}/alternative` or the label
«Запросить альтернативу» scrolls to the decisions overview. `POST
/runs/{id}/withdraw` and run advance stay CLI until M4.

### Requirements phase (T089, ADR-034)

`src/components/RequirementsPhase.tsx` + `ContextPanel.tsx`. «Дельта
требований» lists the `spec` artifacts of the change branch (the proposed
change; the product baseline is what is already accepted, ADR-037 p.7) with
their stable ids (`document.anchors`: frontmatter `id`, `REQ-*`/`AC-*`,
heading slugs), open counts, a «Просмотрено» button (`POST
/artifact-views/{path}` — a view mark, never an approval) and a link into the
editor. Blocking questions are cards answered in one click: `choice` → one
button per option, `text`/`number` → a small input + «Ответить». Assumptions
are **not** a backend entity: a question with `blocking === false` is
rendered as an assumption card with «Подтвердить» (answers the literal text
`confirmed`; offered for `text` questions only, since a number or a choice
cannot be "confirmed" literally), «Исправить» (free text) and «Варианты»
(the options of a `choice`). Comments go through the context panel: artifact
+ anchor (or the whole document) + text → `POST /comments` with the document
revision; `anchor_state === "detached"` is shown as «якорь потерян» with an
explanation and is never re-attached; `addressed` is the agent's mark — the
operator can «Закрыть» or «Переоткрыть», the `addressed` endpoint is never
exposed as a button. «На доработку» opens a form (open comments + answered
questions preselected, instruction) → `POST /rework-orders` bound to the tree
revision; 409/422 show the server detail. Each order shows its status and,
when finished, the agent summary «Что изменил / Что осталось» and the
comments it claims to have addressed.

### Architecture phase (T095/T093)

`src/components/ArchitecturePhase.tsx`. «Обзор проектирования» renders
`design/overview.md` read-only (react-markdown + GFM); a ```` ```mermaid ````
fence becomes a diagram through `MermaidBlock`, which loads the `mermaid`
package lazily (`import("mermaid")` → its own chunks, fetched only when a
diagram is on screen) and always keeps the source on screen until the SVG
lands — a diagram that fails to render shows the error and the source, never
an empty box. «Открыть в редакторе» opens the same document in the
`MarkdownEditor`. «Обзор решений» is `GET /changes/{id}/decisions`: one card
per ADR with the derived status (`proposed` / `accepted` / `needs_revision` /
`superseded`, Russian labels, distinct tones), the frontmatter status when it
differs, impact chips, proposal / rationale / alternatives (table) /
consequences, «Открыть ADR» (editor pane) and «Запросить альтернативу» — an
inline form on the card (instruction + optional open comments of the phase)
that posts `POST /decisions/{id}/alternative`; 409 (another order of the
phase is pending) and 404 show the server text on the card, nothing leaves
the screen. A card with `pending_alternative` shows the order state instead
of the button; a finished order (`decision_ids` ∋ id) shows the agent summary
«Что изменил / Что осталось» and `affected_artifacts` open the editor.
`errors[]` of the read model is a warning. Approval is the guidance CTA
(`approve_phase` with `phase: architecture`), not a button in the phase.

### Interface phase (T096)

`src/components/InterfacePhase.tsx`. `GET /changes/{id}/ui` rendered as the
sub-tabs `Сценарии / Экраны / Связи`, «Сценарии» first: scenario cards with
their steps, each step's screen chip switches to the gallery and focuses the
screen card. «Экраны» is a grid of text cards: title, id, route, purpose,
the five states `loading / empty / error / success / access` in a fixed
order — a state that is absent or has no description is the explicit
warning chip «<state>: не описано» — the elements (id, kind, label, UIKit
component) with a «Комментарий» per element that selects `screen.path +
EL-*` as the fragment for the context panel composer (the comment is posted
by the panel with the anchor), and «Открыть на dev» only when a preview URL
exists (`lib/artifacts.ts::previewHref`: absolute as is, relative joined to
`dev_url`; otherwise the honest «dev-окружение появится после доставки»).
A `detached` comment on an element is marked «привязка потеряна» on the
element; one whose element disappeared is listed under the elements with the
same mark — never re-attached (ADR-034 p.1). «Связи» is the table from → to
with trigger and condition. `errors[]` is a warning; no branch / empty spec
/ 503 are said in words.

### Markdown editor (T090, ADR-035)

`src/components/MarkdownEditor.tsx`. Modes `Документ · Markdown · Чтение`.
The single source of truth in the component is the raw `content` string:
«Markdown» edits it in a textarea; «Документ» renders it with react-markdown
(+GFM) and edits *only* the frontmatter through the properties panel —
protected keys (`schema`, `id`, `type`, `product`, `change`) are read-only,
editable scalar keys become fields whose values travel as `properties` in the
PUT and are applied by the server (`apply_properties`); «Чтение» renders it
read-only. No mode ever rewrites `content` (`lib/artifacts.ts::splitFrontmatter`
only *reads* the body for rendering), so unknown constructs (`:::note`, HTML
comments, `{#anchors}`, footnotes) survive verbatim and switching modes is a
no-op on the text — `MarkdownEditor.test.tsx` proves the round-trip Документ →
Markdown → Чтение → Markdown leaves the textarea value identical and sends
nothing. Draft autosave (`PUT /artifact-drafts/{path}` with `base_revision` =
the loaded revision) fires ~1.5 s after the last keystroke, only with a token,
and the state is always visible («Не сохранено», «Сохраняю…», «Черновик
сохранён hh:mm», «Не сохранено: <ошибка>»); on load an existing draft offers
«Продолжить черновик» / «Отбросить» (DELETE). «Сохранить в git» is the
explicit commit (`PUT /artifacts/{path}` with `base_revision`, optional
message and property edits); a 409 shows the server detail and offers a
reload — nothing is merged or overwritten. «История версий» lists
`artifact-versions` and renders the `unified` diff of `artifact-diff` between
two chosen revisions in a `<pre>`. Selecting text in Документ/Чтение offers
«Комментировать»: the anchor is the first document anchor found *inside* the
selection (`anchorForSelection`), else the whole document — never a guessed
"nearest" element; the context panel composer is prefilled with the quote.
A new revision remounts the editor (key), so the committed text becomes the
source. Artifact paths contain slashes: `encodeArtifactPath` encodes each
segment and keeps the `/` (server route `{path:path}`).

### NextStep (ADR-033)

`src/components/NextStep.tsx` renders a `Guidance` read model exactly as the
server computed it: headline, why, **one** primary action with the server's
label, secondary actions (label + CLI/API hint), blockers as
`what — снимает: <оператор/агент/CI/фабрика/внешняя система> — как: how`, and
`after`. The Console has no "next step" logic of its own: `lib/guidance.ts`
only parses the server's `api` string. When it names something the Console
can do (`POST /products/{id}/validate`, `POST /changes`,
`PUT /changes/{id}/brief`, `GET /products/{id}`, `GET /changes/{id}`,
`GET /runs/{id}`, `POST /changes/{id}/approvals`) the primary is a button that
performs it (validate, navigate to the intake, focus the brief editor, reload,
open the gates); otherwise the server's `cli` command is shown in a `<code>`
block under «в CLI:» — run advance/withdraw come to the Console in M4. A
disabled primary keeps its label and shows the server `reason`. No percentage
progress is ever rendered.

### Intake (T071/T072/T076)

The operator describes the intent in free text and presses «Помоги
сформулировать» → `POST /briefs/formulate`. The four brief fields (problem,
goal, constraints, out_of_scope — the last two one-per-line) are filled from
the answer and stay editable; `source_text` is kept. When the agent harness is
not configured or fails, the API still answers 200 with a `draft` brief and a
human-readable `error`: the Console shows «Бриф остался черновиком: <error>»
and the operator fills the fields by hand (T072 DoD). `formulated_by` is
`agent` only while the agent's wording is untouched, `operator` otherwise
(`lib/brief.ts`). Scenario (`specs_only` / `full`), a required positive USD
limit and an optional token limit go into `scenario` / `spend_limit`; the
forecast block honestly says «Прогноз расхода появится после первого прогона;
лимит: N USD» — there is no forecast data before a run and none is invented.
Submit is `POST /changes` with `product_id`, `product` (the product's
repository), `brief`, `scenario`, `spend_limit`, `source: "console"`.

## Architecture

```
console/
├── e2e/                  # Playwright smoke suite + synthetic API fixtures
├── src/
│   ├── api/              # thin typed fetch client (client.ts), wire types (types.ts),
│   │                     # token/localStorage store (token.ts), base URL (settings.ts), useAsync hook
│   ├── components/       # Section/badges/Layout, Radix token dialog, NextStep, BriefSection,
│   │                     # ProductForm, legacy IntakeForm; M2: MarkdownEditor, RequirementsPhase,
│   │                     # ContextPanel (comments + rework form), PhaseGatePanel, ApprovalForm
│   ├── generated/        # meta.json — COMMITTED snapshot, generated from Python sources
│   ├── lib/              # formatting, meta accessors, id helpers, guidance api parser, brief form helpers,
│   │                     # artifacts.ts (phases, path encoding, frontmatter split, anchors, phase state)
│   ├── pages/            # screens (react-router): products, product, intake, ChangeSet workspace,
│   │                     # M1 change card, gates, placeholders (attention/activity/service), budgets,
│   │                     # CI stages, settings
│   └── test/             # vitest setup + shared fixtures / fetch stub
└── tools/
    └── export_meta.py    # regenerates src/generated/meta.json (see below)
```

**API client** (`src/api/client.ts`): plain `fetch`, no client libraries.
Reads (GET) never carry the token; writes (POST/PUT/DELETE) attach
`Authorization: Bearer <token>` and an `Idempotency-Key` (UUID v4). One token
store serves every scope (`changes:write`, `products:write`, `approvals:write`,
`ci:write`); a missing scope surfaces as the server's 403 detail. Errors are
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
attached only to write requests. Every mutating form is fail-closed: without
a token the fieldset is disabled with a hint, and a button that needs the
token opens the token dialog instead of firing a request. Losing the cache is acceptable by design
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
- **No react-query/axios.** Read-mostly screens; a `useAsync` hook over
  the typed client is enough. Cache invalidation is an explicit `reload()`.
- **react-router v7** in declarative mode with plain hooks — data loaders /
  framework mode were skipped to keep the toolchain minimal; `useAsync` covers
  fetching, `Link`/`useParams` cover navigation.
- **`meta.json` snapshot** is a generated copy of Python state, refreshed
  manually (guarded by the drift test). Acceptable tech debt until a live
  config endpoint exists.
- **Legacy intake at `/changes`** stays minimal (title, provider/slug, risk
  class, description; no product, no brief, `scenario: "full"`); the real
  intake is `/products/:id/new-change`. Ids are generated client-side
  (`chg_<hex>`, `prd_<hex>` — the product id is editable), `source` is always
  `console`, `change_request`/`external_ref` are out of scope.
- **What is empty and why (M1/M2)**: «Требует внимания» has no inbox until M5
  (T116) and «Активность» is out of the MVP scope — both say so instead of
  showing an empty list as "all clear". On the product page «База» shows only
  `baseline_ref` and «Доставки» only `dev_env_ref` (delivery history comes in
  M5). The intake forecast has no numbers before the first run and the
  workspace budget «прогноз» stays `—` until M4. Run advance/withdraw are
  CLI-only until M4, so the NextStep shows the CLI command for them. In the
  workspace, phases Ф2–Ф7 are placeholders naming their milestone; the
  artifact routes answer 503 on a contour without a repository and the
  Console shows that detail instead of inventing documents.
- **Phase gates are fetched per phase** (`GET /phase-gate?phase=` × 8) for the
  «Проверки» tab and the approval form; the left column itself is `GET
  /changes/{id}/phases`. Artifacts are not polled (they are git reads): they
  are re-read after every write and on the manual reload paths; the documents
  read up front are the `spec`, `design` and `ui` nodes (anchors for the
  composer, the design overview) — ADRs come through the decisions read model.
- **Decisions and the UI spec are read by the phase components** (`useAsync`
  keyed on the workspace reload nonce), so `GET /decisions` and `GET /ui` are
  only requested while that phase is on screen.
- **`mermaid` is the one heavy dependency of M3** and it is a lazy chunk:
  the workspace bundle stays as before; the diagram engine is fetched the
  first time a ```` ```mermaid ```` fence is rendered. In vitest it is mocked.
- **Diff is rendered as the server's `unified` text** in a `<pre>`; a
  side-by-side «Сравнение» mode is deferred by ADR-035 p.8.
- **Selection anchors are found inside the selection only** — no DOM range
  mapping to the source; the fallback is the whole document, which the
  operator sees before posting.
- **«Новая фича» on the product page** is a plain link to the intake; the API
  accepts a change for a non-ready product and the guidance then lists the
  blocker. Only when the server lists `POST /changes` as disabled is the
  button rendered disabled with the server `reason`.
- **Brief editor remounts on a server-side brief change** (component key):
  after a successful save the reloaded card becomes the editor's new initial
  value; the success notice lives in the parent section so it survives.
- **Approval form resets on card reload** (the `expected_state_revision` field
  is re-derived from the fresh card via a component key).
- **E2E fixtures are inline TypeScript** (`e2e/fixtures/api.ts`) instead of
  JSON files — the same synthetic data, but type-checked against the wire
  types.
- **CI stage switches are GitHub repository variables.** `/ci` toggles the
  `CI_SKIP_<JOB>` repository variables of the factory repository through
  `GET`/`PUT /api/v1/ci/stages` (T058, [ADR-026](../docs/adr/ADR-026-parameterizable-ci-stages.md);
  operator guide `docs/instructions/manage-ci-stages.md`). The API is
  fail-closed when its GitHub credentials are absent: it answers
  `available: false` with a human-readable `reason`, `enabled` is null and the
  screen disables every control. A failed write never flips the row — the
  switch keeps the server state and shows the error. The switch is a plain
  styled `button[role="switch"]` (`src/index.css`): no new dependency
  (`@radix-ui/react-switch` was deliberately not added).
