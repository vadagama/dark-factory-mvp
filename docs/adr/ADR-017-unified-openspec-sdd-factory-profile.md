# ADR-017: Unified OpenSpec SDD Model with Factory Profile

- **Статус**: заменено ([ADR-020](ADR-020-native-sdd-core.md), 2026-09-14)
- **Дата**: 2026-09-13
- **Автор**: software-architect
- Решение согласовано пользователем (ревью первоначальной редакции, 2026-09-13)
- Частично перекрывает [ADR-001](ADR-001-adopt-spec-kit.md): ADR-001 сохраняется как историческое решение bootstrap-фазы
- **Уточнено** (2026-09-13, архитектурное ревью ADR-пакета): Implementation Contract в графе артефактов (пп.5–6, п.7), exit criteria удаления `SpecKitAdapter` (п.8)
- **Заменено** [ADR-020](ADR-020-native-sdd-core.md) (2026-09-14): канонической моделью SDD принят Native SDD Core; OpenSpec — compatibility-инструмент. Реализация по настоящему ADR не выполнялась (`openspec/` не создан); ADR сохраняется как историческое решение.

## Контекст

- Q-16 plan §5: ADR-001 выбрал Spec Kit, но исходные материалы проработки целиком описывают OpenSpec-формат (`proposal.md`, `openspec/changes/<id>/`, skill `openspec-change`); соответствие стадий Flow (Specification/Planning) командам `/speckit-*`, расположение и формат спек в продуктовых репо нигде не закреплены.
- Влияние: контракты спецификационного гейта (T-020/T-021), шаблоны `specs/`, трассируемость evidence; решение до T-020.
- Ревью первоначальной редакции настоящего ADR («Spec Kit для фабрики, OpenSpec для продуктов») выявило архитектурный разрыв: фабрика должна уметь разрабатывать саму себя тем же процессом, которым она разрабатывает продукты. Постоянная пара форматов потребовала бы поддерживать self-improvement loop в двух форматах, два набора skills и две модели трассировки.
- OpenSpec поддерживает project config, собственные схемы, шаблоны и зависимости между артефактами ([OpenSpec Customization](https://github.com/Fission-AI/OpenSpec/blob/main/docs/customization.md)); адаптируется профиль и схема, а не код инструмента.

## Решение

1. **OpenSpec принимается как каноническая модель SDD** для:

   - разработки самой Dark Factory;
   - самоизменений и learning loop фабрики;
   - создаваемых фабрикой продуктовых систем.

2. **Spec Kit используется только как bootstrap-инструмент** до завершения первоначальной спецификации и планирования MVP:

   - существующие `.specify/` и `specs/<фича>/` не переписываются немедленно;
   - новые изменения после контрольной точки T-020 создаются в OpenSpec;
   - ADR-001 считается исполненным для bootstrap-фазы и частично заменяется настоящим ADR.

3. Создаются два профиля одной OpenSpec-модели:

   | Профиль | Назначение | Строгость |
   |---|---|---|
   | `factory-sdd` | Изменения ядра, агентов, flows, policies, adapters и инфраструктуры фабрики | Повышенная |
   | `product-sdd` | Изменения создаваемых продуктовых систем | Базовая, расширяемая по risk class |

   Это **не две SDD-технологии**, а две схемы одного формата с общими понятиями change, artifact, requirement, evidence и archive.

4. Профиль `factory-sdd` реализуется как version-controlled custom schema:

   ```text
   openspec/
   ├── config.yaml
   ├── schemas/
   │   ├── factory-sdd/
   │   │   ├── schema.yaml
   │   │   └── templates/
   │   └── product-sdd/
   │       ├── schema.yaml
   │       └── templates/
   ├── specs/
   └── changes/
       └── <change-id>/
   ```

   OpenSpec позволяет описывать собственные артефакты и зависимости между ними в `schema.yaml` ([OpenSpec Custom Schemas](https://github.com/Fission-AI/OpenSpec/blob/main/docs/customization.md#custom-schemas)), поэтому factory-процесс можно сделать строже без изменения OpenSpec core.

5. Для `factory-sdd` устанавливается следующий граф артефактов:

   ```mermaid
   flowchart TD
       P["Proposal"] --> S["Requirements and spec deltas"]
       P --> I["Impact and risk"]
       S --> D["Design"]
       I --> D
       D --> T["Test and evidence plan"]
       T --> W["Implementation tasks"]
       W --> V["Verification evidence"]
       V --> R["Retrospective and learning"]
       R --> A["Archive and spec update"]
   ```

   Минимальный состав change:

   ```text
   openspec/changes/<change-id>/
   ├── proposal.md
   ├── specs/
   ├── impact.md
   ├── design.md
   ├── implementation-contract.md
   ├── test-plan.md
   ├── tasks.md
   ├── evidence.yaml
   ├── verification.md
   └── retrospective.md
   ```

   `implementation-contract.md` — утверждённый Implementation Contract (ADR-018 п.3): scope, acceptance criteria, архитектурные ограничения, UI evidence, риск-класс, бюджет, правила эскалации. Зависимость графа: approved Implementation Contract → `tasks.md`/implementation; нормализованный контракт гейта (п.7) включает его hash/revision/approval.

6. Для `product-sdd` обязательны:

   ```text
   proposal.md
   specs/**
   design.md          # обязательно для R1+
   implementation-contract.md  # обязателен перед автономной реализацией (ADR-018)
   tasks.md
   evidence.yaml
   ```

   Дополнительные артефакты включаются по классу риска, типу продукта и применимым policies.

7. **OpenSpec управляет жизненным циклом спецификаций, но не принимает решение о прохождении gate.**

   Спецификационный gate T-021 работает через внутренний нормализованный контракт:

   ```yaml
   change_id: CHG-017
   profile: factory-sdd
   spec_format: openspec
   risk_class: R1
   requirements:
     total: 12
     validated: 12
   traceability:
     tasks_covered: true
     tests_covered: true
   evidence:
     manifest: evidence.yaml
   implementation_contract:
     status: approved
     hash: sha256:...
     approved_by: human
   result: passed
   ```

   Проверки completeness, consistency, policy compliance, test coverage и evidence выполняются Factory Quality/Security/Architecture agents и CI. Это особенно важно, поскольку наличие custom-артефакта ещё не доказывает качество его содержания.

8. SDD Adapter остаётся портом архитектуры:

   ```text
   SDDPort
   ├── OpenSpecAdapter        # основной
   └── SpecKitAdapter         # миграция и импорт legacy-артефактов
   ```

   `SpecKitAdapter` после миграции не участвует в штатном execution path и может быть удалён после закрытия переходного периода. **Exit criteria удаления**: (1) нет активных Spec Kit changes; (2) незавершённые изменения импортированы в OpenSpec и проверены; (3) execution path не вызывает адаптер; (4) `.specify/` и `specs/` доступны read-only как historical bootstrap evidence; contract-тесты импорта сохраняются либо адаптер архивируется отдельно.

9. Спецификации хранятся рядом с системой, которой они принадлежат. Отдельный глобальный репозиторий не создаётся. Для cross-repository change фабрика формирует родительский `changeId` и связанные локальные changes в затронутых репозиториях.

10. Миграция не должна механически переносить все документы Spec Kit. Переносятся:

    - утверждённые требования — в `openspec/specs/`;
    - незавершённые фичи — в `openspec/changes/`;
    - архитектурные решения — в ADR;
    - задачи реализации — в `tasks.md`;
    - исходные `.specify/` сохраняются как historical bootstrap evidence до завершения MVP.

Жёсткая граница ответственности: **OpenSpec хранит intent и артефакты изменения; Factory Orchestrator управляет исполнением, состояниями, retries, approvals и gates.**

Связанные задачи: T-020, T-021, T-022.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Spec Kit и в фабрике, и в продуктах | Одна технология во всём конвейере; актуальный Spec Kit поддерживает existing projects, расширения, presets и пользовательские workflows ([GitHub Spec Kit](https://github.github.com/spec-kit/)) | Параллельное использование не даёт преимуществ, достаточных для поддержки второй модели артефактов, команд, skills и трассировки | Отклонено |
| Spec Kit для фабрики + адаптированный OpenSpec для продуктов (первоначальная редакция настоящего ADR) | Работоспособно; гейт не зависит от формата спеки | Архитектурный разрыв: self-improvement loop в двух форматах, два набора команд и skills, две модели трассировки, dogfooding частичное | Отклонено ревью |
| Единый адаптированный OpenSpec с профилями `factory-sdd` / `product-sdd`; Spec Kit — только bootstrap | Фабрика проходит собственный процесс; единая модель и один основной адаптер; learning обновляет те же схемы; `changeId` связывает requirements, tasks, evidence и learning | Миграция артефактов; custom schema — поддерживаемый контракт; совместимость обновлений OpenSpec; семантические gates вне OpenSpec | Выбрано |

### Сравнение с первоначальной редакцией ADR

| Критерий | Spec Kit (фабрика) + OpenSpec (продукты) | Единый OpenSpec с профилями |
|---|---|---|
| Self-development | Отдельный процесс фабрики | Фабрика проходит собственный процесс |
| Skills агентов | Два набора команд и понятий | Единая модель, разные профили |
| Gate implementation | Два адаптера постоянно | Один основной адаптер |
| Learning loop | Нужна трансляция форматов | Learning обновляет те же схемы |
| Brownfield | OpenSpec только для продуктов | OpenSpec для всех последующих изменений |
| Bootstrap | Spec Kit остаётся навсегда | Spec Kit ограничен переходным периодом |
| Dogfooding | Частичное | Полное |

## Последствия

**Позитивные**

- Единая модель изменений для фабрики и продуктов; настоящее dogfooding и self-improvement.
- Меньше adapters, skills и правил трансляции; OpenSpec change — естественная единица оркестрации.
- Требования, tasks, evidence и learning связываются одним `changeId`.
- Фабричные изменения могут иметь более строгий профиль без создания второго SDD-стека.

**Негативные / риски**

- Потребуется миграция уже созданных Spec Kit-артефактов.
- Custom schema становится внутренним поддерживаемым контрактом; обновления OpenSpec нужно проверять на совместимость.
- Формальная схема проверяет структуру и зависимости артефактов; семантические gates (completeness, consistency, policy compliance, coverage, evidence) придётся реализовать отдельно.
- Чрезмерное расширение OpenSpec может превратить его в дублирующий workflow engine; риск снимается жёсткой границей ответственности из раздела «Решение».

**Дальше**

- T-020: контрольная точка перехода — новые изменения фабрики и продуктов создаются в OpenSpec (профили `factory-sdd` / `product-sdd`); `SpecKitAdapter` — импорт legacy-артефактов.
- T-021: контракт гейта поверх нормализованной модели OpenSpec.
- T-022: constitution-шаблон для продуктов в формате OpenSpec.
