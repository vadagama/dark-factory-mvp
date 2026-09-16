# Small UIKit (`packs/ui`) — React-кит с UI-гейтами

Small UIKit — версионированный UI-слой пилотных продуктов (ADR-014, задача
T042 / `docs/plan.md` T-071): React-примитивы поверх Radix с адаптированным
shadcn-слоем, токены DTCG, Storybook как исполняемая UI-спека и четыре
машиночитаемых гейта качества. Пак живёт в `packs/ui/` (манифест `pack.yaml`,
правила `rules.md`, blueprint `blueprint/ui/`); Figma и Web Components — вне
MVP (ADR-014 п.5), Console фабрики UI-кит продуктов не определяет (ADR-014
п.4). Storybook — 10.x, Node 22 (`.nvmrc`).

## Состав

| Компоненты (12) | Паттерны (5) |
|---|---|
| Alert, Badge, Button, Card, Checkbox, Dialog, Input, Select, Spinner, Switch, Tabs, Textarea | ConfirmDialog, EmptyState, FormField, StatusBadge, Toolbar |

Паттерны — композиции над компонентами (ConfirmDialog строится на Dialog,
FormField объединяет label/подсказку/ошибку с Input или Textarea и т.д.).
Каждый компонент и паттерн имеет историю Storybook — всего 45 историй, они же
являются входом visual-гейта.

## Токены DTCG и генерация

Источник — `tokens/*.tokens.json` (color, radius, space, typography) в формате
DTCG. Генерация: `npm run tokens:build` (`scripts/build-tokens.mjs`) создаёт
`src/tokens.css` (CSS-переменные `--small-*`) и `src/tokens.ts` (типизированный
объект `tokens`). Генерат руками не редактируется; дрейф генерата против
источника ловит vitest drift-тест. В рукописном CSS цвета задаются только через
`var(--small-*)` — hex/именованные цвета блокирует stylelint; единственное
исключение — сам сгенерированный `src/tokens.css`.

## Публичная поверхность

Продукт импортирует кит только из корня пакета:

```ts
import { Button, FormField } from "@small/ui";
import "@small/ui/tokens.css";
import "@small/ui/styles.css";
```

Deep-импорты `@small/ui/...` и прямые импорты `@radix-ui/*` в продуктовом коде
запрещены ESLint-политикой `policy/eslint-small-ui.mjs` (подключается в flat
config продуктового репозитория; корректность самой политики доказывается
фикстурами: `npm run test:gates` линтит `policy/fixtures/` — 9 фикстур,
нарушения блокируются, чистый код проходит). Всё вне `src/index.ts` не покрыто
гейтами и visual-бейзлайнами кита.

## Storybook — исполняемая спека

`.storybook/` (autodocs) документирует токены, компоненты и паттерны историями;
сборка `npm run storybook:build` — обязательный гейт CI (DoD T-071). Построенный
`storybook-static` — единственный источник снимков visual-гейта: спека и
пиксельные бейзлайны не могут разъехаться.

## Четыре гейта и как они блокируют

| Гейт | Что проверяет | Где исполняется |
|---|---|---|
| UIKit-policy (ESLint) | импорты только из корня `@small/ui`; Radix — только внутри `src/components/**` кита | `npm run lint` + самотест `test:gates` |
| stylelint + токены | цвета только `var(--small-*)`; drift `tokens.css` против DTCG-источника | `npm run lint` + vitest drift-тест |
| axe / WCAG 2.2 AA | автоматизируемые axe-violations на всех компонентах (jsdom), программный контраст | vitest a11y-сюита (`tests/a11y.test.tsx`) |
| Visual regression | каждая из 45 историй против коммиченного бейзлайна (Playwright, chromium) | `npm run test:visual` |

Локально всё это — `npm run lint && npm run typecheck && npm run test && npm
run test:gates`. Неавтоматизируемые критерии WCAG 2.2 AA (контраст
нестандартных состояний, фокус-порядок в живом приложении, скринридер-сценарии,
понятность текстов ошибок) подтверждает человек как UI evidence — checklist в
`rules.md` (ADR-014 п.3, ADR-018 п.1).

## Детерминизм visual-гейта

Контракт зафиксирован в `packs/ui/rules.md` и воспроизведён в
`playwright.config.ts`: браузерный образ в CI закреплён
(`mcr.microsoft.com/playwright`, тег = версии `@playwright/test` из
package-lock, `v1.63.0-noble`; в фабричном CI тег дополнительно пиннится
дайджестом), фиксированные viewport/locale/timezone/colorScheme/reducedMotion,
анимации отключены, порог `maxDiffPixelRatio 0.01`. Бейзлайны коммятся рядом со
спекой без платформенного суффикса и обновляются только через ревью —
регенерация в закреплённом контейнере. Это делает бейзлайны воспроизводимыми
локально и в CI.

## Интеграция в web-app pack

Blueprint кита копируется в продуктовый репозиторий как workspace-пакет
`frontend/packages/ui` (`packs/web-app`, с версии 0.2.0 копия уже внутри
blueprint). Побайтовый паритет копии с `packs/ui/blueprint/ui/` проверяет
`tests/test_packs_ui.py` — дрейф шаблона ломает CI фабрики. Продуктовый
frontend даёт скрипты-обёртки `ui:lint / ui:typecheck / ui:test / ui:gates /
ui:storybook:build` (через npm workspaces), ESLint-политика кита подключена к
коду приложения, в продуктовом CI есть джоба `frontend-ui-gates`. Visual-гейт в
продуктовый CI не входит — он закреплён за фабричным CI.

Фабричный CI (`.github/workflows/ci.yml`) гоняет кит в `packs/ui/blueprint/ui`:
`uikit-lint`, `uikit-typecheck`, `uikit-test` (55 vitest-тестов), `uikit-gates`,
`uikit-storybook` и `uikit-visual` — последняя в закреплённом контейнере
Playwright (`npm run test:visual`, webServer поднимается конфигурацией сам).

## Известные ограничения

- Коммиченные бейзлайны visual-гейта действительны только для закреплённого
  контейнера: Mac-бейзлайны из рабочего дерева в репозиторий не попадают,
  каноничные генерируются в контейнере (оркестратор).
- Дайджест-пиннинг контейнерного тега `v1.63.0-noble` в фабричном CI добавляет
  оркестратор; в репозитории пока тег с комментарием.
- WCAG 2.2 AA не автоматизируется целиком — часть критериев подтверждается
  человеком при приёмке UI-паттерна (checklist в `rules.md`).
