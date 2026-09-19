# Приёмка M3: архитектура и интерфейс — принять или запросить альтернативу

**Кому**: оператору фабрики.
**Что проверяем**: веху M3 плана ChangeSet (`docs/plan-changeset-workspace-mvp.md`, T092–T099, ADR-039): стадия `specification` исполняется раундами по фазам (требования → архитектура → интерфейс); ADR и design создаются агентом-архитектором как артефакты фазы, UI-спека — дизайнером; решения версионно привязаны к ревизии **своей** фазы; «Запросить альтернативу» относится к конкретному решению; UI-фаза либо согласуется, либо явно пропускается с основанием; axe/visual regression показаны как «запланировано на исполнении», не зелёным; правка после согласования делает согласование неактуальным.
**Живой прогон**: см. раздел «Журнал прогона».

## Что понадобится

- Всё из `run-m2-requirements-loop.md` (кластер с PostgreSQL, port-forward `55432`, `.env`, GitHub App фабрики установлен на репозиторий продукта — с 2026-09-19 `vadagama/dark-factory-calc` входит в установку `dark-factory-local-1`).
- Схема БД на `head`: `alembic upgrade head` (миграция `0007_phase_rounds` добавляет `decision.phase`).
- Окружение локального контура — как в M2 (`DATABASE_URL` → `127.0.0.1:55432`, `DARK_FACTORY_WORKSPACE_ROOT`, `DARK_FACTORY_WORKSPACE_MIRROR_ROOT`, `DARK_FACTORY_PACKS_ROOT`).

## Шаги (CLI)

1. **Требования** — шаги 1–5 из `run-m2-requirements-loop.md` (baseline, раунд `product`, ответы, замечание, согласование). Дополнительно:
   ```sh
   factory change phases --id <chg>        # F1 requirements: needs_decision → approved; F2 architecture: pending
   factory change approve --id <chg> --phase requirements --comment "ok"
   factory change status --id <chg>        # «Следующий шаг»: Запустить раунд «Архитектура» (после advance)
   ```
   Ожидание: решение записано с `phase=requirements` и `commit_sha` = ревизия `spec/**`; `factory run advance` резолвит ожидание в `phase_round` → новая операция стадии `specification`, раунд доработки не потрачен (`rework=0/3`).
2. **Архитектура** — раунд `architect` (`solution-design`).
   ```sh
   factory run advance --change-id <chg>   # архитектор пишет design/overview.md (frontmatter ui: …) и design/decisions/ADR-NNN-*.md
   factory change decisions --id <chg>     # карточки: предложение / обоснование / альтернативы / последствия / статус proposed
   factory change phases --id <chg>        # F1 approved (ревизия spec/** не изменилась!), F2 needs_decision
   ```
   Ожидание: коммит архитектора **не** делает согласование требований неактуальным (`change phases`: F1 `approved`, `approved_revision` прежний); гейт `architecture` привязан к ревизии `design/**`.
3. **Запросить альтернативу.**
   ```sh
   factory change alternative --id <chg> --decision <adr:…> --instruction "Рассмотреть вариант без новой зависимости"
   factory change decisions --id <chg>     # статус решения: needs_revision; поручение pending с decision_ids
   factory run advance --change-id <chg>   # раунд доработки архитектора (rework=1/3), сводка «что изменил / что осталось»
   factory change decisions --id <chg>     # затронутые артефакты после пересмотра
   factory change approve --id <chg> --phase architecture --comment "ok"
   ```
   Ожидание: отказ адресован конкретному решению; поручение тратит раунд бюджета; после согласования статус карточки — `accepted` (в git по-прежнему `status: proposed` — статус производный, ADR-039 п.8).
