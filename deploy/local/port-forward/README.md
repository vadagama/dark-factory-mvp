# Автоподнятие локальных port-forward (T-093)

Два launchd-агента, которые держат туннели локального контура живыми:

| Агент | Туннель | Что даёт |
|---|---|---|
| `dev.dark-factory.port-forward.api` | `svc/dark-factory 8000:8000` | http://127.0.0.1:8000/docs (Swagger) |
| `dev.dark-factory.port-forward.console` | `svc/dark-factory-console 8080:80` | http://127.0.0.1:8080/ (Console, same-origin `/api`) |

## Зачем

Штатный локальный доступ к Console/API — `kubectl port-forward` (Ingress-контроллера в bootstrap нет). Ручные туннели умирают вместе с кластером Docker Desktop и при перезагрузке ноутбука (инцидент 2026-09-18: кластер перезапустился — `localhost:8080` «опять недоступен»). launchd перезапускает `kubectl`, пока кластер недоступен (ретрай раз в ~10 с), и восстанавливает туннели при логине (`RunAtLoad`).

## Установка

```bash
deploy/local/port-forward/install.sh
```

Идемпотентно: повторный запуск перерендеривает plist-ы и перезагружает агентов. Ручные port-forward для тех же сервисов останавливаются, чтобы освободить порты. Кластер может быть выключен — агенты сами подхватят, когда он поднимется.

Другой контекст (по умолчанию `docker-desktop`, как у остальных deploy-скриптов):

```bash
KUBECTL_CTX=<имя> deploy/local/port-forward/install.sh
```

## Что устанавливается

- `~/Library/LaunchAgents/dev.dark-factory.port-forward.{api,console}.plist` — рендер шаблона `launchd.plist.template` (абсолютный путь к kubectl, контекст, сервис, порты).
- Логи: `~/Library/Logs/dark-factory/port-forward/{api,console}.log`.

Не редактируйте рендеренные plist-ы руками: правьте шаблон и перезапускайте `install.sh`.

## Проверка

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/             # 200 — Console
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/runs  # 200 — API через прокси nginx
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/openapi.json # 200 — API напрямую
launchctl print "gui/$(id -u)/dev.dark-factory.port-forward.console"
```

## Удаление

```bash
deploy/local/port-forward/uninstall.sh
```

## Нюансы

- Форварды идут на Service, а не на под: после пересоздания подов (helm upgrade, синк Argo CD) туннель рвётся и launchd пересобирает его за ~10 с — без ручного вмешательства.
- Пока кластер выключен, агенты ретраят раз в ~10 с и пишут ошибки в лог — это норма, а не поломка.
- Порт 8080 конфликтует с другим типовым форвардом — `kubectl -n argocd port-forward svc/argocd-server 8080:8080` (см. `deploy/argocd/README.md`): одновременно держать нельзя.
- Ручной режим (без launchd) — прежние две команды из `docs/instructions/run-console-ui-t036.md`, шаг 4.
