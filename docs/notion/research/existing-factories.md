<!--
Источник: Notion — Существующие фабрики
URL: https://app.notion.com/p/3d3db33037c88081a361f0112078bfd0
Выгружено: 2026-09-12
-->

# Существующие фабрики

**Да, кроме EPAM есть несколько серьёзных направлений: StrongDM, Factory, Warp, 8090, Thoughtworks, Globant, Cognition/Devin и OpenHands.** Самые полезные инженерные материалы при этом часто публикуют компании, которые строят фабрики для себя: Spotify, Stripe, OpenAI и Anthropic.

Я собрал ниже карту решений, открытые реализации, технические руководства и практики, которые можно использовать для собственной платформы. Состояние публичных материалов — на **6 сентября 2026 года**. Наличие документации и кода проверено; продукты в рамках этого исследования я не разворачивал. Цифры результатов — заявления авторов кейсов, а не независимые измерения.

**1. Что именно называют Software Dark Factory**

Термин используют довольно свободно. В строгой трактовке это процесс, в котором люди определяют требования, ограничения и критерии приёмки, а система самостоятельно реализует и проверяет программное обеспечение. У StrongDM принцип доведён до отказа от ручного написания и ручного ревью кода. В модели Dan Shapiro это высшая ступень автономности разработки. [StrongDM: Software Factory](https://factory.strongdm.ai/), [Dan Shapiro: Five Levels](https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/).

Для сравнения предложений я бы различал четыре категории:

<table header-row="true">
<tr>
<td>Категория</td>
<td>Что фактически автоматизировано</td>
<td>Как оценивать</td>
</tr>
<tr>
<td>Coding agent</td>
<td>Выполнение отдельной задачи в репозитории</td>
<td>Насколько качественный результат он возвращает</td>
</tr>
<tr>
<td>Агентный конвейер</td>
<td>Планирование, реализация, проверки, исправления, создание PR/MR</td>
<td>Сколько ручных вмешательств требуется до принятого изменения</td>
</tr>
<tr>
<td>Платформа Software Factory</td>
<td>Контекст, очередь работ, агенты, окружения, контроль качества, наблюдаемость</td>
<td>Насколько воспроизводимо она обслуживает несколько команд и продуктов</td>
</tr>
<tr>
<td>Dark Factory в строгом смысле</td>
<td>Поставка изменений без обязательного ручного чтения кода</td>
<td>Насколько независимая приёмка действительно обнаруживает неправильное поведение</td>
</tr>
</table>

**Автономно создать PR, получить зелёный CI и безопасно выпустить изменение — три разных результата.** Это главное различие при изучении кейсов.

**2. Карта вендоров и платформ**

<table header-row="true">
<tr>
<td>Решение</td>
<td>Что представляет собой</td>
<td>Практический интерес</td>
<td>Ограничение или статус</td>
</tr>
<tr>
<td>**EPAM DMTools**</td>
<td>Оркестратор delivery workflows: CLI, MCP, jobs, агенты, skills, CI/CD</td>
<td>Открытая основа для автоматизации существующих инженерных процессов</td>
<td>Требуется собрать и адаптировать конкретный процесс. [Код и документация](https://github.com/epam/dm.ai)</td>
</tr>
<tr>
<td>**CodeMie**</td>
<td>Платформа агентов, знаний и многоагентных workflows по SDLC</td>
<td>Можно изучать backend, интеграции и запуск в собственной инфраструктуре</td>
<td>Репозиторий `codemie` содержит именно backend платформы. [Репозиторий](https://github.com/codemie-ai/codemie)</td>
</tr>
<tr>
<td>**StrongDM Software Factory**</td>
<td>Собственная фабрика и опубликованные принципы, техники, компоненты</td>
<td>Наиболее содержательный источник про автономность и независимую проверку</td>
<td>Не следует воспринимать публикации как готовый универсальный коробочный продукт. [Материалы](https://factory.strongdm.ai/)</td>
</tr>
<tr>
<td>**Factory — Software Factory / Droid**</td>
<td>Управление автоматизациями от triage до проверки, релиза, документации и мониторинга</td>
<td>Сквозная карта покрытия SDLC, метрики, интеграции, постоянные автоматизации</td>
<td>Именно Software Factory обозначен как **Private Preview**. [Документация](https://docs.factory.ai/software-factory/overview)</td>
</tr>
<tr>
<td>**Warp Factories**</td>
<td>Облачные фабрики специализированных агентов</td>
<td>Есть продукт и пошаговые инструкции самостоятельной сборки конвейера</td>
<td>**Early Access**; описанный процесс сохраняет ручное слияние PR. [Обзор](https://docs.warp.dev/factories/)</td>
</tr>
<tr>
<td>**8090 Software Factory**</td>
<td>Платформа требований, архитектурных blueprints, work orders, тестов и обратной связи</td>
<td>Особенно интересна связью спецификаций и реализации через граф знаний</td>
<td>Обещания синхронизации артефактов нужно проверять на своём проекте. [Продукт](https://www.8090.ai/software-factory)</td>
</tr>
<tr>
<td>**Thoughtworks AI/works**</td>
<td>Enterprise-платформа разработки и модернизации на основе спецификаций</td>
<td>Reverse engineering, библиотека контекста, Spec-to-Code, evals, эксплуатация</td>
<td>Платформа с инженерной методологией и услугами; публичный technical guide — обзор возможностей. [Технический обзор](https://www.thoughtworks.com/en-us/ai/works/technical-guide)</td>
</tr>
<tr>
<td>**Globant AI Pods**</td>
<td>Управляемая поставка результатов с агентами под надзором экспертов</td>
<td>Альтернатива покупке платформы: подписка на определённую инженерную работу</td>
<td>Люди участвуют в контроле результатов. [Описание и сценарии](https://www.globant.com/ai-pods)</td>
</tr>
<tr>
<td>**Cognition / Devin**</td>
<td>Агент разработки с playbooks и автоматизациями</td>
<td>Повторяемые задачи, запуск сессий по событиям, формализация командных процедур</td>
<td>Сам coding agent не закрывает весь процесс управления требованиями и приёмкой. [Документация](https://docs.devin.ai/get-started/devin-intro)</td>
</tr>
<tr>
<td>**OpenHands**</td>
<td>SDK, сервер выполнения агентов и Agent Canvas</td>
<td>Сильный кандидат для собственной платформы с управляемым исполнением</td>
<td>Нужно отдельно проектировать приёмку и delivery policies. [SDK](https://docs.openhands.dev/sdk), [Agent Canvas](https://github.com/OpenHands/OpenHands)</td>
</tr>
<tr>
<td>**GitLab Duo Agent Platform**</td>
<td>Агенты и flows внутри GitLab</td>
<td>Issue → MR, диагностика CI, security, review, собственные workflows</td>
<td>Доступность зависит от версии, тарифа и конфигурации. [Описание](https://about.gitlab.com/gitlab-duo-agent-platform/)</td>
</tr>
<tr>
<td>**GitHub Agentic Workflows**</td>
<td>Описанные в Markdown агентные workflows в GitHub Actions</td>
<td>Открытый пример совмещения AI и детерминированного CI/CD</td>
<td>Ориентирован на GitHub; не является готовой платформой всего SDLC. [Документация](https://github.github.com/gh-aw/)</td>
</tr>
<tr>
<td>**Superconductor**</td>
<td>Совместное рабочее пространство людей и coding agents</td>
<td>Координация параллельной разработки и ревью</td>
<td>Прежде всего среда совместной работы; наличие самостоятельной приёмки нужно оценивать отдельно. [Продукт](https://www.superconductor.com/)</td>
</tr>
</table>

**3. Что дополнительно изучить у EPAM**

У EPAM есть несколько разных материалов и компонентов, которые полезно рассматривать отдельно.

**DMTools — наиболее прикладная отправная точка.**

В репозитории есть оркестрация через jobs и agents, интеграции с трекерами, документацией, дизайном и CI/CD, а также проектные skills. CLI ориентирован на Java 17+.

Материалы:
- [Основной репозиторий DMTools](https://github.com/epam/dm.ai) — устройство и варианты использования.
- [Центр документации](https://dmtools.lab.epam.com/docs/) — установка, конфигурация, инструменты и workflows.
- [AI Teammate в GitHub Actions](https://github.com/epam/dm.ai/blob/main/dmtools-ai-docs/references/workflows/github-actions-teammate.md) — полноценный пример YAML, параметры запуска, secrets, расписание и matrix для конфигураций.
- [DMTools Agent Skill](https://github.com/epam/dm.ai/blob/main/dmtools-ai-docs/README.md) — установка полного пакета или отдельных skills для Jira, GitHub, Azure DevOps, TestRail.

**Что брать:** способ упаковать повторяемые инженерные операции в конфигурации и workflows, доступные агентам.

**CodeMie — более широкая платформа.**

Открытый backend построен на FastAPI и LangChain/LangGraph. В нём есть агенты, REST API, интеграции и индексация знаний. README содержит Docker Compose quickstart. Для собственной реализации на Python это полезный объект архитектурного разбора. [CodeMie backend](https://github.com/codemie-ai/codemie).

**Отдельно — честный эксперимент Figma → frontend.**

EPAM описывает семиагентный процесс: разбор дизайна, подготовка сценариев, реализация с промежуточным review, функциональная и визуальная проверка. Заявленный результат: примерно два часа автономной работы вместо двух дней разработки, затем ещё 2–4 часа ручной проверки и доводки; визуальное соответствие около 90%.

Наиболее полезные наблюдения: проверять после небольших изменений; выделять визуальную проверку; хранить состояние оркестрации; не доверять заявлению агента, что тест выполнен. Авторы прямо описывают случай, когда недоступный E2E-тест агент заменил чтением кода. [EPAM: Building a Dark Factory with AI Agents](https://www.epam.com/insights/ai/blogs/building-a-dark-factory-with-ai-agents).

**4. StrongDM — основной источник про устройство именно Dark Factory**

У StrongDM самая интересная часть — **архитектура проверки результата**.

Их подход опирается на:
- спецификации как вход;
- внешние сценарии приёмки, которые сложнее подогнать под реализацию;
- оценку наблюдаемого поведения;
- поведенческие копии внешних систем для массового тестирования.

Они описывают Digital Twin Universe с копиями API и поведения Okta, Jira, Slack, Google Docs и других зависимостей. Это позволяет воспроизводить ошибки и выполнять множество сценариев без ограничений реальных сервисов. [Описание подхода](https://factory.strongdm.ai/).

Практические материалы:

<table header-row="true">
<tr>
<td>Материал</td>
<td>Что из него извлечь</td>
</tr>
<tr>
<td>[Attractor](https://github.com/strongdm/attractor)</td>
<td>Спецификации оркестратора, coding loop и унифицированного LLM-клиента</td>
</tr>
<tr>
<td>[Attractor specification](https://github.com/strongdm/attractor/blob/main/attractor-spec.md)</td>
<td>Устройство конвейера выполнения</td>
</tr>
<tr>
<td>[Coding agent loop specification](https://github.com/strongdm/attractor/blob/main/coding-agent-loop-spec.md)</td>
<td>Контракт цикла агента</td>
</tr>
<tr>
<td>[Unified LLM specification](https://github.com/strongdm/attractor/blob/main/unified-llm-spec.md)</td>
<td>Абстракцию работы с моделями</td>
</tr>
<tr>
<td>[Каталог техник](https://factory.strongdm.ai/techniques)</td>
<td>Управление контекстом, перенос решений, автономное выполнение подготовленных задач</td>
</tr>
<tr>
<td>[CXDB](https://factory.strongdm.ai/products/cxdb)</td>
<td>Хранилище контекста с DAG истории, дедупликацией и визуальной отладкой</td>
</tr>
</table>

**Важный нюанс: публичный Attractor — набор NLSpec, а не готовый runtime.** Авторы предлагают реализовать его по спецификациям выбранным coding agent. Это полезный архитектурный образец, но объём внедрения будет больше, чем «скачать и запустить». [README Attractor](https://github.com/strongdm/attractor).

Из каталога техник особенно полезны:

<table header-row="true">
<tr>
<td>Техника</td>
<td>Практическое применение</td>
</tr>
<tr>
<td>**Shift Work**</td>
<td>Сначала человек и агент снимают неоднозначность; затем полностью подготовленная задача выполняется автономно</td>
</tr>
<tr>
<td>**Gene Transfusion**</td>
<td>Давать агенту работающий пример из другого сервиса как образец реализации</td>
</tr>
<tr>
<td>**Filesystem**</td>
<td>Использовать файлы, индексы и сохранённое состояние как память процесса</td>
</tr>
<tr>
<td>**Pyramid Summaries**</td>
<td>Хранить описание системы на нескольких уровнях детализации с возможностью перейти к первоисточнику</td>
</tr>
<tr>
<td>**Semport**</td>
<td>Переносить реализацию между языками и фреймворками с сохранением смысла</td>
</tr>
</table>

Эти паттерны опубликованы самим StrongDM. [Techniques](https://factory.strongdm.ai/techniques).

Моя рекомендация для enterprise: взять независимые сценарии и воспроизводимые окружения, а отказ от ручного review вводить только для конкретных классов изменений, качество которых уже измерено.

**5. 8090 и Thoughtworks — наиболее интересные решения вокруг спецификаций**

**8090 стоит посмотреть в первую очередь, если важны требования, архитектура и граф знаний.**

Его основной процесс:

**Requirements → Blueprints → Work Orders → реализация → обратная связь.**

Документация описывает общий Knowledge Graph, связывающий требования, архитектуру и детали реализации. Предполагается распространение изменений между связанными артефактами. [Архитектура продукта](https://docs.8090.ai/general/introduction).

В quickstart есть конкретный порядок:
1. Создать организацию и проект.
2. Подключить GitHub или GitLab и выбрать индексируемую ветку.
3. Добавить контекстные документы.
4. Описать продуктовые и функциональные требования.
5. Подготовить blueprints и work orders.

[8090 Quickstart](https://docs.8090.ai/general/quickstart).

На демо я бы проверял: изменение API-контракта, обнаружение затронутых требований и тестов, обновление документации после изменения кода, экспорт графа и сохранение истории решений. Именно эти проверки покажут ценность сверх обычной генерации Markdown.

**Thoughtworks AI/works интересен как образец полной enterprise-архитектуры.**

В technical guide выделены:
- сбор и обогащение требований;
- reverse engineering существующей системы в спецификации;
- библиотека архитектурного, предметного и технического контекста;
- библиотека компонентов;
- динамическая спецификация — Super Spec;
- Spec-to-Code и evals;
- эксплуатация;
- централизованный control plane с бюджетами, аудитом и политиками.

[AI/works Technical Guide](https://www.thoughtworks.com/en-us/ai/works/technical-guide).

Практическая ценность — перечень подсистем, которые приходится создавать вокруг генератора кода. Для изучения коммерческого предложения есть [главная страница с кейсами](https://www.thoughtworks.com/ai/works).

**6. Factory, Warp и Devin — кандидаты на готовое выполнение работ**

**Factory** объединяет автоматизации по этапам SDLC: triage, code generation, validation, release, documentation и monitoring. Документация показывает покрытие репозиториев, состояние интеграций и метрики выполненной работы; среди интеграций перечислены GitHub, GitLab, Slack и Linear. Software Factory находится в Private Preview. [Factory Software Factory](https://docs.factory.ai/software-factory/overview).

**Что проверять:** сколько переходов между этапами действительно выполняется автоматически и где нужен человек; как задаются независимые проверки и ограничения для репозиториев.

**Warp** даёт особенно полезную пару: управляемый продукт и воспроизводимое руководство сборки.
- [Warp Factories](https://docs.warp.dev/factories/) — продукт, Early Access.
- [Set up your software factory](https://docs.warp.dev/guides/agent-workflows/set-up-a-software-factory) — настройка конвейера.
- [Run a software factory in the cloud](https://docs.warp.dev/guides/agent-workflows/run-a-software-factory-in-the-cloud) — окружения, secrets, permissions, triggers и наблюдаемость.

В руководстве описаны отдельные права для triage, spec, implementation и reviewer, изолированные запуски и аудит. Это хороший материал для проектирования собственного исполнения в CI.

Управляемый Warp Factories по умолчанию ведёт запрос до готового к слиянию PR; люди сохраняют ответственность за merge. Также предлагается группировать фабрики по совместно поставляемым продуктам и их политикам. [Модель Warp Factories](https://docs.warp.dev/factories/).

**Devin** полезно изучать через повторяемость работы:
- [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks) — упаковка инструкций для повторяемых задач.
- [Automations](https://docs.devin.ai/product-guides/automations) — автоматизированные запуски.
- [Общая документация](https://docs.devin.ai/get-started/devin-intro) — knowledge, skills, security profiles, scheduled sessions и интеграции.

При оценке стоит дать ему серию однотипных изменений по вашим сервисам и измерить ручную доводку каждого результата.

**7. Реальные инженерные кейсы: что читать обязательно**

**Spotify Honk — особенно полезен для компании с множеством сервисов.**

Spotify встроил background coding agent в существующую Fleet Management. Сохранились выбор репозиториев, создание PR и процедуры доставки; агент расширил возможности преобразования кода. В публикации ноября 2025 года указаны более 1 500 принятых PR и экономия времени 60–90% на рассматриваемых миграциях. Эти цифры нельзя переносить на любую продуктовую разработку. [Honk, часть 1](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1).

Вся серия:

<table header-row="true">
<tr>
<td>Публикация</td>
<td>Практическая польза</td>
</tr>
<tr>
<td>[Часть 1: устройство и опыт внедрения](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1)</td>
<td>Как встроить агента в существующую платформу массовых изменений</td>
</tr>
<tr>
<td>[Часть 2: Context Engineering](https://engineering.atspotify.com/2025/11/context-engineering-background-coding-agents-part-2)</td>
<td>Как готовить контекст для работы без постоянных уточнений</td>
</tr>
<tr>
<td>[Часть 3: Feedback Loops](https://engineering.atspotify.com/2025/12/feedback-loops-background-coding-agents-part-3)</td>
<td>Как устроить проверку до создания PR</td>
</tr>
<tr>
<td>[Часть 4: Dataset Migrations](https://engineering.atspotify.com/2026/4/background-coding-agents-dataset-migrations-honk-part-4)</td>
<td>Применение к миграциям потребителей данных</td>
</tr>
</table>

Особенно полезна идея из третьей части: **дать агенту единый инструмент проверки**, за которым скрываются подходящие для проекта build, lint и tests. Инструмент возвращает компактные ошибки; обязательная проверка выполняется и перед завершением. Поверх неё используется LLM-проверка соблюдения границ задачи. [Verification loops](https://engineering.atspotify.com/2025/12/feedback-loops-background-coding-agents-part-3).

**Stripe Minions — пример большого потока агентных изменений.**

Stripe описывает собственных end-to-end coding agents, обеспечивающих более тысячи merged PR в неделю, при сохранении человеческого review. Это существенная автоматизация, но цифра PR сама по себе не доказывает полностью автономную разработку.
- [Minions, часть 1](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents).
- [Minions, часть 2](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2).

**OpenAI Harness Engineering — практический материал о подготовке среды.**

Полезные решения: отдельное окружение приложения на worktree, доступ агента к браузеру, логам и метрикам, короткий `AGENTS.md` как карта документации, версионируемые планы, автоматические проверки структуры базы знаний.

Описан конкретный внутренний продукт с примерно 1 500 PR за пять месяцев; ручное написание кода исключили, человеческое review допускалось, но не было обязательным. Это кейс одной команды, не универсальный показатель разработки OpenAI. [Harness Engineering](https://openai.com/index/harness-engineering/).

**Anthropic — как поддерживать долгую работу через несколько контекстных окон.**

Паттерн состоит из initializer agent и последующих coding sessions. Первый подготавливает окружение, список функций и журнал прогресса. Последующие выполняют небольшие изменения, проверяют приложение и оставляют состояние следующему запуску. Простого сжатия контекста недостаточно.
- [Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).
- [Рабочий пример autonomous-coding](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding).

**Cursor — опыт масштабирования числа агентов.**

Полезен разбор координации, разделения ролей и неудачных архитектурных решений. Авторы отмечают, что дополнительный integrator создавал узкое место, а избыточная структура делала систему хрупкой. Это исследовательские эксперименты; большие объёмы сгенерированного кода не равны доказанной готовности продуктов. [Scaling Long-Running Autonomous Coding](https://cursor.com/blog/scaling-agents).

**8. Открытые реализации и заготовки для собственной фабрики**

<table header-row="true">
<tr>
<td>Проект</td>
<td>Что можно использовать</td>
<td>Что учитывать</td>
</tr>
<tr>
<td>[OpenHands Software Agent SDK](https://docs.openhands.dev/sdk)</td>
<td>Python API, инструменты, MCP, REST agent server, удалённое выполнение в Docker/Kubernetes</td>
<td>SDK решает исполнение; бизнес-приёмка остаётся отдельной задачей</td>
</tr>
<tr>
<td>[OpenHands Agent Canvas](https://github.com/OpenHands/OpenHands)</td>
<td>Self-hosted control center, автоматизации, разные agent backends, ACP-совместимые агенты</td>
<td>Проверять нужные enterprise-функции выбранной конфигурации</td>
</tr>
<tr>
<td>[EPAM DMTools](https://github.com/epam/dm.ai)</td>
<td>Интеграции, jobs, workflows, skills</td>
<td>Адаптация к своему трекеру и CI</td>
</tr>
<tr>
<td>[CodeMie](https://github.com/codemie-ai/codemie)</td>
<td>Backend платформы знаний и агентов</td>
<td>Более широкий объём внедрения</td>
</tr>
<tr>
<td>[StrongDM Attractor](https://github.com/strongdm/attractor)</td>
<td>Спецификации собственного runtime</td>
<td>Реализацию предстоит создать и проверить</td>
</tr>
<tr>
<td>[GitHub Agentic Workflows](https://github.github.com/gh-aw/)</td>
<td>Markdown → исполняемый workflow, budgets, safe outputs, sandbox</td>
<td>Привязка к GitHub Actions</td>
</tr>
<tr>
<td>[Superpowers](https://github.com/obra/superpowers)</td>
<td>Skills для планирования, TDD, worktrees, review и проверки завершения</td>
<td>Методология работы агента, а не серверная фабрика</td>
</tr>
<tr>
<td>[Anthropic autonomous-coding](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding)</td>
<td>Минимальный пример продолжительной автономной разработки</td>
<td>Учебная основа, которую нужно расширять</td>
</tr>
<tr>
<td>[peter-stratton/dark-factory](https://github.com/peter-stratton/dark-factory)</td>
<td>Go CLI `godark`: исполнение issues, независимые reviewer agents, Docker, архитектурные проверки</td>
<td>Ориентирован на Claude Code и GitHub; лицензия **Elastic License 2.0**, не MIT/Apache</td>
</tr>
<tr>
<td>[nickfujita/dark-factory](https://github.com/nickfujita/dark-factory)</td>
<td>Workflow assets для Claude Code/Codex: PRD, планы, review, acceptance runbooks</td>
<td>Небольшой проект; полезнее как источник шаблонов, чем доказанная enterprise-платформа</td>
</tr>
</table>

У `godark` интересны проверяемые архитектурные ограничения и независимые reviewer agents без прав редактирования файлов. У `nickfujita/dark-factory` — явные точки участия человека и ограниченные циклы review. Эти свойства описаны в README соответствующих проектов.

Дополнительно:
- [Ralph Wiggum — исходное описание Geoffrey Huntley](https://ghuntley.com/ralph/) — техника повторяющегося агентного цикла. Полезна для понимания автономного исполнения; сама по себе не обеспечивает качество.
- [GitHub Agentic Workflows: Security Architecture](https://github.github.com/gh-aw/introduction/architecture/) — полезный образец разделения выполнения агента, credentials и применения разрешённых изменений.
- [GitHub Agentic Workflows: Gallery](https://github.github.com/gh-aw/) — каталог сценариев triage, документации, CI, review и работы с несколькими репозиториями.

**9. Какие практики стоит перенести в собственную платформу**

Ниже — мой инженерный синтез изученных материалов.

<table header-row="true">
<tr>
<td>Практика</td>
<td>Как реализовать практически</td>
</tr>
<tr>
<td>**Контракт входной задачи**</td>
<td>Цель, затронутые компоненты, ограничения, допустимые изменения интерфейсов, критерии приёмки</td>
</tr>
<tr>
<td>**Подготовка до автономного запуска**</td>
<td>Неясные задачи возвращать на уточнение; в исполнение передавать согласованную постановку</td>
</tr>
<tr>
<td>**Сохраняемое состояние процесса**</td>
<td>Хранить этап, commit, результаты проверок, использованный контекст, число попыток и причину остановки</td>
</tr>
<tr>
<td>**Короткие единицы работы**</td>
<td>Одна проверяемая задача на изменение; крупную функциональность разбивать по зависимостям</td>
</tr>
<tr>
<td>**Изолированное окружение**</td>
<td>Отдельные checkout/worktree, runtime и тестовые данные на запуск</td>
</tr>
<tr>
<td>**Единый интерфейс проверки**</td>
<td>Например, `verify`: форматирование, сборка, тесты, контракты и архитектурные ограничения</td>
</tr>
<tr>
<td>**Независимая приёмка**</td>
<td>Часть сценариев и критериев хранить вне области изменения coding agent</td>
</tr>
<tr>
<td>\*\*</td>
<td></td>
</tr>
</table>

---

**Да, EPAM — далеко не единственный участник.** Нашёл несколько продуктов для построения программных фабрик, открытые реализации и подробные инженерные отчёты компаний, которые уже используют автономную разработку.

Наибольшую практическую ценность, на мой взгляд, представляют **StrongDM, EPAM DMTools, 8090, Warp, Factory, Thoughtworks AI/works и серия Spotify Honk**. Они раскрывают разные части задачи: от спецификаций и графа знаний до исполнения, проверки и массового изменения репозиториев.

Ниже — подборка по состоянию на **6 сентября 2026 года**. Это исследование публичных материалов и документации; продукты я не разворачивал. Численные результаты компаний приведены как их собственные заявления.

**1. Что именно искать под названием Software Dark Factory**

Термин используется довольно свободно. Для сравнения предложений полезно различать три модели:

<table header-row="true">
<tr>
<td>Модель</td>
<td>Что происходит</td>
<td>Где остаётся человек</td>
</tr>
<tr>
<td>**AI-assisted engineering**</td>
<td>Агент помогает писать, менять и проверять код</td>
<td>Управляет практически каждой задачей</td>
</tr>
<tr>
<td>**Agentic Software Factory**</td>
<td>Конвейер принимает задание, готовит контекст, реализует изменение, проверяет его и создаёт PR/MR</td>
<td>Утверждает требования, разбирает исключения, принимает изменения</td>
</tr>
<tr>
<td>**Dark Factory в строгом смысле**</td>
<td>Спецификации и приёмочные сценарии управляют выпуском ПО без обязательного чтения кода человеком</td>
<td>Задаёт намерение, ограничения и способы доказать правильность результата</td>
</tr>
</table>

Последняя модель соответствует радикальному подходу StrongDM. В шкале Dan Shapiro она выступает высшим уровнем автоматизации, но **это авторская модель зрелости, а не отраслевой стандарт**. [Dan Shapiro — Five Levels](https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/), [StrongDM — Software Factory](https://factory.strongdm.ai/).

Поэтому искать стоит также по запросам:

`agentic software factory`, `autonomous software engineering`, `background coding agents`, `agentic SDLC`, `spec-to-code`, `harness engineering`, `software engineering agents`, `continuous AI`.

**2. Карта вендоров и платформ**

В таблице я отделил готовые предложения от инженерных компонентов. Название Software Factory само по себе не означает автономный production deployment.

<table header-row="true">
<tr>
<td>Вендор / решение</td>
<td>Что предлагает</td>
<td>Практическая ценность и ограничения</td>
</tr>
<tr>
<td>**EPAM DMTools**</td>
<td>Оркестрация delivery workflows через CLI, MCP, jobs, agents и CI/CD</td>
<td>Открытый репозиторий, документация интеграций и рабочие примеры. Подходит для изучения собственной фабрики в корпоративной среде. [Репозиторий](https://github.com/epam/dm.ai)</td>
</tr>
<tr>
<td>**CodeMie**</td>
<td>Платформа агентов, workflows, знаний и интеграций для SDLC</td>
<td>Доступен backend на FastAPI и LangChain/LangGraph, Apache-2.0. Можно изучать устройство и запускать локально; состав enterprise-функций проверять отдельно. [Код](https://github.com/codemie-ai/codemie)</td>
</tr>
<tr>
<td>**StrongDM Software Factory**</td>
<td>Собственная модель разработки из спецификаций и сценариев</td>
<td>Один из наиболее содержательных первоисточников. Опубликованы методы, компоненты и спецификации; это не подтверждённая универсальная фабрика «под ключ». [Материалы](https://factory.strongdm.ai/)</td>
</tr>
<tr>
<td>**8090 Software Factory**</td>
<td>Управление требованиями, архитектурными blueprints, work orders, тестами и обратной связью</td>
<td>Особенно интересно сочетание SDD и графа знаний. Полезно для организации сквозной связи бизнеса, архитектуры и реализации. [Продукт](https://www.8090.ai/software-factory)</td>
</tr>
<tr>
<td>**Factory / Droids**</td>
<td>Автоматизация стадий от triage до validation, release, documentation и monitoring</td>
<td>Отдельный продукт Software Factory находится в **Private Preview**. Документация показывает структуру автоматизаций и покрытие репозиториев. [Документация](https://docs.factory.ai/software-factory/overview)</td>
</tr>
<tr>
<td>**Warp Factories**</td>
<td>Специализированные агенты обрабатывают запросы до готового к merge PR</td>
<td>**Early Access**. Документация прямо сохраняет merge за человеком. Есть отдельные инструкции для самостоятельной сборки конвейера. [Обзор](https://docs.warp.dev/factories/)</td>
</tr>
<tr>
<td>**Cognition / Devin**</td>
<td>Автономный исполнитель инженерных задач, playbooks и automations</td>
<td>Кандидат на исполнительный слой фабрики. Для повторяемой работы полезнее изучать playbooks и автоматизации, чем демонстрации разовых задач. [Документация](https://docs.devin.ai/get-started/devin-intro)</td>
</tr>
<tr>
<td>**Thoughtworks AI/works**</td>
<td>Agentic Development Platform: reverse engineering, спецификации, генерация, эксплуатация и управление</td>
<td>Содержательное enterprise-предложение для новых систем и модернизации. Предусматривает human oversight. [Описание](https://www.thoughtworks.com/ai/works)</td>
</tr>
<tr>
<td>**Globant AI Pods**</td>
<td>Подписка на delivery-процессы, выполняемые агентами под надзором экспертов</td>
<td>Вариант покупки результата и услуги. Есть направления modernization, backlog acceleration, architecture, testing. [Предложение](https://www.globant.com/ai-pods)</td>
</tr>
<tr>
<td>**GitLab Duo Agent Platform**</td>
<td>Агенты и многошаговые flows внутри GitLab</td>
<td>Особенно релевантно GitLab-ландшафту: задачи, MR, CI/CD, права и журналы выполнения связаны в одной платформе. [Описание](https://about.gitlab.com/gitlab-duo-agent-platform/)</td>
</tr>
<tr>
<td>**OpenHands**</td>
<td>SDK инженерных агентов и self-hosted Agent Canvas для управления ими</td>
<td>Основа для собственной реализации: Python API, инструменты, удалённое исполнение, Docker/Kubernetes. [SDK](https://docs.openhands.dev/sdk), [Agent Canvas](https://github.com/OpenHands/OpenHands)</td>
</tr>
<tr>
<td>**GitHub Agentic Workflows**</td>
<td>Агентные задания в GitHub Actions, описываемые в Markdown</td>
<td>Полезный образец исполнения через CI с ограниченными правами, бюджетами и контролируемыми выходными действиями. [Документация](https://github.github.com/gh-aw/)</td>
</tr>
<tr>
<td>**Superconductor**</td>
<td>Совместное рабочее пространство для команды и coding agents</td>
<td>Полезно для параллельной разработки и review. По публичному позиционированию это рабочая среда сотрудничества. [Продукт](https://www.superconductor.com/)</td>
</tr>
</table>

**Мой shortlist по назначению:** для самостоятельной сборки — **DMTools и OpenHands**; для управления спецификациями — **8090**; для готового конвейера — **Warp и Factory**; для enterprise-модернизации с подрядчиком — **Thoughtworks и Globant**.

**3. EPAM: изучать стоит сразу несколько разных материалов**

**DMTools — наиболее прикладная находка.**

Проект прямо позиционируется как enterprise dark-factory orchestrator. Он объединяет работу с трекерами, исходным кодом, документацией, дизайном, AI-провайдерами и CI/CD.

Что смотреть:
- [Основной репозиторий](https://github.com/epam/dm.ai) — назначение, структура, установка, варианты использования.
- [Документация](https://dmtools.lab.epam.com/docs/) — интеграции, инструменты и конфигурация.
- [DMTools Agent Skill](https://github.com/epam/dm.ai/blob/main/dmtools-ai-docs/README.md) — упаковка знаний об инструменте в skills для проекта.
- [AI Teammate в GitHub Actions](https://github.com/epam/dm.ai/blob/main/dmtools-ai-docs/references/workflows/github-actions-teammate.md) — полноценный пример workflow.

Последний материал особенно полезен: показаны запуск через `workflow_dispatch`, конфигурация агента, обработка Jira-задач, кеширование установки, secrets, расписание и запуск разных конфигураций через matrix.

**Что можно перенять:** представление фабрики как набора версионируемых jobs и конфигураций, запускаемых существующим CI. Для GitLab потребуется проверить интеграции и адаптировать workflow.

**CodeMie — более широкая платформенная основа.**

В открытом backend есть REST API, оркестрация агентов, интеграции и индексирование знаний из Git, Jira и Confluence. README содержит Docker Compose quickstart. Это полезный материал для анализа устройства корпоративной платформы агентов. [CodeMie backend](https://github.com/codemie-ai/codemie).

**Отдельно — честный инженерный эксперимент EPAM с frontend.**

В статье описана цепочка из семи агентов: разбор дизайна и тестовых сценариев, реализация с промежуточным review, функциональная и визуальная проверка.

Заявленный результат: около **2 часов автономного выполнения плюс 2–4 часа ручной доводки**, вместо примерно двух дней работы разработчика. Авторы оценивают визуальное соответствие примерно в 90% и признают нестабильность между повторными запусками. Кодирование занимает менее трети времени; основное время уходит на проверки и исправления.

Стек эксперимента: Copilot custom agents, Figma MCP, Chrome DevTools MCP, skills и `AGENTS.md`. Это отдельный workflow; не следует автоматически приписывать его результаты DMTools или CodeMie. [EPAM — Building a Dark Factory with AI Agents](https://www.epam.com/insights/ai/blogs/building-a-dark-factory-with-ai-agents).

**4. StrongDM: наиболее интересная архитектура строгой Dark Factory**

У StrongDM два принципиальных решения:
- Разработку направляют **спецификации и внешние приёмочные сценарии**.
- Правильность проверяется через наблюдаемое поведение системы, без обязательного человеческого code review.

Авторы описывают проблему: агент может подогнать код под узкие тесты или изменить сами тесты. Поэтому сценарии часто хранятся вне рабочего codebase, по аналогии с отложенной выборкой при обучении моделей.

Для интеграционного тестирования они создали **Digital Twin Universe** — поведенческие копии зависимостей, включая Okta, Jira, Slack и сервисы Google. Это позволяет воспроизводить ошибки и запускать большие объёмы сценариев без ограничений реальных SaaS. [Описание подхода](https://factory.strongdm.ai/).

Наиболее полезные материалы:

<table header-row="true">
<tr>
<td>Материал</td>
<td>Что из него извлечь</td>
</tr>
<tr>
<td>[Techniques](https://factory.strongdm.ai/techniques)</td>
<td>Практические паттерны организации фабрики</td>
</tr>
<tr>
<td>[Attractor](https://github.com/strongdm/attractor)</td>
<td>Спецификации оркестратора, coding-agent loop и унифицированного LLM-клиента</td>
</tr>
<tr>
<td>[CXDB](https://factory.strongdm.ai/products/cxdb)</td>
<td>Self-hosted хранилище контекста: DAG диалогов, дедупликация, типизированные данные и визуальная отладка</td>
</tr>
<tr>
<td>[Products](https://factory.strongdm.ai/products)</td>
<td>Связь опубликованных компонентов фабрики</td>
</tr>
</table>

**Важная деталь: репозиторий Attractor содержит NLSpecs — спецификации для создания собственной реализации.** Это не готовый production-сервис, который достаточно запустить через Docker.

Из каталога техник я бы выделил:
- **Shift Work:** сначала интерактивно снять неопределённость, затем передать полностью описанную работу на автономное выполнение.
- **Gene Transfusion:** давать агенту конкретный успешный образец реализации из другого проекта.
- **Pyramid Summaries:** поддерживать несколько уровней детализации контекста с возможностью вернуться к исходным материалам.
- **Filesystem as memory:** использовать файлы, индексы и сохранённое состояние для продолжения работы.
- **Semport:** переносить реализацию между языками и фреймворками с сохранением смысла. [Каталог техник](https://factory.strongdm.ai/techniques).

Мой практический вывод: **самая ценная часть StrongDM — независимый контур проверки**. Для корпоративного пилота можно начать с поведенческих симуляторов нескольких критичных зависимостей, без копирования всего ИТ-ландшафта.

**5. 8090: фабрика вокруг требований и графа знаний**

8090 интересно рассмотреть именно архитектору. Платформа связывает:

**Requirements → Blueprints → Work Orders → Tests → Feedback.**

В документации Knowledge Graph описан как общий контекст требований, архитектурных планов и реализации. При изменениях система должна распространять обновления между связанными артефактами. Это заявление о возможностях продукта; фактическую полноту такого распространения нужно проверять на пилоте. [Архитектура подхода](https://docs.8090.ai/general/introduction).

В [Quickstart](https://docs.8090.ai/general/quickstart) показан конкретный процесс:
1. Создать организацию и проект.
2. Подключить GitHub или GitLab и выбрать индексируемую ветку.
3. Загрузить дополнительные знания.
4. Описать продуктовые и функциональные требования.
5. Сформировать архитектурные blueprints.
6. Подготовить work orders для реализации.

**Что я бы обязательно проверил на демонстрации:**
- Изменение API вызывает обновление каких именно артефактов?
- Как различаются предлагаемое изменение спецификации и утверждённое?
- Есть ли связь requirement → work order → commit → test?
- Можно ли экспортировать спецификации и связи?
- Что происходит, если код и документация противоречат друг другу?
- Поддерживается ли ваш GitLab Self-Managed, а не только доступный извне GitLab?

Это один из наиболее близких найденных продуктов к идее **SDD с управляемым графом артефактов**.

**6. Warp и Factory: изучение готового конвейера**

**У Warp особенно хороши инструкции по сборке.**

Есть два пути: управляемый продукт Warp Factories и самостоятельная настройка workflow.
- [Warp Factories overview](https://docs.warp.dev/factories/) — границы продукта и роли человека.
- [Set up your software factory](https://docs.warp.dev/guides/agent-workflows/set-up-a-software-factory) — сборка конвейера.
- [Run a software factory in the cloud](https://docs.warp.dev/guides/agent-workflows/run-a-software-factory-in-the-cloud) — окружения, права, триггеры и аудит.

Типовая последовательность: **triage → specification → implementation → review**. Для разных ролей задаются разные права; каждый запуск получает отдельное окружение и журнал.

Практически полезен их принцип группировки: фабрика объединяет репозитории, которые поставляют один продукт; специализация добавляется агентами и skills. Группы с разной политикой исполнения выделяются отдельно. Управляемый продукт остаётся в Early Access, а merge выполняют люди. [Модель Warp Factories](https://docs.warp.dev/factories/).

**Factory интереснее как управление покрытием всего SDLC.**

В документации показаны стадии Triage, Code-gen, Validate, Release, Document, Monitor, покрытие репозиториев и состояние автоматизаций. Среди сценариев — code review, QA, security audit, AutoWiki и incident response. Software Factory находится в Private Preview. [Factory — Overview](https://docs.factory.ai/software-factory/overview).

Мой вывод: при сравнении этих продуктов надо измерять не количество «агентов», а **какую долю жизненного цикла конкретного изменения они доводят до проверяемого результата**.

**7. Thoughtworks и Globant: альтернативы EPAM как enterprise-партнёру**

**Thoughtworks AI/works** публикует достаточно содержательный [Technical Guide](https://www.thoughtworks.com/en-us/ai/works/technical-guide).

В нём выделены:
- извлечение бизнес-логики из legacy-кода;
- библиотека контекста, стандартов и ограничений;
- повторно используемые компоненты;
- динамическая спецификация — Super Spec;
- генерация кода и тестов;
- evals, проверяющие качество, безопасность и трассируемость;
- runtime operations;
- control plane для политик, аудита и затрат.

**Практическая польза:** это готовая карта функциональных возможностей, по которой можно проверять полноту собственной платформы или предложения подрядчика. Особенно ценно, что рассматриваются обратное проектирование и дальнейшее сопровождение.

**Globant AI Pods** полезен как другая модель закупки: подписка на определённую delivery-способность с агентами и экспертным надзором. Есть пакеты для декомпозиции монолита, миграций, архитектуры, разработки, тестирования и исправлений. Результаты входят в обычные PR-review и CI/CD процессы. [Globant AI Pods](https://www.globant.com/ai-pods).

Для сравнения с EPAM я бы запросил одинаковый пилот: **одна существующая система, одно изменение бизнес-логики, одна интеграция и фиксированный набор приёмочных сценариев**.

**8. Реальный внутренний опыт: Spotify, Stripe, OpenAI, Anthropic и Cursor**

Эти компании не обязательно продают свою фабрику, но публикуют полезные инженерные решения.

<table header-row="true">
<tr>
<td>Источник</td>
<td>Что описано</td>
<td>Что практически перенять</td>
</tr>
<tr>
<td>[Spotify Honk, часть 1](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1)</td>
<td>Более 1 500 merged PR от фонового агента на момент публикации; развитие Fleet Management</td>
<td>Массовые миграции и обновления по множеству репозиториев</td>
</tr>
<tr>
<td>[Spotify Honk, часть 2](https://engineering.atspotify.com/2025/11/context-engineering-background-coding-agents-part-2)</td>
<td>Context engineering для фоновых агентов</td>
<td>Подготовку самодостаточного задания</td>
</tr>
<tr>
<td>[Spotify Honk, часть 3](https://engineering.atspotify.com/2025/12/feedback-loops-background-coding-agents-part-3)</td>
<td>Независимые verifiers и циклы обратной связи</td>
<td>Единый интерфейс проверки, скрывающий детали сборки и тестов</td>
</tr>
<tr>
<td>[Spotify Honk, часть 4](https://engineering.atspotify.com/2026/4/background-coding-agents-dataset-migrations-honk-part-4)</td>
<td>Миграции потребителей datasets</td>
<td>Автоматизацию изменений в зависимых компонентах</td>
</tr>
<tr>
<td>[Stripe Minions, часть 1](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents)</td>
<td>Внутренние end-to-end coding agents; более тысячи merged PR в неделю по заявлению Stripe</td>
<td>Организацию потока автономно подготовленных изменений с человеческим review</td>
</tr>
<tr>
<td>[Stripe Minions, часть 2](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)</td>
<td>Продолжение инженерного разбора</td>
<td>Дополнительный материал об устройстве внутренней платформы</td>
</tr>
<tr>
<td>[OpenAI — Harness Engineering](https://openai.com/index/harness-engineering/)</td>
<td>Разработка внутреннего продукта агентами, устройство репозитория и среды</td>
<td>Контекст, архитектурные ограничения и наблюдаемость, доступные агенту</td>
</tr>
<tr>
<td>[Anthropic — Effective Harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)</td>
<td>Продолжительная работа через несколько контекстных окон</td>
<td>Инициализацию проекта, сохранение прогресса и последовательное выполнение</td>
</tr>
<tr>
<td>[Cursor — Scaling Agents](https://cursor.com/blog/scaling-agents)</td>
<td>Эксперименты с длительной параллельной разработкой</td>
<td>Координацию, разделение ролей и анализ проблем масштабирования</td>
</tr>
</table>

**Самая полезная практика Spotify — verifier как отдельный инструмент.**

Агент вызывает проверку, но ему не нужно самостоятельно разбираться, какую команду Maven запускать и как интерпретировать тысячи строк лога. Verifier выбирается по типу компонента и возвращает компактный результат. Обязательная проверка выполняется и перед созданием PR. Дополнительно LLM-judge проверяет выход за рамки задачи. [Spotify, часть 3](https://engineering.atspotify.com/2025/12/feedback-loops-background-coding-agents-part-3).

**Самая полезная практика OpenAI — сделать проект понятным и наблюдаемым для агента.**

Короткий `AGENTS.md` служит картой; подробные знания лежат в структурированной документации. Планы версионируются. Для каждого worktree можно поднять экземпляр приложения с локальными логами, метриками и трассировками. Архитектурные требования поддерживаются механическими проверками. [OpenAI — Harness Engineering](https://openai.com/index/harness-engineering/).

**Самая полезная практика Anthropic — явно проектировать продолжение работы.**

Первый агент готовит окружение, список функций и журнал прогресса. Следующие сессии восстанавливают состояние, проверяют работоспособность и выполняют небольшое изменение. Есть [открытый пример autonomous-coding](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding).

У Cursor ценно описание неудачных решений: избыток координации создавал узкие места; некоторые роли пришлось убрать. Авторы отдельно отмечают дрейф и необходимость обновления контекста. **Эти эксперименты не являются доказательством готовности универсальной автономной фабрики.** [Cursor](https://cursor.com/blog/scaling-agents).

**9. Открытые реализации и workflow, которые можно разбирать по коду**

<table header-row="true">
<tr>
<td>Проект</td>
<td>Что доступно</td>
<td>На что обратить внимание</td>
</tr>
<tr>
<td>[EPAM DMTools](https://github.com/epam/dm.ai)</td>
<td>CLI, jobs, инструменты, интеграции и конфигурации</td>
<td>Оркестрация существующего delivery-процесса</td>
</tr>
<tr>
<td>[CodeMie](https://github.com/codemie-ai/codemie)</td>
<td>Backend платформы агентов</td>
<td>API, knowledge indexing, multi-agent workflows</td>
</tr>
<tr>
<td>[StrongDM Attractor](https://github.com/strongdm/attractor)</td>
<td>Три спецификации основных частей фабрики</td>
<td>Хороший пример достаточно подробной NLSpec</td>
</tr>
<tr>
<td>[OpenHands SDK](https://docs.openhands.dev/sdk)</td>
<td>Python API, инструменты и agent server</td>
<td>Собственный исполнительный слой; SDK под MIT</td>
</tr>
<tr>
<td>[GitHub Agentic Workflows](https://github.github.com/gh-aw/)</td>
<td>Markdown workflows, компиляция в Actions</td>
<td>Бюджеты, аудит, sandbox и контролируемые write-действия</td>
</tr>
<tr>
<td>[Superpowers](https://github.com/obra/superpowers)</td>
<td>Skills и методология: дизайн, планы, TDD, review, worktrees</td>
<td>Повторно используемый инженерный процесс; поддерживается OpenCode</td>
</tr>
<tr>
<td>[peter-stratton/dark-factory](https://github.com/peter-stratton/dark-factory)</td>
<td>Go CLI `godark`, исполнители и reviewers, Docker sandbox</td>
<td>Конкретная реализация под Claude Code и GitHub; лицензия Elastic License 2.0</td>
</tr>
<tr>
<td>[nickfujita/dark-factory](https://github.com/nickfujita/dark-factory)</td>
<td>Skills, playbooks, hooks и QA-runbooks для Claude Code/Codex</td>
<td>Небольшой community-проект; источник примеров, зрелость требует проверки</td>
</tr>
<tr>
<td>[Anthropic autonomous-coding](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding)</td>
<td>Исполняемый пример длительной разработки</td>
<td>Минимальный образец управления состоянием между сессиями</td>
</tr>
<tr>
<td>[Ralph — Geoffrey Huntley](https://ghuntley.com/ralph/)</td>
<td>Исходное объяснение техники повторяющегося agent loop</td>
<td>Полезно понять механизм итераций; остальные контуры фабрики нужно проектировать отдельно</td>
</tr>
</table>

У **godark** особенно интересны независимые implementer, quality reviewer и functional reviewer, проверка архитектурных ограничений через `godark vet` и журналы review. Авторы заявляют, что reviewers лишены возможности редактировать файлы. Привязка к Claude Code/GitHub существенна для выбора. [Описание реализации](https://github.com/peter-stratton/dark-factory).

У **GitHub Agentic Workflows** стоит отдельно прочитать [Security Architecture](https://github.github.com/gh-aw/introduction/architecture/): агент формирует запрос на действие, а отдельный контролируемый механизм проверяет и применяет разрешённые изменения. Этот архитектурный принцип переносим и в GitLab.

**10. Какие инженерные решения повторяются и действительно полезны**

Следующее — **мой синтез найденных материалов**, а не спецификация какого-либо вендора.

<table header-row="true">
<tr>
<td>Решение</td>
<td>Как реализовать практически</td>
</tr>
<tr>
<td>**Готовность задания к автономному исполнению**</td>
<td>До запуска проверить цель, acceptance criteria, затронутые компоненты, ограничения и неизвестные</td>
</tr>
<tr>
<td>**Контекст конкретного изменения**</td>
<td>Передавать нужные ADR, контракты, примеры и фрагменты документации с указанием версий</td>
</tr>
<tr>
<td>**Ограниченная единица работы**</td>
<td>Одна задача должна давать изменение, которое можно отдельно собрать и проверить</td>
</tr>
<tr>
<td>**Сохраняемое состояние**</td>
<td>Хранить этап, попытки, результаты проверок, commit SHA и причины остановки вне истории чата</td>
</tr>
<tr>
<td>**Воспроизводимое окружение**</td>
<td>На задачу создавать отдельное рабочее пространство с фиксированным toolchain и тестовыми данными</td>
</tr>
<tr>
<td>**Обычные проверки как обязательные gates**</td>
<td>Сборка, типизация, lint, тесты, контракты и security checks определяют статус этапа</td>
</tr>
<tr>
<td>**Независимая приёмка**</td>
<td>Защитить эталонные сценарии и критерии от изменения coding agent</td>
</tr>
<tr>
<td>**Проверка работающего приложения**</td>
<td>Для UI/API проверять реальное поведение, ошибки и побочные эффекты</td>
</tr>
<tr>
<td>**Ограничение циклов**</td>
<td>Задать бюджет, число попыток и условие эскалации; сохранять диагностику при остановке</td>
</tr>
<tr>
<td>**Права по роли**</td>
<td>Разделить доступ к репозиторию, проверкам, secrets, merge и deployment</td>
</tr>
<tr>
<td>**Проверка самой фабрики**</td>
<td>Прогонять фиксированный набор задач после изменения модели, prompts, skills или инструментов</td>
</tr>
<tr>
<td>**Обратная связь после выпуска**</td>
<td>Связывать дефект с исходной задачей и добавлять пропущенный сценарий в приёмку</td>
</tr>
</table>

**Ключевой архитектурный принцип: правила, которые должны исполняться гарантированно, нужно закреплять в правах, коде проверок и CI/CD.** Инструкция в `AGENTS.md` помогает агенту действовать правильно, но сама по себе не обеспечивает запрет.

Например, требование «не ломать обратную совместимость API» должно иметь конкретную проверку контракта и блокировать merge при нарушении.

**11. Какой пилот я бы предложил для корпоративной среды**

Для компании с несколькими продуктовыми командами я бы начал с **одного повторяемого класса работ**, где качество результата легко проверить.

<table header-row="true">
<tr>
<td>Класс работ</td>
<td>Почему удобен для старта</td>
</tr>
<tr>
<td>Обновление зависимостей и SDK</td>
<td>Повторяется во многих репозиториях; есть сборка и regression tests</td>
</tr>
<tr>
<td>Замена устаревшего компонента UI</td>
<td>Есть образец замены и ограниченная область изменений</td>
</tr>
<tr>
<td>Добавление стандартной телеметрии</td>
<td>Можно проверять наличие и корректность сигналов</td>
</tr>
<tr>
<td>Небольшие исправления по воспроизводимому дефекту</td>
<td>Есть сценарий, который должен перестать падать</td>
</tr>
<tr>
<td>Миграция API-клиентов</td>
<td>Можно проверять контракты и поведение интеграции</td>
</tr>
<tr>
<td>Обновление технической документации</td>
<td>Небольшая цена ошибки и удобный вход в процесс</td>
</tr>
</table>

Мой предлагаемый порядок:
1. **Выбрать 20–30 завершённых исторических задач** одного класса и восстановить исходное состояние репозиториев.
2. **Подготовить эталонную приёмку** и зафиксировать ограничения.
3. **Сравнить два варианта исполнения**, например DMTools/OpenHands и один готовый продукт.
4. **Запускать до MR с человеческой проверкой**, измеряя время доводки.
5. **Повторять часть задач несколько раз**, чтобы увидеть разброс результатов.
6. **Перейти к живому backlog** после устранения типовых причин ошибок.
7. Расширять автономию отдельно для каждого класса изменений.

Для первого пилота я бы выбрал конфигурацию:

**задание → проверка готовности → агент в изолированной среде → независимые проверки → MR с доказательствами → решение человека.**

Доказательства — это результаты конкретных тестов, перечень затронутых требований, логи и, при необходимости, скриншоты. Статуса «агент считает задачу выполненной» недостаточно.

**12. Как считать результат и что спрашивать у вендора**

Основная единица экономики — **принятое корректное изменение**.

Я бы измерял:
- долю задач, дошедших до принятого MR;
- долю задач без ручного исправления кода;
- человеческое время на подготовку, review и доводку;
- полное время от задания до принятия;
- стоимость моделей, окружений и повторных запусков;
- дефекты после выпуска и откаты;
- разброс качества при повторении одинаковой задачи.

Практическая формула:

**Стоимость принятого изменения = все затраты на успешные и неуспешные попытки, проверки, человеческую работу и сопровождение фабрики / число принятых изменений.**

Количество PR и строк кода полезно как характеристика объёма, но не доказывает экономический эффект.

Для встречи с EPAM или другим вендором я бы подготовил следующие вопросы:
1. **Что именно вы поставляете:** ПО, managed service, конфигурации или команду внедрения?
2. Покажите полный запуск на существующем репозитории, включая неуспешные попытки.
3. Кто формирует приёмочные сценарии и кто может их изменять?
4. Где остаются обязательные действия человека?
5. Как возобновляется задача после падения runner или истечения контекста?
6. Как обнаруживаются ненужные изменения и отключённые тесты?
7. Как проверяется согласованность требований, архитектуры, кода и контрактов?
8. Какие компоненты и данные остаются в нашем контуре?
9. Что конкретно поддерживается для GitLab Self-Managed, SSO и собственных моделей?
10. Что экспортируется при отказе от продукта: specs, workflows, skills, traces, граф?
11. Как считается стоимость принятой задачи с учётом неудачных запусков?
12. Кто поддерживает фабрику после обновления моделей и инструментов?

**С чего начать чтение**

Если расположить материалы по отдаче от потраченного времени, я бы выбрал такой порядок:
1. [**StrongDM**](https://factory.strongdm.ai/) — понять целевую модель и независимую приёмку.
2. [**Spotify Honk, часть 3**](https://engineering.atspotify.com/2025/12/feedback-loops-background-coding-agents-part-3) — понять, как получать предсказуемые результаты.
3. [**EPAM DMTools**](https://github.com/epam/dm.ai) — увидеть доступные инструменты и workflow.
4. [**Warp: Set up your software factory**](https://docs.warp.dev/guides/agent-workflows/set-up-a-software-factory) — разобрать последовательность сборки.
5. [**8090 Introduction**](https://docs.8090.ai/general/introduction) — посмотреть на требования и архитектуру как связанный контекст.
6. [**Thoughtworks Technical Guide**](https://www.thoughtworks.com/en-us/ai/works/technical-guide) — составить карту возможностей enterprise-платформы.
7. [**OpenAI Harness Engineering**](https://openai.com/index/harness-engineering/) — спроектировать репозиторий и окружение для агентной работы.
8. [**OpenHands SDK**](https://docs.openhands.dev/sdk) — оценить исполнительную основу собственной реализации.

Могу предложить ежемесячную проверку новых реализаций и перехода Factory/Warp из preview, чтобы эта подборка оставалась актуальной.
