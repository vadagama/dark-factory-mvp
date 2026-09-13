# dark-factory-mvp

**Software Dark Factory** — MVP «тёмной фабрики» разработки ПО: конвейер агентной разработки, в котором специализированные роли (product, design, architect, infrastructure, security, develop, quality, CI/CD, operation) превращают задачу в работающий, протестированный и задокументированный код с минимальным участием человека.

## Статус

Pre-MVP. Каркас кода и базовый CI настроены (T-002); стек зафиксирован в ADR-002.

## Инфраструктура агентной разработки

| Компонент | Назначение |
|---|---|
| `AGENTS.md` | Правила и контекст для всех ИИ-агентов; читается автоматически в начале каждой сессии |
| `.agents/skills/dark-factory/` | Локальный скилл-оркестратор конвейера (в git — общий для команды) |
| `~/.agents/skills/` | Глобальные скиллы-роли: `product`, `design`, `architect`, `infrastructure`, `security`, `develop`, `quality`, `ci-cd`, `operation`, `yandex-cloud-engineer` |
| `.zed/settings.json` | Настройки редактора Zed (форматирование, сканирование файлов) |
| `docs/` | Проектная документация: индекс, ADR; `vision-*.md`, `plan.md`, `hld.md` создаются ролями |
| `.editorconfig`, `.gitignore` | Гигиена репозитория |

## Как устроен конвейер

```
задача → dark-factory (классификация, план)
        → product (видение, скоуп)
        → architect (ADR, архитектура)
        → ci-cd (ветка задачи от main)
        → develop (код, тесты)
        → проверка (диагностика, тесты, lint/typecheck; CI на MR)
        → ci-cd (MR против main)
        → merge — человек (ADR-011) → приёмка по DoD (AGENTS.md)
```

Каждая роль — скилл с инструкциями; оркестратор выбирает маршрут под тип задачи и делегирует этапы субагентам. Реализация идёт в ветке задачи и завершается MR; merge выполняет человек (ADR-011). Правила, Definition of Done и структура репозитория — в `AGENTS.md`; git-цикл задачи — в `docs/development-workflow.md`.

## Быстрый старт

1. Сформулируй задачу в Zed-агенте — оркестратор подберёт роли и запустит конвейер.
2. Продуктовые вопросы и идеи без ТЗ проходят через `product` (видение и план появляются в `docs/`).
3. Значимые технические решения фиксируются в `docs/adr/`.
4. Задачи с кодом идут в ветке задачи и завершаются MR; merge в `main` — за человеком (`docs/development-workflow.md`).
5. Каркас кода: `src/dark_factory/` (модули HLD), тесты в `tests/`, CI — `.github/workflows/ci.yml`.

## Разработка

Управление окружением — [uv](https://docs.astral.sh/uv/), Python 3.12:

```sh
uv sync                         # создать .venv, установить зависимости (uv.lock коммитится)
uv run pytest                   # тесты, включая проверку границ импортов (ADR-015 п.3)
uv run ruff check .             # линт
uv run ruff format --check .    # проверка форматирования
uv run mypy .                   # строгая типизация core
```

CI повторяет эти шаги в GitHub Actions (push в `main` и PR).
