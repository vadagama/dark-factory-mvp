# ADR-038: Пакеты `flows`, `rules` и `ci` входят в `orchestration` (T119)

- **Статус**: принято
- **Дата**: 2026-09-19
- **Автор**: architect

## Контекст

- Аудит архитектуры (T119) показал три пакета верхнего уровня в `src/dark_factory/`, каждый из одного–трёх модулей: `flows/` (`routes.py` — порядок стадий и полоса риска маршрута), `rules/` (`gates.py`, `limits.py`, `merge_protection.py` — обязательные гейты, rework/token/cost/deadline-лимиты, branch protection) и `ci/` (`stages.py` — каталог этапов CI и их переключателей, [ADR-026](ADR-026-parameterizable-ci-stages.md), [ADR-027](ADR-027-console-ci-stage-toggles.md)).
- HLD §6 относил `flows` и `rules` к «точкам входа и расширения» ([ADR-008](ADR-008-plugin-architecture-core-sdk.md)). Фактически это внутренние таблицы межстадийного Flow: их единственные потребители — `orchestration/flow.py`, `orchestration/policy/*`, `orchestration/stages/*`, `orchestration/budget/*` и `runtime`. Внешнего (plugin) контракта на них нет, и в MVP он не планируется.
- Каталог `ci/stages.py` потребляют только `api/routes_ci.py`, `api/dto.py` и адаптер переключателей через `ports`; сам каталог — часть оркестрации CI-гейтов, а не отдельная подсистема.
- Пакет из одного модуля стоит столько же, сколько и большой: собственный `__init__.py` с `__all__`, строка в HLD, раздел в `docs/descriptions`, отдельное правило чтения для агентов. Слоевых причин держать их отдельно нет: `flows → rules, context, changes`, `rules → changes`, `ci → changes`; `orchestration` уже импортирует всё перечисленное, а `ports` каталог CI не импортирует (только упоминает в докстринге), так что инверсии `ports → orchestration` не возникает.

## Решение

1. `flows/routes.py` → `orchestration/routes.py`; пакет `flows` удалён.
2. `rules/` → `orchestration/rules/` (подпакет с тем же составом модулей и тем же `__init__`).
3. `ci/stages.py` → `orchestration/ci.py`; пакет `ci` удалён.
4. Compatibility-шимы (`dark_factory.flows`, `dark_factory.rules`, `dark_factory.ci`) не создаются: все импорты в `src`, `tests` и активной документации переписаны в одном изменении. Публичные имена (`route_profile`, `required_gates`, `CI_STAGES` и т. д.) не меняются.
5. Правила границ ([ADR-015](ADR-015-repository-boundaries.md) п.3, `tests/test_import_boundaries.py`) действуют без изменений; `api → orchestration` — уже разрешённое направление.
6. Будущие маршруты, правила гейтов/лимитов и каталоги CI-этапов добавляются внутрь `orchestration`. Если появится внешний plugin-контракт ([ADR-008](ADR-008-plugin-architecture-core-sdk.md)), он оформляется отдельным ADR как порт, а не как пакет верхнего уровня.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Оставить три пакета верхнего уровня | Ничего не двигать | Три пакета-одиночки без внешних потребителей; ложный сигнал о «точке расширения» | Стоимость структуры не оправдана содержимым |
| Перенести с compatibility-шимами (`dark_factory.flows` реэкспортирует новое место) | Внешние скрипты не ломаются | Внешних потребителей нет; шимы — мёртвый код, который придётся удалять отдельно | YAGNI |
| Вынести `flows`/`rules` в отдельный plugin SDK (ADR-008) | Формальная точка расширения | Контракта расширения в MVP нет; преждевременная абстракция | Отложено до появления реального внешнего потребителя |

## Последствия

- Позитивные: на три пакета верхнего уровня меньше; таблица модулей HLD §6 и карта `docs/descriptions` короче; оркестрация читается как одно целое («какая стадия следующая», «можно ли продолжать», «какие CI-гейты есть»).
- Негативные: исторические ADR ([ADR-021](ADR-021-console-mvp-delivery.md), [ADR-023](ADR-023-risk-classes-and-control-points.md), [ADR-027](ADR-027-console-ci-stage-toggles.md), [ADR-029](ADR-029-human-gates-at-design-phase.md)) и журналы пилота ссылаются на старые пути; они не переписываются — это записи на дату принятия, актуальные пути даёт HLD §6 и этот ADR.
- Нейтральные: `docs/descriptions/routes.md` и `rules.md` сохраняют имена файлов, меняются только ссылки на исходники.
