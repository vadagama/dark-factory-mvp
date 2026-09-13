<!--
SYNC IMPACT REPORT
==================
Version change: 2.1.0 → 3.0.0 (MAJOR: переопределена каноническая модель SDD)
Основание: итоговое видение SDD пользователя (2026-09-14) — канонической моделью
  принят Native SDD Core (ADR-020), заменяющий единый OpenSpec (ADR-017).
Modified principles:
  - I. Specification-Driven Development (SDD) — каноническая модель: Native SDD Core
    (ChangeSet + Product Baseline `.factory/`, delta specifications, reconciliation,
    frontmatter-политика OKF). Spec Kit и OpenSpec — bootstrap-/compatibility-
    инструменты; bootstrap-фаза на Spec Kit продолжается до готовности Native Core.
Modified sections:
  - Language & Communication Standards — `openspec/` → `.factory/`; идентификаторы SDD.
  - Development Workflow & Quality Gates — гейты целевой модели поверх нормализованного
    контракта ChangeSet с фиксацией GateDecision (ADR-020).
Added sections: нет
Removed sections: нет
Follow-up TODOs: задачи T-020/T-021/T-022 переформулированы в docs/plan.md.
-->

# Software Dark Factory Constitution

## Core Principles

### I. Specification-Driven Development (SDD)

Каждая фича проходит SDD-слой. Код реализуется против спецификации, а не контекста
чата; артефакты коммитятся. Оркестратор `dark-factory` подбирает строгость:
быстрому fix SDD-артефакты не нужны, фиче — обязательны.

**Каноническая модель — Native SDD Core** (ADR-020, полная спецификация —
`docs/sdd-native-core.md`): каждая разработка — ChangeSet со стабильным ID и
цепочкой `Intent → Spec → Design → Tasks → Verification → Evidence →
Reconciliation`; ChangeSet содержит дельту (`add/modify/supersede/retire`)
относительно канонического Product Baseline. Baseline хранится в `.factory/`
репозитория системы (для multi-repo продукта — в отдельном product-spec
репозитории) и содержит только принятые состояния (`active`, `superseded`,
`retired`); acceptance → reconciliation переводит артефакты ChangeSet в
baseline. Все самостоятельно адресуемые SDD-артефакты имеют YAML frontmatter
и становятся узлами OKF-графа; generated views и вспомогательная документация
могут его не иметь. Центральный OKF агрегирует baselines как федеративная
проекция и не заменяет их как source of truth.

**Spec Kit — инструмент bootstrap-фазы** (ADR-001), действует до готовности
Native SDD Core (T-020): до этого момента фичи проходят `/speckit-specify` →
`/speckit-plan` → `/speckit-tasks` → `/speckit-implement` → `/speckit-converge`
(опционально `/speckit-clarify`, `/speckit-checklist`, `/speckit-analyze`),
артефакты живут в `specs/<фича>/`. Существующие `.specify/` и `specs/<фича>/` немедленно не
переписываются; после перехода они сохраняются как historical bootstrap evidence.
**OpenSpec — compatibility-инструмент** (ADR-017 заменён ADR-020): импорт/экспорт
— через адаптеры `SDDPort`; отдельный актуальный baseline в формате OpenSpec не
поддерживается.

Параллельные канонические SDD-процессы не допускаются: в каждый момент действует
ровно одна каноническая модель, переход выполняется один раз — через T-020, а не
по усмотрению исполнителя. Импорт legacy-артефактов Spec Kit — через
`SpecKitAdapter` (ADR-020 п.8).

### II. Minimal Surgical Changes

Изменения ограничены требованиями задачи: SOLID/DRY/KISS/YAGNI, без попутных
рефакторингов и несвязанных багов (они фиксируются в отчёте, но не чинятся).
Запрещено трогать файлы и изменения, созданные другими, без явного запроса.
Смена стека, зависимостей и структуры репозитория — только через ADR (принцип IV)
или явное согласование пользователя.

### III. Evidence-Based Validation (NON-NEGOTIABLE)

