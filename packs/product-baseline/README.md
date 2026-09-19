# Пак `product-baseline`

Шаблоны **Product Baseline** и **ChangeSet** для продуктовых репозиториев — каноническая модель Native SDD Core (ADR-020, полная спецификация: `docs/sdd-native-core.md`). Пак — данные, не код: шаблоны загружаются теми же строгими схемами и адаптером, что использует фабрика, поэтому «уплывший» шаблон ловится тестами, а не падает в продуктовом репозитории.

## Структура

```text
product-baseline/
├── README.md          # этот файл
├── rules.md           # правила минимальных изменений и evidence-based validation
├── baseline/          # скелет .factory/ продуктового репозитория
│   ├── factory.yaml   # identity продукта
│   └── product/       # product.md + примеры нод (requirements/, scenarios/, decisions/, capabilities/)
└── changeset/         # канонический скелет ChangeSet (profile product-feature, R1)
    ├── change.yaml    # манифест, точка входа
    ├── intent.md      # без frontmatter (§7: вспомогательный документ)
    ├── spec/          # delta.yaml + артефакты дельты
    ├── design/
    ├── tasks/
    ├── verification/
    └── evidence/
```

## Как применить

1. **Baseline**: скопировать `baseline/` в `.factory/` продуктового репозитория, поправить identity-поля `factory.yaml` (`product`, `title`) и поля `id`/`product`/`change` во frontmatter артефактов.
2. **Новое изменение**: скопировать `changeset/` в `.factory/changes/<год>/CHG-NNNN-<slug>/`, присвоить реальный id `chg:<product>:<год>:<NNNN>` и заменить placeholder-ревизию (строка из 64 нулей) в `change.yaml` и `spec/delta.yaml` на актуальную — её возвращает `dark_factory.context.sdd.baseline.current_revision`.

**Автоматическое применение.** Для провижинённого продуктового репозитория пак применяет сама фабрика: `RepositoryProvisioningPort.bootstrap_baseline` (адаптер `ProviderClone`, T069, ADR-031) берёт паки из корня `DARK_FACTORY_PACKS_ROOT`, читает `pack.yaml` пака (`name` и версия — evidence применения) и переносит `baseline/` в корень репозитория как `.factory/`, после чего коммитит и push'ит в ветку по умолчанию. Повтор идемпотентен: тот же `idempotency_key` возвращает ту же ревизию; при новом ключе payload записывается снова, но коммит создаётся только при фактических изменениях — если `.factory/` уже совпадает с содержимым пака и baseline присутствует в HEAD, это физический no-op (второго коммита нет, evidence берётся из коммита baseline), а если изменений нет и baseline в HEAD отсутствует, bootstrap падает fail-closed. Bootstrap — не механизм обновления версии пака: смена версии в манифесте без изменения содержимого коммита не создаёт. Ручное копирование шага 1 остаётся out-of-band путём: для оператора без провижининга, для пилота и для обновления baseline вне bootstrap.

**Пустые каталоги не создаются** (§5): разделы `product/` — `architecture/`, `contracts/`, `data/`, `ui/`, `deployment/` и т.д. — создаются по мере необходимости; каждая нода — markdown с минимальным frontmatter по §7-8 (`schema`, `id`, `type`, `title`, `product`, `status`, `change`).

## Как проверять

ChangeSet проверяется через `NativeChangeSetAdapter` (`dark_factory.context.sdd.native`): `read_change` + `evaluate_specification_gate` (спецификационный гейт T-021), применение принятой дельты — `apply_delta` с guard ревизии от параллельных изменений. Канонический e2e-пример применения пака — `tests/test_packs_product_baseline.py`.

> Примечание: применение пака к пилотному репозиторию — отдельная задача T041 (исторический T-070).
