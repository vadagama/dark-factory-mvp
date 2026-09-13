# ADR-020: Native SDD Core (ChangeSet, Product Baseline, OKF)

- **Статус**: принято
- **Дата**: 2026-09-14
- **Автор**: software-architect
- Решение согласовано пользователем (итоговое видение SDD, 2026-09-14)
- Заменяет [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) (полностью); [ADR-001](ADR-001-adopt-spec-kit.md) считается исполненным и сохраняется как историческое решение bootstrap-фазы
- Полная спецификация модели — [`docs/sdd-native-core.md`](../sdd-native-core.md)

## Контекст

- [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) принял единый OpenSpec как каноническую модель SDD фабрики и продуктов. Реализация не начата: T-020 не выполнен, каталог `openspec/` в репозитории не создан.
- 2026-09-14 пользователь утвердил итоговое видение SDD: внешние SDD-инструменты (Spec Kit, OpenSpec) не должны определять архитектуру SDD Dark Factory — нужен собственный **Native SDD Core**.
- Драйверы пересмотра:
  - OKF-нативность: каждый самостоятельно адресуемый артефакт — узел knowledge graph с YAML frontmatter (schema, id, status, relations);
  - multi-repo продукты: канонический baseline и координирующий ChangeSet в отдельном product-spec репозитории;
  - собственные lifecycle, gates, evidence и WorkGraph вместо подгонки под формат внешнего инструмента;
  - разделение состояния: семантическое — в Git, runtime (job, lease, retry, attempt) — в PostgreSQL ([ADR-004](ADR-004-postgresql-factory-state.md));
  - отсутствие внешних SDD-runtime-зависимостей: Spec Kit и OpenSpec — bootstrap- и compatibility-инструменты, источники практик, import/export adapters.
- Поскольку модель ADR-017 не была реализована, замена не требует миграции артефактов — обновляются только документы и задачи плана.

## Решение

1. **Native SDD Core принимается как каноническая модель SDD** для разработки самой фабрики, её самоизменений и создаваемых продуктовых систем. SDD — типизированная система управления изменениями, связывающая Intent, Spec, Design, Tasks, Code, Verification, Evidence и актуальный Product Baseline.
2. Единица изменения — **ChangeSet** со стабильным ID (`chg:<product>:<year>:<num>`) и манифестом `change.yaml`; цепочка `Intent → Spec → Design → Tasks → Verification → Evidence → Reconciliation`. Состав артефактов определяется workflow profile и risk class; путь каталога ChangeSet стабилен, семантическое состояние (`draft → … → closed`) хранится в Git.
3. Канонический **Product Baseline** — `.factory/` рядом с кодом (один продукт — один репозиторий) либо в отдельном product-spec репозитории (multi-repo продукт). Baseline содержит только принятое состояние (`active`, `superseded`, `retired`); принятые артефакты попадают в него через **reconciliation** (`add/modify/supersede/retire`). Отдельный актуальный baseline в формате OpenSpec не поддерживается.
4. **Frontmatter-политика**: все самостоятельно адресуемые SDD-артефакты имеют минимальный YAML frontmatter (`schema`, `id`, `type`, `title`, `product`, `status`, `change`) и становятся узлами OKF-графа; generated views (`tasks.md`, собранный `spec.md`) и вспомогательная документация могут его не иметь.
5. **Spec** — дельта над baseline (`delta.yaml`: `add/modify/supersede`), а не копия всей спецификации. **Декомпозиция** — `tasks/graph.yaml` (WorkGraph с трассировкой `satisfies`); `tasks.md` — генерируемое представление; Plane — внешнее рабочее представление графа ([ADR-013](ADR-013-plane-tracker-trackerport.md)). `plan.md` как архитектурный артефакт не используется.
6. **Verification** определяется до начала реализации (`verification/plan.yaml`); **evidence** — ссылки, digest и provenance в Git, тяжёлые отчёты — в CI artifacts/Object Storage; **gates** фиксируют GateDecision (policy и версия, revision ChangeSet, результат, evidence, объяснение, агент/человек, разрешённый override).
7. Размещение вычислений: **product repositories — canonical knowledge; central OKF — федеративная read-проекция** (индексация baselines, enterprise knowledge graph, поиск и context retrieval). OKF не является source of truth и не должен становиться bottleneck'ом продуктовых изменений.
8. `SDDPort` остаётся портом архитектуры:

   ```text
   SDDPort
   ├── NativeChangeSetAdapter   # основной
   ├── SpecKitAdapter           # bootstrap-импорт legacy-артефактов (specs/)
   └── OpenSpecAdapter          # compatibility import/export
   ```

   До готовности Native SDD Core bootstrap-разработка фабрики продолжается на Spec Kit (ADR-001, `/speckit-*`); после перехода (пересмотренная T-020) новые изменения фабрики и продуктов создаются как ChangeSet.

Жёсткая граница ответственности: **ChangeSet и baseline хранят intent, знания и артефакты изменения; Factory Orchestrator управляет исполнением, runtime-состояниями, retries, approvals и gates.**

Связанные задачи: T-020, T-021, T-022.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Единый OpenSpec с профилями ([ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md)) | Готовый change-инструмент, кастомные схемы | Внешняя runtime-зависимость задаёт архитектуру; ограничения формата для OKF-графа, multi-repo baseline, evidence/gates; кастомизация упирается в схему инструмента | Заменено настоящим ADR |
| Spec Kit как каноническая модель | Единый набор готовых скиллов | Нет delta specifications, baseline и reconciliation; рассчитан на greenfield-фичи, а не непрерывный change-management | Отклонено (остаётся bootstrap-инструментом) |
| Native SDD Core | Полный контроль модели; OKF-нативность; без внешних SDD-runtime-зависимостей; фабрика и продукты в одном процессе | Собственная поддержка схем и валидаторов; governance-практики (clarify, analyze, checklist, converge) реализуются самостоятельно | Выбрано |

## Последствия

**Позитивные**

- Модель проектируется под OKF, multi-repo, gates/evidence, а не адаптируется под внешний формат.
- Нет внешних SDD-runtime-зависимостей; Spec Kit и OpenSpec — только bootstrap/compatibility через адаптеры.
- Единая точка истины: baseline продукта рядом с кодом (или в product-spec репозитории); центральный OKF — производная федеративная проекция.
- Dogfooding из ADR-017 сохраняется: фабрика и продукты проходят один процесс.

**Негативные / риски**

- Схемы (`change/v1`, `requirement/v1`, `task-graph/v1`, …) и их валидаторы — собственный поддерживаемый контракт.
- Governance-практики, ранее «бесплатные» из Spec Kit (clarify, analyze, checklist, converge), реализуются в Native Core; источники практик — Spec Kit и OpenSpec.
- Миграция артефактов не требуется (`openspec/` не создан), но задачи T-020/T-021/T-022 переформулированы; bootstrap-фаза на Spec Kit продолжается до готовности Native Core.

**Дальше**

- T-020 (переформулирована): Native SDD Core MVP — `SDDPort` + `NativeChangeSetAdapter`, схемы артефактов, Product Baseline `.factory/`, reconciliation.
- T-021: specification gate поверх нормализованного контракта ChangeSet с фиксацией GateDecision.
- T-022: шаблоны Product Baseline и ChangeSet для продуктовых репозиториев.
- Изменения модели — только через новый ADR; канонический текст — `docs/sdd-native-core.md`.
