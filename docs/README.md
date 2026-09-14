# Документация проекта

Индекс документации **dark-factory-mvp**. Поддерживается в актуальном состоянии всеми агентами, работающими в репозитории.

## Структура

| Файл / папка | Назначение | Кто создаёт |
|---|---|---|
| `vision-<дата>-<версия>.md` | Видение продукта, дорожная карта, скоуп MVP | `product` |
| `plan.md` | План работ (приоритизированный бэклог) | `product` |
| `development-workflow.md` | Git-цикл задачи: ветка → проверка → MR; merge — человек | `dark-factory` / `ci-cd` |
| `hld.md` | HLD: актуальная архитектура, связывает все ADR | `architect` |
| `sdd-native-core.md` | Каноническая модель SDD: Native SDD Core (ChangeSet, Product Baseline, OKF) | `architect` |
| `architecture-target.md` | Целевая архитектура, health score | `architect` |
| `tech-dept.md` | Реестр технического долга | `architect` |
| `adr/` | Архитектурные решения (ADR) | `architect` |
| `descriptions/` | Понятные описания реализованных модулей, контрактов и runtime-механизмов | `architect` |
| `instructions/` | Пошаговые ручные инструкции для людей: как прогнать реализованное своими руками | `dark-factory` |

## Вне `docs/` — связанные артефакты

| Путь | Назначение |
|---|---|
| `AGENTS.md` | Правила для агентов: роли, конвейер, DoD, структура репозитория |
| `.factory/` | Канонический SDD-слой Native SDD Core (ADR-020): `product/` baseline + `changes/`. Создаётся в T-020 |
| `.agents/skills/` | Локальные скиллы: `dark-factory` (оркестратор), `speckit-*` (SDD bootstrap-фазы, ADR-001) |
| `.specify/` | Spec Kit bootstrap-фазы: шаблоны, скрипты, `memory/constitution.md` (ADR-001) |
| `specs/` | Артефакты фич Spec Kit bootstrap-фазы; после перехода на Native SDD Core — historical bootstrap evidence |
| `packs/` | Шаблоны Product Baseline и ChangeSet для продуктовых репозиториев (ADR-020) |

## Правила ведения

- Документация на русском, ёмко, без воды.
- Один документ — одна тема. Крупные темы разбивать на разделы с якорями.
- Даты и версии — в именах файлов (`vision-2026-09-12-v1.md`).
- Каждое значимое техническое решение — ADR в `docs/adr/` (см. `docs/adr/README.md`).
- Перед внесением правок читать свежие документы, чтобы не плодить противоречия.
