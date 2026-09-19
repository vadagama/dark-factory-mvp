# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

## [0.2.0] - 2026-09-19

### Added

- Шаблоны фаз архитектуры и интерфейса ChangeSet (M3, T092/T094, ADR-032/ADR-035):
  `changeset/design/overview.md` получает `ui: required | not_required` и
  `ui_reason` во frontmatter и тело `## Обзор` / mermaid / `## Компоненты` /
  `## Решения`; примеры `design/decisions/ADR-001-example.md` (frontmatter
  `impact`, секции Контекст / Решение / Обоснование / Альтернативы / Последствия,
  `status: proposed`), `design/ui/scenarios/SCN-001-example.md` (`steps` с
  `id/text/screen`) и `design/ui/screens/SCR-001-example.md` (`route`,
  `preview_url`, пять `states`, `elements` с `EL-*` и компонентами UIKit,
  `transitions`). Идентификаторы `SCN-*`, `SCR-*`, `EL-*`, `S<n>` — стабильные
  якоря комментариев.

## [0.1.0] - 2026-09-19

### Added

- Манифест пака `pack.yaml` и версия 0.1.0 (T069, ADR-031 p.1/p.6).
- `bootstrap_baseline` применяет поддерево `baseline/` как `.factory/`
  продуктового репозитория: пустой репозиторий (unborn HEAD) — штатный случай,
  повтор идемпотентен, результат — evidence (ревизия, применённые паки и версии).
