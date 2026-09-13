<!--
Источник: Notion — Техстек Dark Factory
URL: https://app.notion.com/p/3d9db33037c880679deec7ee17ef024e
Выгружено: 2026-09-12
-->

# Техстек Dark Factory

## Архитектурная позиция

Для Dark Factory предлагается **Python-платформа с собственным типизированным workflow-ядром на Pydantic Graph, сменными agent harness через порты, API на FastAPI, состоянием в PostgreSQL, консолью на React и исполнением инженерных операций через GitLab Runner**.

Система строится как **модульный монолит с отдельными worker-процессами и изолированными средами исполнения**. Это позволяет быстро собрать MVP без преждевременного перехода к микросервисам, Kafka и Temporal, но сохраняет границы, по которым компоненты можно будет вынести позднее.

Первый целевой сквозной процесс:

> Идея или задача → анализ → OpenSpec change → дизайн → разработка → проверки → Merge Request → review → ограниченный rework → human gate → merge → deploy → наблюдение и learning loop.

## Базовый технологический стек

<table header-row="true">
<tr>
<td>Слой</td>
<td>Выбор</td>
<td>Назначение</td>
</tr>
<tr>
<td>Язык платформы</td>
<td>Python, `uv`, Pydantic, строгая типизация</td>
<td>Workflow-ядро, агенты, интеграции, политики и CLI</td>
</tr>
<tr>
<td>API / Control Plane</td>
<td>FastAPI</td>
<td>Управление задачами, запусками, approvals, событиями и конфигурацией</td>
</tr>
<tr>
<td>Workflow Engine</td>
<td>Pydantic Graph за собственным `WorkflowEnginePort`</td>
<td>Типизированные узлы, переходы, ветвление, циклы, параллельные ветви и визуализация графа</td>
</tr>
<tr>
<td>Первый agent runtime</td>
<td>PydanticAI за `HarnessPort`</td>
<td>Модельный цикл, tools, MCP, structured output, limits и agent execution</td>
</tr>
<tr>
<td>Следующие harness</td>
<td>Pi, DeepSeek Harness и другие через адаптеры</td>
<td>Смена среды исполнения без изменения workflow и ролей</td>
</tr>
<tr>
<td>Worker</td>
<td>Отдельный Python-процесс из той же кодовой базы</td>
<td>Исполнение workflow-узлов, agent jobs и ожиданий</td>
</tr>
<tr>
<td>Персистентное состояние</td>
<td>PostgreSQL, SQLAlchemy 2, Alembic</td>
<td>Runs, steps, attempts, approvals, events, locks, budgets и provenance</td>
</tr>
<tr>
<td>Очередь MVP</td>
<td>PostgreSQL job table + `FOR UPDATE SKIP LOCKED`</td>
<td>Надёжная выдача заданий без отдельного брокера</td>
</tr>
<tr>
<td>Доступ к моделям</td>
<td>Существующий LiteLLM</td>
<td>Единый model gateway, маршрутизация, лимиты и учёт использования</td>
</tr>
<tr>
<td>CLI</td>
<td>Typer</td>
<td>Локальные команды, диагностика и вызов тех же use cases из CI</td>
</tr>
<tr>
<td>Консоль</td>
<td>React + TypeScript + Vite</td>
<td>Задачи, спецификации, графы, запуски, approvals и review</td>
</tr>
<tr>
<td>UI</td>
<td>Radix + shadcn/ui + Tailwind как основа Small UIKit</td>
<td>Доступные примитивы и управляемый исходный код компонентов</td>
</tr>
<tr>
<td>UI data/forms</td>
<td>TanStack Query, React Hook Form, Zod</td>
<td>API-состояние, формы и клиентская валидация</td>
</tr>
<tr>
<td>События в UI</td>
<td>SSE</td>
<td>Прогресс run, логи, запросы согласования и результаты проверок</td>
</tr>
<tr>
<td>Инженерное исполнение</td>
<td>GitLab, GitLab Runner, MR pipelines</td>
<td>Репозитории, ветки, CI, проверки, MR, merge и deployment jobs</td>
</tr>
<tr>
<td>Изоляция</td>
<td>Отдельный git worktree + rootless Podman/Docker container на attempt</td>
<td>Разделение файлов и процессов; worktree сам по себе не является security boundary</td>
</tr>
<tr>
<td>Наблюдаемость</td>
<td>OpenTelemetry → Prometheus/Grafana и Jaeger/Tempo</td>
<td>Трассировка run → step → agent call → tool call → CI job</td>
</tr>
<tr>
<td>IAM и secrets</td>
<td>Keycloak OIDC, Vault, защищённые CI variables</td>
<td>Аутентификация, роли, сервисные учётные записи и секреты</td>
</tr>
<tr>
<td>Крупные артефакты</td>
<td>MinIO/S3</td>
<td>Логи, отчёты, screenshots, bundles и иные бинарные результаты</td>
</tr>
</table>

