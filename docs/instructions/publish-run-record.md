# Как опубликовать run-запись в `dark-factory-runs` (T-061)

**Кому**: оператору фабрики — человеку за клавиатурой.
**Время**: ~5 минут.
**Проверено**: 2026-09-15, фабрика 0.1.0 (`factory stage run` → `factory run publish` дважды: `created`, затем `unchanged`).
**Фича**: Phase 8 / User Story 6, задача T-061 «Run records в `dark-factory-runs`» (`specs/001-dark-factory-mvp/tasks.md`, T039).
**Перед этим**: прогон стадии — [`run-one-stage-us1.md`](./run-one-stage-us1.md).

## Простыми словами: что мы сейчас сделаем

`factory stage run --evidence-dir <dir>` уже оставляет в каталоге evidence
`run_record.json` — компактный индекс доказательств прогона (T011). Но evidence
живёт в CI artifacts ограниченный срок, а аудит должен пережить и их, и сам
прогон. Поэтому запись **публикуется** в отдельный репозиторий
`dark-factory-runs` (ADR-015 п.4): `RunManifest`, итоговый `StageResult`,
approvals/decisions, ссылки и digest — без тяжёлых данных.

Публикация — отдельный шаг, а не часть прогона стадии: стадии идут в sandboxed
подах (ADR-018), а запись в аудит-репозиторий делает доверенный публикатор.
Команда `factory run publish` проверяет запись (секреты, размер, целостность
цепочки evidence) и раскладывает её в checkout `dark-factory-runs`. Коммит и
push в этот репозиторий — задача CI/CD-контура, не публикатора.

## Что понадобится

- рабочая копия фабрики с установленным `uv` (то же окружение, что для US1);
- **git**: манифест записи ссылается на точные коммиты входов — в репозитории
  фабрики они резолвятся из `DARK_FACTORY_COMMIT` / `DARK_FACTORY_PRODUCT_COMMIT`
  / `GITHUB_SHA`, а в их отсутствие — из `git rev-parse HEAD`;
- checkout репозитория `dark-factory-runs` (если его ещё нет — см.
  `deploy/runs/README.md`, раздел Bootstrap; репозиторий создаёт человек).

## Шаг 1. Получите запись прогона

```bash
uv run factory stage run \
  --change fixtures/chg_smoke.yaml \
  --stage construction \
  --evidence-dir .tmp-smoke/evidence
```

Код выхода `10` (`waiting`) — нормальный исход детерминированного прогона:
результат сохранён до внешнего ожидания (ADR-006 п.8). В каталоге появились
`change_snapshot.yaml`, `stage_result.json` и нужный нам `run_record.json`.

## Шаг 2. Опубликуйте запись

```bash
uv run factory run publish \
  --record .tmp-smoke/evidence/run_record.json \
  --runs-root ../dark-factory-runs
```

Корень репозитория можно задать переменной окружения `DARK_FACTORY_RUNS_ROOT`
вместо `--runs-root`. Ожидаемый вывод:

```text
factory run publish: created chg_smoke at runs/2026/09/chg_smoke/<run-id>
```

Чтобы получить машиночитаемый результат, добавьте `--json`:

```json
{"outcome": "created", "change_id": "chg_smoke", "run_id": "...", "path": "runs/2026/09/chg_smoke/..."}
```

## Шаг 3. Проверьте идемпотентность и структуру

```bash
# Повторный запуск ничего не переписывает: outcome меняется на unchanged
uv run factory run publish \
  --record .tmp-smoke/evidence/run_record.json \
  --runs-root ../dark-factory-runs --json

# Дерево записи
find ../dark-factory-runs/runs -type f
```

Ожидаемая структура одного прогона:

```text
runs/2026/09/<change-id>/<run-id>/
├── manifest.yaml         # RunManifest: точные ревизии входов (ADR-015 п.5)
├── snapshot.json         # самодостаточный RunRecord — первичный артефакт
├── stages/construction-1.json
├── usage.json
└── evidence-index.json
```

`decisions.md` появляется только тогда, когда у записи есть решения.

## Что проверить глазами

- `manifest.yaml` — все ссылки на входы конкретные (`factory_commit`,
  `product_commit`), нигде нет `latest`;
- `evidence-index.json` — у evidence есть `uri` и `checksum`, класс retention
  `audit` у обязательной evidence;
- повторная публикация **не меняет** ни одного файла (сравните `ls -l --time-style=full-iso`);
- `git status` в `dark-factory-runs`: изменения лежат в рабочем дереве и ждут
  обычного цикла (ветка → MR), публикатор сам ничего не коммитит.

## Если команда отказала

| Сообщение | Причина | Что делать |
|---|---|---|
| `no runs repository: pass --runs-root or set DARK_FACTORY_RUNS_ROOT` | не задан корень репозитория | указать `--runs-root` или переменную |
| `cannot read the run record …` | нет файла записи | сначала выполнить Шаг 1 |
| `does not match the RunRecord schema` | файл не является записью прогона | взять `run_record.json`, а не `stage_result.json` |
| `the run record is not safe to publish: … [github_token]` | в тексте записи похоже на секрет | убрать секрет из источника (заголовок/описание/комментарий); в сообщении только JSON-путь, само значение не печатается |
| `the evidence chain of the run record is incomplete: …` | ссылка на несуществующую evidence, `latest` в URI, обязательная evidence без `checksum` | исправить цепочку evidence прогона |
| `a different run record is already published at …` | по тому же адресу уже лежит другая запись | исправление — новая запись/ревизия, перезапись истории запрещена (ADR-015 п.4) |

## Ограничения

Публикатор только раскладывает запись в checkout: коммит, push и MR в
`dark-factory-runs` делает CI/CD-контур. Протокол single-writer / append, политики
retention и подпись manifest — P1 (ADR-015 п.4), учтены в `docs/tech-dept.md`.
