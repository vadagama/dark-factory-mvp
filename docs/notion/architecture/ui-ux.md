<!--
Источник: Notion — UI/UX
URL: https://app.notion.com/p/3d6db33037c8806e8e41c5a7ca792e06
Выгружено: 2026-09-12
-->

# UI/UX

Да. Для внутренних систем SMALL отказ от Figma не только реалистичен, но и хорошо соответствует Dark Factory: макет, прототип и конечный UI становятся одним и тем же версионируемым React-кодом.

Целевая модель:

> Plane-задача → UX-контракт → композиция из Small UIKit → Storybook-прототип → Review App → реализация → автоматические quality gates → релиз.

При этом `Small UIKit` должен быть не просто библиотекой кнопок, а полноценной исполняемой дизайн-системой.

## 1. Чем заменить Figma

<table header-row="true">
<tr>
<td>Функция Figma</td>
<td>Замена в Dark Factory</td>
</tr>
<tr>
<td>Визуальные стили</td>
<td>Design Tokens в Git</td>
</tr>
<tr>
<td>Каталог компонентов</td>
<td>Storybook</td>
</tr>
<tr>
<td>Макеты экранов</td>
<td>Page Stories на React</td>
</tr>
<tr>
<td>Кликабельный прототип</td>
<td>Storybook + MSW</td>
</tr>
<tr>
<td>Демонстрация заказчику</td>
<td>GitLab Review App</td>
</tr>
<tr>
<td>Комментарии и согласование</td>
<td>Plane + GitLab MR</td>
</tr>
<tr>
<td>Передача дизайна разработчику</td>
<td>Не требуется: прототип становится рабочим кодом</td>
</tr>
<tr>
<td>Контроль визуальных изменений</td>
<td>Playwright screenshot comparison</td>
</tr>
<tr>
<td>Документация компонентов</td>
<td>Storybook Autodocs, stories и JSDoc</td>
</tr>
</table>

