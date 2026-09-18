# Журнал T043 — E2E-пилот

Хронология отклонений, решений и наблюдений пилота. Формат: дата, инкремент,
событие → решение/следствие. Метрики прогонов инкремента 1 — здесь же.

## 2026-09-18 — T043: W2 — p01 пере-intake'нут, B3 закрыт

- **Причина:** `chg_t043_p01` был приколот к битой snapshot-ревизии (B3): спецификация не
  публиковалась, `specification` держала 3 blocked-попытки. Лечение — новое изменение, старая
  запись не трогается (решение D4a).
- **Что сделано** (ветка `chore/t-043-p01-reintake`, MR #104): в `deploy/local/pilot/tasks.json`
  добавлена `chg_t043_p01b` (`external_ref` `t043-pilot-01b`, title/description/risk_class = p01);
  подготовлен черновик `contracts/chg_t043_p01b.contract.json` (R1, scope `README.md`,
  `approval: null`).
- **Живая проверка (AC W2):** `intake` — `1 created, 10 replayed, 0 failed` (старые не тронуты,
  дедуп по id/external_ref); первый `advance` не упал `WorkspaceError` — specification
  (роль product, attempt 1, ~2 мин) опубликовала `vadagama/dark-factory-product-1#17` на ветке
  `factory/chg_t043_p01b` и запарковалась на human-гейте (ADR-018/ADR-029). Exit 10 = waiting —
  норма.
- **Состояние:** `chg_t043_p01b` — `waiting`, гейты `specification`/`ui` `pending`; CR #17 — OPEN,
  `MERGEABLE`. Дальше — approving review + merge спека-CR оператором (ADR-011), затем
  `advance-contract … --approve` по рецепту §7.1 (контракт до резолва перехода planning→
  construction — решение D5a).
- **Метрика:** ручных вмешательств в W2 нет; LLM-расход — одна specification-попытка.

## 2026-09-18 — T043: W1 — фикс B1 (риск-осознанный маппинг гейтов) в ветке, MR #103

- **Что сделано** (ветка `fix/t-043-risk-aware-gate-mapping`, MR #103): `ScmFactsProvider` теперь
  считает эффективный риск-класс рана в `__call__` и читает гейт ревью из
  `required_human_gates(route, stage, risk_class)`; фолбэк `Gate.REVIEW` сохранён для стадий без
  человеческих гейтов. Это закрывает B1: ревью plan-CR на R2+ теперь несёт `Gate.PLANNING`,
  wait резолвится, контрольная точка `solution` закрывается (было — всегда `Gate.REVIEW`, deadlock
  на p06–p10).
- **Проверки (фактически запущены):** `uv run pytest` — 1835 passed, 74 skipped;
  `tests/test_runtime_facts.py` — 11 passed (7 прежних + 4 новых: R2-planning → `Gate.PLANNING` и
  точка `solution` закрыта; R1-planning → фолбэк и wait не резолвится; `specification`/
  `review_verification` без изменений; `merged` резолвит стадию независимо от маппинга);
  `uv run mypy` — clean (294 файла); `ruff check .`/`ruff format --check .` — clean.
- **Осталось:** живая валидация W1 — после merge MR #103 прогнать p06 по §7.2 (approving review на
  plan-CR → `advance` резолвит planning и уводит ран в construction). До merge ветки пилот идёт на
  старом коде, поэтому standard-задачи остаются заблокированными.

## 2026-09-18 — T043: решения D1–D5 по плану закрытия приняты (оператор)

- **Основание:** `docs/plan-t043-closure.md` §4; оператор принял рекомендованные варианты
  («принимаю решения рекомендованные»). Работы W1 и W3 разблокированы.
- **D1 = (a) — фикс B1 в коде.** Точка `solution` (R2) закрывается наблюдаемым review'ем:
  `ScmFactsProvider._approvals` переводится на `required_human_gates(route, stage, risk_class)`,
  эффективный класс считается в `__call__` через `effective_change_risk_class`. Ветка
  `fix/t-043-risk-aware-gate-mapping`, тесты — в том же MR. Вариант (b) (сузить объём до quick)
  отклонён: без фикса теряется единственная живая валидация R2-контрольных точек.
- **D2 = (a) — p02/p03 считаются в «≥7» с документированным отклонением.** p03 — покрыт до merge
  (все гейты, кроме `release`, `passed`); p02 — код в `main`, но merge прошёл без version-bound
  review и ран не закрывается (TD-030). Отклонения описаны в §2.1 плана и в записях журнала.
- **D3 = (a) — минимальный набор на первом проходе:** quick `p04`, `p05`, `p01b` + standard
  `p06`, `p07` + зачтённые `p02`, `p03` = 7/10. Вариант (b) (все 10) — только если метрики
  окажутся важнее сроков.
- **D4 = (a) — судьба зависших ранов:** `p01` — пере-intake (новый `id`/`external_ref`, новый
  черновик контракта); `p02` — оставить в `waiting`; TD-030 (снятие рана оператором) остаётся
  открытым — территория reconciler'а, пилот не блокирует.
- **D5 = (a) — B2 обходится порядком шагов:** контракт прикреплять и утверждать **до** advance,
  резолвящего переход planning→construction (иначе retry переделывает planning и жжёт LLM).
  Исправление атрибуции гейта входа (вариант b) — отдельная задача/TD.
- **W4 = (b) — по дефекту измерения `usage` при supersede:** отчитаться измеренным и явно
  назвать потери (например, 313 722 токена construction p02); перенос `usage`/артефактов в
  superseding-результат — кандидат в TD.
- **Следствие:** фазы 1–4 плана (`docs/plan-t043-closure.md` §6) разрешены; приёмка T043 — по
  чеклисту §10.

## 2026-09-18 — Инкремент 1: `release` вынесен из объёма (решение оператора, вариант C)

- **Решение (человек, ADR-011):** объём инкремента 1 покрывает путь `intake → SDD →
  реализация → MR → CI/review → merge`; промоушен/`release` (`image → GitOps → dev → smoke`)
  **вынесен** из объёма пилота. Выбран вариант C из трёх (A — один digest, B — единица релиза
  = набор digest'ов через ADR); мотив — модель промоушена не описывает продукт, а живой прогон
  требует ещё и конфигурации, которой в окружении нет (см. предыдущую запись).
- **Что решило вопрос** (проверено по репозиториям, детали — предыдущая запись): промоушен
  фабрики пинит **один** digest в новый файл `releases/<run_id>/digest`, а Argo читает
  desired state `envs/dev/dark-factory-product-1/values.yaml` с **двумя** релизными digest'ами;
  потребителя `releases/*` в gitops-репо нет (промоушен инертен); сид документирует релиз как
  MR, правящий digest в overlay (`examples/gitops-mr-digest-change.md`, откат revert);
  продуктовый CI публикует только из `main`, поэтому digest не атрибутируется одному изменению
  (`p03`+`p04` в `38a52ad0`).
- **Следствия:**
  - `chg_t043_p03` остаётся `blocked` на `release` attempt 1 — снять run нечем (TD-030); для
    метрик он считается покрытым **до merge**: все гейты, кроме `release`, `passed`
    (`code`/`review`/`verification`/`specification`/`planning`).
  - Живой прогон `release` не заявляется; стадия остаётся подтверждённой юнит/статическими
    тестами (T-092 S4). Ограничение и открытая модель промоушена зафиксированы как **TD-031**
    (кандидат — ADR: единица релиза = набор digest'ов, промоушен правит desired-state overlay).
  - DoD инкремента 1 сужен явной записью в `docs/plan-t043-e2e-pilot.md` и трекере (T043).
- **Что остаётся в объёме инкремента 1**: довести изменения до merge (`review_verification`) —
  `p04` (контракт → construction), `p05`–`p10` (planning; `p06`–`p10` упираются в R2-блокер
  `solution`), `p01` — пере-intake; собрать метрики (стоимость с учётом попыток, first-pass,
  раунды rework, время до принятого MR, ручные вмешательства) и отчёт по SC-001…SC-008 в
  границах суженного объёма.

## 2026-09-18 — Инкремент 1: p03 дошёл до release и встал (нет GitOps-контура); p04 — контракт-гейт перезапустил планирование (открыт)

- **p03 закрыт до `release` решением оператора.** Оператор заапрувил и смержил review-гейт:
  approving review (`vadagama`) на `db6c9f6` (18:02:29Z) → merge `product-1#13` (18:02:37Z).
  `advance chg_t043_p03` наблюдаемым merge + approval резолвнул `review_verification`
  (`result_status=succeeded`, `next_stage=release`), следующий advance перевёл ран в `release`.
  Гейты на финальной ревизии: `code`/`review`/`verification` — `passed sha=db6c9f6`,
  `gate release` — `pending` (не промоутился).
- **release attempt 1 — `blocked`: `stage release: no skill is mapped to this stage (role 'ci_cd')`.**
  Это честный pre-S4-путь: `Runtime.release_stage_executor()` отдаёт `None`, когда блок
  `DARK_FACTORY_GITOPS_*` не задан (`runtime/entrypoint.py`), и стадия `release` уходит в
  агентный исполнитель, у которого нет скилла на эту стадию (`stages/agent.py`).
- **Два независимых гейта на пути промоушена (оба не открыты).**
  1. **Конфигурация промоушена отсутствует.** Нужен блок из четырёх обязательных переменных
     (`DARK_FACTORY_GITOPS_APP_ID`/`_APP_PRIVATE_KEY`/`_INSTALLATION_ID`/`_REPOSITORY_SLUG`) —
     отдельная GitHub App-идентичность для записи в `dark-factory-gitops` (ADR-024 §7 S4),
     all-or-nothing: задание любой переменной без остальных — fail-closed `ValueError`.
  2. **Digest неоткуда взять в прогоне.** `ReleaseStageExecutor` промоутит
     `--expected-digest`/`--digest-json` (XOR; без digest — честный `blocked`),
     а `increment-1.sh advance` этих опций не передаёт: нужен шаг драйвера с release-опциями
     и наблюдением (`--observed-digest`/`--argo-sync`/`--argo-health`/`--smoke-*`).
- **Открытый вопрос модели промоушена (решение оператора/архитектуры).** Промоушен пинит **один**
  digest (`releases/<run_id>/digest`), а пилотный продукт отдаёт **две** картинки (backend и
  frontend, обе в чарте GitOps). Как единая величина релиза для двух образов определяется — не
  решено; в живом контуре не промоутился ни один digest. Данные для промоушена уже есть:
  push-CI `main` после merge (#13/#14) собирает и публикует образы `sha-<sha>` с digests
  (run `35377857579`, head `38a52ad0`, на момент записи in_progress).
- **p04: контракт-гейт перезапустил уже сделанную работу (LLM-расход).**
  `advance chg_t043_p04` при `planning` в состоянии `blocked` (гейт входа в construction:
  контракт не прикреплён) запустил **planning attempt 2** (роль product, ~62 с,
  18:03:55Z → 18:04:58Z), опубликовал свежую ревизию и открыл **новый CR `product-1#15`**
  (прежний `#14` уже смержен — публикация открыла продолжение; фикс cr-after-merge отработал
  как задумано). Гейт `planning` вернулся в `pending` на новой ревизии — прогон ждёт CI `#15`.
- **Корень (механика, проверено по коду).** Гейт входа — `contract_entry_violation`
  (`orchestration/policy/escalation.py`), вызывается из `flow._escalation_reason` при переходе
  в `construction`; при нарушении `flow._handle_action` вызывает `_stop(stage_run, run, reason)`,
  который ставит `BLOCKED` **исходящей** стадии (planning), а не входной (construction).
  Раннер retry-протоколом исполняет `blocked`-стадию заново (`runner.py`: «a stage whose attempt
  ended failed/blocked is retried … next attempt number», ADR-006 p.7) — то есть работа уже
  успешной стадии переделывается (LLM + лишний CR). Класс затрогивает все 10 изменений: каждое
  один раз проходит этот гейт.
- **Обходное правило для пилота (без правок кода).** Прикреплять и утверждать контракт **до**
  резолва перехода planning→construction — тогда гейт не срабатывает и стадия не переделывается.
  Порядок для p04 (и далее): `advance-contract <file> --approve` (человек) → дождаться зелёного
  CI continuation-CR → `advance` (planning успешен на утверждённом контракте → construction).
  Кандидат фикса (решение оператора, вероятно ADR): атрибутировать гейт входа стадии
  `construction` (blocked-попытка construction **до** внешних эффектов), чтобы retry не
  переделывал работу исходящей стадии.
- **Состояние на момент записи**: p03 — `release` attempt 1 `blocked`; p04 — `planning`
  attempt 2 `waiting` (CI `#15`); p02 — `waiting` (невалидный, TD-030); p01 — `blocked`
  (нужен пере-intake); p05 — `planning` pending; p06–p10 — R2-блокер `solution`.
- **Метрики (FR-024)**: usage попыток по-прежнему не виден в отчёте advance (измерение
  «сгоревшего» planning attempt 2 p04 невозможно — usage не публикуется).

## 2026-09-18 — Инкремент 1: планирование p04 → контракт-гейт; p03 на human review (открыт)

- **Живое состояние на входе** (проверено `increment-1.sh status` перед работой).
  `chg_t043_p03` — `review_verification` attempt 1 `waiting` (журнал выше фиксировал
  `pending, attempts=0`): качественная попытка уже отработана и запаркована; CR
  `product-1#13` — CI run `35375790641` 9/9 SUCCESS, `mergeable`, `reviewDecision=REVIEW_REQUIRED`.
  Дальше — approving review + merge оператора (ADR-011, ADR-029 п.5), затем advance → `release`.
  `chg_t043_p02` — `waiting` (`review_verification` attempts=2), гейты
  `review`/`verification`/`ui` `passed` на `f61f247a`. `chg_t043_p01` — `blocked`
  (битый snapshot-digest, нужен пере-intake). `chg_t043_p04`/`p05` — R1, `specification`
  passed, `planning` pending. `chg_t043_p06…p10` — R2, `specification` passed, `planning`
  pending (известный R2-блокер: контрольная точка `solution` без пути резолва — записи выше).
- **p04, шаг «advance → CI».** `advance chg_t043_p04` — planning attempt 1 (роль product,
  `factory.stage.agent`, 17:54:50Z → 17:55:41Z, ~51 с): агент опубликовал ревизию в ветку
  `factory/chg_t043_p04`, CR `product-1#14` открыт автоматически; стадия `waiting`,
  `next_action=wait_for_ci` (exit 10).
- **CI p04 зелёный.** `product-1#14` — 9/9 чеков SUCCESS (Backend ruff/mypy/pytest/OCI,
  Frontend ESLint/tsc/vitest/UI-kit gates/OCI), workflow run `35377210024`.
- **p04, шаг «CI → advance».** `advance chg_t043_p04` — planning резолвнута зелёным CI:
  `result_status=succeeded`, `gate planning: passed sha=d750066c…`; ран сразу упёрся в гейт
  входа в construction: `no implementation contract attached to the run; implementation
  must not start without one (ADR-018 p.3)` — `blocked` (exit 20, LLM не потрачен). Класс
  ровно тот, что зафиксирован по p02; ожидаемое поведение политики, не дефект.
- **Черновик контракта p04 на месте** (`deploy/local/pilot/contracts/chg_t043_p04.contract.json`,
  `approval: null`). Прикрепление и утверждение — одно действие человека (ADR-018 п.3; флаг
  `--approve-contract` агент не ставит никогда):
  `deploy/local/pilot/increment-1.sh advance-contract chg_t043_p04 deploy/local/pilot/contracts/chg_t043_p04.contract.json --approve`,
  затем `advance chg_t043_p04` (construction, LLM-расход).
- **Метрики (FR-024)**: usage попытки planning p04 в отчёте advance не показан — прежняя
  открытая тема (непоказ/потеря usage) остаётся; в этом прогоне не измерялась.
- **Статус инкремента 1**: p01 — пере-intake; p02 — невалидный (оставлен `waiting`, TD-030);
  p03 — human review+merge; p04 — контракт оператора; p05 — той же схемой (готов к advance);
  p06–p10 — упираются в R2-блокер `solution`.

## 2026-09-18 — Инкремент 1: конфликт p03 снят merge-forward'ом, construction зелёная (закрыт)

- **Выбран вариант 1** (ручное снятие конфликта в ветке изменения) — оператор
  указал на страницу конфликтов `product-1#13`. Ручное вмешательство считается в
  метриках (SC-008).
- **Merge-forward** `main` → `factory/chg_t043_p03` выполнен в скретч-клоне
  (`/tmp`, зеркало фабрики не тронуто). Конфликт — ровно один файл
  `frontend/src/pages/HealthPage.test.tsx`: одна точка вставки плюс
  переименованный p02 тест; `HealthPage.tsx` смержился автоматически и несёт обе
  правки (сообщение p02 «Database/Backend unavailable» и `<h1>Service Status</h1>`
  p03). Резолюция «оставить обе стороны»: ассерт p02 (`queryByRole("alert")`) +
  ассерты заголовка p03; error-ветка покрыта ассертами заголовка в последнем
  тесте. Merge-коммит `db6c9f6` (автор — оператор) запушен в ветку изменения.
- **Локальная проверка до пуша** (рецепт CI продукта, каталог `frontend/`):
  `eslint` чисто, `tsc --noEmit` чисто, `vitest run` — 2 файла / 10 тестов зелёные
  (`HealthPage.test.tsx` — 7).
- **Механика тупика подтверждена**: сразу после снятия конфликта
  `pull_request`-CI запустился на новом head сам — run `35375790641`, 9/9 зелёных
  (Backend: pytest/mypy/ruff/OCI; Frontend: ESLint/tsc/vitest/UI-kit gates/OCI); до
  этого на `01c4e7be` не было ни одного check-run. `mergeStateStatus`: `DIRTY` →
  `BLOCKED` (`BLOCKED` — ожидаемое «нужен approving review», ADR-011).
- **p03 разблокирован**: `advance chg_t043_p03` (LLM не потрачен — путь резолюции
  ожидания) → `construction succeeded`, `gate code: passed sha=db6c9f6`, ран →
  `review_verification` (pending, attempts=0).
- **Замечание на будущее**: merge-forward втянул в ветку изменения накопленные
  `main`-коммиты (behind было 20: спеки/сценарии/патчи других изменений) — диф PR
  стал шумным; альтернатива — rebase, но он переписывает опубликованную ревизию и
  дороже для наблюдения гейтов.
- **Дальше по p03**: следующий advance запускает агента роли quality
  (`review_verification`, LLM-расход) и паркует стадию в `waiting`; решение —
  approving review + merge `product-1#13` (ADR-011, ADR-029 п.5), затем advance →
  `release`.
- **Системный класс дефекта остаётся открытым**: merge-forward в конвейере так и не
  появился (варианты 2/3 предыдущей записи не выбраны) — следующая пара изменений,
  тронувших один файл, упрётся в тот же тупик.

## 2026-09-18 — Инкремент 1: construction p03 стартовала и встала на конфликтующем CR (открыт)

- **Не записанный ранее шаг: planning p03 резолвнута.** Зелёный CI на
  continuation-CR `product-1#13` (planning-ревизия `94621e3b`; run `35371390579`,
  16:56:16Z; PR создан 16:56:12Z) закрыл гейт `planning` — статус рана
  `run_d58e805c61874dd75a433fee139504ff`: `planning succeeded (attempts=2)`,
  `gate planning passed sha=94621e3b`. Ран переведён в construction.
- **Контракт p03 прикреплён и утверждён человеком.** Гейт входа в construction
  требует контракт **и** его утверждение (`orchestration/policy/escalation.py:40-55`:
  `contract is None` → «no implementation contract attached…»; `contract.approval is
  None` → «is not approved»); attempt отработал, значит оба условия выполнены
  (утверждение — действие человека: ADR-011, ADR-018 п.3). Черновик в репо
  (`deploy/local/pilot/contracts/chg_t043_p03.contract.json`) остаётся
  `approval: null` — утверждение живёт на ране (attach-путь T-016), это ожидаемо,
  не дефект.
- **Construction attempt 1 (LLM-расход, ~41 с):** агент роли develop отработал
  (`factory.stage.agent`, 17:30:35Z → 17:31:16Z) и опубликовал ревизию `01c4e7be`
  в ветку `factory/chg_t043_p03` (CR `product-1#13`, head обновлён 17:31:21Z);
  стадия — `waiting`, `next_action=wait_for_ci`, гейт `code` `pending`. Usage
  попытки в отчёте advance не показан (открытая тема FR-024).
- **Дельта — строго в границах контракта:** `frontend/src/pages/HealthPage.tsx`
  (оба `<h1>dark-factory-product-1</h1>` → `<h1>Service Status</h1>`, +2/-2) и
  `frontend/src/pages/HealthPage.test.tsx` (+8: обе ветки — ready и error —
  проверяют h1 `Service Status` и отсутствие имени репозитория); ac-1/ac-2
  контракта покрыты. Плюс артефакт самой фабрики
  `.factory/patches/e82a772d….patch` (ChangeSet-дельта; тот же паттерн в #12) —
  не выход агента за скоуп.
- **Блокер (новый класс тупика): конфликтующий CR не получает CI.**
  - `product-1#13`: `mergeable=CONFLICTING`, `mergeStateStatus=DIRTY`;
    `compare main...factory/chg_t043_p03` → `diverged`, ahead=2, behind=20,
    merge base `b7b0d4de` (спека p03).
  - Конфликтующий файл ровно один: `git merge-tree origin/main
    origin/factory/chg_t043_p03` (dry run в зеркале) → `CONFLICT (content):
    frontend/src/pages/HealthPage.test.tsx`; `HealthPage.tsx` мержится
    автоматически.
  - Причина: p02 (#12, merge 16:11Z) и p03 правят тест-файл в одной точке —
    вставки обеих правок идут сразу после `expect(markers).toHaveLength(2);`,
    а p02 вдобавок переименовал второй тест (`…when the backend fails` →
    `…is unreachable`), который p03 использует как контекст хунка.
  - **Механика тупика**: GitHub не запускает `pull_request`-workflow, пока PR
    конфликтует с базой (workflow строится на виртуальном merge-коммите
    `refs/pull/13/merge`). Evidence: на head `01c4e7be` нет ни одного check-run
    (`/commits/01c4e7be/check-runs` пуст), последний ран ветки — на
    planning-ревизии `94621e3b` (16:56Z). Machine-гейт `code` резолвится только
    зелёным пайплайном на финальном SHA (FR-009), значит закрыться не может →
    ран навсегда в `waiting`. Класс тот же, что у «планирование после смерженного
    спек-CR» (PR #89), но корень другой: не несущий CR, а ветка изменения, не
    синхронизированная с `main`.
- **Корень системный для пилота**: ветка изменения берёт базой собственную
  предыдущую ревизию изменения, а не актуальный `main`; merge-forward с `main` не
  делает ни одна стадия. Пока по продукту идут параллельные изменения, любые два,
  тронувшие один файл/регион, дадут конфликт и тупик на `wait_for_ci`.
- **Варианты (решение — оператор/продукт; значимое — через ADR):**
  1. Снять конфликт в ветке изменения вручную (merge-forward `main` в
     `factory/chg_t043_p03`, сохранить обе правки) → push → CI → стадия
     резолвится обычным advance. Кода фабрики не меняет; ручное вмешательство
     считается в метриках (SC-008).
  2. Штатный merge-forward/конфликт-резолв в конвейере (перед construction или
     при публикации) — изменение потока/политики, территория ADR (авторезолв —
     ещё и reconciler, ADR-006).
  3. Продуктовое ограничение: не вести параллельно изменения, трогающие одни
     файлы (для пилота — сериализовать такие пары).
- **Статус**: блокер открыт; p03 — `waiting` и сам не резолвится. Дальнейший
  advance p03 до снятия конфликта смысла не имеет. **Закрыто** — см. запись
  «конфликт p03 снят merge-forward'ом» выше.

## 2026-09-18 — Инкремент 1: решения по хендоффу (p02, p03, окружение)

- **#94 (`docs/t-043-review-gate-finding`) смержен** оператором после зелёного CI —
  протокол «approving review → merge» зафиксирован в журнале, плане и ADR-029.
  Merge выполнен человеком (ADR-011), агенты не мержат; этот же протокол
  распространяется на CR остальных задач (p02…p10).
- **Зависший ран p02 — решение: оставить в `waiting` как есть.** Снятие/отзыв не
  поддерживается: операторской cancel/withdraw-команды нет (`factory run` знает
  только `advance`/`status`/`publish`, `cli/main.py`), а ожидающий run — легитимное
  паркованное состояние; снятие как отдельный переход — территория reconciler
  (ADR-006), не пилотного фикса. `chg_t043_p02` уже квалифицирован как невалидный
  проход (merge без version-bound approving review); пере-запуск ради продукта не
  нужен. Ограничение зафиксировано как **TD-030**.
- **p03 — контракт готов к утверждению оператором.** Черновик
  `deploy/local/pilot/contracts/chg_t043_p03.contract.json` сверен с задачей
  (R1, `in_scope` = `frontend/src/pages/HealthPage.tsx` + тест, 2 AC) и валиден по
  схеме `ImplementationContract`; все девять черновиков (p02–p10) валидны
  (read-only сверка, `approval: null`). Прикрепление и утверждение — одно действие
  человека (ADR-018 п.3; флаг `--approve-contract` агент не ставит никогда):
  `deploy/local/pilot/increment-1.sh advance-contract chg_t043_p03 \
  deploy/local/pilot/contracts/chg_t043_p03.contract.json --approve`, затем
  `advance chg_t043_p03` (construction, LLM-расход).
- **Окружение: защита `main` продуктового репо применена** (было: 404 «Branch not
  protected»). Настройка по образцу уже принятой для `dark-factory-gitops`
  (TD-026): required pull request + ≥1 approving review, запрет force-push и
  удаления, `enforce_admins=false` (на Free «Include administrators» недоступен —
  оговорка TD-026 в силе). Дополнительно `dismiss_stale_reviews=true` — прямое
  требование T-032/`rules/merge_protection.py` (ADR-009 п.7) и ровно смысл нового
  протокола «approval привязан к SHA». Намеренно отложены `required_status_checks`
  (нужны точные имена check'ов продуктового CI) и squash-only (меняет семантику
  истории merge'ей пилота) — по плану погашения TD-026.
- **Наблюдение `protection_violations` (T-032)** по-прежнему не подключено к
  адаптеру (T-030): защита применена на провайдере, но фабрика её не наблюдает.
- **Открытая тема метрик (FR-024)**: потеря usage при `supersede` ожидающего
  чекпоинта (см. ниже) остаётся нерешённой и в этом прогоне не измерялась.

## 2026-09-18 — Инкремент 1: p02 не закрывается наблюдаемым merge — нужен approving review на CR

- **Факт.** После merge `vadagama/dark-factory-product-1#12` (merge `0e15cad`, head
  `f61f247a`, `merged_by=vadagama`) выполнен `advance chg_t043_p02` → снова `waiting`
  (exit 10): `no human approval on gate 'review' bound to the final SHA f61f247a…
  (ADR-009 p.7, FR-010)`. Ран в `release` не перешёл.
- **Механика (поведение гейта, не дефект).** `_resolve_review`
  (`orchestration/stages/gates.py`) на `merged=True` строит merge-результат с
  merge-контекстом (`executor="human"`), а merge policy требует version-bound
  approving decision на гейте `review` при `commit_sha == expected_sha`
  (`orchestration/policy/merge.py::evaluate_merge`, `_latest_human_decision`).
  Approvals в контекст даёт только SCM-наблюдение
  (`runtime/facts.py::ScmFactsProvider._approvals` →
  `MergeRequestPort.observe().reviews`); approving review на итоговом SHA нет →
  `manual_merge_required` → парковка в `waiting` (`flow.py::_wait_for_human_merge`).
  Закреплено тестами: `test_advance_run_parks_the_observed_merge_for_the_human`
  (merged + `approvals=()`) против
  `test_advance_run_advances_to_release_on_an_observed_merge_with_approval`.
- **Коррекция хендоффа.** Запись ниже («стадия review завершится на observed merge»)
  неверна. Merge сам по себе закрывает **design**-стадии — `specification` резолвится
  merge'ом дизайн-CR (ADR-029 п.2/п.4, `_resolve_human_gated`), а
  `review_verification` завершается merge'ом, **авторизованным** approving review на
  итоговом SHA (ADR-011 п.2, ADR-009 п.7, T-032 `rules/merge_protection.py`:
  `required_approving_reviews=1`). Это уже было записано ниже («Известные открытые
  темы»): «Для пилота путь аппрува — review на change request'е».
- **Почему p02 не закрыть.** У #12 ревью нет (`gh api .../pulls/12/reviews` → `[]`),
  approving review на `f61f247a` пост-фактум недостижим — PR merged. Ран остаётся в
  `waiting`, в `release` не перейдёт.
- **Живые дефекты окружения (найдены тем же прогоном).**
  - `main` продуктового репо **не защищён**: `branches/main/protection` → 404 «Branch
    not protected». Требования ADR-011 п.2 / T-032 (protected branch, ≥1 approving
    review, required checks, squash-only, dismiss stale) в окружении не выполнены —
    жёстче задокументированного в TD-026 ослабления для gitops.
  - Фабрика **не наблюдает** настройки защиты: `protection_violations` вызывается
    только тестами (`docs/descriptions/rules.md` §4.2), наблюдение адаптером (T-030)
    не подключено.
- **Решение оператора (2026-09-18, ADR-011 — решение человека).** Вариант «протокол»:
  перед merge ставить approving review на CR (автор CR — `dark-factory-local-1[bot]`,
  поэтому approve человеком возможен; GitHub запрещает approve только своих PR). Гейт
  не ослабляем, код не меняем; `decision-store ↔ драйвер` остаётся отдельным куском
  (см. открытые темы ниже). `chg_t043_p02` — **невалидный прогон по
  протоколу/окружению**; пере-запуск ради продукта не делаем (код изменения уже в
  `main`). Формулировка ADR-029 п.5 уточнена.
- **Дальше.** p03…p10 — по протоколу «approve CR → merge»; вопрос «снять или оставить
  зависший ран p02» — решение оператора.

## 2026-09-18 — Инкремент 1: p02 `review_verification` запаркован в waiting (ждёт merge)

- **Шаг после ADR-029**: `advance chg_t043_p02` — retry стадии `review_verification`
  (attempt 2) на исправленном коде (commit `d1d9eb5`). Агент роли quality отработал
  (`factory.stage.agent`, ~99 с), файловых изменений не породил — `_publish` вернул
  `_published_under_review`, стадия взяла открытый CR ревьюируемого изменения
  `vadagama/dark-factory-product-1#12` (ветка `factory/chg_t043_p02`, CI 9/9 зелёный,
  mergeable) и **запарковалась в `waiting`** (exit 10). Блокер «the attempt produced no
  changes to publish» больше не воспроизводится.
- **Семантика резолюции (проверено по коду `stages/gates.py::gate_resolved`)**: стадия
  `review_verification` резолвится **только наблюдаемым merge** — зелёный пайплайн при
  непустом human-наборе (`review`) возвращает `False` (ADR-029 п.5). Поэтому action
  `wait_for_ci` здесь лишь носитель машинного гейта `verification`; следующий advance
  **после merge #12** построит merge-результат и завершит стадию, дальше — `release`.
  Повторный advance без merge честно `replayed` (наблюдение не резолвит wait).
- **Живой статус**: run `run_df80e146c9d0d2ec94816984833bce8c`, стадия `review_verification`
  attempt 2 `waiting`, run `waiting`, `next_action=wait_for_ci`.
- **Хендофф (человек)**: merge `vadagama/dark-factory-product-1#12` → `advance chg_t043_p02`
  (стадия review завершится на observed merge) → стадия `release`. Решение — оператора
  (ADR-011); агенты не мержат. **Уточнено 2026-09-18**: merge без version-bound
  approving review стадию не закрывает — см. запись выше.
- **Наблюдение по метрикам (FR-024)**: usage попытки-чекпоинта не попадает в отчёт advance;
  ранее найденная потеря usage при `supersede` (см. запись про пилотный прогон) остаётся
  открытой темой и здесь не измеряется.

## 2026-09-18 — Инкремент 1: гейт входа в construction — нет пути прикрепления Implementation Contract (открыт)

- **Живой факт: advance p02 после резолва planning (зелёный CI на continuation-PR product-1#10,
  PLANNING PASSED на head `c96a8b7c`) остановился гейтом входа в construction:**
  `no implementation contract attached to the run; implementation must not start without
  one (ADR-018 p.3)` — exit 20, честная блокировка по T-016 DoD (политика работает как
  задумано). Проверено по БД: ни у одного из 10 ранов `chg_t043_p*` контракт не прикреплён.
- **Причина — пробел контура инкремента 1, не дефект политики**: `run advance --change-id`
  создаёт ран всегда без контракта (docs/descriptions/cli.md §3.2: «контракт не утверждён»),
  `create_run` идемпотентен и контракт/бюджет существующего рана не трогает
  (state/run_store.py), API контракта не принимает (contracts/api.md), CLI-флага нет.
  T023 (T-016) реализовала модель и политику, wiring «одобренный человеком контракт → ран»
  не построен — территория человеческих решений/reconciler (та же открытая тема, что
  approvals).
- **Что это значит для пилота**: все 10 ранов встанут в этот же блокер перед construction.
  До решения — дальше planning/CI гонять можно (p03–p10), construction не стартует.
- **Варианты решения (выбор — оператор/продукт, значимое решение → согласование/ADR):**
  1. CLI `run advance --contract-json <файл|->` (+ `--approve-contract`?) — контракт
     передаётся при первом advance, человек подтверждает; идемпотентность `create_run`
     требует отдельного `attach_contract`-пути для уже созданного рана.
  2. API-эндпоинт `POST /changes/{id}/contract` (role operator, approvals:write) —
     operator-approve контракта операторским токеном; reconciler-склейка с драйвером.
  3. Для пилота — минимальный скрипт, записывающий контракт прямо в `execution` (обход
     контура, грязный; только как временный мост с фиксацией в журнале).
- **Статус**: решён оператором — **вариант 1**, реализован (см. следующую запись).
  Блокер зафиксирован до реализации; прогресс пилота продолжался стадиями, не
  требующими контракта (planning-агенты p03–p10 + CI + review-путь p02).

## 2026-09-18 — Инкремент 1: прикрепление Implementation Contract через `run advance --contract-json` (вариант 1, закрыт)

- **Решение оператора (два пункта).** (1) находку с ревью — чинить; (2) человек должен
  подтверждать **спецификацию, UI и архитектуру на фазе проектирования** — то есть человеческие
  гейты не резолвятся пайплайном, а подтверждение дизайна происходит до реализации.
- **ADR-029** (`docs/adr/ADR-029-human-gates-at-design-phase.md`, принято): `ui` переносится с
  construction на стадию `specification` и входит в базовый человеческий набор
  (`HUMAN_GATES = {specification, ui, review}`); пайплайн маппит только машинные гейты
  (`required_gates − required_human_gates(route, stage, risk)`); стадия с неудовлетворёнными
  человеческими гейтами не резолвится вердиктом CI; `_resolve_human_gated` записывает **все**
  человеческие гейты стадии; `review_verification` завершается без публикации кода — ревьюер
  проверяет уже открытый CR (артефакт construction) и стадия резолвится человеческим merge'ом.
- **Живая матрица после ADR-029** (`standard`): specification — `specification`+`ui`, оба
  человеческие (решение на дизайн-фазе, резолв — version-bound approval или merge дизайн-CR);
  planning — машинный `planning` для R1 / человеческий для R2; construction — только машинный
  `code` (CI по итоговому SHA); review — `review` (человеческий merge) + машинный
  `verification`; release — машинный.
- **Реализация**: `rules/gates.py` (перенос `ui`, базовый человеческий набор),
  `stages/gates.py` (риск-зависимые `_human_gates`/`_machine_gate_results`/`gate_resolved`,
  все человеческие гейты в резолюции), `stages/context.py` (`StageContext.human_gates` +
  эффективный класс), `stages/agent.py` (ревью без публикации; ожидание по машинному набору),
  `orchestration/runner.py` (эффективный класс в обоих call-site'ах), `policy/risk.py`
  (`ux` → `(specification, ui)`), `console/src/generated/meta.json` (регенерация).
- **Валидация**: полный набор **1853 passed / 51 skipped** на PostgreSQL 16 (интеграционные
  выполнены), ruff/mypy чисто; новые тесты: риск-зависимый машинный набор (`wait_for_input`
  для полностью человеческой стадии), пайплайн не проходит `ui`, specification требует оба
  человеческих гейта, ревью паркуется с CR ревьюируемого изменения и блокируется без него.
- **Открытые темы после ADR-029 (не блокируют пилот)**: (а) для R3/R4 полностью человеческие
  стадии (в т.ч. construction) паркуются на `wait_for_input` — таблица переходов flow для
  такого ожидания не проверялась; (б) вердикты ревью (`REVIEW_REPORT`/`ACCEPTANCE_VERDICT`)
  по-прежнему не сохраняются артефактами — публикуется только `change_request`.
- **Живой статус p02**: stays `blocked` на `review_verification` (попытка 1 израсходована до
  фикса); следующий advance — retry стадии (новый прогон агента роли quality, ~300k токенов),
  который по ADR-029 должен запарковаться в `waiting` на человеческий merge дизайн/констракт-CR.


- **Живой прогон p02 после одобрения оператора (контракт attach+approve → advance).** Контракт
  прикреплён и утверждён (БД: `implementation_contract` не NULL, `approval.approved_by=product`);
  повторная передача того же файла с `--approve` честно отказана
  (`already carries a different implementation contract` — `decided_at` отличается), дальше
  продвижение идёт обычным `advance` без флага.
- **Маршрут p02**: planning attempt 3 (ReadTimeout на attempt 2 — разовый сбой LLM) → PR
  `product-1#12` (коммит `f61f247a`, CI зелёный) → гейты `code`+`ui` passed → **construction
  attempt 1**: агент изменил ровно in-scope файлы контракта (`frontend/src/pages/HealthPage.tsx`
  +3/-1, `HealthPage.test.tsx` +64/-3), PR #12, revision `f61f247a`, usage 313,722 токена →
  стадия `waiting` (CI) → CI зелёный → construction `succeeded`, run → `review_verification`.
- **Блокер дальнейшего e2e (новый, честная находка)**: `review_verification` attempt 1 —
  `blocked`, reason «the attempt produced no changes to publish» (агент роли quality отработал,
  298,529 токенов). Причина в коде: `stages/agent.py::_publish` возвращает `None`, когда
  `collect_changes(workspace)` пуст, а `checks.py` (`_STAGES_REQUIRING_CHANGE_REQUEST`)
  требует change request для review-стадии (её завершение — через `merge`, ADR-005 p.2,
  ADR-011). Ревью уже сделанного изменения файловых изменений не порождает → у стадии нет
  пути опубликовать вердикт. Все 10 ранов упрутся в это место. Нужно решение: привязывать
  change request стадии к PR, который ревьюится (артефакт предыдущей стадии), или считать
  вердикт артефактом без публикации commit'а.
- **Находка (политика гейтов)**: у смешанной стадии construction (`code` machine + `ui` human,
  R1+standard) **оба** гейта прошли по `pipeline success at f61f247a…` — machine-путь
  (`stages/gates.py::_resolve_machine_gated`) резолвит и human-гейт стадии. Human-вердикт
  по UI на пилоте фактически не требуется; подтвердить как решение или разделить резолюцию
  (purely-human → только version-bound approval, mixed → human-часть отдельно).
- **Находка (метрики, FR-024)**: при резолюции waiting-чекпоинта `stage_result` той же попытки
  **перезаписывается** (`supersede`, ADR-006 p.8), и usage ожидавшего результата теряется:
  construction attempt 1 после резолюции несёт `usage = NULL` (313,722 токена не учтены в
  store; сумма по p02 = 343,515 вместо ~657k). Кандидат фикса — переносить usage/артефакты
  чекпоинта в superseding-результат.


- **Реализация выбранного оператором варианта 1** — CLI-путь прикрепления контракта:
  - `RunStore.attach_contract(run_id, contract)` (state/run_store.py): запуск без
    контракта записывает переданный; идентичный — no-op; **другой** — отказ
    `ContractConflictError` (новый подкласс `StateError` в state/repositories.py):
    утверждённая граница работающего изменения не подменяется (ADR-018 p.3).
    Идемпотентность `create_run` не тронута.
  - `run advance --contract-json <path|->` (+ `--approve-contract`): контракт читается
    (`-` — stdin), валидируется pydantic-схемой **до** обращения к store (битый
    файл/JSON/схема → exit 2, ничего не записано) и прикрепляется к резолвнутому
    запуску в той же транзакции, что и решение стадии, — обе ветки (`--change-id`:
    контракт уходит в `create_run` + `attach_contract`; `--run-id`: `attach_contract`).
  - `--approve-contract` проставляет human-утверждение (`approved_by=Role.PRODUCT`,
    `decided_at=now(UTC)`) на загруженный контракт; флаг без `--contract-json` — exit 2;
    контракт с уже стоящим approval вместе с флагом — двусмысленность, exit 2.
    Утверждение — решение человека (ADR-011): агент флаг не ставит никогда.
  - Контракт в `run status --json` виден (доменный `ChangeRun` несёт
    `implementation_contract`).
- **Документация**: contract cli.md (usage + блок Contract-опций),
  docs/descriptions/cli.md (таблица §3 + §3.2, включая разбор `--contract-json`).
- **Валидация**: юнит-тесты attach-семантики и CLI-ошибок (9 новых, test_cli_runner.py,
  test_cli_parser.py), integration: attach идемпотентен/swap-free на PostgreSQL +
  сквозной «advance с контрактом на существующем ране → контракт в run status --json»
  (test_runner_advance.py). Полный набор: 1838 passed / 51 skipped (PostgreSQL 16 в
  одноразовом docker-контейнере), ruff/mypy чисто.
- **Дальше по пилоту**: черновики контрактов p02–p10 (deploy/local/pilot/contracts/),
  утверждение оператором, advance p02–p05 (construction), затем тема маппинга
  human-гейтов в `ScmFactsProvider` для UI-гейта R1 и control point `solution` R2.

## 2026-09-18 — Инкремент 1: публикация после смерженного спек-CR (фикс cr-after-merge)

- **Живой дефект: planning p02 застряла в waiting навсегда.** Оператор смержил спек-CR
  product-1#1 (решение выражено мержем, ADR-011 — резолв human-гейта specification по
  9ed3d9b). Planning-агент опубликовал ревизию `c96a8b7c` в ветку `factory/chg_t043_p02`,
  но `_publish` нашёл по change-маркеру в теле **смерженный** PR #1 (`find_existing`
  сканирует все PR, новейшие первыми) и переиспользовал его вместо открытия нового.
  Пуш в ветку смерженного PR не создаёт PR и не триггерит CI → на финальном SHA нет
  check-runs (`pipelines.status` → `queued`) → наблюдение `merged=True`, а
  `gate_resolved` для machine-gated стадии резолвится только зелёным пайплайном при
  открытом CR. Тупик: реплаи чекпоинта без шанса на резолв.
- **Причина**: переиспользование CR в `_publish` не различало «CR открыт — несёт
  коммиты дальше» и «CR смержен/закрыт — несущей больше нет». Merge-as-decision —
  официально поддержанный путь (9ed3d9b), так что дефект системный, не частность пилота.
- **Фикс** (ветка `fix/t-043-publish-continues-after-merge`): `_publish` переиспользует
  CR только в статусе `open`; иначе открывает свежий CR той же ветки (`open`
  идемпотентен по head-ветке, маркер change-id в теле сохраняет FR-011 cold lookup —
  новейший PR с маркером находится первым). Тесты: смерженный CR → executor planning
  открывает CR #2, wait-action называет его, cold lookup резолвит #2; открытый CR
  переиспользуется без дубля.
- **Проверки**: pytest 1804 passed/72 skipped (было 1802/72, +2), ruff check+format
  чисто, mypy чисто (176 файлов).
- **Следствие для прогона**: p02 — planning-попытка уже запаркована на merged CR #1,
  чекпоинт неизменяем (ADR-006 p.8), фикс на неё не действует ретроактивно → оператор
  открывает continuation-PR с change-маркером в теле (1 ручное вмешательство, считается
  в метриках SC-008); далее advance резолвит planning зелёным CI на PR. p03–p10 —
  planning ещё не стартовали: их публикации откроют continuation-CR автоматически.
- **Открытая тема (из предыдущей записи, актуальна):** R2-раны p06–p10 — контрольная
  точка `solution` не закрывается наблюдаемым review'ем (маппинг
  `ScmFactsProvider._approvals` на `Gate.REVIEW`); до её решения planning R2-ранов
  после зелёного CI будет парковаться на human-гейте без пути резолва.

## 2026-09-18 — Инкремент 1: планирование паркуется на CI (фикс planning → wait_for_ci)

- **Живой дефект: advance p02 из planning падал `InvalidFlowTransition: Flow transition planning -> wait_for_ci is not allowed` (exit 1).** LLM-вызов успевал пройти, commit + change request публиковались идемпотентно *до* крэша, но решение не персистилось — валидация перехода в `apply_result` стоит раньше записи. Каждый повторный advance перезапускал LLM и падал снова (выгорание бюджета при нулевом прогрессе). До мерджа фикса planning-стадии не advance'ить.
- **Причина**: `AgentStageExecutor._waiting` выбирает wait-действие по характеру гейтов стадии (`required_gates − HUMAN_GATES`); для planning `{planning}` — machine-гейт → `WaitForCIAction`, но `FLOW_TRANSITIONS[Stage.PLANNING]` не содержал `wait_for_ci` (таблица росла от construction; specification не спотыкается, т.к. её гейты чисто human). Тот же класс дефекта, что помечен в коде как «found in T-043 increment 1».
- **Фикс** (ветка `fix/t-043-planning-wait-for-ci`): `wait_for_ci` в `FLOW_TRANSITIONS[Stage.PLANNING]` + комментарий-инвариант таблицы (planning/construction публикуют работу одним коммитом + CR — machine-гейты гоняются в CI по финальному SHA, FR-009; specification чисто human). Тесты: executor planning → `WaitForCIAction` с CR и producer `product`; runner-регрессия парковки свежей planning-попытки на `wait_for_ci`; runner-резолв ожидающего planning зелёным пайплайном → SUCCESS, `execute_stage` → construction, `[(Gate.PLANNING, PASSED, head_sha)]`; exhaustive-траверсал пар таблицы подхватывает новую пару автоматически.
- **Проверки**: pytest 1802 passed/72 skipped (HEAD до фикса — 1799/72, +3 новых), ruff check+format чисто, mypy чисто (176 файлов).
- **Открытые темы (наблюдения пилота, фикс отдельно):**
  - **R2-раны (p06–p10): контрольная точка `solution` не закрывается наблюдаемым review'ем.** После зелёного CI на plan-CR выход из planning требует human-аппрува точки `solution` (PLANNING-гейт), но `ScmFactsProvider._approvals` мапит review'и planning-стадии на `Gate.REVIEW` (фолбэк: у planning нет базового human-гейта) — точка не может закрыться через наблюдение. Нужен путь operator-decision для planning-гейта или правка маппинга провайдера.
  - **Эффективный риск-класс до approved-контракта — R0/фолбэк классификатора.** Поэтому R2-раны прошли specification без контрольной точки `problem` (обязательства R2+ считаются от эффективного класса, а он ещё не поднят контрактом).
- **Инфра-замечание (повтор)**: инструменты правки сессии дважды за сессию отдали/записали устаревшее состояние файла (перезапись буфера редактора поверх `git checkout`; агрессивный fuzzy-match правок, портящий соседние блоки). Рабочий паттерн подтверждён: восстановление из HEAD + python-патчер с якорем «ровно одно вхождение» + сверка `git diff` (только добавления) до коммита.

## 2026-09-18 — Инкремент 1: резолюция human-гейтов (PR #88), оператор смержил спеки

- **Оператор выразил решения мержем всех 9 спек-CR (product-1#1–#9, без формальных review).** На момент проверки все PR `MERGED`, CI зелёный. Механизм такое решение не съедал — три дефекта доводки (PR #88, ветка `fix/t-043-human-gate-resolution`):
  1. `gate_resolved` учитывал `merged` только для review-стадии — чисто human-gated стадия с мерженым CR оставалась в waiting навсегда (живой блокер). Теперь merge CR резолвит human-gated стадию (наблюдаемый human merge — сильнейшая форма решения, ADR-011 p.2).
  2. `build_gate_resolution` гнал резолвнутую human-gated стадию через `_resolve_machine_gated`: machine-набор пуст → `unsatisfied=[specification]` → честный BLOCKED с нерелевантной FR-009-причиной. Даже review-approval ушёл бы в BLOCKED. Выделен `_resolve_human_gated`: SUCCESS + human-гейт PASSED, забинженный к observed head SHA (ADR-009 p.7).
  3. Control points R2+ (`flow._escalation_reason`) биндинг к `result.input_revision` (base-коммит стадии), а approval забинжен к head CR — версии никогда не совпадают → p06–p10 (R2) остановились бы «risk class R2 obligations are not met». Теперь точка биндится к SHA, на котором прошёл human-гейт стадии (фолбэк — input_revision, прежнее поведение); unbound approval больше не закрывает точку при зафиксированном SHA (усиление по ADR-009 p.7, тесты сьюта flow-policy обновлены под bound-approvals).
- **Проверки**: pytest 1799 passed/72 skipped (+7 новых тестов: truth-table резолва, builder для approval/merge, интеграция раннера merged/approval/replay, control-point binding), ruff check+format чисто, mypy чисто (176 файлов).
- **След**: advance p02–p10 → раны в planning (стадия pending; следующий advance запускает агента планирования — LLM-расход). p01 — пере-intake после подтверждения резолва p02. Прим.: изменения доков в PR #88 — только журнал/план.
- **Инфра-замечание**: read-инструменты сессии отдавали устаревший снимок файлов (конфликт с checkout'ом веток) — правки вносились python-патчерами с якорями «ровно одно вхождение», диф сверялся через `git diff` перед коммитом. Кандидат в tech-dept: надёжность инструментов чтения агента не относится к продукту, но паттерн «якорь + проверка дифа» стоит держать в AR-памяти.

## 2026-09-18 — Инкремент 1: доводка драйвера (PR #85/#86), матрица на гейтах

- **Tool-ошибки роняли попытку агента (fixed, PR #86).** Модель читала ещё не созданный файл → `read_file` → `collect_evidence` поднимал `KeyError` → harness-адаптер переводил исключение в `ok=False` → попытка целиком `blocked` («harness did not produce a result»). p04 пережила 2 такие попытки (3–4), p08/p09 — застряли на attempt 1. → `_model_facing`-декоратор в `WorkspaceTools`: модель-корректируемые сбои (`KeyError`, `UnsafeWorkspacePath`, пустые argv/pattern) возвращаются модели текстом `error: ...` — по собственной политике модуля; неожиданные исключения продолжают пробрасываться. `resolve_path` не изменён — изоляция workspace прежняя. Evidence: p08/p09 после фикса с первой попытки опубликовали спеки (product-1#8, product-1#9).
- **`aclose` на чужом event loop (fixed, PR #86).** Sync-швы драйвера живут по одному `asyncio.run`; entry point закрывает runtime в новом loop'е — `GitHubClient.aclose` делал await пула, привязанного к уже закрытому loop'у → `RuntimeError` → exit 1 после успешно сделанной работы (p04: попытка опубликовала product-1#7 и процесс упал на aclose). → Клиент чужого loop'а выбрасывается на GC, зеркально `_client_for_loop`.
- **`chg_t043_p01` приколот к битой ревизии — до пере-intake.** Ран создан до фикса ключа первой стадии (PR #85): первая стадия приколота к snapshot-digest `d507b6f…`, которого нет в git → `WorkspaceError` на каждом advance (attempt 3). Дефект данных старого рана, не кода; снимается пере-intake p01 после решения оператора по гейту p02 (план).
- **Матрица на human-гейтах `specification` (9/10).** Спеки опубликованы: p02→product-1#1, p03→#2, p05→#3, p06→#4, p07→#5, p10→#6, p04→#7, p08→#8, p09→#9; все `waiting` на human-гейте (ADR-018: спека — человеческое решение). Решения оператора — следующий шаг инкремента; агенты гейты не подают (ADR-011).

## 2026-09-18 — Инкремент 0: bootstrap пилотного продукта

- **CI run 1 (35299665450, `940b3ae`) — Backend pytest + mypy упали.**
  `app.main` собирал модульный `app = create_app()` → `ValidationError`
  (`DATABASE_URL` обязателен) при импорте; mypy strict поверх pydantic
  `dataclass_transform` не пропускал `Settings(_env_file=None)`.
  → Каноничный фикс в паке (`packs/web-app/`, bump 0.2.1), зеркало в продукт:
  фабрика `create_app` + uvicorn `--factory`; изоляция тестов на уровне
  `model_config` + `# type: ignore[call-arg]` на no-arg `Settings()`.
  Урок: шаблоны пака валидируются фабрикой структурно, но CI продукта —
  первый настоящий исполнитель; дефекты шаблона обязаны чиниться в паке
  (иначе bootstrap следующего продукта воспроизведёт баг).

- **CI run 2 (35300089368, `acd7fb7`) — Backend OCI image упал на trivy.**
  3 HIGH в `libpcre2-8-0 10.42-1` (debian 12.15 в пиннутой базе
  `python:3.12-slim-bookworm@sha256:782412e8…`); тег базы не пересобран
  upstream, т.е. bump digest не помогает. → `apt-get upgrade -y` в
  runtime-стадии (паттерн фабричного образа «fresh mirror serves patched
  packages»; пер-пакетный список не успевает за новыми advisory).

- **CI run 3 (35300427112, `911fa98`) — Backend OCI image упал на trivy
  снова, набор уязвимостей другой.** arm64-скан: `pcre2 10.32-3.el8_6`
  (AlmaLinux RPM!) ×6 CVE, при этом amd64 — 0. Расследование локально
  (сборка arm64 + trivy + SBOM): psycopg-binary **3.3.5** вшивает в колесо
  `dist-info/sboms/auditwheel.cdx.json` (новое в 3.3.x) со списком
  bundled-библиотек build-окружения; arm64-колесо бандлит `libpcre2-8`
  от AlmaLinux 8.6 с 6 CVE. Trivy читает embedded SBOM и смешивает
  wheel-компоненты с OS-сканом (PkgType «debian», FixedVersion от
  debian-трекера к rpm-purl — очевидный mismatch). amd64-колесо бандлит
  pcre1 (el7) — CVE в базе нет, поэтому прошло.
  → Пин `psycopg[binary]>=3.2,<3.3` (3.2.x колёса без SBOM). Пин снять,
  когда psycopg обновит bundled-библиотеки или trivy перестанет читать
  wheel-SBOM как OS-пакеты. Кандидат в tech-dept (следить за psycopg 3.3.x).

- **CI run 4 (35301270578, `2a80607`) — 9/9 джоб зелёные.** Digest'ы:
  backend `sha256:b5d96345…`, frontend `sha256:e12eda99…`.

- **GitOps `dark-factory-gitops#5` смержен admin-override** (TD-026: на
  GitHub Free enforce_admins недоступен, требование review владелец
  обходит `--admin`; правило «merge — человек» для gitops-репо пилота не
  вводилось, но ветка main защищена). Учесть при формализации
  release-политики: admin-merge — это то же «ручное решение человека».

- **Квота `apps-dev`: seed-fixture удалена до деплоя продукта.** Продукт
  занимает 1 CPU limits (пик с миграционным хуком), busybox-фixture (50m)
  не влезает. `envs/dev/pilot/` удалён из gitops, Application `pilot-dev`
  удалён из Argo (root app с `prune:false` не удаляет ресурсы автоматически —
  deploy/pilot удалён вручную). DoD fixture (US5/T-044) ранее подтверждён
  (TD-020), потеря не критична.

- **БЛОКЕР: ErrImagePull `unauthorized` — ghcr-пакеты приватные.** Пакеты,
  запушенные GITHUB_TOKEN приватного репо, создаются приватными; kubelet
  docker-desktop тянет анонимно. Сменить видимость API нельзя
  (`PATCH /user/packages/container/…` не существует; локальный gh-токен без
  `read:packages`). → Решение пилота: сделать оба пакета публичными в UI
  (toy digest-pinned образы). Альтернатива на будущее для приватных
  продуктов: imagePullSecret (PAT с `read:packages`) + поддержка
  `imagePullSecrets` в чарте пака.

- Наблюдение: фабричный образ (`deploy/ci/image/Dockerfile`) собран на той же
  базе `python:3.12-slim-bookworm@sha256:782412e8…`, но уже содержит
  `apt-get upgrade -y` из snapshot.debian.org (20260906T000000Z) — дефект
  «старая база» на фабричный образ не распространяется, если фикс
  `libpcre2-8-0 10.42-1+deb12u1` вошёл в snapshot до 2026-09-06
  (проверить при первом релизе фабричного образа из ghcr, T-045/T-052).

- Наблюдение: проброс PostgreSQL для CLI (`127.0.0.1:5432`) не настроен —
  нужен для инкремента 1 (intake/advance из CLI); делать внутри скрипта.

## 2026-09-18 — Инкремент 0: докат деплоя и smoke (инкремент закрыт)

- **Миграционный хук: first-install deadlock на pre-фазах.** Хук шёл в
  `pre-install,pre-upgrade` → Argo CD мапит pre-* хуки в фазу PreSync, которая
  выполняется ДО применения ресурсов чарта — на первой установке хук не может
  разрешить `<release>-postgres` (DNS: `Name or service not known`, 26+
  попыток). → Каноничный фикс в паке (web-app 0.2.1, коммиты `2ab3709`,
  `03d30da`): хук — только post-фазы (`post-install,post-upgrade`), обёртка
  wait-for-database (30×5s внутри `activeDeadlineSeconds: 300`),
  `hook-delete-policy: before-hook-creation,hook-succeeded`. Следствие
  зафиксировано в шаблоне: на апгрейдах миграции идут ПОСЛЕ деплоя нового
  backend — schema-изменения обязаны быть backward-compatible
  (expand/contract). Упавший install/PostSync ресурсы сохраняет — следующий
  прогон Argo/helm сходится (хук добивает миграции на живой БД).

- **Блокер ErrImagePull снят оператором**: пакеты
  `dark-factory-product-1-{backend,frontend}` сделаны публичными в GitHub UI
  (решение из предыдущей записи, TD-029). Argo докатил деплой: поды backend/
  frontend/postgres Running, миграционный хук отработал (retry поглотил и
  окно инициализации initdb, и окно применения ресурсов), job убран по
  hook-delete-policy, Application `product-1-dev` — Synced/Healthy
  (gitops-ревизия `b7da87f`).

- **Smoke инкремента 0 зелёный**: `GET /api/healthz` →
  `{"status":"ok","database":"ok"}`, `GET /` → HTTP 200 (frontend HTML,
  заголовок `dark-factory-product-1`). DoD T-070 «blueprint разворачивается
  в apps-dev» подтверждён живым деплоем; TD-010 и TD-029 закрыты. Живой цикл
  «образы CI → GitOps-MR с digest → Argo → apps-dev → smoke» пройден целиком.

- Наблюдение на будущее: в ходе цикла подведения PVC postgres пересоздавался
  (новый claim) — reinstall релиза теряет данные БД; с инкремента 1 релиз
  обновлять только upgrade'ом (или откатом revert-коммита в gitops, TD-020),
  за PVC следить при каждом вмешательстве.

## 2026-09-18 — Инкремент 1: прогон до human-гейта (инкремент в работе)

Состояние: окружение инкремента 1 поднято (pg-forward 55432, intake 10
изменений `chg_t043_p01…p10`), e2e-цикл агентной стадии пройден живым
прогоном `chg_t043_p02` — от intake до waiting на human-гейте, с
опубликованным коммитом и открытым change request. Ниже — найденные и
закрытые дефекты (все вскрыты только живым прогоном; юнит-контур их не
видел).

- **run-creation keying (критический, закрыт).** `factory run advance
  --change-id` создавал run с `input_revision` = digest снапшота change'а
  (S1), и первая stage-строка наследовала его — резолвер `ScmRevision`
  (ADR-006 p.4) не вызывался вовсе, поэтому `_mint` пытался сминтить
  worktree на SHA, которого не существует в git → WorkspaceError на каждой
  попытке. Юнит-тест резолвера выставлял `stages[0].input_revision = None`
  вручную и не покрывал путь создания run'а CLI. Фикс:
  `RunStore.create_run(..., initial_stage_revision=...)` — run-id остаётся
  от digest (контракт cli.md «one run per snapshot»), первая stage-строка
  ключится SCM-ревизией (`cli.runner._resolve_run` вызывает резолвер);
  e2e-регрессионный тест — `tests/integration/test_runner_advance.py`.

- **GitHub 422 для несуществующего ref (закрыт).** Контракт
  `RepositoryPort` — «missing ref = KeyError» (404), но GitHub отвечает
  **422** на `GET /commits/{ref}` для ref'а, который не резолвится
  (несуществующая тасковая ветка — нормальное состояние до первой стадии).
  Адаптеры (`repository.get_revision`, `repository._head`, `ci._resolve_ref`)
  теперь мапят 422 как absent; контрактный эмулятор воспроизводит 422,
  добавлен контрактный тест `test_get_revision_of_a_missing_ref_is_a_keyerror`.

- **httpx2-клиент GitHub переживает смену event loop (закрыт).** Sync-швы
  драйвера (`ScmRevision`, `ScmFactsProvider`, executor) работают через
  `asyncio.run` — по одному короткому loop'у на вызов; keep-alive пул
  httpx2 привязан к loop'у и падал на следующем вызове
  (`RuntimeError: Event loop is closed` — хаотично: то KeyError, то
  обрыв LLM-вызова на полпути). `GitHubClient` держит клиент **per loop**:
  запрос на новом loop'е пересоздаёт клиент, пул мёртвого loop'а
  сбрасывается (ADR-025-совместимо, binding-слой).

- **product-профиль без write_file (закрыт).** Скилл `spec-authoring`
  требует записать спеку в layout репо, а `PRODUCT_PROFILE.tools` не
  содержал `write_file` — агент писал спеку в чат, `_publish` видел пустую
  дельту. Добавлен `write_file` (version 1.0.0 → 1.0.1), meta-снапшот
  консоли перегенерирован.

- **wait-действие агентной стадии (закрыт).** `AgentStageExecutor._waiting`
  всегда возвращал `WaitForCIAction`, но `FLOW_TRANSITIONS` разрешает
  `wait_for_ci` только construction/review/release — спецификация с
  публикацией падала `InvalidFlowTransition` (exit 1). Теперь действие
  выбирается по характеру гейтов стадии (`rules.gates`): только human-гейты
  → `wait_for_input` (парковка на человека), есть machine-гейты →
  `wait_for_ci`.

- **human-гейт не резолвился пайплайном (закрыт).** `gate_resolved`
  считал зелёный пайплайн резолюцией для любой не-review стадии — спека
  «продет» без человека, а `_resolve_machine_gated` честно блокировался
  («cannot be attributed to a head SHA»). Новая семантика: стадия с
  чисто-human-гейтами резолвится **только** version-bound human approval
  (наблюдаемым review'ем change request'а); пайплайн решает только стадии
  с machine-гейтами. `ScmFactsProvider` теперь привязывает approvals к
  human-гейту стадии (specification → Gate.SPECIFICATION, review → REVIEW).
  `gate_resolved(observation, stage, route)` — route передают оба call-site
  драйвера.

- **Наблюдаемость харнесса (закрыт).** Падение `agent.run` глушилось в
  «harness did not produce a result» без указания типа — отладка вслепую.
  `PydanticAIHarness` пишет тип исключения в лог процесса (не в reason —
  контракт ADR-009 сохранён), плюс диагностический
  `DARK_FACTORY_HARNESS_TRACEBACK=1` (локальный stderr, по умолчанию выкл).

- **psycopg pin (закрыт, из bring-up).** `psycopg[binary]>=3.2,<3.3` —
  3.3.x без libpq не работает локально на macOS.

Живой статус прогона: `chg_t043_p02` — waiting (human-гейт specification,
change request `vadagama/dark-factory-product-1#1`, CI на PR зелёный,
head `3b56f569`). Решение — оператора (ADR-011). `chg_t043_p01` — run
создан до фикса keying'а, операционно приколот к digest-ревизии (retry
сохраняет операционную идентичность, ADR-006 p.7); будет пере-intake под
новым id после закрытия human-гейта p02. Остальные 8 изменений — не
запускались, ждут разблокировки контура гейтов.

Известные открытые темы (не блокируют дальнейший прогон, фиксируются):
- **aclose после закрытого loop'а** — `Runtime.entrypoint` зовёт
  `asyncio.run(runtime.aclose())` в finally; если ресурсы родились на
  закрытом `asyncio.run`-loop'е швов, aclose падает с шумным traceback
  (после основного результата; exit-код в CLI-ветке не искажает — но
  traceback грязнит stderr). Кандидат: общий loop для швов в ADR-025.
- **API-approvals не видны драйверу** — operator-approve через
  `POST /changes/{id}/approvals` пишет Decision в store, но драйвер читает
  human-факты только из observation (GitHub reviews). Для пилота путь
  аппрува — review на change request'е; склейку decision-store ↔ драйвер
  делать отдельным куском (reconciler-территория, ADR-006).
- **advance-all паттерн ERROR(1)** — при прогоне p02 через advance-all
  retry-циклы агента (blocked → attempt N) выгорают попытки LLM; перед
  следующим advance-all прогнать аккуратно с паузами и наблюдением.
