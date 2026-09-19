# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

## [0.1.0] - 2026-09-19

### Added

- Манифест пака `pack.yaml` и версия 0.1.0 (T069, ADR-031 p.1/p.6).
- `bootstrap_baseline` применяет поддерево `baseline/` как `.factory/`
  продуктового репозитория: пустой репозиторий (unborn HEAD) — штатный случай,
  повтор идемпотентен, результат — evidence (ревизия, применённые паки и версии).
