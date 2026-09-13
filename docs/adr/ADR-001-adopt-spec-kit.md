# ADR-001: Adopt GitHub Spec Kit as the SDD toolkit

- **Статус**: принято (bootstrap-фаза; целевая модель заменена)
- **Дата**: 2026-09-13
- **Автор**: dark-factory (оркестратор); решение согласовано пользователем
- **Частично перекрыт** [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) (2026-09-13): OpenSpec принят как единый целевой SDD-формат.
- **Перекрыт** [ADR-020](ADR-020-native-sdd-core.md) (2026-09-14): целевая SDD-модель — Native SDD Core; Spec Kit остаётся bootstrap- и compatibility-инструментом. Настоящий ADR сохраняется как историческое решение bootstrap-фазы — Spec Kit действителен до готовности Native SDD Core.

## Контекст

- Проект pre-MVP, greenfield: стек не выбран, `src/` пуст, продуктовых репозиториев ещё нет.
- Слой Spec-Driven Development признан необходимым: стабильный контракт «человек ↔ агенты», контроль решений до генерации кода, проверяемые quality gates, координация нескольких агентов.
- Анализ вариантов рекомендовал OpenSpec как основной формат изменений, а из Spec Kit — перенести только quality gates (clarify, analyze, checklist, converge). OpenSpec в репозитории не установлен и не настраивался.
- Пользователь установил github/spec-kit: CLI `specify` 1.0.6 (uv tool, тег `v1.0.6`) и выполнил `specify init --here` (интеграция `zed`): в репозитории появились `.specify/` (шаблоны, скрипты, workflow) и 10 скиллов `speckit-*` в `.agents/skills/` (в git — общие для команды).
- Ключевой аргумент исследования за OpenSpec — brownfield change-management; для текущего greenfield-MVP этот сценарий пока не актуален.

## Решение

1. Принять **Spec Kit (github/spec-kit)** как SDD-инструментарий этого репозитория **на bootstrap-фазу до контрольной точки T-020** (далее — единый OpenSpec, [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md)).
2. Версия: CLI `specify` 1.0.6; установка `uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.0.6`, обновление — `specify self upgrade`. Изменения шаблонов в `.specify/` после обновления коммитить отдельно.
3. Канонический workflow фич bootstrap-фазы — скиллы `speckit-*`: `/speckit-constitution` (один раз на проект) → `/speckit-specify` → `/speckit-clarify` (опц.) → `/speckit-plan` → `/speckit-checklist` (опц.) → `/speckit-tasks` → `/speckit-analyze` (опц.) → `/speckit-implement` → `/speckit-converge`.
4. Артефакты: `.specify/` — инфраструктура и принципы (`memory/constitution.md`); `specs/<фича>/` — спецификации, планы, задачи (создаётся с первой фичей). Оба пути — в git; machine-local файлы скрыты через `.specify/.gitignore`.
5. Интеграция с оркестратором `dark-factory`: speckit-этапы — часть конвейера ролей; строгость подбирается под тип задачи (быстрому fix SDD-артефакты не нужны; значимые технические решения — по-прежнему через ADR).
6. Рекомендация «OpenSpec как основной SDD» на bootstrap-фазе перекрывалась настоящим ADR; целевым решением принят единый OpenSpec ([ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md)).

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| OpenSpec как основной SDD | Заточен под непрерывные brownfield-изменения, лёгкий change-процесс | Не установлен; brownfield-сценарий пока отсутствует (pre-MVP, greenfield) | Отложено; переоценено — [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) принял единый OpenSpec целевым форматом |
| Не устанавливать инструменты, «перенести идеи Spec Kit в оркестратор» | Ноль внешних зависимостей | Пришлось бы самостоятельно реализовывать clarify/analyze/checklist/converge; готовые скиллы уже установлены и работают | Дублирование готового решения дороже |
| Ручной SDD без инструментов | Максимальная простота | Контракт живёт в чате, нестабилен между сессиями и моделями | Не обеспечивает воспроизводимость — противоречит цели фабрики |

## Последствия

**Позитивные**
- Полный конвейер SDD «из коробки» с quality gates: clarify, analyze, checklist, converge.
- Контракт «человек ↔ агенты» зафиксирован в файлах, переживает смену модели и очистку контекста.
- Скиллы в `.agents/skills/` — единый стандарт для всех участников команды.

**Негативные / риски**
- Осознанное расхождение с рекомендацией «OpenSpec-first» — зафиксировано здесь.
- Два семейства скиллов в `.agents/skills/` (`dark-factory` и `speckit-*`): чтобы не возникло двух параллельных SDD-процессов, SDD-этапы выполняются только через speckit-скиллы, оркестратор их координирует.
- Обновления CLI меняют шаблоны в `.specify/` — изменения надо ревьюить и коммитить.

**Дальше**
- Выполнить `/speckit-constitution` — зафиксировать принципы фабрики (первый артефакт workflow).
- Переоценка состоялась: единый OpenSpec принят [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) (2026-09-13); настоящий ADR сохраняется как историческое решение bootstrap-фазы.
