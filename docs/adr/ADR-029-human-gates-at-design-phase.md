# ADR-029: Human Gates at the Design Phase and Machine-Only Pipeline Resolution

- **Статус**: принято
- **Дата**: 2026-09-18
- **Автор**: architect
- Записано по решению оператора, принятому на живом e2e-пилоте T-043 (изменение `chg_t043_p02`, 2026-09-18) и зафиксированному в журнале `docs/t043-pilot-journal.md`. Уточняет [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1 (базовый набор человеческих гейтов) и [ADR-023](ADR-023-risk-classes-and-control-points.md) п.1, п.3, п.4, п.6 (место гейта `ui` в матрицах). Опирается на [ADR-005](ADR-005-stage-scoped-graphs-light-workflow-core.md) п.2 (review завершается через merge), [ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2 (merge — человеческое решение) и [ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7 (version-bound approval).
- **Уточнено** (2026-09-18, живой прогон T-043, `chg_t043_p02`): наблюдаемый merge сам по себе не закрывает `review_verification` — стадия завершается merge'ом, **авторизованным** version-bound approving review на итоговом SHA ([ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2, [ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7, T-032); merge без такого review паркует ран в `waiting` (п.5). Design-стадии (`specification`) закрываются самим merge'ом дизайн-CR (п.2/п.4).
- **Уточнено** ([ADR-039](ADR-039-phase-rounds-and-phase-bound-decisions.md), 2026-09-19, M3): человеческие гейты `specification` и `ui` design-стадии закрываются по фазам — согласование требований и архитектуры (`specification`, различаются `Decision.phase`) и согласование либо явный пропуск интерфейса (`ui`: `passed` или `skipped` при `waived`). Стадия завершается, когда согласованы все фазы маршрута; согласование нефинальной фазы запускает следующий раунд той же стадии.

## Контекст

- **Что задано источниками.** [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1 уже относит формирование требований, UX/UI и архитектуру к фазам **Human-in-the-loop**, а гейт `specification` стадии specification назван покрывающим requirement/UX/architecture discovery approval (`rules/gates.py:47-54`). То есть подтверждение UX/UI — решение design-фазы, а не фазы реализации.
- **Как это выражалось в политике гейтов (и где разошлось).** [ADR-023](ADR-023-risk-classes-and-control-points.md) сделал человеческие гейты частично риск-зависимыми: базовый набор `HUMAN_GATES = {specification, review}`, класс риска расширяет его (`RISK_HUMAN_GATES`: R1 → `ui`, R2 → `ui`+`planning`, R3/R4 → все гейты), а фактический набор человеческих гейтов стадии — `required_human_gates(route, stage, risk) = (HUMAN_GATES | RISK_HUMAN_GATES[risk]) & required_gates(route, stage)` (`rules/gates.py:59-82`). Но **место гейта `ui` было выбрано неверно**: маршрут `standard` (и строже) требовал `ui` на стадии **construction** (`_ROUTE_EXTRA_GATES`, `rules/gates.py:37-42`), а точка контроля `ux` была привязана к паре `(construction, ui)` (`CONTROL_POINT_BINDING`, `orchestration/policy/risk.py:160-167`). На construction `ui` попадал в машинный набор: `_machine_gate_results` считает машинные гейты как `required_gates(route, stage) - HUMAN_GATES` (`orchestration/stages/gates.py:472`), то есть вычитает только **базовый** набор, а не риск-зависимый.
- **Живая находка пилота T-043.** На изменении `chg_t043_p02` попытка construction записала **оба** гейта, `code` и `ui`, как `passed` с summary «pipeline success at f61f247a…»: зелёный пайплайн подал человеческое UI-решение за человека. Журнал (`docs/t043-pilot-journal.md`, запись 2026-09-18 «прикрепление Implementation Contract через `run advance --contract-json`») фиксирует это как находку «политика гейтов»: «у смешанной стадии construction (`code` machine + `ui` human, R1+standard) **оба** гейта прошли по `pipeline success» — human-вердикт по UI на пилоте фактически не требуется.
- **Вторая живая находка того же прогона.** Попытка `review_verification` завершилась `blocked` с reason «the attempt produced no changes to publish» (агент роли quality отработал, 298 529 токенов). Механика: `agent.py::_attempt` блокирует попытку, когда `_publish` вернул `None` (`orchestration/stages/agent.py:259-265`), а `_publish` возвращает `None` при пустом `collect_changes(workspace)` — ревью уже сделанного изменения файловых изменений не порождает. При этом `review_verification` — единственная стадия, чей success-path требует change request (`_STAGES_REQUIRING_CHANGE_REQUEST`, `orchestration/stages/checks.py:34-38`), и это CR **ревьюируемого** изменения: `checks.py::change_request_missing` проверяет `change.change_request`, а завершение стадии идёт через `merge` с обязательным `ChangeRequestRef` ([ADR-005](ADR-005-stage-scoped-graphs-light-workflow-core.md) п.2, [ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2). У стадии нет пути опубликовать вердикт, и все 10 ранов пилота упрутся в это место.
- **Кто решает.** Оба пункта — значимые решения политики гейтов; оператор принял их на пилоте. Этот ADR их фиксирует и не пересматривает.

## Решение

### 1. Человек подтверждает спецификацию, UX/UI и архитектуру на design-фазе — на стадии `specification`

Подтверждение человеком спецификации, UX/UI и архитектуры происходит на design-фазе, то есть на стадии `specification` маршрута — она уже покрывает requirement/UX/architecture discovery approval ([ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1). Поэтому **UI-гейт переносится со стадии construction на стадию specification** для маршрутов, которые его требуют (`standard`, `architecture`, `foundation`); `quick` остаётся без UI. Формально `_ROUTE_EXTRA_GATES` для этих трёх маршрутов становится `{Stage.SPECIFICATION: {Gate.UI}}` вместо `{Stage.CONSTRUCTION: {Gate.UI}}`. Число гейтов маршрута не меняется (те же семь на `standard`), меняется только стадия, несущая `ui`: `specification {specification, ui}`, `planning {planning}`, `construction {code}`, `review_verification {review, verification}`, `release {release}`.

### 2. `ui` входит в базовый человеческий набор; пайплайн никогда не подаёт человеческий гейт

`ui` становится частью базового набора [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1: `HUMAN_GATES = {specification, ui, review}`. На любом маршруте, который требует UI, `ui` — человеческое решение. Точка контроля `ux` переезжает вместе с гейтом: её привязка становится `(specification, ui)` (`CONTROL_POINT_BINDING`), а не `(construction, ui)`.

Пайплайн-вердикт (CI) **никогда** не удовлетворяет человеческий гейт. Машинное отображение стадии покрывает только её машинные гейты — `required_gates(route, stage)` минус риск-зависимый человеческий набор `required_human_gates(route, stage, risk_class)`, — и стадия, у которой человеческие гейты не удовлетворены, пайплайном не резолвится: она остаётся запаркованной на человека.

### 3. Машинный набор стадии — `required_gates − required_human_gates(route, stage, risk)`

Точная формулировка п.2 как правки политики: в резолюции ожидания машинные гейты вычисляются как

```
machine_gates(route, stage, risk_class)
    = required_gates(route, stage) − required_human_gates(route, stage, risk_class)
```

вместо сегодняшнего `required_gates(route, stage) − HUMAN_GATES` (`orchestration/stages/gates.py:472`) и вместо базового набора в `_human_gates` / `_is_purely_human_gated` (`:70-81`). Отсюда же следует выбор ожидания агентом (`agent.py::_waiting`, `:379-402`): стадия без машинных гейтов паркуется на вход человека (`WaitForInputAction`), стадия с машинными — на CI (`WaitForCIAction`). Риск-класс здесь — эффективный (`effective_change_risk_class(run.implementation_contract, run.route)`), тот же, что уже используется в merge-контексте (`orchestration/stages/gates.py:404`).

### 4. Следствие для маршрута `standard` с R1/R2

На `standard` с R1/R2 стадия `specification` требует `specification` и `ui` — **оба человеческие**: чисто человеческая стадия, которая резолвится version-bound человеческим approval'ом ([ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7) или наблюдённым merge'ом дизайн-change-request'а ([ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2). Construction после этого требует только машинный гейт `code`, который резолвится CI по итоговому SHA (FR-009). Это и есть предмет решения: UI подтверждается до реализации, а не машинным вердиктом поверх неё.

### 5. `review_verification` завершается без публикации change

`review_verification` завершается **без публикации изменения кода**: ревьюер проверяет change request, который уже находится в ревью (change request стадии construction), и стадия резолвится человеческим merge'ом ([ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2). Попытка ревью, не породившая изменений в workspace, **не должна** блокироваться с «no changes to publish»: отсутствие файловых изменений — нормальный результат ревью, а именованный стадией CR — это CR ревьюируемого изменения (живой lookup открытого change request'а изменения через порт merge-requests, FR-011), а не новый CR. Стадия паркуется в `waiting` на человеческий merge (наблюдение `merged=True` уже обрабатывается `_resolve_review`, `orchestration/stages/gates.py:355-409`).

Завершение стадии идёт при этом **через merge policy**, а не через сам факт merge: наблюдаемый merge несёт merge-контекст (`executor="human"`), и политика требует version-bound approving decision на гейте `review` при `commit_sha == expected_sha` (`orchestration/policy/merge.py::evaluate_merge`; T-032 `rules/merge_protection.py`: `required_approving_reviews=1`) — merge без такого решения паркует ран в `waiting`, не завершая стадию (живая проверка — `chg_t043_p02`, 2026-09-18, `docs/t043-pilot-journal.md`). Этим `review_verification` отличается от design-стадий, которые резолвятся самим merge'ом дизайн-change-request'а (п.2/п.4).

## Реализация

Раздел задаёт implementation-дизайн для роли `develop`; новых зависимостей из него не следует.

**`src/dark_factory/rules/gates.py`**
- `_ROUTE_EXTRA_GATES`: `ui` переносится в `Stage.SPECIFICATION` для `standard`/`architecture`/`foundation` (п.1).
- `HUMAN_GATES`: добавить `Gate.UI` (п.2). Комментарий «quick skips UI/extended gates, not human ones» остаётся верным: `quick` не требует `ui`, поэтому человеческим он не становится.
- `RISK_HUMAN_GATES` и `required_human_gates` не меняются: R1 уже содержит `ui`, пересечение с `required_gates` по-прежнему делает `ui` человеческим только там, где маршрут его требует; меняется только базовый набор.

**`src/dark_factory/orchestration/stages/gates.py`**
- `_human_gates` и `_is_purely_human_gated` принимают `risk_class` и считают человеческий набор через `required_human_gates` (п.3); `gate_resolved` получает класс (или готовый человеческий набор) как параметр — сигнатура уже несёт `route`, добавляется риск.
- `gate_resolved`: зелёный пайплайн резолвит стадию только если у неё **нет** обязательных человеческих гейтов; ветка человеческого решения (наблюдённый version-bound approval или `merged`) сохраняется и распространяется на все человеческие гейты стадии. Тем самым `_resolve_machine_gated` вызывается только при пустом человеческом наборе, а стадия с неудовлетворёнными человеческими гейтами остаётся в `waiting`.
- `_machine_gate_results` считает `required_gates − required_human_gates` (п.3): на `standard` construction машинным остаётся только `code`, `ui` больше не может быть `passed` пайплайном.
- `_resolve_human_gated` записывает `GateResult(PASSED)` для **каждого** человеческого гейта стадии, а не для первого по алфавиту (`:241,265-272`): на `specification` с `ui` это `specification` и `ui`. Иначе `flow._block_reason` (`orchestration/flow.py:476-486`) заблокирует продвижение по `unsatisfied_gates` для гейта без результата.

**`src/dark_factory/orchestration/policy/risk.py`**
- `CONTROL_POINT_BINDING[ControlPoint.UX]` → `ControlPointBinding(stage=Stage.SPECIFICATION, gate=Gate.UI)` (п.2). Матрица точек контроля выводится из `required_human_gates` (`required_control_points`), поэтому отдельной таблицы не появляется.

**`src/dark_factory/orchestration/stages/agent.py`**
- `_publish` для `REVIEW_VERIFICATION`: пустой workspace не возвращает `None`, а разрешает CR, который находится в ревью — открытый change request изменения через порт merge-requests (`find_existing`, живой FR-011-lookup; поле `Change.change_request` сегодня нигде не заполняется, поэтому буквальный доступ к нему не сработал бы) — и попытка паркуется в `waiting` с этим CR; открытого CR нет (merged/closed или отсутствует) → попытка по-прежнему блокируется (`agent.py:271-...`). Поведение других стадий (construction) не меняется.
- `_waiting` выбирает ожидание по машинному набору `context.required_gates − context.human_gates`, а не по `required_gates − HUMAN_GATES` (`:407-...`).

**`src/dark_factory/orchestration/stages/context.py`**
- В `StageContext` добавлен риск-зависимый набор `human_gates`; `build_context` принимает `risk_class` (эффективный класс рана; без него — объявленный класс снапшота) и вычисляет его через `required_human_gates`. Так ожидание стадии и проверки flow весят **один и тот же** набор гейтов.

**`src/dark_factory/orchestration/runner.py`**
- Оба call-site'а `gate_resolved` и `build_context` получают эффективный класс рана через `_effective_risk_class(run)` (`:416,473,613`).

**`src/dark_factory/orchestration/stages/checks.py`**
- `_STAGES_REQUIRING_CHANGE_REQUEST` и `change_request_missing` сохраняются, но их семантика уточняется: для `review_verification` требуется CR **ревьюируемого** изменения (артефакт construction), а не публикация самой стадии (`:34-38,123-131`).

**Тесты** (для роли `develop`): truth-table `gate_resolved`/`_resolve_*` для смешанной стадии; `ui` на `specification` не проходит по зелёному пайплайну; construction `standard` резолвится пайплайном по `code`; `_resolve_human_gated` записывает все человеческие гейты стадии; точка `ux` обязательна на стадии `specification`; review завершается без публикации (пустой workspace → `waiting`, не `blocked`) и резолвится merge'ом; выбор `wait_for_input`/`wait_for_ci` для риск-зависимого машинного набора.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Оставить `ui` на construction, но резолвить его отдельно от машинного `code` (двухфазная резолюция одной стадии) | Маршрутная матрица не меняется | Человеческое UX-решение попадает внутрь фазы реализации — после расхода на code; противоречит порядку фаз [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1; у одной стадии два разных протокола завершения | Отклонено решением оператора: подтверждение — на design-фазе |
| Оставить как есть и считать зелёный пайплайн UI-решением | Ноль изменений | Противоречит [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1 (UX/UI — Human-in-the-loop) и п.5 («UI нельзя подтвердить автоматическим сравнением»); живой пилот показал, что человек при этом исключён из решения | Отклонено: человеческое решение не может подаваться машиной |
| Ревью публикует пустой/вердикт-коммит, чтобы у стадии был свой change request | Не трогает `checks.py`/`_publish` | Фиктивный CR/коммит для вердикта; непонятно, что мержит человек; загрязняет историю и merge-путь | Отклонено: вердикт ревью — не изменение кода |
| Review паркуется без CR и резолвится человеческим approval'ом | Не нужен CR | Завершение review идёт через merge ([ADR-005](ADR-005-stage-scoped-graphs-light-workflow-core.md) п.2), а merge требует CR ([ADR-011](ADR-011-risk-based-merge-release-policy.md) п.2); без merge исчезает человеческий release-гейт | Отклонено: merge — единственный путь завершения review |
| Риск-зависимый машинный набор применить только к `ui`, не к `planning` | Меньше правок | Правило «пайплайн не подаёт человеческий гейт» перестаёт быть общим и снова даёт расхождение на R2 (`planning` — человеческий гейт) | Отклонено: правило формулируется один раз — `required_gates − required_human_gates` |

## Последствия

**Позитивные**
- Человеческое подтверждение UX/UI стоит там, где оно и есть по [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1 — на design-фазе, до расхода на реализацию.
- Пайплайн-вердикт больше не может подать человеческий гейт: правило «машинный набор = required − человеческий» верно для `ui`, `planning` и будущих риск-зависимых человеческих гейтов.
- Construction на `standard` становится чисто машинной стадией (`code` по итоговому SHA, FR-009) — ровно то, что нужно автономному исполнению.
- У `review_verification` появляется путь завершения: вердикт ревьюера — не публикация, а решение по уже открытому CR.

**Негативные / риски**
- На одной стадии (`specification`) теперь два человеческих гейта (`specification`, `ui`); резолюция и запись результатов должны покрывать оба, иначе продвижение заблокируется (`flow._block_reason`). Это правка `_resolve_human_gated`, отмеченная в «Реализации».
- Design-change-request становится носителем двух решений (требования и UI), и его merge — составное решение. Для R0 на `standard` UI-подтверждение теперь обязательно, что повышает человеческую цену косметического изменения; короткий путь для таких изменений — `quick` (там `ui` не требуется).
- Правило становится риск-зависимым в резолюции, а не только в матрице: `gate_resolved` и резолюции получают риск-класс. Ошибка в передаче класса даёт неверный набор человеческих гейтов — компенсируется тем же полом класса, что и в [ADR-023](ADR-023-risk-classes-and-control-points.md) п.2.
- Открытая тема пилота этим ADR не закрывается: путь человеческого решения для риск-зависимого `planning` (точка `solution` у R2) по-прежнему требует наблюдаемого решения (`ScmFactsProvider` мапит review'и planning-стадии на `Gate.REVIEW`; см. открытые темы в журнале) — отдельный кусок работы.

**Что это уточняет и отменяет в предыдущих ADR**
- [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1: таблица фаз не меняется; её проекция на стадии — базовый человеческий набор `HUMAN_GATES` — расширяется гейтом `ui`. Фаза UX/UI (Human-in-the-loop) теперь проверяется на стадии, соответствующей design-фазе.
- [ADR-023](ADR-023-risk-classes-and-control-points.md) п.1: перечень человеческих гейтов `{specification, review}` заменяется на `{specification, ui, review}`; `ui` — человеческий, а не машинный гейт.
- [ADR-023](ADR-023-risk-classes-and-control-points.md) п.3: формула `required_human_gates` сохраняется, но колонка «человеческие гейты сверх ADR-018» для `R0` получает `ui` на маршрутах, требующих UI (сегодня — «—»), а «машинные гейты» читаются как `required_gates − required_human_gates`, а не как `base(route, stage)`.
- [ADR-023](ADR-023-risk-classes-and-control-points.md) п.4: строка точки контроля `ux` меняет стадию — `specification` вместо `construction`; колонка `R0` становится «обязательна, когда маршрут требует `ui`».
- [ADR-023](ADR-023-risk-classes-and-control-points.md) п.6: матрица маршрутов — «`base` + `ui` на Construction» заменяется на «`ui` на Specification» для `standard`/`architecture`/`foundation`.

## Дальше

- Реализация — в рамках T-043 (инкремент пилота); после неё живой прогон `chg_t043_p02` должен пройти construction машинным `code` и завершить review человеческим merge'ом.
- Путь человеческого решения для риск-зависимого `planning` (точка `solution` у R2) — отдельная находка пилота, этим ADR не решённая.

Связанные задачи: T-043 (пилот, инициировал решение), T-004/T-013/T-021 (гейты и их исполнение), T-016 ([ADR-018](ADR-018-human-participation-autonomous-execution.md) п.3, Implementation Contract), T-032/T-085 (merge и release evidence, [ADR-011](ADR-011-risk-based-merge-release-policy.md)), T-080 ([ADR-023](ADR-023-risk-classes-and-control-points.md), матрицы гейтов и точек контроля).

Связанные ADR: [ADR-005](ADR-005-stage-scoped-graphs-light-workflow-core.md), [ADR-009](ADR-009-minimal-bootstrap-otel.md), [ADR-011](ADR-011-risk-based-merge-release-policy.md), [ADR-018](ADR-018-human-participation-autonomous-execution.md), [ADR-023](ADR-023-risk-classes-and-control-points.md).