4. **Интерфейс** — раунд `design` (`ui-spec`) **или** пропуск.
   ```sh
   factory run advance --change-id <chg>   # phase_round → раунд дизайнера: design/ui/scenarios/SCN-*.md, design/ui/screens/SCR-*.md
   factory change ui --id <chg>            # сценарии, экраны с пятью состояниями, связи, компоненты UIKit
   factory change comment --id <chg> --artifact <design/ui/screens/SCR-001-….md> --anchor EL-… --body "…"
   factory change approve --id <chg> --phase interface --comment "ok"
   ```
   Backend-only изменение: архитектор указал `ui: not_required` — гейт `interface` показывает «Не требуется: <основание>», раунд дизайнера не запускается; подтверждение оператора:
   ```sh
   factory change approve --id <chg> --phase interface --waive --comment "<основание>"
   ```
   Ожидание: `factory change status` → гейт `interface`: `checks` axe/visual_regression = `planned` (или `not_required` при пропуске), никогда `passed`; после согласования/пропуска `run advance` завершает стадию (`specification` passed, `ui` passed/skipped) и создаёт стадию `planning`.
5. **Правка после согласования.**
   ```sh
   factory change artifacts edit --id <chg> --path <design/decisions/ADR-001-….md> --file ./adr.md --base-revision <rev>
   factory change phases --id <chg>        # F2 architecture: stale; F1 requirements: approved (её файлы не менялись)
   ```

## Шаги (API)

```sh
curl -s http://127.0.0.1:8010/api/v1/changes/<chg>/phases
curl -s http://127.0.0.1:8010/api/v1/changes/<chg>/decisions
curl -s http://127.0.0.1:8010/api/v1/changes/<chg>/ui
curl -s "http://127.0.0.1:8010/api/v1/changes/<chg>/phase-gate?phase=interface"
```

Ожидание: `phases.current` и `guidance.phase` совпадают с CLI; `POST …/approvals` с `phase: architecture` и `gate: ui` — 422; `POST …/decisions/<adr>/alternative` — 201, повтор при открытом поручении — 409.

## Шаги (Console)

1. Левая колонка фаз рендерится из `GET …/phases` (состояния, счётчики, итерация); процента готовности нет.
2. Фаза «Архитектура»: обзор с mermaid-схемой, карточки решений, ADR в редакторе, «Запросить альтернативу» inline (409 показан текстом), после пересмотра — затронутые артефакты; CTA «Согласовать архитектуру».
3. Фаза «Интерфейс»: по умолчанию «Сценарии»; галерея экранов с пятью состояниями и элементами; ссылка на dev или честный текст; комментарий к элементу с пометкой потерянной привязки; «Проверки»: axe/visual regression «запланировано на исполнении», «Не требуется» + «Подтвердить пропуск UI».
4. Тот же следующий шаг, что и в `factory change status`.

## Журнал прогона (2026-09-19, ветка `feat/m3-architecture-interface`, продукт `prd-calc` → `vadagama/dark-factory-calc`)

Живой контур: кластерная БД через port-forward `55432`, миграции `0007_phase_rounds` и `0008_stage_created_at` накатаны на боевую БД, реальный LLM (`DARK_FACTORY_LLM_*`), GitHub App `dark-factory-local-1` теперь установлен на `vadagama/dark-factory-calc`.

### Часть 1 — доигрывание M2 (T091) на `chg_calc_m1_001`

