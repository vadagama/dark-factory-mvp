---
schema: dark-factory.dev/requirement/v1
id: req:dark-factory:changeset-workspace:merge-assistant
type: requirement
title: Помощник merge
product: dark-factory
status: proposed
change: chg:dark-factory:2026:0017
---

Merge остаётся решением человека, но становится исполнимым и понятным из интерфейса: оператор видит требования политики и снимает блокеры по шагам.

## Acceptance Criteria

- AC-1: чек-лист готовности собирается из наблюдаемого состояния: обязательные проверки CI, version-bound approving review на head SHA, требования защиты ветки, согласования фаз.
- AC-2: approve в интерфейсе фиксирует версионно-привязанное человеческое решение; после merge фабрика продолжает по наблюдаемому merge, без ручного «дотолкнуть».
- AC-3: автоматического merge нет; merge выполняется явным человеческим действием со сверкой SHA, а несовпадение SHA блокирует действие и не создаёт эффекта.
