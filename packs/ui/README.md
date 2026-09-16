# Пак `ui` — Small UIKit (ADR-014)

Версионированный пак UI-слоя пилотных продуктов (T042, `docs/plan.md` T-071):
Small UIKit на React — примитивы поверх Radix с адаптированным shadcn-слоем,
токены DTCG, Storybook как исполняемая UI-спека и четыре машиночитаемых
UI-гейта. Figma и Web Components — вне MVP (ADR-014 п.5). Console фабрики
UI-кит продуктов не определяет (ADR-014 п.4).

Пак — данные, не код (ADR-015 п.5): blueprint копируется в продуктовый
репозиторий как есть и валидируется тестами фабрики (`tests/test_packs_ui.py` —
включая побайтовый паритет с `packs/ui/blueprint/ui/`), поэтому дрейф шаблона
ломает CI фабрики, а не продукта.

## Состав blueprint

```text
blueprint/ui/
├── package.json            # @small/ui: экспорты корень + styles.css + tokens.css
├── tokens/                 # источник токенов DTCG (color/radius/space/typography)
├── src/
│   ├── index.ts            # публичная поверхность: 12 компонентов + 5 паттернов + tokens
│   ├── tokens.css/.ts      # генерат из tokens/*.tokens.json (не редактировать руками)
│   ├── styles.css          # стили кита (BEM small-*)
│   ├── components/         # 12 компонентов + истории
│   └── patterns/           # 5 паттернов + истории
├── .storybook/             # Storybook 10: исполняемая спека (autodocs)
├── policy/                 # продуктовая ESLint-политика + fixtures для самотеста
├── scripts/                # build-tokens, static-server, test-gates (без зависимостей)
├── tests/                  # vitest: компоненты, axe a11y, drift токенов, WCAG-контраст
├── tests/visual/           # Playwright visual regression по всем историям Storybook
├── eslint.config.js        # линтер кита (Radix только в src/components/**)
├── stylelint.config.js     # токеновая дисциплина CSS
├── playwright.config.ts    # детерминизм visual-гейта
└── vitest.config.ts / tsconfig.json / .nvmrc / .gitignore
```

Компоненты (12): Alert, Badge, Button, Card, Checkbox, Dialog, Input, Select,
Spinner, Switch, Tabs, Textarea. Паттерны (5): ConfirmDialog, EmptyState,
FormField, StatusBadge, Toolbar. Полное описание — `docs/descriptions/ui-kit.md`
фабричного репозитория.

## UI-гейты

| Гейт | Что проверяет | Как блокирует |
|---|---|---|
| UIKit-policy (ESLint) | импорт только из корня `@small/ui`, запрет прямых импортов `@radix-ui/*` | `lint` + самотест `test:gates` (фикстуры: нарушения блокируются, чистый код проходит) |
| stylelint + токены | цвета только `var(--small-*)`; hex/именованные цвета вне генерата запрещены; drift `tokens.css` против DTCG-источника | `lint` + vitest drift-тест |
| axe / WCAG 2.2 AA | автоматизируемые violations на всех компонентах (jsdom) | vitest a11y-сюита |
| Visual regression | каждая история Storybook против коммиченного бейзлайна | `npm run test:visual`; детерминизм — `rules.md` |

Неавтоматизируемые критерии WCAG 2.2 AA (контраст нестандартных состояний,
фокус-порядок в живом приложении, скринридер-сценарии, понятность текстов
ошибок) подтверждает человек как UI evidence — checklist в `rules.md`
(ADR-014 п.3, ADR-018 п.1).

## Как применить

1. Скопировать `blueprint/ui/` в продуктовый репозиторий как workspace-пакет
   `frontend/packages/ui` (пак `packs/web-app` уже содержит актуальную копию и
   подключает гейты — см. его `blueprint/frontend/package.json`, `eslint.config.js`
   и CI).
2. Импортировать только из корня: `import { Button } from "@small/ui"`, стили —
   `@small/ui/tokens.css`, затем `@small/ui/styles.css`.
3. Токены менять только в `tokens/*.tokens.json` с последующим
   `npm run tokens:build`; коммитить генерат вместе с источником.
4. Изменение визуала: регенерировать бейзлайны только в закреплённом
   браузерном контейнере (`npm run test:visual -- --update-snapshots`) и
   проводить их через ревью — контракт в `rules.md`.

## Версионирование

SemVer в `pack.yaml.version` = запись в `CHANGELOG.md`; изменения шаблонов
требуют bump версии и записи в CHANGELOG (правила паков —
`packs/product-baseline/README.md`, ADR-015 п.5). Валидация консистентности —
тесты фабрики.