| Шаг | Результат |
|---|---|
| `factory product bootstrap --id prd-calc` | **ok** — блокер M2 снят: зеркало допушило `bootstrap: apply product-baseline@0.1.0` (`ced19e74…`), `applied_packs` = product-baseline 0.1.0 |
| `factory change status` на пустом репозитории | **дефект найден и исправлен**: GitHub отвечает 409 «Git Repository is empty» на `commits/<ref>`, адаптер бросал `GitHubAPIError` вместо «ревизии нет» → CLI падал; `GitHubRepository.get_revision`/`list_commits` теперь читают 409 как `KeyError` (тест `tests/test_adapters_github_empty_repository.py`) |
| `factory run advance` (раунд `product`, попытка 1) | **blocked**: `harness call failed: UnsafeWorkspacePath` — агент вызвал инструмент с путём `.`; `resolve_path` пропускал его как пустую цель, порт воркспейса отказывал исключением другого класса, и попытка обрывалась вместо подсказки модели. Исправлено: `resolve_path` отвергает корень воркспейса сообщением модели, `_model_facing` переводит порт-уровневый `UnsafeWorkspacePathError` в `error: …` |
| `factory run advance` (попытка 2) | **waiting**: агент создал `intent.md`, `spec/delta.yaml`, `REQ-001…REQ-005`, открыл CR #1, задал 3 вопроса (1 блокирующий, якоря `REQ-00N#AC-k`); гейт `requirements: closed` — «Блокирующих вопросов без ответа: 1» |
| `change answer` ×3, повтор ответа | ответы записаны; повторный ответ на отвеченный вопрос — отказ (exit 2); гейт `available` |
| `change comment --anchor AC-1` | замечание привязано к ревизии `5d3eddcf…`, гейт остаётся `available` (замечание не запускает доработку) |
| `change rework` + `run advance` (резолюция) | поручение `pending → in_progress (round 1)`, стадия ушла в rework |
| `run advance` (раунд доработки) | агент отработал (сводка «changed 7 / remaining 2», замечание → `addressed`), **но голова ветки не изменилась** — **дефект M2 найден**: раунд доработки той же стадии переиспользовал операцию (тот же `input_revision`, попытка 3), ключ эффекта `publish_commit` совпал с первым раундом и адаптер вернул старый коммит; заодно `used_rework_rounds` остался 0 — `persist_decision` не сохранял бюджет |
| второе поручение + `run advance` | ограниченный цикл честно **эскалировал**: «rework did not produce a new commit … re-review is impossible» — верное поведение при потерянном раунде; run снят `factory run withdraw` |
| Исправления | `flow._handle_rework`/`_handle_phase_round` → `_restart` (новая операция стадии), `runner._created_stages` объявляет новую строку той же стадии, `RunStore.persist_decision` сохраняет `budget`, миграция `0008_stage_created_at` + порядок строк стадии по времени создания; тесты `tests/test_orchestration_runner.py`, `tests/integration/test_runner_advance.py::test_a_rework_round_persists_the_spent_budget_and_a_new_stage_operation` |

### Часть 2 — M3 (T099) на новом изменении `chg_calc_m3_001`

Изменение: `chg_calc_m3_001` «Percent button for the calculator (M3)», сценарий `specs_only`, лимит 20 USD, маршрут `standard` (R1), run `run_4c306df1…`.

| Шаг | Результат |
|---|---|
| `factory change create` + `run advance` (раунд `product`) | **waiting**: агент создал `intent.md`, `spec/delta.yaml`, `REQ-001…REQ-004`, открыл CR #2, задал 3 блокирующих вопроса (`choice`, якоря `REQ-00N#AC-k`); ревизия `26986e37…` |
| `change answer` ×3, `change approve --phase requirements` | решение `dec_b2d415fc…` записано с `phase=requirements`, `commit_sha=26986e37…` (ревизия `spec/**`); `change phases`: F1 `approved` |
| `run advance` (резолюция) | `phase_round` → новая операция стадии `specification` на `26986e37…`, `rework=0/3`, F2 `active` |
| `run advance` (ожидался раунд `architect`) | **дефект найден**: раунд выполнился как `specification/requirements` (роль `product`) — коммит `86452886…` применил ответы в `spec/**` и сделал согласование требований `stale`. Причина: `run_advance_command` принимал `repository`, но не передавал его в `_advance`; без `ArtifactService` ревизии фаз неизвестны, согласование читалось как stale, `current_change_phase` → `requirements`. Исправлено (`cli/runner.py`), тест `test_run_advance_binds_the_repository_to_the_phase_and_facts_seams` |
| `change approve --phase requirements` на `86452886…` + `run advance` | **второй дефект**: `RunnerError «stage … was not persisted and has no committed result»`. Две причины: (1) `StageResultStore.get` искал результат по `(run, stage, attempt)` без операции — второй раунд стадии (attempt 1 снова) получал результат первого раунда, checkpoint читался как отсутствующий и попытка исполнялась заново (вызов LLM впустую, запись отвергнута как replay); (2) `with_store_facts` брал фазу наблюдения как «первую несогласованную» по решениям (`architecture`), а не как раунд ожидающего checkpoint'а (`requirements`) — согласование, записанное после раунда, не резолвило ожидание. Исправлено: поиск результата по `input_revision`; `GateObservation.phase` = `StageResult.phase` checkpoint'а (`waiting_phase_of`); `Guidance`/`change phases` при согласованном раунде показывают «Запустить раунд «Архитектура»» вместо «Согласовать архитектуру». Тесты: `test_the_second_operation_of_a_stage_owns_its_checkpoint_and_names_its_round` (integration), `test_a_settled_earlier_round_asks_for_the_advance_not_for_a_decision`, `test_projection_shows_a_settled_round_waiting_for_its_advance` |
| `run advance` (резолюция) → `run advance` (раунд `architect`) | **ok**: `phase_round(architecture)`, затем роль `architect` со скиллом `solution-design` — коммит `014e043a…` «(specification/architecture)»: `design/overview.md` (frontmatter `ui: required` + `ui_reason`, mermaid-схема), `design/decisions/ADR-002…ADR-004`; согласование требований **осталось действующим** (ревизия `spec/**` не менялась); `change decisions` — 3 карточки `proposed` с предложением, альтернативами (5), влиянием; `Guidance`: «Согласовать архитектуру», secondary «Запросить альтернативу» |
| Наблюдение (техдолг) | ADR получили id `adr:example-product:000N` и `product: example-product`: шаблоны `packs/product-baseline` не параметризуются id продукта при `bootstrap`, и агент наследует плейсхолдер. Зарегистрировать как техдолг (параметризация пака при применении) |

