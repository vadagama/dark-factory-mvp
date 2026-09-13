# Contract: OpenSpec change artifacts

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`../plan.md`](../plan.md) | **ADR**: ADR-017

Канонический SDD-формат — OpenSpec с профилями `factory-sdd` (изменения фабрики, повышенная строгость) и `product-sdd` (изменения продуктов). Артефакты изменения — `openspec/changes/<change-id>/`; действующие требования — `openspec/specs/`. OpenSpec управляет жизненным циклом спецификаций, но **не принимает решение о прохождении гейта**: гейт T-021 работает поверх внутреннего нормализованного контракта (ADR-017 §7).

> В bootstrap-фазе (до T-020) действующий инструмент — Spec Kit (ADR-001), артефакты — `specs/<фича>/`. Этот договор описывает целевую форму; `SpecKitAdapter` обеспечивает импорт legacy.

## Артефакты изменения

```text
openspec/changes/<change-id>/
├── proposal.md          # зачем, что, scope / out of scope
├── requirements.md      # требования и spec deltas (acceptance criteria)
├── impact.md            # влияние и риск-класс (R0–R4), затрагиваемые системы
├── design.md            # техническое решение, альтернативы, ADR-ссылки
├── test-plan.md         # план проверок и evidence
├── tasks.md             # задачи реализации (dependency-order)
└── verification.md      # evidence прохождения (по завершении)
```

## `change-id` и профиль

- `change-id` — английский, kebab-case, стабильный; например `add-github-adapter`.
- Профиль (`factory-sdd` | `product-sdd`) выбирается по принадлежности системы; спека живёт рядом с системой (фабрика — в этом репозитории, продукт — в своём).
- **Один канонический формат в момент времени**; параллельные SDD-процессы запрещены (конституция, ADR-017).

## Нормализованный контракт для гейта (T-021)

Внутренний контракт, поверх которого работает машинный spec-gate (ADR-017 §7):

| Ось | Что проверяется |
|---|---|
| completeness | proposal/requirements/impact/design/test-plan присутствуют; критерии приёмки проверяемы |
| consistency | требования не противоречат друг другу и действующим `openspec/specs/` |
| policy compliance | риск-класс, гейты и участие человека соответствуют ADR-011/018 |
| coverage | каждое требование покрыто задачей и проверкой |
| evidence | для завершённого изменения есть verification evidence; `required && !available` запрещает успех |

**Наличие артефакта ≠ пройденный гейт**: гейт вычисляет результат и публикует `GateResult`; артефакт — вход, не доказательство прохождения.

## Жизненный цикл

```text
Proposal → Requirements/spec deltas → Impact & risk → Design
        → Test & evidence plan → Tasks → Verification evidence
        → Retrospective & learning → Archive & spec update
```

- Изменение спецификации фиксируется ревизией; approval связан с ревизией и инвалидируется при её смене (FR-003).
- После завершения действующие требования переносятся в `openspec/specs/` (spec update), изменение архивируется.
- Реализация разрешена только для согласованной ревизии спецификации (FR-003).

## Трассируемость

Цепочка «задача → спецификация (SHA) → план → код (SHA) → проверки → релиз» (SC-007) восстанавливается по ссылкам между `change-id`, ревизией спецификации, `RunManifest` и `RunRecord`.