[GitLab Review Apps](https://docs.gitlab.com/ci/review_apps/) создают отдельный временный URL для каждого MR. Заказчик или UX-ответственный работает уже с настоящим интерактивным интерфейсом, а не с картинкой.

## 2. Каким должен быть Small UIKit

Приложения фабрики должны импортировать только публичный пакет:

```typescript
import {
  Page,
  Stack,
  DataTable,
  Form,
  Button,
  ConfirmDialog
} from '@small/ui';
```

Прямые импорты из MUI, Ant Design, Headless UI и других библиотек должны быть запрещены. Какая библиотека используется внутри `@small/ui`, для приложений неважно.

Полноценный UIKit должен иметь следующие уровни.

<table header-row="true">
<tr>
<td>Уровень</td>
<td>Что включить</td>
</tr>
<tr>
<td>Tokens</td>
<td>Цвета, типографика, интервалы, размеры, радиусы, тени, breakpoints, motion</td>
</tr>
<tr>
<td>Layout primitives</td>
<td>`Page`, `Stack`, `Inline`, `Grid`, `Section`, `Sidebar`, `Toolbar`</td>
</tr>
<tr>
<td>Components</td>
<td>Поля, кнопки, таблицы, меню, диалоги, уведомления</td>
</tr>
<tr>
<td>Patterns</td>
<td>Поиск, фильтры, bulk actions, формы, мастер-деталь, wizard</td>
</tr>
<tr>
<td>Templates</td>
<td>Список, карточка объекта, редактирование, dashboard, настройки</td>
</tr>
<tr>
<td>Application shell</td>
<td>Навигация, header, breadcrumbs, права, профиль</td>
</tr>
<tr>
<td>System states</td>
<td>Loading, empty, error, forbidden, offline, partial data</td>
</tr>
<tr>
<td>Guidance</td>
<td>Когда применять компонент, ограничения, хорошие и плохие примеры</td>
</tr>
<tr>
<td>Contracts</td>
<td>Props, события, accessibility и responsive-поведение</td>
</tr>
<tr>
<td>Tests</td>
<td>Stories, interactions, visual baselines, a11y-проверки</td>
</tr>
</table>

Токены лучше хранить в стандартном DTCG JSON-формате. Он рассчитан именно на машиночитаемое хранение дизайн-решений вне конкретного редактора и считается стабильным для реализации: [Design Tokens Format Module](https://www.designtokens.org/tr/2025.10/format/).

Критически важно добавить в UIKit не только атомарные компоненты, но и готовые бизнес-паттерны. Иначе агент будет правильно использовать кнопки и поля, но плохо компоновать целые страницы.

## 3. Как связать UIKit со скиллами

Нужно разделить три разных механизма:

<table header-row="true">
<tr>
<td>Механизм</td>
<td>За что отвечает</td>
</tr>
<tr>
<td>Skill</td>
<td>Как агент должен проектировать UI и какой процесс соблюдать</td>
</tr>
<tr>
<td>Storybook/MCP</td>
<td>Какие компоненты, props и примеры реально существуют сейчас</td>
</tr>
<tr>
<td>ESLint/CI</td>
<td>Что агенту физически разрешено закоммитить</td>
</tr>
</table>

Не следует копировать полный каталог компонентов внутрь скилла — он быстро устареет.

В скилле должно быть правило:
1. Прочитать UX-контракт.
2. Запросить актуальные компоненты и patterns из Storybook.
3. Не придумывать неизвестные props.
4. Не создавать собственный компонент, пока не доказано отсутствие подходящего в UIKit.
5. Создать stories для всех существенных состояний.
6. Выполнить тесты.
7. Исправлять результат до прохождения gates.
8. Эскалировать `UIKit gap`, если нужного паттерна действительно нет.

Storybook уже предоставляет components manifest и MCP-интерфейс, через который агент может получать документацию компонентов, находить stories, создавать previews и запускать тесты. Это практически идеально соответствует Dark Factory. Однако сейчас эта возможность имеет статус preview, поэтому я бы спрятал её за собственным `UI Knowledge Provider` интерфейсом и зафиксировал версию Storybook. [Storybook MCP](https://storybook.js.org/docs/ai/mcp/overview), [рекомендации Storybook для AI](https://storybook.js.org/docs/ai/best-practices).

## 4. Что нужно предоставить Dark Factory на вход

Вход разделяется на настройку платформы и описание конкретной задачи.

### Один раз для всей фабрики

Необходимо подготовить:
- репозиторий `small-uikit`;
- design tokens и темы;
- Application Shell;
- стандартные page templates и UX-patterns;
- поддерживаемые устройства, браузеры и размеры экранов;
- требования по accessibility;
- корпоративную терминологию и правила текстов;
- правила навигации, форм, подтверждений и ошибок;
- хорошие примеры и запрещённые антипаттерны;
- правила, когда необходимо согласование человеком;
- каталог ролей и прав в OKF;
- версионируемые UI-скиллы.

Особенно важно первоначально утвердить 10–15 базовых паттернов: список, таблица, фильтрация, карточка, форма, wizard, master-detail, поиск, bulk operations, confirmation, empty/error/loading/offline и permission denied.

### Для каждой задачи в Plane

Заказчик не должен описывать расположение кнопок. На вход нужны:

```yaml
actor: Директор магазина
goal: Создать накладную списания
trigger: Обнаружен товар, который необходимо списать

main_flow:
  - выбрать товары
  - указать количество и причину
  - проверить документ
  - отправить на проведение

business_rules:
  - количество не может превышать доступный остаток
  - причина списания обязательна
  - после проведения редактирование запрещено

important_states:
  - отсутствуют товары
  - недостаточный остаток
  - сервер недоступен
  - нет права на проведение

devices:
  - desktop
  - TSD

success:
  - пользователь создаёт корректный документ без обращения к инструкции
```

Минимально достаточно пяти вещей:
1. Кто пользователь.
2. Какую задачу он хочет выполнить.
3. Как выглядит основной сценарий.
4. Какие есть бизнес-ограничения.
5. По какому признаку результат считается успешным.

Остальные сведения агент получает из OKF, OpenAPI, существующих экранов и UIKit. Недостающие существенные решения превращаются в вопросы `RefinementWorkflow`.

## 5. Как будет проходить UI/UX-разработка

### 1. UI Intake

Plane issue поступает в `RefinementWorkflow`.

PydanticAI преобразует описание в типизированную модель `UIIntent`:
- actor;
- goal;
- user journey;
- данные и действия;
- ограничения;
- устройства;
- критичность;
- критерии успеха.

### 2. UX Specification

UX-агент использует OKF и формирует в OpenSpec:
- user flow;
- информационную структуру;
- список экранов;
- состояния каждого экрана;
- поведение полей и действий;
- тексты ошибок;
- UX acceptance criteria;
- accessibility-требования;
- список необходимых tests/stories.

Требования должны быть проверяемыми. Например, не «форма должна быть удобной», а:
- ошибки отображаются рядом с соответствующими полями;
- введённые данные не теряются после серверной ошибки;
- основное действие доступно с клавиатуры;
- destructive action требует подтверждения;
- экран не имеет горизонтального scroll на целевых устройствах.

### 3. UI Composition

UI Composer:
1. Запрашивает Storybook manifest.
2. Находит подходящие templates, patterns и components.
3. Создаёт страницу на React.
4. Использует только `@small/ui`.
5. Создаёт stories для всех необходимых состояний.
6. Подключает API-fixtures через MSW.

[MSW](https://mswjs.io/docs/) позволяет теми же декларативными mocks моделировать ответы API в браузере и тестовой среде.

Если подходящего паттерна нет, агент не пишет локальный аналог. Он создаёт `UIKit Gap`:

```plain text
Application issue
    └── UIKit gap
          └── UIKit MR
                └── Новый релиз @small/ui
                      └── Продолжение application issue
```

### 4. Живой UX-прототип

GitLab публикует:
- Storybook с нужными stories;
- Review App приложения;
- ссылку из Plane;
- перечень UX acceptance criteria.

Это точка UX-согласования. Согласование требуется только для нового или рискованного UX. Обычные страницы, собранные из утверждённых patterns, могут двигаться автоматически.

### 5. Implementation

После принятия прототипа агент подключает:
- реальные API;
- авторизацию и permissions;
- маршрутизацию;
- обработку ошибок;
- аналитику событий;
- production data states.

Прототип не выбрасывается — его код и stories становятся частью продукта.

### 6. Verification и rework loop

Temporal запускает проверки и ограниченный цикл исправлений:

```plain text
test → classify failure → fix → rerun
```

После исчерпания допустимого количества попыток задача передаётся человеку с диагностикой, а не продолжает бесконечно изменять UI.

### 7. Release

После merge:
- обновляется связь Plane → OpenSpec → MR → tests;
- публикуется новая версия;
- visual baseline изменяется только после отдельного approval;
- реальные ошибки и UX-метрики возвращаются в Plane.

## 6. Необходимые UI quality gates

<table header-row="true">
<tr>
<td>Gate</td>
<td>Инструмент</td>
<td>Что блокирует merge</td>
</tr>
<tr>
<td>UIKit Policy</td>
<td>собственный ESLint plugin</td>
<td>Прямые импорты внешнего UI Kit, неизвестные компоненты, raw controls</td>
</tr>
<tr>
<td>Style Policy</td>
<td>Stylelint</td>
<td>HEX/RGB, произвольные отступы, обход tokens, локальные переопределения UIKit</td>
</tr>
<tr>
<td>Type safety</td>
<td>TypeScript strict</td>
<td>Ошибки типов и неизвестные props</td>
</tr>
<tr>
<td>Component tests</td>
<td>Storybook Test + Vitest</td>
<td>Компонент не рендерится или неправильно реагирует</td>
</tr>
<tr>
<td>User-oriented tests</td>
<td>Testing Library</td>
<td>Элементы нельзя найти по роли/label, нарушена семантика</td>
</tr>
<tr>
<td>State coverage</td>
<td>собственный validator</td>
<td>Для состояния из UX-контракта отсутствует story/test</td>
</tr>
<tr>
<td>API states</td>
<td>MSW</td>
<td>Не обработаны error, empty, timeout, partial response</td>
</tr>
<tr>
<td>Accessibility</td>
<td>axe-core + Storybook + Playwright</td>
<td>Новые WCAG 2.2 A/AA нарушения</td>
</tr>
<tr>
<td>Visual regression</td>
<td>Playwright screenshots</td>
<td>Необъяснённое изменение пикселей</td>
</tr>
<tr>
<td>Functional E2E</td>
<td>Playwright</td>
<td>Не проходит пользовательский сценарий</td>
</tr>
<tr>
<td>Performance</td>
<td>Lighthouse CI</td>
<td>Нарушен performance или bundle budget</td>
</tr>
<tr>
<td>UX review</td>
<td>multimodal evaluator</td>
<td>Низкая оценка или нарушение UX-rules требует human review</td>
</tr>
</table>

Playwright умеет хранить эталонные скриншоты и сравнивать следующие версии с baseline. Для воспроизводимости браузер, ОС, шрифты и настройки должны быть закреплены в одном container image: [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots).

Storybook accessibility addon и `@axe-core/playwright` следует использовать одновременно на уровне components и страниц. Автоматическая проверка не покрывает все UX/a11y-проблемы, поэтому для новых и критичных patterns остаётся ручная проверка: [Storybook accessibility testing](https://storybook.js.org/docs/writing-tests/accessibility-testing), [Playwright accessibility testing](https://playwright.dev/docs/accessibility-testing).

Для защиты производительности можно задавать budgets и блокировать регрессии через [Lighthouse CI](https://github.com/GoogleChrome/lighthouse-ci).

## 7. Как не допустить разрушения UI от версии к версии

Нужны следующие правила:
- UIKit выпускается по SemVer.
- Каждое изменение сопровождается Changeset и migration note.
- Версия UIKit в приложении зафиксирована lock-файлом.
- Обновление UIKit выполняется отдельным MR.
- Перед выпуском UIKit прогоняются visual tests всех его components и reference pages.
- Для крупных изменений запускаются consumer tests нескольких эталонных приложений.
- Visual baseline не может автоматически обновлять тот же агент, который написал код.
- Изменение tokens, public API и baseline требует CODEOWNERS approval.
- Major-версия никогда не обновляется автоматически.
- Deprecated components остаются доступными на период миграции.
- В MR прикладываются `expected / actual / diff`.
- Полный browser/device matrix запускается nightly, а в MR — только affected stories.

Для управления версиями monorepo подходит [Changesets](https://changesets-docs.vercel.app/): изменения версии и changelog хранятся вместе с кодом и автоматизируются через CI.

## 8. Как оценивать именно UX

UX нельзя гарантировать только тестами. Здесь должно быть три уровня контроля:
1. **Детерминированный:** все ли состояния реализованы, доступны ли действия, соблюдены ли правила UIKit.
2. **Экспертно-эвристический:** AI UX Reviewer анализирует screenshot, DOM, user flow и UX-rubric.
3. **Фактический:** пользователь действительно смог выполнить задачу — это определяется человеком и production-метриками.

AI UX Reviewer может проверять:
- понятность основного действия;
- визуальную иерархию;
- перегруженность;
- последовательность шагов;
- качество ошибок;
- соответствие терминологии;
- консистентность с похожими экранами;
- лишние действия;
- потенциально опасные сценарии.

Результат лучше хранить как типизированный `UXReviewResult` через PydanticAI. Низкая оценка должна не автоматически отвергать UI, а переводить его на human review.

## 9. Требуемые UI-скиллы

Я бы начал с пяти:

<table header-row="true">
<tr>
<td>Skill</td>
<td>Ответственность</td>
</tr>
<tr>
<td>`small-ui-spec`</td>
<td>Превращает задачу в user flow, screen states и UX acceptance criteria</td>
</tr>
<tr>
<td>`small-ui-compose`</td>
<td>Собирает UI исключительно из Small UIKit</td>
</tr>
<tr>
<td>`small-ui-test`</td>
<td>Генерирует stories, fixtures, interaction, a11y, visual и E2E tests</td>
</tr>
<tr>
<td>`small-ux-review`</td>
<td>Проверяет UX-rules, требования и консистентность</td>
</tr>
<tr>
<td>`small-uikit-maintain`</td>
<td>Обрабатывает UIKit gaps, новые patterns, tokens и migrations</td>
</tr>
</table>

Исходники скиллов лучше хранить рядом с UIKit, а публиковать как отдельный версионируемый пакет. У каждого релиза скилла должна быть совместимость:

```yaml
uikit:
  min_version: 3.4.0
  max_version: 3.x
```

## 10. Итоговая роль компонентов Dark Factory

- **Plane** — постановка задачи, вопросы, статус и UX approval.
- **OpenSpec** — UX-контракт и acceptance criteria.
- **OKF** — роли, терминология, бизнес-правила и связи с системами.
- **PydanticAI** — UI/UX-агенты и типизированные результаты.
- **Storybook + MCP** — актуальные знания о UIKit, прототипы и component tests.
- **MSW** — состояния API без готового backend.
- **GitLab Review Apps** — кликабельный интерфейс для согласования.
- **Playwright/axe/Lighthouse** — объективные quality gates.
- **Temporal** — управление UI workflow, retries и human checkpoints.
- **Langfuse** — наблюдаемость решений и качества AI-ревью.
- **Sentry/RUM** — ошибки и UX-сигналы после релиза.

Главное архитектурное решение: **агент не рисует интерфейс с нуля — он собирает его из утверждённых элементов и patterns**. Figma при этом действительно становится не нужна, потому что роль визуального источника истины выполняют Small UIKit и Storybook, а роль интерактивного макета — реальный React-код в Review App.
