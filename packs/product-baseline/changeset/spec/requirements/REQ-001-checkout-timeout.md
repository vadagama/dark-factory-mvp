---
schema: dark-factory.dev/requirement/v1
id: req:example-product:checkout:timeout
type: requirement
title: Checkout timeout
product: example-product
status: proposed
change: chg:example-product:2026:0001
---

Оформление заказа, не завершившееся за отведённое время, прерывается по таймауту с явной ошибкой для пользователя.

## Acceptance Criteria

- AC-1: оформление заказа прерывается по таймауту с понятным сообщением об ошибке.
- AC-2: длительность таймаута задаётся конфигурацией без изменения кода.
