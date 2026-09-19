---
schema: dark-factory.dev/requirement/v1
id: req:dark-factory:changeset-workspace:repository-provisioning
type: requirement
title: Провижининг репозитория продукта
product: dark-factory
status: proposed
change: chg:dark-factory:2026:0017
---

Фабрика валидирует репозиторий продукта и приводит его в рабочее состояние (клон/зеркало и baseline), подтверждая результат evidence, а не сообщением в интерфейсе.

## Acceptance Criteria

- AC-1: после создания продукта фабрика валидирует репозиторий и приводит статус к `ready` или `error` с человекочитаемой причиной.
- AC-2: пустой репозиторий (без коммитов) — штатный случай: baseline из паков применяется идемпотентно, повтор не создаёт второй эффект.
- AC-3: клон выполняется на короткоживущем installation-токене GitHub App; секреты не попадают в код, git, логи и сообщения об ошибках.
