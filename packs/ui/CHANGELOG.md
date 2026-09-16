# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

## [0.1.0] - 2026-09-16

### Added

- Начальная версия пака (T042, docs plan T-071, ADR-014).
- Small UIKit `@small/ui`: 12 компонентов (Alert, Badge, Button, Card,
  Checkbox, Dialog, Input, Select, Spinner, Switch, Tabs, Textarea) и
  5 паттернов (ConfirmDialog, EmptyState, FormField, StatusBadge, Toolbar)
  поверх Radix с адаптированным shadcn-слоем.
- Дизайн-токены DTCG (`tokens/*.tokens.json` → `src/tokens.css` +
  `src/tokens.ts` через `scripts/build-tokens.mjs`); дисциплина: цвета в CSS
  только через `var(--small-*)`, генерат под drift-тестом.
- Storybook как исполняемая UI-спека: истории компонентов и паттернов,
  сборка в CI (`storybook:build`), все истории автоматически покрываются
  visual regression.
- UI-гейты: UIKit-policy ESLint (`policy/eslint-small-ui.mjs` — запрет
  deep-импортов `@small/ui/*` и прямых импортов `@radix-ui/*` в продукте),
  stylelint (токеновая дисциплина), axe a11y-тесты (WCAG 2.2 AA
  автоматизируемая часть), visual regression Playwright (детерминизм —
  `rules.md`), самотест гейтов (`test:gates` — нарушения блокируются).
- `rules.md`: контракт детерминизма visual regression, дисциплина токенов,
  правило публичной поверхности, WCAG 2.2 AA acceptance checklist
  (неавтоматизируемые критерии принимает человек).
