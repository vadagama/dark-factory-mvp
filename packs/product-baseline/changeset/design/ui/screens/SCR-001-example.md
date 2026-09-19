---
schema: dark-factory.dev/ui-screen/v1
id: SCR-001
type: ui_screen
title: Подтверждение заказа
product: example-product
status: proposed
change: chg:example-product:2026:0001
route: /checkout/confirm
preview_url: /checkout/confirm
states:
  loading: Показан индикатор ожидания, кнопка «Подтвердить» недоступна.
  empty: Корзина пуста — предложение вернуться к каталогу.
  error: Оформление прервано по таймауту — сообщение об ошибке и кнопка «Повторить».
  success: Заказ подтверждён — номер заказа и переход к его странице.
  access: Пользователь не авторизован — предложение войти.
elements:
  - id: EL-summary
    kind: text
    label: Состав заказа
    component: Card
  - id: EL-confirm
    kind: button
    label: Подтвердить
    component: Button
  - id: EL-timeout-alert
    kind: text
    label: Ошибка таймаута
    component: Alert
  - id: EL-retry
    kind: button
    label: Повторить
    component: Button
transitions:
  - to: SCR-001
    trigger: Нажатие «Повторить»
    condition: Состояние error
---

Пример (иллюстративный): экран подтверждения заказа. Показывает состав заказа,
кнопку подтверждения и — при таймауте — сообщение об ошибке с возможностью
повторить попытку.