| `change alternative --decision adr:example-product:0004` | поручение `rw_a63e37…` фазы `architecture` с `decision_ids=[adr:…:0004]`, `rejected`-решение фазы; `change decisions`: 0004 → `needs_revision`, остальные `proposed`; `Guidance`: гейт закрыт «Отправлено на доработку: раунд ещё не запущен» |
| `run advance` ×2 (резолюция → раунд доработки архитектора) | **третий дефект**: раунд отработал (коммит `1822e96e…`, ADR-0004 переписан, 9 альтернатив), но поручение осталось `pending` без сводки — результат `_resolve_rework_order` не нёс `phase`, и `record_stage_outcome` искал поручение в фазе `requirements`. Исправлено (`stages/gates.py`), тест `test_a_rework_round_of_a_design_phase_carries_the_phase_of_the_parked_round`. На живом контуре поручение довыполнено ещё одним раундом (`rework=2/3`): `pending → in_progress (round 2) → done`, сводка `changed/remaining`, `affected_artifacts` у карточки 0004 (ADR-002…005, overview), появился ADR-005 «keypad API boundary»; статус 0004 → `proposed` |
| `change approve --phase architecture` | решение `dec_…5abc544c` `phase=architecture`, `gate=specification`, `commit_sha=aba3f75d…` (ревизия `design/**`); `change decisions`: все карточки `accepted`, в git по-прежнему `status: proposed` — статус производный (ADR-039 п.8) |
| `run advance` (резолюция) → `run advance` (раунд `design`) | `phase_round(interface)` → роль `design` со скиллом `ui-spec`: коммит `d604b7e2…` «(specification/interface)» — `design/ui/scenarios/SCN-001…SCN-007`, `design/ui/screens/SCR-001-calculator.md` (5 состояний, 20+ элементов `EL-*` с компонентами UIKit `Card/Input/Toolbar/Button/Spinner/Alert`); `change ui` — сценарии/экраны/связи/компоненты; гейт `interface`: `ui_requirement{required: true, source: agent}`, `checks`: axe и visual_regression = `planned` (не зелёные) |
| `change comment --anchor EL-display` + `change approve --phase interface` | замечание к элементу привязано (`interface`, `open`), гейт остаётся `available`; решение `dec_…578ee381` `phase=interface`, `gate=ui`, `commit_sha=d604b7e2…` |
| `run advance` (последняя фаза) | стадия `specification` завершена: `execute_stage → planning` (гейты `specification` и `ui` — `passed`), `change phases`: F1–F3 `approved`, F4 «План» `active`; `iter` считается по поручениям своей фазы (F2 = 1) — исправлено в этом прогоне (`rework_orders_spent`) |

