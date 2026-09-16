# ADR-023: Risk Classes R0–R4 and Control Points

- **Статус**: принято
- **Дата**: 2026-09-16
- **Автор**: architect
- Закрывает отложенные условия [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.5 («риск повышен до R2+» — ручная оценка до T-080) и [ADR-011](ADR-011-risk-based-merge-release-policy.md) п.5 («вход политики — риск-классы R0–R4 (T-080)»); реализует задачу T-080 (docs `plan.md`), чеклист `specs/001-dark-factory-mvp/tasks.md` T045.

## Контекст

- **Что уже есть.** Доменные типы риск-класса и политика монотонности реализованы с T-016: `RiskClass` (`src/dark_factory/changes/enums.py:25`), `RISK_ORDER` / `R2_THRESHOLD` / `RISK_ASSESSMENT_MANUAL` / `transition_risk_class` / `is_r2_or_higher` (`src/dark_factory/orchestration/policy/risk.py:18-48`), проверка `risk_raise_violation` (`src/dark_factory/orchestration/policy/escalation.py:116`), таблица участия по фазам `PHASE_PARTICIPATION` (`src/dark_factory/orchestration/policy/participation.py:27`) и машиночитаемая запись нарушения `EscalationViolation` (`src/dark_factory/changes/escalations.py:8`).
- **Чего не хватает.** Риск-класс сегодня — *поле и повод для эскалации*, но не *политика*: нет детерминированного вывода класса из фактов изменения, нет матрицы «класс → гейты», нет машиночитаемого набора человеческих точек контроля, а условие «риск повышен до R2+» остаётся ручной оценкой с честной пометкой в диагносте (`RISK_ASSESSMENT_MANUAL = True`, `manual_assessment=True`). Это прямо зафиксировано как незакрытый разрыв: [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.5 «Дальше» («до T-080 условие действует как ручная оценка, а не как гейт») и `docs/descriptions/crm-end-to-end-flow.md` §12 («Риск-матрица R0–R4 как гейт | не реализовано»).
- **Требование DoD T-080.** «Маршрутизация детерминирована; опасные изменения не проходят по короткому пути; условие эскалации R2+ работает как гейт, а не как ручная оценка» (`docs/plan.md` T-080).
- **Что задано источниками (политику не изобретаем).** Следствия класса — `docs/descriptions/crm-end-to-end-flow.md` §5.4: `R0` — косметика/документация, короткий маршрут, UI-гейт не требуется; `R1` — автономно до состояния «готово к merge»; `R2` — обязателен reconciliation-план, эскалация человеку при обнаружении, release-гейт с вердиктом operation; `R3`/`R4` — только с явным человеческим контролем. Монотонность — [ADR-011](ADR-011-risk-based-merge-release-policy.md) п.5: LLM повышает класс, понижает — только формальная политика. Участие человека по фазам — [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1. Четыре точки контроля — `crm-end-to-end-flow.md` §1.2 (строка Discovery: «Problem, Solution, UX, Discovery Gate»). Границы доверия — [ADR-015](ADR-015-repository-boundaries.md) п.3: «Агент не должен через обычный R0/R1-процесс одновременно менять реализацию, правила классификации риска и собственные quality gates».
- **Ограничения реализации.** `Route`, `Gate`, `EscalationRule`, `RiskClass` — сериализуемые значения контрактов (ADR-015 п.3): `Route` зафиксирован в CHECK-констрейнте БД (`migrations/versions/0001_state_schema.py:71` — `route IN ('quick', 'standard')`), `EscalationRule` и `EscalationViolation` — в `deploy/runs/schema/stage-result.schema.json`. `RouteProfile.human_gates` сегодня возвращает набор, не зависящий от маршрута и класса (`src/dark_factory/flows/routes.py:29,45`), а `HUMAN_GATES` не используется в `flow.py` — то есть человеческие гейты сейчас *декларативны*, а не машинно проверяемы.

## Решение

### 1. Три ортогональных понятия: класс, гейт, точка контроля

- **Гейт** (`Gate`) — проверка стадии; бывает машинной (`code`, `verification`, `release`) и человеческой (`specification`, `review` — `HUMAN_GATES`).
- **Риск-класс** (`RiskClass`, R0–R4) — свойство изменения; определяет, какие гейты обязаны требовать человеческого решения и на каком маршруте изменение вообще допустимо. Класс **не создаёт новых гейтов**: набор машинных гейтов остаётся функцией `(route, stage)` (ADR-005: `rules/gates.py` — единственный источник гейт-политики).
- **Точка контроля** (`ControlPoint`) — новая сущность: именованное решение человека, привязанное к паре `(stage, gate)` и обязательное начиная с определённого класса риска.

Такое разделение сохраняет существующие контракты (`Gate` не расширяется, `required_gates` не меняет набор значений) и делает расширение риска аддитивным.

### 2. Семантика классов и детерминированный вывод класса

Класс выводится **чистой функцией** из наблюдаемых фактов изменения (`RiskFacts`), первый совпавший уровень выигрывает:

| Класс | Смысл | Условие (первое совпадение) |
|---|---|---|
| **R4** | Изменение правил самой фабрики: реализация, правила классификации риска и quality gates в одном изменении | `factory_self_modification` |
| **R3** | Регуляторное или необратимое изменение: ПДн/регулируемые данные, необратимая операция | `irreversible` or `regulated_data` |
| **R2** | Изменение в защищённой границе или новое решение (новая технология, граница сервиса, хранилище, протокол, IAM-модель) | `boundaries` непусто or `decision_class is NEW_PATH` |
| **R0** | Документация/косметика: поведения не меняет, UI не затрагивает | `documentation_only` |
| **R1** | Обычное ограниченное изменение внутри утверждённого scope | иначе |

Входы — минимальный достаточный набор из уже существующих типов: `boundaries: tuple[BoundaryArea, ...]` (`BoundaryArea` — `changes/enums.py:35`), `decision_class: DecisionClass` (результат `classify_decision`, `policy/decision_class.py:70`), `irreversible`, `regulated_data`, `documentation_only`, `factory_self_modification` (наблюдаемые булевы факты: необратимость запрошенной операции — ср. `irreversible_operation_violation`; «регулируемые данные» и «правит trusted-политику» — предикаты по diff/путям, ADR-015 п.3).

**Монотонность и запрет понижения.** Эффективный класс есть максимум по `RISK_ORDER` из трёх слагаемых:

```
effective = max(declared_risk_class, classify_risk(facts), route_floor)
```

- агент может **повысить** класс (declared выше пола) — значение сохраняется;
- агент **не может понизить** класс: `transition_risk_class` уже запрещает понижение при `decided_by=AGENT` (`RiskClassPolicyError`, `policy/risk.py:38-43`) и переиспользуется как есть;
- понижение *declared* ниже формального пола невозможно по построению: пол вычисляется политикой и берётся максимумом. Понижение, сделанное политикой или человеком, остаётся возможным только до пола;
- гейт спецификации (T-021) уже машинно блокирует понижение класса контрактом — находка `risk_class_lowered_without_policy` (`quality/gates/specification.py:64-67,114-124`) остаётся в силе.

Формальная политика понижения — **сам пол**: он не может быть опущен решением агента, и именно он определяет, какие гейты и точки контроля обязательны.

### 3. Матрица «класс → маршрут → гейты»

Риск-класс **не расширяет** набор машинных гейтов; он (а) ограничивает допустимые маршруты и (б) расширяет набор **человеческих** гейтов. Итоговая матрица:

| Класс | Допустимые маршруты | Машинные гейты | Человеческие гейты сверх ADR-018 |
|---|---|---|---|
| `R0` | `quick`, `standard` | `base(route, stage)`; на `quick` — без `ui` | — |
| `R1` | `quick`, `standard` | `base(route, stage)`; на `standard` — с `ui` | `ui` |
| `R2` | **только `standard`** (и строже) | `base(standard, stage)` — все 7 | `ui`, `planning` |
| `R3` | `standard`, `architecture`, `foundation` | все 7 | **все гейты стадии** |
| `R4` | `standard`, `architecture`, `foundation` | все 7 | **все гейты стадии** |

где `base(route, stage)` — существующая функция `required_gates(route, stage)` (`rules/gates.py:35`), а «человеческие гейты» вычисляются как

```
required_human_gates(route, stage, risk_class)
    = (HUMAN_GATES | RISK_HUMAN_GATES[risk_class]) & required_gates(route, stage)
```

`RISK_HUMAN_GATES` — данные политики: `R0 → {}`, `R1 → {ui}`, `R2 → {ui, planning}`, `R3/R4 → все Gate`. Пересечение с `required_gates` нужно, чтобы человеческим становился только фактически требуемый гейт (например, `ui` на `quick` не требуется, поэтому точкой контроля не становится).

**«Опасные изменения не проходят по короткому пути» — машинно:** `route_allows_risk(route, risk_class)` истинно, только если класс попадает в полосу маршрута; `quick` имеет полосу `R0–R1`. Проверка вызывается на каждом продвижении стадии (`flow._escalation_reason`) и при выборе маршрута; нарушение даёт `EscalationViolation(rule=RISK_RAISED_TO_R2)`, а не декларацию в документе. R2+ на `quick` невозможен, потому что маршрут не может быть выбран, а если он был выбран до повышения класса — продвижение стадии блокируется.

### 4. Матрица «класс → участие человека» и точки контроля

Точка контроля обязательна **тогда и только тогда**, когда её гейт входит в `required_human_gates(route, stage, risk_class)`. Это единственное правило; таблица ниже — его следствие, а не отдельная таблица:

| Точка контроля | Смысл (источник) | Стадия | Гейт | R0 | R1 | R2 | R3 | R4 |
|---|---|---|---|---|:--:|:--:|:--:|:--:|:--:|
| `problem` | проблема, цели, scope, acceptance criteria подтверждены человеком (ADR-018 п.1, фаза «Формирование проблемы и требований») | `specification` | `specification` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `solution` | решение, контракты, данные, NFR и риск-класс подтверждены (фаза «Архитектура») | `planning` | `planning` | — | — | ✅ | ✅ | ✅ |
| `ux` | UX-флоу, состояния экранов, UI-AC подтверждены (фаза «UX/UI») | `construction` | `ui` | — | ✅ | ✅ | ✅ | ✅ |
| `discovery_release` | закрытие discovery и авторизация выпуска: утверждённый Implementation Contract (ADR-018 п.3) + человеческое решение на гейте release-авторизации (ADR-011 п.2) | `review_verification` | `review` | ✅ | ✅ | ✅ | ✅ | ✅ |

Пояснения к таблице:

- `problem` и `discovery_release` обязательны для всех классов: это базовые человеческие гейты ADR-018 (`HUMAN_GATES = {specification, review}`), и они сохраняются как есть — риск только **добавляет** человеческие гейты и никогда не убирает их;
- `ux` для `R1` обязателен, только когда гейт `ui` требуется маршрутом (`standard`); на `quick` UI-гейта нет, поэтому нет и точки `ux` — это и есть «R0/R1 короткий маршрут, UI-гейт не требуется» (`crm-end-to-end-flow.md` §5.4) с поправкой на то, что R1 обычно идёт по `standard`;
- `solution` с `R2` — «человек проверяет уровень риска» на фазе планирования (ADR-018 п.1) становится обязательным решением, а не on-the-loop наблюдением;
- `R3`/`R4` делают человеческим каждый требуемый гейт стадии — «только с явным человеческим контролем» (§5.4);
- `discovery_release` — четвёртая точка; в `crm-end-to-end-flow.md` §1.2 она названа «Discovery Gate». Её release-половина — это решение, которое уже проверяет merge policy (человеческое решение на `review`, привязанное к итоговому SHA, ADR-011 п.2); discovery-половина — утверждение Implementation Contract, уже машинно проверяемое `contract_entry_violation` (T-016) на входе в construction.

Режимы участия по фазам из [ADR-018](ADR-018-human-participation-autonomous-execution.md) п.1 и их проекция на стадии (`participation.py`) **не дублируются** этим ADR: матрица точек контроля расширяет таблицу фаз в части «какие решения обязаны быть зафиксированы», но не переписывает режимы фаз.

### 5. Машинная проверка R2+ (закрытие отложенного условия ADR-018 п.5)

Условие «риск повышен до R2+» перестаёт быть ручной оценкой и становится **машинной проверкой обязательств класса**:

Что проверяется при эффективном классе ≥ R2:
1. `route_allows_risk(run.route, effective)` — короткий маршрут запрещён;
2. `missing_control_points(route, stage, effective, decisions, sha=...)` — обязательные точки контроля класса имеют человеческое решение `APPROVED` (`DecisionSource.HUMAN`, `DecisionOutcome.APPROVED`), привязанное к итоговому SHA (version-bound approval, ADR-009 п.7);
3. наличие reconciliation-плана — уже проверяется гейтом спецификации находкой `reconciliation_required_for_high_risk` (`quality/gates/specification.py:47-62,101-111`) и `required_artifacts` (`context/sdd/strictness.py:53-60`); дублирования не вводим.

Что возвращается: `None`, если обязательства выполнены, иначе один `EscalationViolation` с `rule=EscalationRule.RISK_RAISED_TO_R2` и `reason`, перечисляющей **конкретные невыполненные обязательства** (маршрут, отсутствующие точки контроля). Значение правила `risk_raised_to_r2` в контракте не меняется (ADR-015 п.3: переименование enum-значения — breaking change).

`RISK_ASSESSMENT_MANUAL` **удаляется**: ни одно условие эскалации больше не является ручной оценкой, и константа-признак теряет смысл. Поле `EscalationViolation.manual_assessment` **сохраняется** как deprecated и всегда `False`: удаление поля — это breaking change сериализуемого контракта (`deploy/runs/schema/stage-result.schema.json`, `console/src/api/types.ts`), требующий инкремента `SchemaVersion` (ADR-015 п.3). Уборка поля относится к следующей ревизии схемы записи.

### 6. Расширение маршрутов: `architecture` и `foundation`

Добавляются два маршрута (аддитивное расширение `Route`, ADR-005):

| Маршрут | Стадии | Гейты | Полоса классов | Профиль строгости (`context/sdd/strictness.py`) |
|---|---|---|---|---|
| `quick` | 5 (существующие) | `base` без `ui` | R0–R1 | `bugfix-r0` |
| `standard` | 5 (существующие) | `base` + `ui` на Construction | R0–R4 | `product-feature`, `ui-research` |
| `architecture` | 5 (существующие) | все 7 (`base` + `ui`) | **R2–R4** | `architecture-change` |
| `foundation` | 5 (существующие) | все 7 | **R3–R4** | `repository-rebuild`, `platform-change` |

- **Топология не меняется** в T-080: все четыре маршрута проходят `SPECIFICATION → PLANNING → CONSTRUCTION → REVIEW_VERIFICATION → RELEASE`. Это сохраняет инварианты `routes.md` §7 и не требует правок таблицы переходов; `RouteProfile` уже поддерживает произвольную последовательность стадий, если в будущем понадобится маршрут с пропуском (`architecture-change` по `sdd-native-core.md` §13 — «impact → design → ADR → approval» — кандидат на это, но не в этой задаче). Разница маршрутов — только гейты и полоса классов, как сегодня у `quick`/`standard`.
- **Пол маршрута** (`RouteProfile.min_risk_class`, `max_risk_class`) — часть формулы эффективного класса: `foundation` поднимает класс до R3, `architecture` — до R2, потому что изменение архитектурной границы по определению попадает в `boundaries` (§2). Маршрут, таким образом, может только **повышать** класс, но не понижать.
- **Выбор маршрута детерминирован:** `select_route(profile, risk_class) -> Route` по профилю строгости, затем ужесточение по классу — маршрут повышается, если класс превышает его потолок (R2+ с профилем `bugfix-r0` даёт `standard`, а не `quick`). Порядок строгости: `quick < standard ≤ architecture ≤ foundation`.
- Расширение `Route` аддитивно на уровне Python/JSON (значение не входит ни в один JSON-схемный контракт), но **требует миграции БД**: CHECK-констрейнт `execution.route` перечисляет значения явно (`migrations/versions/0001_state_schema.py:71`), поэтому добавляется миграция, расширяющая список значений. Обратимость — с оговоркой по данным: `downgrade` возвращает узкий CHECK и **падает fail-closed**, пока в `execution` есть строки с новыми маршрутами (сначала переводятся/снимаются такие строки, потом откат) — проверено на живой PostgreSQL в T-080.

### 7. Границы доверия (ADR-015 п.3)

- **Правила классификации и матрица — trusted-слой.** Таблицы и функции живут в `changes/risk.py`, `rules/gates.py` и `orchestration/policy/`; они не читаются из промптов, скиллов и packs. Агент не может передать «свой» класс в обход функции: класс всегда пересчитывается политикой (§2).
- **Агент не может понизить класс.** Единственные пути изменения — `transition_risk_class` (понижение только `POLICY`/`HUMAN`) и `effective_risk_class` (только максимум). Оба — чистые функции trusted-ядра.
- **Изменение самих правил классификации принудительно R4.** Факт `factory_self_modification` (изменение `rules/**`, `orchestration/policy/**`, quality-gates) даёт R4 и маршрут `foundation`; такое изменение не может пройти по короткому пути R0/R1 и не может быть применено тем же изменением, которое правит классификацию — прямое исполнение требования ADR-015 п.3.
- **Машинная проверка понижения в гейте спецификации** (`risk_class_lowered_without_policy`, T-021) сохраняется как независимый контроль: даже если бы пол был обойдён, гейт блокирует контракт с пониженным классом.

### 8. Связанные задачи и решения

ADR-023: T-080 (реализация), T-085 (auto-merge R0/R1 использует полосу классов этого ADR), T-032/T-045 (merge и release evidence), T-021 (гейт спецификации), T-016 (монотонность, Implementation Contract, точки входа эскалаций), T-081/T-082 (профили Design/Architect и роли, реализующие точки контроля).

## Реализация

Раздел задаёт конкретный implementation-дизайн для роли `develop` (T-080). Изменения — только в `src/dark_factory/**`, `migrations/**`, `deploy/runs/schema/**`, `console/src/api/types.ts`, тестах и документации; новых зависимостей нет.

### 8.1. Новый модуль `src/dark_factory/changes/risk.py`

Доменные примитивы риска, свободные от зависимостей на `quality`/`rules`/`orchestration` (направление зависимостей: `changes/` — самый нижний слой, ср. `quality/gates/specification.py:8-11`):

| Символ | Тип | Примечание |
|---|---|---|
| `RISK_ORDER: Final[dict[RiskClass, int]]` | данные | переносится из `orchestration/policy/risk.py:18` без изменения значения |
| `R2_THRESHOLD: Final = RiskClass.R2` | данные | переносится |
| `is_r2_or_higher(risk_class: RiskClass) -> bool` | функция | переносится |
| `RiskFacts` | `@dataclass(frozen=True)` | `boundaries: tuple[BoundaryArea, ...] = ()`, `decision_class: DecisionClass = DecisionClass.KNOWN_PATH`, `irreversible: bool = False`, `regulated_data: bool = False`, `documentation_only: bool = False`, `factory_self_modification: bool = False` |
| `classify_risk(facts: RiskFacts) -> RiskClass` | функция | правила §2, первый совпавший уровень; детерминирована |
| `effective_risk_class(declared: RiskClass, facts: RiskFacts, *, route_floor: RiskClass = RiskClass.R0) -> RiskClass` | функция | `max` по `RISK_ORDER` из трёх слагаемых (§2) |

### 8.2. Изменяемые модули и символы

| Файл | Изменение |
|---|---|
| `src/dark_factory/changes/enums.py` | `Route`: добавить `ARCHITECTURE = "architecture"`, `FOUNDATION = "foundation"`. Новый `ControlPoint(StrEnum)`: `PROBLEM = "problem"`, `SOLUTION = "solution"`, `UX = "ux"`, `DISCOVERY_RELEASE = "discovery_release"` |
| `src/dark_factory/orchestration/policy/risk.py` | `RISK_ORDER`/`R2_THRESHOLD`/`is_r2_or_higher` — реэкспорт из `changes.risk` (публичная поверхность пакета сохраняется). **Удалить** `RISK_ASSESSMENT_MANUAL`. `RiskClassPolicyError` и `transition_risk_class` — без изменений. Добавить `ControlPointBinding` (`@dataclass(frozen=True)`: `stage: Stage`, `gate: Gate`), `CONTROL_POINT_BINDING: Final[Mapping[ControlPoint, ControlPointBinding]]`, `required_control_points(route: Route, stage: Stage, risk_class: RiskClass) -> frozenset[ControlPoint]`, `missing_control_points(route: Route, stage: Stage, risk_class: RiskClass, decisions: Sequence[Decision], *, sha: str \| None = None) -> frozenset[ControlPoint]`. Ввод класса в пайплайне (§2/§6): `TRUSTED_CHANGE_PATHS` (пути trusted-слоя, ADR-015 п.3: `rules/**`, `orchestration/policy/**`, `changes/risk.py`, `quality/gates/**`), `risk_facts(contract: ImplementationContract) -> RiskFacts` (наблюдаемые факты: `boundaries` из `allowed_boundaries`, `factory_self_modification` из путей `scope.in_scope`) и `effective_change_risk_class(contract, route) -> RiskClass` = `max(declared, classify_risk(facts), route_floor)` |
| `src/dark_factory/rules/gates.py` | Добавить `RISK_HUMAN_GATES: Final[Mapping[RiskClass, frozenset[Gate]]]` (§3) и `required_human_gates(route: Route, stage: Stage, risk_class: RiskClass) -> frozenset[Gate]`; сюда же переносится базовый `HUMAN_GATES` (ADR-005: модуль — единственный источник гейт-политики), `flows/routes.py` его реэкспортирует. `_ROUTE_EXTRA_GATES` расширяется на `architecture` и `foundation` (`ui` на Construction — §3/§6): маршрут без UI-гейта — только `quick`. `required_gates` и `unsatisfied_gates` **не меняют сигнатуру** (см. «Альтернативы», вариант 2) |
| `src/dark_factory/flows/routes.py` | `RouteProfile`: добавить `min_risk_class: RiskClass = RiskClass.R0`, `max_risk_class: RiskClass = RiskClass.R4`; добавить `route_allows_risk(route: Route, risk_class: RiskClass) -> bool`, `ROUTE_STRICTNESS: Final[Mapping[Route, int]]`, `PROFILE_ROUTE: Final[Mapping[WorkflowProfile, Route]]` (ссылка на `context/sdd/strictness.py`, не копия), `select_route(profile: WorkflowProfile, risk_class: RiskClass) -> Route`. `ROUTE_PROFILES` — явный dict по всем четырём маршрутам (не comprehension, т.к. полосы различны); `route_profile` и `HUMAN_GATES` сохраняются, `RouteProfile.human_gates` остаётся базовым (ADR-018) |
| `src/dark_factory/orchestration/policy/escalation.py` | `risk_raise_violation` → **`risk_escalation_violation(*, risk_class: RiskClass, route: Route, stage: Stage, decisions: Sequence[Decision] = (), sha: str \| None = None) -> EscalationViolation \| None`**: правила §5, `rule=EscalationRule.RISK_RAISED_TO_R2`, `reason` перечисляет невыполненные обязательства; `manual_assessment` больше не передаётся. Импорт `RISK_ASSESSMENT_MANUAL` удалить |
| `src/dark_factory/orchestration/policy/merge.py` | В `evaluate_merge` после проверки гейтов добавить проверку точек контроля: при `missing_control_points(context.route, context.stage, context.risk_class, context.human_approvals, sha=context.expected_sha)` — `MergeDecision(kind="blocked", reason=...)` с перечнем отсутствующих точек. Проверка действует только для R2+ (`is_r2_or_higher`): ниже R2 отсутствие approval остаётся `manual_merge_required`, как раньше (§5 — «при эффективном классе ≥ R2»). `MergeRequestContext.human_approvals` и `risk_class` уже есть — сигнатуры не меняются |
| `src/dark_factory/orchestration/flow.py` | `_escalation_reason` проверяет обязательства класса через `risk_escalation_violation` (полоса маршрута + точки контроля стадии, которую run покидает) и возвращает причину с именем правила; `_risk_band_reason` проверяет полосу против **эффективного** класса (`effective_change_risk_class` — declared, факты контракта и пол маршрута) и вызывается на всех продвигающих действиях: `ExecuteStageAction`, `MergeAction`, `ReleaseAction` (§3: «на каждом продвижении стадии»). `apply_result`/`_handle_action` принимают `human_decisions: Sequence[Decision] = ()` — решения, наблюдённые вызывающим (без них обязательства R2+ не выполнены по определению, fail-closed). `_merge_policy_decision` якорит к run также `risk_class` (эффективный) — вызывающий не может занизить класс |
| `src/dark_factory/orchestration/stages/context.py` | **Отменено на ревью T-080**: добавление `StageContext.required_human_gates` и union в `pending_gate_results` оказалось мёртвым кодом (человеческий гейт всегда подмножество требуемого — §3) с тавтологичным тестом. Человеческие гейты и точки контроля считает политика там, где они потребляются (`required_control_points`) и с эффективным классом; `StageContext` остался прежним — машинный набор гейтов `required_gates(route, stage)` |
| `src/dark_factory/quality/gates/specification.py` | **Устранить дублирование порядка рисков** (строки 8-11, 63-65, 80-82): удалить `_RISK_ORDER`, `_R2_INDEX`, `_is_r2_or_higher`, импортировать `RISK_ORDER` и `is_r2_or_higher` из `dark_factory.changes.risk`. Направление зависимости корректно (`quality → changes`), циклов нет |
| `src/dark_factory/orchestration/policy/__init__.py` | Обновить `__all__`: убрать `RISK_ASSESSMENT_MANUAL` (и `risk_raise_violation`), добавить `risk_escalation_violation`, `ControlPointBinding`, `CONTROL_POINT_BINDING`, `required_control_points`, `missing_control_points`, `risk_facts`, `effective_change_risk_class`, `TRUSTED_CHANGE_PATHS`; `RISK_ORDER`/`R2_THRESHOLD`/`is_r2_or_higher` остаются |
| `src/dark_factory/changes/escalations.py` | Поле `manual_assessment` сохраняется (deprecated, всегда `False`); обновить docstring: ручных условий после T-080 нет |
| `migrations/versions/0003_route_risk_classes.py` (новый) | Расширить `ck_execution_route_allowed` до `('quick', 'standard', 'architecture', 'foundation')`; `down_revision = "0002_api_state"`. Значения — из `Route`, чтобы миграция не разошлась с enum |
| `deploy/runs/schema/stage-result.schema.json` | Перегенерировать (меняется описание `EscalationViolation` и `EscalationRule`: «manual assessment until T-080» больше не верно) |

**Организация обхода циклов.** `changes/risk.py` не импортирует ничего из `rules`, `quality`, `orchestration`, `flows` → его могут импортировать все. `rules/gates.py` импортирует только `changes/*`: базовый `HUMAN_GATES` определён здесь (ADR-005 — единственный источник гейт-политики) и реэкспортируется из `flows/routes.py`. `orchestration/policy/risk.py` импортирует `rules/gates.py`, `changes/risk.py` и `flows/routes.py` (пол маршрута) — то же направление, что уже есть у `policy/merge.py:46`. `flows/routes.py` импортирует `changes/enums.py`, `changes/risk.py`, `rules/gates.py` и `context/sdd/strictness.py` (`context/sdd/**` не импортирует `flows`/`rules`/`orchestration` — проверено). `quality/gates/specification.py` теряет локальную копию и импортирует `changes/risk.py`, не приобретая зависимости на `orchestration` — исходное обоснование дублирования («quality не должен зависеть от orchestration») сохраняется.

### 8.3. Необходимые тесты

| Файл | Что проверяется |
|---|---|
| `tests/test_changes_risk.py` (новый) | Детерминизм `classify_risk` (одинаковые факты → одинаковый класс, полный перебор уровней); приоритет правил (R4 > R3 > R2 > R0/R1); `effective_risk_class` только повышает: `declared` выше пола сохраняется, факты и `route_floor` не могут понизить; монотонность |
| `tests/test_rules_gates_risk.py` (новый) | `route_allows_risk`: `quick` запрещён для R2+, разрешён для R0/R1; `foundation` — R3–R4; `required_human_gates` по классам и стадиям; инвариант «человеческий гейт ⊆ требуемый гейт» для всех `(route, stage, risk_class)` |
| `tests/test_flows_routes.py` (расширить) | Каждый `Route` имеет профиль; полосы классов; `select_route` детерминирован и только ужесточает маршрут (R2+ с `bugfix-r0` → `standard`); существующие инварианты последовательности стадий сохранены |
| `tests/test_policy_risk.py` (обновить) | Убрать проверку `RISK_ASSESSMENT_MANUAL`; добавить `required_control_points` и `missing_control_points` (в т.ч. привязку решения к SHA: approval на старом SHA не закрывает точку) |
| `tests/test_policy_escalation.py` (обновить) | `test_risk_raise_to_r2_or_higher_escalates_manually` заменяется на машинную проверку: R2+ даёт `RISK_RAISED_TO_R2` с `manual_assessment is False`; нарушение перечисляет невыполненные обязательства; обязательства выполнены → `None` |
| `tests/test_flow_policy.py` (обновить) | `_declared_violation` строит нарушение новым вызовом; опасное изменение не продвигается на коротком маршруте (R2+ на `quick` → `StopAction(blocked)`) |
| `tests/test_policy_merge.py` (расширить) | Для R2+ merge блокируется, если обязательные точки контроля класса не имеют человеческого approval на итоговом SHA; для R0/R1 поведение не меняется |
| `tests/test_quality_specification_gate.py` | Поведение не изменилось после устранения дублирования порядка рисков (те же находки `risk_class_lowered_without_policy`, `reconciliation_required_for_high_risk`) |
| `tests/test_import_boundaries.py` | Без изменений; должна остаться зелёной (новых нарушений границ нет) |

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| **Класс расширяет машинные гейты** (`required_gates(route, stage, risk_class)`, добавка гейтов по классу) | Прямолинейно читается как «матрица класс → гейты» | Риск-класс начинает конкурировать с маршрутом за один и тот же набор гейтов; сигнатуру приходится менять во всех вызовах (`stages/context.py`, `policy/merge.py`, `flow.py`, тесты), а поведение при этом не меняется — реальные рисковые добавки лежат в человеческой плоскости и в полосе маршрута | Отклонено: расширяется **человеческий** набор гейтов и ограничивается маршрут, машинный набор остаётся функцией `(route, stage)` (ADR-005 сохраняет единственный источник гейт-политики) |
| **Четыре точки контроля как новые значения `Gate`** | Точки контроля стали бы полноценными гейтами с `GateResult` | `Gate` — сериализуемое значение контракта и CHECK-значение в БД; расширение требует синхронного изменения `stage-result.schema.json`, миграции и console-типов; при этом точка контроля — не проверка стадии, а **решение человека**, и в модели `Decision` уже есть гейт-привязка | Отклонено: `ControlPoint` вводится отдельным понятием, привязанным к существующему гейту; контракты `Gate` не трогаются |
| **Отдельный модуль `orchestration/policy/risk_matrix.py`** для матрицы | Изоляция матрицы | Разрывает гейт-политику между `rules/gates.py` и новым модулем и создаёт вторую точку правды о гейтах | Отклонено: человеческие гейты остаются в `rules/gates.py` (гейт-политика, ADR-005), полосы маршрутов — в `flows/routes.py`, доменные примитивы риска — в `changes/risk.py` |
| **Удалить поле `EscalationViolation.manual_assessment`** | Чище модель, нет «мёртвого» поля | Удаление поля — breaking change сериализуемого контракта: требуется инкремент `SchemaVersion` и синхронная правка `deploy/runs/schema/stage-result.schema.json`, `console/src/api/types.ts` и проверок версии в CI | Отклонено в T-080: поле сохраняется как deprecated и всегда `False`, уборка — в следующей ревизии схемы записи (ADR-015 п.3) |
| **Маршрут `architecture` с пропуском Construction** | Точнее отражает «impact → design → ADR → approval» для чисто проектных изменений | Меняет топологию и требует верификации таблицы переходов, `REWORK_TARGET` и требований к change request на Review | Отклонено в T-080: `RouteProfile` уже умеет пропускать стадии, но расширение топологии — отдельное решение; в T-080 маршруты различаются гейтами и полосой классов |
| **Риск-класс на `ChangeRun`** (поле рядом с `route`) | Flow читал бы класс напрямую | `ChangeRun` сериализуется в `RunRecord` и читается обратно (`RunRecordStore.read_record` → `from_json`), а обязательное новое поле ломает чтение уже опубликованных записей; опциональное поле с дефолтом даёт fail-open дефолт | Отклонено: эффективный класс берётся из `implementation_contract.risk_class` (контракт обязателен для входа в construction, T-016), а вычислимые правила остаются чистыми функциями |

## Последствия

**Позитивные**
- Маршрутизация и класс детерминированы: класс — чистая функция фактов, маршрут — чистая функция `(profile, risk_class)`, гейты — функция `(route, stage, risk_class)`. DoD T-080 становится проверяемым набором unit-тестов, а не декларацией.
- Опасные изменения не проходят по короткому пути машинно: R2+ несовместим с `quick`, violation даёт `StopAction(blocked)` с диагностикой.
- Условие «риск повышен до R2+» стало гейтом: `RISK_ASSESSMENT_MANUAL` исчезает, `manual_assessment` перестаёт быть `True` — ADR-018 п.5 закрыт.
- Четыре точки контроля выводятся из одного правила (обязательность = «гейт требуется и является человеческим»), а не поддерживаются отдельной таблицей — расхождение таблиц и кода структурно невозможно.
- Дублирование порядка рисков устранено в правильном направлении зависимостей (`changes/risk.py`), исходный запрет `quality → orchestration` сохранён.
- Границы доверия ADR-015 усилены: изменение правил классификации принудительно R4/foundation, понижение класса агентом невозможно ни одним путём.

**Негативные / риски**
- Матрица добавляет политику, которую нужно поддерживать при появлении новых `BoundaryArea`, гейтов и профилей строгости: `RISK_HUMAN_GATES`, `CONTROL_POINT_BINDING` и `PROFILE_ROUTE` обязаны покрывать все значения enum — это закрепляется тестами полноты (по образцу `test_policy_participation.py`).
- Классификация зависит от корректности входных фактов (`irreversible`, `regulated_data`, `factory_self_modification`). Ошибка *занижения* факта не компенсируется матрицей; компенсируется частично: агент может повысить класс, гейт спецификации блокирует понижение, а `factory_self_modification` выводится из путей, а не из самоотчёта.
- Расширение `Route` требует миграции CHECK-констрейнта; до применения миграции запись execution с новым маршрутом в PostgreSQL невозможна (fail-closed, но требует порядка деплоя: миграция → код).
- Точки контроля для R2+ делают обязательным человеческое решение на `planning`; это добавляет точку ожидания в цикл и требует SLA реакции человека (риск уже отмечен в ADR-018).
- `manual_assessment` остаётся в контракте как deprecated-поле: временное несоответствие модели и схемы.

**Нейтральные**
- `RouteProfile.human_gates` и `HUMAN_GATES` остаются базовыми (ADR-018); риск-зависимый вид даёт `required_human_gates`. `console/tools/export_meta.py` и снимок `console/src/generated/meta.json` не меняются.
- `Route` и `ControlPoint` не входят ни в одну JSON-схему контрактов; `EscalationRule` сохраняет значение `risk_raised_to_r2`.

**Дальше**
- T-085: полоса классов R0/R1 из этого ADR — вход политики auto-merge (`MergePolicy.auto_merge_risk_classes`).
- T-081/T-082: профили Design/Architect и роли, реализующие точки `ux`/`solution`; роли Infrastructure/Security/CI-CD/Operation — исполнители R3/R4-обязательств.
- Follow-up (вне T-080): «release-гейт с вердиктом operation» для R2+ (`crm-end-to-end-flow.md` §5.4) — требование к содержанию гейта `release`, делегируется T-045/T-032 и здесь не моделируется новым гейтом; определение вердикта operation требует отдельного решения.
- Follow-up: ревизия схемы записи (`SchemaVersion`) с удалением `manual_assessment` и, при необходимости, с добавлением run-level риск-класса как опционального поля.
- Follow-up: раннер стадий (ADR-006) ещё не ведёт run через `apply_result`, поэтому решения человека для точек контроля на стадийной границе должен подавать вызывающий (`human_decisions`); до этого R2+ стадии останавливаются fail-closed. Ограничение — TD-014.

## Открытые вопросы

- **[assumption]** Четвёртая точка контроля названа `discovery_release` (значение `discovery_release`) и трактуется как «закрытие discovery + авторизация выпуска» по `crm-end-to-end-flow.md` §1.2 («Problem, Solution, UX, Discovery Gate») и §1.2 строке 7 (`review` — гейт авторизации merge). Если product/design закрепят иное чтение (например, отдельная точка `release` с явным вердиктом operation), меняется только `CONTROL_POINT_BINDING` и значение enum — модель §4 не затрагивается.
- **[assumption]** Правило `R1 → human ui` (точка `ux` обязательна для R1 на `standard`) — вывод из §5.4 («R1 — автономно до состояния готово к merge») плюс ADR-018 п.1 (фаза UX/UI — human-in-the-loop). Если UX-решение для R1 должно оставаться on-the-loop, изменится одна строка `RISK_HUMAN_GATES`.
- Полоса `foundation` (R3–R4) назначена политикой из «repository rebuild / platform change — высокая цена ошибки»; отдельного наблюдаемого факта для неё не вводится, пол берётся из маршрута. Требует подтверждения product/architect при первом реальном `repository-rebuild`-изменении.
- **[assumption]** Наблюдаемые факты берутся только там, где они есть в пайплайне: `boundaries` — из `ImplementationContract.allowed_boundaries`, `factory_self_modification` — из путей `scope.in_scope` в trusted-слое (`rules/**`, `orchestration/policy/**`, `changes/risk.py`, `quality/gates/**`; ADR-015 п.3). Факты `irreversible`, `regulated_data`, `documentation_only` и `decision_class` **сегодня наблюдать нечем** — контракт их не несёт, поэтому они остаются в консервативном значении по умолчанию (не повышают класс), а не подставляются как «измеренные»: это осознанное ограничение, TD-013. Следствие: уверенно выводимы только повышения до R2/R4 из границ и trusted-путей; занижение через невыводимые факты компенсируется объявленным классом, гейтом спецификации (`risk_class_lowered_without_policy`) и повышением класса агентом, но не детектируется.
- Значения `regulated_data` и `destructive`-характер дельты (`DeltaOperationKind.RETIRE`/`SUPERSEDE`) сведены к `regulated_data` и `BoundaryArea.DATA_SCHEMA` соответственно. Если понадобится отличать «удаление данных» от «изменения схемы», добавляется отдельный факт `destructive_data` без изменения матрицы.
