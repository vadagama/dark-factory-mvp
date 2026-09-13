<!--
Источник: Notion — Инструкции для AI-agent в dm
URL: https://app.notion.com/p/3d6db33037c88051b0c4c0c4d4d0478f
Выгружено: 2026-09-12
-->

# Инструкции для AI-agent в dm

## Итог

Промпты DMTools хорошо подходят как источник процессных паттернов, но не как готовая библиотека для копирования:
- применимость идей и последовательностей — примерно **8/10**;
- применимость текстов без переработки — около **3/10**;
- оптимальный результат для Dark Factory — **14 вызываемых task-prompts**, набор переиспользуемых policy packs и строгие Pydantic-контракты.

Промпты должны находиться рядом с определениями PydanticAI-агентов в репозитории оркестратора. Temporal выбирает нужный агент и версию промпта, но не содержит промпты и не передаёт LLM управление SDLC.

Анализ выполнен по `main`, включая [каталог из 27 prompt-файлов](https://github.com/IstiN/dmtools-agents/tree/main/prompts) и текущие конфигурации агентов.

## Что фактически устроено в DMTools

Репозиторий уже различает:
- `prompts/` — входные задания для CLI-агентов;
- `instructions/` — переиспользуемые правила;
- root `.json` — конфигурацию агента, порядок сборки инструкций и схемы результата;
- `js/` — оркестрацию и side effects.

Это явно описано в [AGENTS.md](https://github.com/IstiN/dmtools-agents/blob/main/AGENTS.md).

При этом каталог `prompts` находится в переходном состоянии:
- файлов — 27;
- прямо подключено текущими базовыми JSON-конфигурациями — 14;
- не подключено — 13: их функциональность либо перенесена в `instructions/`, либо это legacy-варианты.

Например, актуальные [intake.json](https://github.com/IstiN/dmtools-agents/blob/main/intake.json), [story_development.json](https://github.com/IstiN/dmtools-agents/blob/main/story_development.json) и [pr_review.json](https://github.com/IstiN/dmtools-agents/blob/main/pr_review.json) уже собирают поведение из небольших instruction packs, а не используют старые монолитные `intake_prompt.md`, `story_development_prompt.md` и `pr_review_prompt.md`.

## Разбор всех групп промптов

<table header-row="true">
<tr>
<td>DMTools-файлы</td>
<td>Полезная идея</td>
<td>Решение для Dark Factory</td>
</tr>
<tr>
<td>`intake_prompt.md`</td>
<td>Декомпозиция инициативы, проверка дублей, тестируемость Stories, анализ вложений</td>
<td>Сохранить. Перевести на Plane, OpenSpec и OKF. Ограничения и дедупликацию вынести из текста в код</td>
</tr>
<tr>
<td>`questions_prompt.md`</td>
<td>Поиск пробелов и создание структурированных вопросов</td>
<td>Сохранить как `ClarificationAgent`</td>
</tr>
<tr>
<td>`po_refinement_prompt.md`</td>
<td>AI сам отвечает на созданные вопросы</td>
<td>Не переносить как основной поток. Ответ должен дать человек в Plane либо авторитетный источник с доказательством</td>
</tr>
<tr>
<td>`story_description_prompt.md`</td>
<td>Обогащение Story с учётом решений и существующего кода</td>
<td>Объединить с формированием OpenSpec</td>
</tr>
<tr>
<td>`acceptance_criteria_prompt.md`, `acceptance_criterias_prompt.md`</td>
<td>AC, business rules, out of scope, current/new behaviour</td>
<td>Объединить в один `spec_author`. Второй файл удалить как дубликат</td>
</tr>
<tr>
<td>`story_solution_prompt.md`</td>
<td>Архитектурное решение, affected repositories, диаграмма, покрытие AC</td>
<td>Сохранить. Добавить impact analysis через OKF и ADR proposals</td>
</tr>
<tr>
<td>`story_development_prompt.md`</td>
<td>Реализация, unit-тесты, проверка сборки</td>
<td>Сохранить как процесс, но заменить branch flow на короткоживущий worktree + GitLab MR</td>
</tr>
<tr>
<td>`bug_development_prompt.md`</td>
<td>Reproduce → RCA → failing test → fix → verify</td>
<td>Один из сильнейших процессов. Разделить на RCA, план и исполнение</td>
</tr>
<tr>
<td>`bug_rca_prompt.md`</td>
<td>Самостоятельный глубокий RCA</td>
<td>Сохранить для повторных, критичных и неоднозначных дефектов</td>
</tr>
<tr>
<td>`pr_review_minimal.md`, `pr_review_prompt.md`</td>
<td>Независимый review, diff comments, AC coverage, security</td>
<td>Сохранить один компактный entry prompt плюс policy packs</td>
</tr>
<tr>
<td>`pr_rework_minimal.md`, `pr_rework_prompt.md`</td>
<td>Исправление всех замечаний и ответы по discussion threads</td>
<td>Сохранить один prompt; число циклов ограничивает Temporal</td>
</tr>
<tr>
<td>`test_case_automation_prompt.md`; все `story_*test_automation*`, `bug_*test_automation*`, `pr_test_automation_*`</td>
<td>Реализация, review и rework автотестов</td>
<td>Объединить. Различие Story/Bug передавать параметром, а не поддерживать девять похожих файлов</td>
</tr>
<tr>
<td>`bug_creation_prompt.md`, `bulk_bugs_creation_prompt.md`</td>
<td>Дедупликация дефектов и решение create/link/skip</td>
<td>Объединить в `FailureTriageAgent`; batch — это fan-out Temporal, а не отдельный prompt</td>
</tr>
<tr>
<td>`bash_tools.md`, `codegraph_tools.md`</td>
<td>Правила работы с shell и навигации по коду</td>
<td>Это не task-prompts. Перенести в capability/tool policy конкретной execution environment</td>
</tr>
</table>

## Что в исходных промптах требует исправления

Основные проблемы нельзя переносить в Dark Factory:
1. **Workflow-логика находится внутри текста.**

    Последовательность шагов, retry, блокировки, статусы и переходы должны определяться Temporal.
2. **Сильная привязка к Jira, GitHub и Figma.**

    Для вашего решения нужны Plane, GitLab, OpenSpec/OKF и Small UIKit с HTML/CSS/JS-прототипами.
3. **Формат результата описывается текстом.**

    Требования вроде «обязательно запиши `outputs/pr_review.json`» должны стать `output_type=MRReview` и валидироваться Pydantic.
4. **Есть противоречия.**

    Например, старый `pr_review_prompt.md` одновременно допускает approval до повторного automation run и позже требует блокировать при любых неперепроверенных platform failures. Его output contract также расходится с текущей схемой в `pr_review.json`.
5. **Конфликтуют tool instructions.**

    [story_solution_prompt.md](https://github.com/IstiN/dmtools-agents/blob/main/prompts/story_solution_prompt.md) требует финальную команду с `&&`, а [bash_tools.md](https://github.com/IstiN/dmtools-agents/blob/main/prompts/bash_tools.md) запрещает command chaining.
6. **Слишком много специализаций через копирование.**

    Story/Bug test automation отличаются в основном контекстом. Это должен быть один агент с `source_type`, общим контрактом и подключаемым profile.
7. **Сомнительные эвристики находятся в промпте.**

    Например, сравнение первых 60 символов или «70% совпадения шагов» для поиска duplicate bug лучше реализовать как отдельный сервис: structured fingerprint + semantic search + configurable threshold.

## Место рядом с PydanticAI

Распределение ответственности должно быть таким:

<table header-row="true">
<tr>
<td>Компонент</td>
<td>Ответственность</td>
</tr>
<tr>
<td>Temporal</td>
<td>Child workflows, состояния, retry/timeout, лимит review/rework, human gates, idempotency</td>
</tr>
<tr>
<td>PydanticAI</td>
<td>Запуск конкретного агента, instructions, tools/toolsets, dependencies, typed output, validators</td>
</tr>
<tr>
<td>Prompt package</td>
<td>Версионированные роли, цели, reasoning policy и ограничения конкретной задачи</td>
</tr>
<tr>
<td>Plane</td>
<td>Инициатива, статус, прозрачность процесса, вопросы и решения человека</td>
</tr>
<tr>
<td>OpenSpec</td>
<td>Требования, сценарии, AC, change delta, implementation tasks</td>
</tr>
<tr>
<td>OKF</td>
<td>Контекст ландшафта, зависимости, invariants, affected components</td>
</tr>
<tr>
<td>GitLab/worktrees</td>
<td>Код, MR, diff, CI и быстрое изолированное исполнение</td>
</tr>
<tr>
<td>Langfuse</td>
<td>Trace, prompt/model version, tool calls, стоимость, latency, оценки</td>
</tr>
<tr>
<td>Pydantic Evals</td>
<td>Regression-наборы для проверки каждого промпта до публикации</td>
</tr>
</table>

Для stage-specific поведения нужно использовать преимущественно `Agent(instructions=...)`, динамические части — через `@agent.instructions`, а результат — через `output_type`. Именно такой подход рекомендует текущая документация [PydanticAI Agents](https://pydantic.dev/docs/ai/core-concepts/agent/).

PydanticAI уже имеет прямую интеграцию с Temporal: внутри workflow модельные вызовы, tools и MCP-взаимодействия могут исполняться как durable activities. Однако side effects вроде изменения статуса Plane, merge и production deployment всё равно следует оставлять отдельным контролируемым activities. [PydanticAI Temporal integration](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/), [Temporal Durable AI](https://docs.temporal.io/ai).

## Конечный набор task-prompts

Рекомендую следующие **14 вызываемых промптов**.

<table header-row="true">
<tr>
<td>ID и файл</td>
<td>Workflow</td>
<td>Назначение и Pydantic output</td>
</tr>
<tr>
<td>P01 `change/intake_decompose.md`</td>
<td>`ChangeWorkflow`</td>
<td>Классификация и декомпозиция инициативы → `ChangeDecomposition`</td>
</tr>
<tr>
<td>P02 `refinement/clarification_questions.md`</td>
<td>`RefinementWorkflow`</td>
<td>Только действительно неразрешимые вопросы → `ClarificationSet`</td>
</tr>
<tr>
<td>P03 `refinement/spec_author.md`</td>
<td>`RefinementWorkflow`</td>
<td>Business context, scope, requirements, scenarios, AC, out of scope → `OpenSpecDraft`</td>
</tr>
<tr>
<td>P04 `refinement/spec_review.md`</td>
<td>`RefinementWorkflow`</td>
<td>Независимая проверка полноты, противоречий и testability → `SpecReview`</td>
</tr>
<tr>
<td>P05 `refinement/solution_design.md`</td>
<td>`RefinementWorkflow`</td>
<td>Компоненты, API/events, данные, NFR, affected repos, ADR и Mermaid → `SolutionDesign`</td>
</tr>
<tr>
<td>P06 `implementation/plan.md`</td>
<td>`ImplementationWorkflow`</td>
<td>Изменяемые файлы, порядок действий, тесты и риски → `ImplementationPlan`</td>
</tr>
<tr>
<td>P07 `implementation/execute.md`</td>
<td>`ImplementationWorkflow`</td>
<td>Реализация в worktree, unit-тесты и MR description → `ImplementationResult`</td>
</tr>
<tr>
<td>P08 `review/mr_review.md`</td>
<td>`ReviewReworkWorkflow`</td>
<td>AC/spec coverage, correctness, security, testing, inline comments → `MRReview`</td>
</tr>
<tr>
<td>P09 `review/mr_rework.md`</td>
<td>`ReviewReworkWorkflow`</td>
<td>Исправление замечаний и ответы на threads → `ReworkResult`</td>
</tr>
<tr>
<td>P10 `verification/test_design.md`</td>
<td>`VerificationWorkflow`</td>
<td>Набор test cases с трассировкой к AC и рискам → `TestCaseSet`</td>
</tr>
<tr>
<td>P11 `verification/test_automation.md`</td>
<td>`VerificationWorkflow`</td>
<td>Создание/запуск тестов для Story или Bug → `TestAutomationResult`</td>
</tr>
<tr>
<td>P12 `verification/failure_triage.md`</td>
<td>`VerificationWorkflow`</td>
<td>`CreateDefect`, `LinkExisting`, `TestIssue`, `Flaky`, `InfraIssue` → `FailureTriageDecision`</td>
</tr>
<tr>
<td>P13 `verification/root_cause_analysis.md`</td>
<td>`VerificationWorkflow`</td>
<td>Причина, evidence, impact, fix direction → `RCAReport`</td>
</tr>
<tr>
<td>P14 `release/knowledge_update.md`</td>
<td>`ReleaseWorkflow`</td>
<td>Release summary, OpenSpec archive/update и предлагаемый OKF patch → `KnowledgePatch`</td>
</tr>
</table>

Важные сокращения относительно DMTools:
- отдельного prompt для AI-ответа на clarification question нет — Temporal ждёт решение человека через Plane;
- отдельные Story/Bug test prompts не нужны;
- test review использует P08 с profile `test_automation`;
- test rework использует P09 с тем же profile;
- bug fix выполняется как `P13 → P06 → P07`;
- readiness, merge, deploy и изменение статусов не используют LLM.

## Переиспользуемые instruction packs

Дополнительно нужны не вызываемые самостоятельно policy packs:
- `common/context_and_evidence.md`
- `common/tool_and_side_effect_policy.md`
- `knowledge/openspec_okf.md`
- `tracker/plane.md`
- `scm/gitlab_worktree.md`
- `engineering/coding_tdd.md`
- `engineering/review.md`
- `engineering/test_automation.md`
- `security/appsec.md`
- `ui/small_uikit_accessibility.md`
- `release/deployment_policy.md`

Для конкретного трекера задач динамически подключаются project profiles: React + Small UIKit, FastAPI/Python, команды сборки, тестовые фреймворки, deployment constraints. Эти сведения не следует дублировать в 14 основных промптах.

## Хранение и версионирование

Каноническое место — репозиторий `dark-factory-orchestrator`:
- `prompts/tasks/` — 14 task-prompts;
- `prompts/policies/` — общие instruction packs;
- `agents/` — определения PydanticAI Agent;
- `contracts/` — входные и выходные Pydantic-модели;
- `workflows/` — Temporal workflows;
- `evals/` — datasets и evaluators;
- `prompt-manifest.yaml` — соответствие prompt → agent → workflow → contract → allowed tools.

GitLab остаётся source of truth. CI может публиковать проверенную версию в Langfuse. В начале Temporal workflow следует разрешать label вроде `production` в конкретную неизменяемую версию и сохранять её в состоянии workflow, чтобы продолжившийся через несколько дней процесс не получил новый prompt посередине выполнения. Langfuse поддерживает версии, labels и rollback, а также связывание версии промпта с trace. [Langfuse Prompt Management](https://langfuse.com/docs/prompt-management/data-model).

Каждый prompt должен иметь regression dataset: нормальный сценарий, неполный контекст, конфликт источников, недоступный tool, prompt injection, неверный output и запрещённый side effect. Для этого непосредственно подходит code-first модель [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/).

Главный вывод: из DMTools стоит перенести дисциплину этапов, evidence-first подход, TDD, независимый review и defect loop. Сам каталог копировать не нужно — его следует нормализовать в 14 task-prompts, а всю повторяющуюся и детерминированную логику вынести соответственно в PydanticAI capabilities, Pydantic contracts и Temporal workflows.
