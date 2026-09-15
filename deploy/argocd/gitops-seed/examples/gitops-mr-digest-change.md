# Пример GitOps-MR: смена immutable digest (fixture, T032)

Этот файл — fixture: он показывает точную форму изменения, которым релиз
попадает в dev (DoD T-043: «GitOps MR меняет digest, Argo разворачивает его в
apps-dev; откат — revert коммита»). Значения digest здесь — учебные, не реальный
образ. До T033 такой MR создаётся вручную; с T033 его создаёт и мержит trusted
finalizer после ручного merge продуктового MR (ADR-011 п.2).

## MR: `release: promote pilot-dev to sha256:3f5fe0b1…`

- **Что**: пилотная нагрузка в `apps-dev` переводится на новый образ.
- **Почему**: продуктовый MR `vadagama/products-pilot#12` (итоговый SHA
  `731ac91…`) слит человеком; run `run-2026-09-14-0142` собрал образ и
  опубликовал digest (T033); smoke/evidence — T034.
- **Откат**: `git revert <этот-коммит>` — Argo CD синхронизирует прежний
  digest (ADR-010 п.1); схема БД при откате не меняется (expand/contract,
  ADR-010 п.6).

```diff
--- a/envs/dev/pilot/values.yaml
+++ b/envs/dev/pilot/values.yaml
@@
 image:
   repository: busybox
-  # Immutable digest (ADR-015 п.5). The placeholder is valid seed content:
-  # the first GitOps MR replaces it with the real digest of the built image.
-  digest: "sha256:__PILOT_IMAGE_DIGEST__"
+  # Immutable digest (ADR-015 п.5).
+  # promote: product_commit=731ac91 factory_version=0.3.1 run=run-2026-09-14-0142
+  digest: "sha256:3f5fe0b1f4b0e5c8d7a69c2b8e1f0a4d5c6b7a890123456789abcdef0123456f"
```

## Что происходит после merge MR

1. Argo CD (приложение `apps-dev`, app-of-apps, automated sync) замечает
   новый коммит `main` этого репозитория (poll ~3 мин или webhook) и
   синхронизирует дочернее приложение `pilot-dev`.
2. `pilot-dev` (automated sync) применяет отрендеренный Deployment с новым
   `image: busybox@sha256:…` в namespace `apps-dev`.
3. T034 фиксирует health/smoke в release evidence; статус «выпущено» — только
   при успешном smoke на этом digest (ADR-011 п.6).

## Правила, которые проверяет ревью GitOps-MR

- digest — полный `sha256:<64 hex>`, никакого `latest`/тега (ADR-015 п.5);
- в описании указан итоговый SHA продукта и run, собравший образ (протокол
  версионирования, ADR-015 п.5);
- изменений вне `envs/dev/**` нет; секретов в diff нет.
