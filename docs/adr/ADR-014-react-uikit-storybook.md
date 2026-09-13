# ADR-014: React Small UIKit with Storybook

- **Статус**: принято
- **Дата**: 2026-09-13
- **Автор**: software-architect
- Решение согласовано пользователем (ответы на вопросы раздела 5 plan.md, 2026-09-13)
- **Уточнено** (2026-09-13, архитектурное ревью ADR-пакета): разделение автоматического axe и WCAG-приёмки, детерминизм visual regression (п.3)

## Контекст

- Q-13 plan §5: выбор UI pack пилота был оставлен открытым; за Small UIKit на React — «React поверх Radix/shadcn, не Web Components».
- Влияние: engineering pack пилота (T-070/T-071), UI-гейты, вход для Design-профиля (T-081).

## Решение

1. Engineering pack пилота — **Small UIKit на React**: примитивы поверх Radix + адаптированный shadcn-слой.
2. **Storybook — исполняемая UI-спецификация вместо Figma**: токены (DTCG), 10–12 базовых компонентов + 5–7 паттернов (T-071); Storybook собирается в CI как артефакт-спека.
3. UI-гейты: UIKit-policy (ESLint — запрет прямых импортов вне `@small/ui`), stylelint, axe (автоматические violations) + WCAG 2.2 AA acceptance checklist — неавтоматизируемые критерии и baseline новых паттернов подтверждает человек как UI evidence (ADR-018 п.1), visual regression (Playwright: pinned browser image, фиксированные viewport/fonts/locale/timezone, отключение анимаций, порог расхождения; обновление baseline — только через ревью); гейты — плагины типа `gate` (ADR-008).
4. Console фабрики — тоже React + Radix/shadcn (T-051): одна базовая UI-технология фабрики и продуктов; при этом Console **не определяет** UI Kit создаваемых продуктов.
5. Figma и Web Components UIKit — вне MVP (vision, Out of Scope).

Связанные задачи: T-070, T-071, T-081.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Small UIKit на React (Radix + shadcn, Storybook) | Исполнимая спека UI; автоматизируемые гейты; согласованность с Console | Содержание UIKit — постоянная работа | Выбрано |
| Web Components UIKit | Фреймворк-агностичность | Отвергнута идея Web Components; агентный код проще на React | Отклонено |
| Figma-центричный процесс (спецификация в Figma) | Привычный дизайн-инструмент | Спека не исполняема и не гейтится в CI; дороже для агентного цикла | Отклонено |

## Последствия

**Позитивные**
- UI-требования становятся проверяемыми гейтами, а не картинками; Design-профиль (T-081) получает машиночитаемый вход.

**Негативные / риски**
- Visual regression склонен к flaky — обязательны baseline и ревью его обновлений.
- Риск расползания UIKit за пределы SMALL — policy-гейт ограничивает состав.

**Дальше**
- T-070: blueprint пилота с Small UIKit; T-071: UIKit + UI-гейты; T-081: Design-профиль и UI-гейт в Planning.