## Три разных графа

В архитектуре нельзя смешивать три независимые модели графа.

<table header-row="true">
<tr>
<td>Граф</td>
<td>Что описывает</td>
<td>Где хранится и исполняется</td>
</tr>
<tr>
<td>**Workflow graph**</td>
<td>Что фабрика должна выполнить: фазы, узлы, переходы, gates, retry и rework</td>
<td>Декларация процесса в Git; компиляция и исполнение через Pydantic Graph; runtime state в PostgreSQL</td>
</tr>
<tr>
<td>**Specification graph**</td>
<td>Почему и что меняется: proposal, requirements, design, tasks, tests и связи трассировки</td>
<td>OpenSpec и расширения Dark Factory в репозитории продукта</td>
</tr>
<tr>
<td>**Knowledge graph**</td>
<td>В каком контексте принимается решение: системы, capabilities, стандарты, ADR, owners и зависимости</td>
<td>Отдельный OKF-репозиторий; индекс/проекция для поиска и обхода связей</td>
</tr>
</table>

Pydantic Graph применяется только как библиотека исполняемого workflow/state machine. Согласно документации, `pydantic-graph` отделён от `pydantic-ai`, а граф строится из типизированных nodes, edges, decisions, joins и состояния. Это делает его подходящей первой реализацией `WorkflowEnginePort`, но доменные контракты Dark Factory не должны наследоваться от его внутренних классов. [Pydantic Graph](https://pydantic.dev/docs/ai/graph/graph/)

## Разделение workflow engine и harness

**Workflow engine** отвечает за детерминированную часть процесса:
- выбирает следующий узел и разрешённый переход;
- хранит состояние run и step;
- управляет ветвлением, join, retry, timeout и rework;
- останавливает процесс на approval gate;
- применяет лимиты попыток, времени и стоимости;
- восстанавливает выполнение после сбоя;
- фиксирует полную историю решений.

**Harness** отвечает за одну агентную попытку:
- запускает модельный цикл;
- собирает сообщения и контекст в пределах выделенного задания;
- предоставляет tools, MCP и среду исполнения;
- обрабатывает structured output;
- возвращает результат, usage, tool trace и evidence;
- не решает, какая фаза SDLC должна выполняться следующей.

Таким образом, PydanticAI является первым harness, а Pydantic Graph — первой реализацией workflow engine. Их необходимо подключать через разные порты. Это позволит позднее заменить любой из них независимо.

## Использование AI-DLC

Актуальный `awslabs/aidlc-workflows` — уже не только коллекция Markdown-инструкций: проект включает harness-neutral core, детерминированный engine, 5 фаз, 33 стадии, профили workflow, approval gates, audit trail и несколько harness. [AI-DLC repository](https://github.com/awslabs/aidlc-workflows)

Для Dark Factory предлагается **не форкать весь runtime AI-DLC как основу продукта и не создавать второй параллельный engine**. Переиспользовать следует:
- модель жизненного цикла и семантику фаз;
- отобранные роли агентов;
- stage definitions, вопросы и quality gates;
- шаблоны артефактов, чек-листы и review criteria;
- workflow profiles как исходный материал для собственных профилей;
- audit events и learned rules как референс контракта.

После адаптации эти элементы становятся версионированными пакетами Dark Factory. Исходное происхождение, версия upstream и локальные изменения фиксируются в manifest. Исполнение остаётся за собственным workflow-ядром, потому что фабрике нужны нативные интеграции с GitLab, Plane/Linear, Notion, OpenSpec, OKF, Keycloak и собственная модель автономности.

## Набор агентов MVP

Рекомендуемый стартовый набор основан на ролях AI-DLC, но роли описываются декларативно и не являются отдельными постоянно работающими сервисами.

<table header-row="true">
<tr>
<td>Агент</td>
<td>Имя</td>
<td>Основная ответственность</td>
<td>Ключевые результаты</td>
</tr>
<tr>
<td>Product</td>
<td>Kevin</td>
<td>Анализ проблемы, scope, сценарии и acceptance criteria</td>
<td>Proposal, requirements, backlog decomposition</td>
</tr>
<tr>
<td>Design</td>
<td>Bob</td>
<td>UX-потоки и UI-контракты</td>
<td>User flows, screen specs, accessibility criteria</td>
</tr>
<tr>
<td>Architect</td>
<td>Stuart</td>
<td>Архитектура решения и технические решения</td>
<td>Design, ADR, contracts, NFR</td>
</tr>
<tr>
<td>Infrastructure</td>
<td>Dave</td>
<td>Среда исполнения и deployment design</td>
<td>IaC/Helm changes, environment plan</td>
</tr>
<tr>
<td>Security</td>
<td>Mel</td>
<td>Threat modeling и security requirements/review</td>
<td>Threat model, controls, findings</td>
</tr>
<tr>
<td>Develop</td>
<td>Carl</td>
<td>Код, миграции и developer tests</td>
<td>Commits и evidence реализации</td>
</tr>
<tr>
<td>Quality</td>
<td>Phil</td>
<td>Стратегия тестирования и независимая проверка</td>
<td>Test plan, verification report, defects</td>
</tr>
<tr>
<td>CI/CD</td>
<td>Tim</td>
<td>Pipeline, packaging и deployment automation</td>
<td>CI/CD changes и release evidence</td>
</tr>
<tr>
<td>Operation</td>
<td>Jerry</td>
<td>Observability, runbooks и operational readiness</td>
<td>SLO, alerts, dashboards, runbook</td>
</tr>
</table>

На старте один и тот же harness может исполнять все роли. Роль определяет не модель, а **AgentSpec**: цель, входной контракт, доступные skills/tools, knowledge policy, ограничения, output schema и критерии завершения.

## Плагинная модель

Расширения разделяются по природе и жизненному циклу.

<table header-row="true">
<tr>
<td>Тип расширения</td>
<td>Реализация</td>
<td>Контракт</td>
</tr>
<tr>
<td>Adapter</td>
<td>Python package + `Protocol`</td>
<td>GitLab, Plane/Linear, Notion, OpenSpec, OKF, artifact stores</td>
</tr>
<tr>
<td>Harness</td>
<td>In-process adapter либо sidecar/container</td>
<td>Выполнение `AgentTask` → `AgentResult`</td>
</tr>
<tr>
<td>Workflow engine</td>
<td>Адаптер к Pydantic Graph; позднее другая реализация</td>
<td>Compile, start, resume, signal, inspect</td>
</tr>
<tr>
<td>Workflow profile</td>
<td>YAML + JSON Schema</td>
<td>Набор фаз, узлов, policies и gates для типа изменения</td>
</tr>
<tr>
<td>Agent</td>
<td>YAML + Markdown + output schema</td>
<td>Роль, разрешения, skills, context policy и результат</td>
</tr>
<tr>
<td>Skill</td>
<td>Markdown + metadata; при необходимости scripts</td>
<td>Повторяемая инженерная процедура</td>
</tr>
<tr>
<td>Rule/Policy</td>
<td>Policy-as-code + schema; Markdown только для guidance</td>
<td>Машинно проверяемое ограничение или рекомендация</td>
</tr>
<tr>
<td>Artifact template</td>
<td>Версионированные файлы + manifest</td>
<td>Формат создаваемого документа или evidence</td>
</tr>
<tr>
<td>Knowledge provider</td>
<td>Реализация `KnowledgePort`</td>
<td>Search, get, traverse, provenance и version</td>
</tr>
</table>

Python code plugins обнаруживаются через entry points и устанавливаются при сборке образа. Динамическая загрузка непроверенного кода в работающий Control Plane не допускается. Сторонние harness и инструменты с широкими правами выполняются в отдельном процессе или контейнере.

Каждый пакет объявляет `id`, semantic version, supported contract versions, capabilities, dependencies, permissions, source и integrity digest.

## OpenSpec, OKF и трассировка

**OpenSpec** хранит change-oriented спецификации продукта рядом с кодом. Для Dark Factory создаётся custom schema с зависимостями между proposal, requirements/specs, design, test plan, tasks, implementation evidence и verification. OpenSpec поддерживает собственные schemas, шаблоны и зависимости артефактов, но сам факт существования review-файла ещё не равен пройденному gate — решение и evidence проверяет workflow engine/CI. [OpenSpec customization](https://github.com/Fission-AI/OpenSpec/blob/main/docs/customization.md)

**OKF** хранит долговечный архитектурный и организационный контекст: systems, components, capabilities, standards, ADR, APIs, data entities, owners и связи между ними. Он не должен дублировать change history OpenSpec.

**PostgreSQL** хранит только операционное состояние конкретных запусков: какая версия входов использовалась, что было исполнено, кем принято решение и где лежит результат.

Каждый run фиксирует immutable snapshot ссылок:
- commit SHA продукта и OpenSpec change;
- commit SHA OKF;
- версии workflow profile, AgentSpec, skills, rules и templates;
- harness, model и параметры;
- версии интеграционных адаптеров;
- digest execution image;
- входные/выходные артефакты и evidence.

Specification graph строится на стабильных ID и типизированных связях, например `requirement -> realized_by -> component`, `requirement -> verified_by -> test`, `decision -> constrains -> design`, `task -> implements -> requirement`. На старте достаточно валидатора ссылочной целостности и перестраиваемой проекции nodes/edges в PostgreSQL; отдельная graph database не нужна.

## Автономный контур task → MR → merge

Из `dmtools-agents` целесообразно переиспользовать не код целиком, а проверенные паттерны автономного управления issue/MR:
1. Получить задачу из Plane/Linear и нормализовать её во внутренний `WorkItem`.
2. Проверить readiness и при необходимости запросить недостающие решения.
3. Создать run, branch/worktree и изолированную execution environment.
4. Сформировать или обновить OpenSpec change.
5. Выполнить планирование и разработку с checkpoint после каждого узла.
6. Запустить локальные проверки, затем создать/обновить GitLab MR.
7. Запустить MR pipeline и собрать structured evidence.
8. Выполнить независимый Quality/Security/Architecture review в read-only контексте.
9. Преобразовать замечания в типизированные findings и выполнить ограниченный rework.
10. Повторить проверки; остановиться при превышении budget/retry policy или нерешённом blocker.
11. Запросить human approval для рискованных изменений и merge.
12. После merge выполнить разрешённый deployment и operational verification.
13. Синхронизировать статус задачи и записать metrics/feedback в learning loop.

GitLab остаётся системой исполнения кода и управления изменением, но не второй копией workflow engine. MR pipelines запускаются при создании MR и последующих push в source branch; для проверки результата слияния при доступности нужной редакции GitLab используются merged-results pipelines. [GitLab MR pipelines](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/)

## Надёжность и безопасность исполнения

Для MVP обязательны:
- идемпотентный `command_id` для внешних действий;
- inbox/outbox для webhook и исходящих событий;
- дедупликация GitLab и tracker events;
- lease/heartbeat для worker jobs;
- optimistic locking состояния run;
- checkpoint после каждого workflow node;
- retry policy по типу ошибки, а не общий бесконечный retry;
- лимиты tokens, стоимости, времени, tool calls и rework cycles;
- allowlist инструментов и минимальные credentials на роль;
- запрет доступа к production secrets для кода из MR;
- human gates для merge, production deploy, destructive migrations и high-risk security changes;
- неизменяемый audit trail решений, prompts/config references, tool calls и evidence.

Worktree разделяет рабочие копии, но не защищает host. Код и tools выполняются в rootless container с ограничениями CPU/RAM/time, read-only base image, отдельной сетью и краткоживущими credentials.

## Качество и learning loop

<table header-row="true">
<tr>
<td>Область</td>
<td>Инструменты и подход</td>
</tr>
<tr>
<td>Python</td>
<td>Ruff, mypy/pyright, pytest, integration tests, Testcontainers</td>
</tr>
<tr>
<td>Frontend</td>
<td>TypeScript strict, ESLint, Vitest, Testing Library</td>
</tr>
<tr>
<td>UI/E2E</td>
<td>Playwright, axe-core, визуальные проверки ключевых экранов</td>
</tr>
<tr>
<td>API/contracts</td>
<td>OpenAPI validation, schema compatibility, consumer/provider tests</td>
</tr>
<tr>
<td>Security</td>
<td>SAST, dependency/container/IaC scanning, secret detection, threat-model gates</td>
</tr>
<tr>
<td>Agents</td>
<td>Версионированные eval datasets, deterministic checks, LLM-as-judge только как дополнительный сигнал</td>
</tr>
<tr>
<td>Platform</td>
<td>Contract tests для ports/adapters и replay тестовых event traces</td>
</tr>
</table>

Learning loop сначала реализуется как управляемое изменение через MR, а не как автоматическое самоизменение production-системы. Фабрика собирает метрики и предлагает изменение skill/rule/prompt/workflow; изменение проходит evals, review и approval, после чего публикуется новая версия пакета.

Основные метрики:
- доля задач, дошедших до принятого MR;
- lead time и active agent time;
- число rework cycles и причины возврата;
- доля замечаний, найденных до/после merge;
- стоимость принятого изменения;
- pass rate по acceptance criteria и evals;
- rollback/incident rate;
- доля ручных вмешательств и типы gates.

## Граница ответственности с GitLab CI

<table header-row="true">
<tr>
<td>Dark Factory workflow engine</td>
<td>GitLab / Runner</td>
</tr>
<tr>
<td>Выбирает workflow profile, фазу и агента</td>
<td>Создаёт branch/MR и исполняет CI jobs по API-команде</td>
</tr>
<tr>
<td>Собирает контекст и фиксирует версии</td>
<td>Предоставляет репозиторий и execution environment</td>
</tr>
<tr>
<td>Оценивает structured result и findings</td>
<td>Выполняет build, test, scan, package и deploy jobs</td>
</tr>
<tr>
<td>Решает, нужен ли rework или approval</td>
<td>Возвращает job status, logs и artifacts</td>
</tr>
<tr>
<td>Контролирует budgets, retries и policy gates</td>
<td>Применяет protected branches/environments и merge controls</td>
</tr>
<tr>
<td>Хранит бизнес-состояние run</td>
<td>Хранит историю кода, MR и pipeline</td>
</tr>
</table>

CI YAML не должен кодировать весь AI-DLC-процесс. Он содержит переиспользуемые технические jobs и принимает типизированные inputs от Dark Factory.

## Развёртывание по этапам

### Этап 1 — локальный vertical slice
- один репозиторий модульного монолита;
- FastAPI, worker, PostgreSQL и CLI;
- Pydantic Graph + PydanticAI;
- LiteLLM;
- GitLab adapter;
- один `express-feature` workflow;
- Product, Architect, Develop и Quality как минимально активные роли;
- OpenSpec change → code → tests → MR → review → rework;
- простая React-консоль для run timeline и approval.

### Этап 2 — командный MVP
- Plane или Linear adapter;
- Notion knowledge adapter;
- OKF provider и specification graph projection;
- Security, Infrastructure, CI/CD и Operation agents;
- несколько workflow profiles: feature, bugfix, infra, security;
- Keycloak, Vault, OpenTelemetry;
- контейнерная изоляция и централизованные evals;
- deployment на VM через Compose либо в RKE2 через Helm/Argo CD.

### Этап 3 — масштабирование
- горизонтальные workers и приоритетные очереди;
- remote execution pools;
- дополнительные harness;
- полноценный policy engine при росте числа правил;
- Kafka только при реальной необходимости event fan-out и независимых consumers;
- Temporal/Restate/другой durable engine только если ожидания, signals, компенсации и распределённое восстановление делают собственную реализацию слишком сложной.

## Что не включать в MVP

- микросервисную декомпозицию Control Plane;
- Kafka без нескольких независимых consumers;
- отдельную graph database;
- одновременную поддержку нескольких harness;
- marketplace с runtime-установкой непроверенных plugins;
- полностью автоматический production merge/deploy;
- автоматическое самоизменение prompts, skills или workflow без MR и eval gates;
- перенос всего AI-DLC runtime или полный fork upstream.

## Итоговая рекомендация

Главная архитектурная инвестиция Dark Factory — не конкретная модель и не количество агентов. Это:
1. **типизированный, восстанавливаемый workflow graph**;
2. **строгие порты между workflow, harness и внешними системами**;
3. **воспроизводимость каждого run**;
4. **машинно проверяемая трассировка OpenSpec → код → тест → MR evidence**;
5. **безопасный ограниченный цикл review/rework**;
6. **eval-driven learning loop с изменениями через MR**.