| `change artifacts edit` ADR-004 после согласования (шаг 5) | коммит `fa4c54f5…` через write-through; `change phases`: F2 «Архитектура» **`stale`** («Согласование неактуально: артефакты фазы изменились»), F1 и F3 остаются `approved` (их файлы не менялись), F4 «План» `active`; `change decisions`: `approved=no`, ADR-0004 → `needs_revision`. **Четвёртый дефект**: проекция падала `ValueError: tuple.index` при текущей фазе за пределами `specification` — исправлено (`phases._specification_state`), тест `test_an_edit_after_the_stage_completed_marks_that_phase_stale` |
| API (`factory api serve` на 8010, локальный operator-токен) | `GET …/phases` 200 (`current=plan`, F2 `stale`), `GET …/guidance` 200 (та же фаза), `GET …/decisions` 200 (карточки, `affected_artifacts` у 0004 — 13 файлов), `GET …/ui` 200 (7 сценариев, 1 экран, 7 связей, 6 компонентов, `errors=[]`), `GET …/phase-gate?phase=interface` 200 (`approved`, `checks` axe/visual_regression `planned`), `?phase=architecture` — `approved=false`, 2 stale-согласования; `POST …/approvals {gate: ui, phase: architecture}` → **422**; `POST …/decisions/adr:…:0002/alternative` → **201** (`rw_04834458…`, `decision_ids`), повтор для 0003 при открытом поручении → **409**, неизвестное решение → **404**; `POST …/approvals` с устаревшей ревизией → **409**. Побочный эффект проверки: поручение `rw_04834458…` фазы `architecture` осталось `pending` (стадия уже завершена — раунд не запустится; при необходимости снять поручение отдельной командой — вне объёма M3) |

| Console (`npm run dev` → локальный API 8010, `/changes/chg_calc_m3_001`) | Левая колонка из `GET …/phases`: Ф0 «пройдена», Ф1 «согласована», Ф2 «неактуально · итерация 1», Ф3 «согласована · замечаний 1», Ф4 «в работе»; верхняя панель — фаза/бюджет/блокеры, процента готовности нет; нижняя панель — одна CTA из `Guidance`. Ф2 «Результат»: обзор `design/overview.md` с отрисованной mermaid-схемой и таблицами, карточки ADR-002…005 со статусами («требует пересмотра» у 0002 — открытое поручение API-проверки, и у 0004 — правка после согласования), разделы Предложение/Обоснование/Альтернативы/Последствия, поручение и сводка «Что изменил / Что осталось» + затронутые артефакты как ссылки, кнопки «Открыть ADR» и «Запросить альтернативу» на карточке. Ф3 «Результат»: по умолчанию «Сценарии 7» (шаги S1…S10 со ссылками на `SCR-001`), «Экраны 1» — карточка с пятью состояниями и элементами `EL-*` (кнопка «Комментарий» у каждого), «Связи 7»; правая панель — замечание `SCR-001 · EL-display` из CLI. «Проверки» Ф3: гейт `ui` «доступен · согласовано на текущей ревизии», блок «Нужен ли UI: UI требуется, источник: архитектор», axe и визуальная регрессия — «запланировано на исполнении» (не зелёные), решения по гейту с колонкой «Фаза». Ошибок в браузерной консоли нет. **Наблюдение**: первая загрузка рабочего пространства и вкладок Ф2/Ф3 на живом GitHub занимает 60–120 с (TD-042 — ревизии фаз читаются историей коммитов по путям на каждый запрос) |

### Итог T099

DoD выполнен на живом контуре: архитектура принята после «Запросить альтернативу» и раунда доработки, интерфейс принят из CLI; решения версионно привязаны к ревизии **своей** фазы (`phase=requirements|architecture|interface`, `commit_sha` = ревизия `spec/**` / `design/**` / `design/ui/**`); правка ADR после согласования сделала согласование архитектуры `stale`, не тронув требования и интерфейс; CLI, API и Console показывают одну проекцию фаз и один следующий шаг. Найдено и исправлено четыре дефекта (TD-048, TD-049 + проекция за пределами `specification`), зарегистрирован техдолг TD-050 (плейсхолдеры пака), TD-051 (стоимость без прайса), уточнён TD-042 (латентность). Побочные эффекты прогона: изменение `chg_calc_m3_001` стоит в фазе «План» (M4), поручение `rw_04834458…` фазы `architecture` осталось `pending`.
