---
schema: dark-factory.dev/requirement/v1
id: req:dark-factory:changeset-workspace:document-artifacts
type: requirement
title: "Артефакты-документы: git как источник истины"
product: dark-factory
status: proposed
change: chg:dark-factory:2026:0017
---

Артефакты изменения (спека, дизайн, ADR, UI, план) — документы в git: их можно читать, сравнивать и править из интерфейса, и правка становится ревизией в репозитории.

## Acceptance Criteria

- AC-1: правка артефакта из CLI или Console становится коммитом в ветку изменения; правка напрямую в GitHub наблюдается как новая ревизия.
- AC-2: доступны ревизии и diff; свойства документа (YAML frontmatter) читаются и правятся, системные идентификаторы защищены от случайного изменения.
- AC-3: новая ревизия помечает связанные с прежней ревизией согласования и проверки неактуальными.