Каждое изменение кода валидируется фактически: тесты (где применимо) плюс
диагностика по изменённым файлам. Запрещено заявлять о пройденной проверке, если
она не запускалась. Финальный отчёт обязан фиксировать: что изменено, что проверено
(команды и результаты), что осталось.

### IV. Governance via ADR & Docs-First

Значимые технические решения (стек, архитектура, ключевые паттерны, инфраструктура)
фиксируются ADR в `docs/adr/` до реализации; шаблон и реестр решений — там же.
Перед любой задачей агент читает `docs/` — это источник контекста проекта.
Противоречия между документами не допускаются: устаревшие документы обновляются,
конфликт разрешается в пользу ADR.

### V. Safety & Git Discipline

Секреты и ключи никогда не попадают в код и git — только через механизмы секретов
(env, хранилища). Git-цикл задачи — штатная часть конвейера: `ci-cd` создаёт ветку
задачи до реализации и открывает MR против `main` после зелёной проверки; коммиты и
пуши — только в ветку задачи. Формат — Conventional Commits на английском; ветки
`<type>/t-<NNN>-<slug>`; `main` всегда стабильный, push в него запрещён, merge в
`main` — только человек (ADR-011). Вне git-цикла задачи коммиты и пуши — только по
явной просьбе пользователя.

## Language & Communication Standards

- Документация (`docs/`, `.factory/`, `specs/`, ADR, конституция) и общение с
  пользователем — русский; код, идентификаторы, коммиты, имена файлов ADR —
  английский. Идентификаторы SDD (`change-id`, `id` артефактов, схемы) —
  английские.
- Один документ — одна тема; даты и версии — в именах файлов
  (`vision-2026-09-12-v1.md`).
- Источник истины по архитектуре — ADR и актуальные документы `docs/`
  (сводная картина — `docs/hld.md`); при конфликте приоритет у ADR.

## Development Workflow & Quality Gates

- Конвейер: задача → `dark-factory` (классификация, план) → роли (`product`,
  `design`, `architect`, `infrastructure`, `security`, `develop`, `quality`,
  `ci-cd`, `operation`, `yandex-cloud-engineer`)
  → приёмка по DoD.
- Git-цикл задачи (обязательный): `ci-cd` создаёт ветку до реализации,
  `develop` реализует и проверяет, `ci-cd` открывает MR после зелёной
  проверки; merge — человек (ADR-011). Детали — `docs/development-workflow.md`.
- Definition of Done: требования выполнены, побочных изменений нет; работа велась в
  ветке задачи; диагностика по изменённым файлам чистая; тесты (если применимо)
  зелёные; документация и ADR обновлены; MR против `main` открыт (merge — человек);
  пользователю дан отчёт (что сделано, что проверено, что осталось).
- Quality gates SDD в bootstrap-фазе (до T-020): `/speckit-clarify` — до плана;
  `/speckit-checklist` — после плана; `/speckit-analyze` — после задач, до
  реализации; `/speckit-converge` — сопоставление кода со спецификацией.
- Quality gates SDD в целевой модели (после T-020): completeness, consistency,
  policy compliance, test coverage и evidence проверяются гейтами самой фабрики
  (T-021) поверх нормализованного контракта ChangeSet; результат фиксируется как
  GateDecision (policy и версия, revision, результат, evidence, объяснение,
  агент/человек, разрешённый override — ADR-020). Наличие артефакта не равно
  пройденному гейту.
- Критические находки gate блокируют переход к следующему этапу в обеих фазах.

## Governance

- Настоящая конституция — высший документ конвейера: при конфликте с другими
  практиками приоритет у неё. Операционное руководство — `AGENTS.md`; при
  конфликте оба документа согласовываются одной поправкой.
- Поправки формулируются явно; версия меняется по SemVer: MAJOR — удаление или
  переопределение принципов, MINOR — новые разделы и принципы, PATCH —
  уточнения формулировок. Каждая поправка сопровождается Sync Impact Report.
- Все агенты читают конституцию в начале сессии. Приёмка DoD включает проверку
  соответствия принципам; несоответствие блокирует завершение задачи.

**Version**: 3.0.0 | **Ratified**: 2026-09-13 | **Last Amended**: 2026-09-14
