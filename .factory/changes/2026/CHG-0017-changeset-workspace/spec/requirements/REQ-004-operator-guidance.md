---
schema: dark-factory.dev/requirement/v1
id: req:dark-factory:changeset-workspace:operator-guidance
type: requirement
title: Единый следующий шаг (Guidance)
product: dark-factory
status: proposed
change: chg:dark-factory:2026:0017
---

Оператор никогда не остаётся без следующего шага: «что делать дальше» вычисляется в ядре и одинаково показывается в CLI и Console.

## Acceptance Criteria

- AC-1: на каждом шаге и в CLI, и в Console виден один и тот же следующий шаг: состояние, причина, одно основное действие, дополнительные действия, блокеры и что будет после.
- AC-2: недоступное действие объясняет причину и предлагает действие по её снятию; экранов-тупиков нет.
- AC-3: Guidance — детерминированная функция состояния ChangeSet; вычисляется сервером, CLI и Console только рендерят её и не дублируют логику.
