# Пилот T-043, инкремент 1 — прогон 10 задач через фабрику

Операторские команды инкремента 1: проброс PostgreSQL для CLI, intake задач,
продвижение запусков `factory run advance`. План —
`docs/plan-t043-e2e-pilot.md`, журнал — `docs/t043-pilot-journal.md`.

## Состав

| Файл | Назначение |
|---|---|
| `increment-1.sh` | драйвер: port-forward, окружение, doctor, intake, advance |
| `pilot_api.py` | хелпер API (stdlib): intake / status / approve |
| `tasks.json` | финализированная матрица 10 задач (5 quick R1 + 5 standard R2) |

## Отличие от плана: порт 55432

План номинально использует `127.0.0.1:5432`, но на машине оператора этот адрес
занят слушателем Docker Desktop (`com.docker`), который отвечает ошибкой
аутентификации для пользователя `factory` — IPv4-соединение до kubectl-форварда
не доходит. Проброс поднимается на `127.0.0.1:55432`
(переопределение: `PILOT_PG_LOCAL_PORT`), `DATABASE_URL` переписывается на этот
порт внутри скрипта. Обнаружено живой проверкой 2026-09-18 (журнал).

## Подготовка (один раз)

```sh
uv sync                # psycopg[binary]<3.3 — работа с БД без системного libpq
deploy/local/port-forward/install.sh   # launchd-форварды API (8000) и Console (8080)
```

Зеркало продукта для agent-стадий подготовлено оператором:
`~/Documents/GitHub/df-mirrors/github/vadagama/dark-factory-product-1`
(раскладка `<mirror_root>/<provider>/<slug>`, см. `execution/workspace.py`).
Каталог workspaces (`df-workspaces`) скрипт создаст сам.

## Прогон

```sh
# 1. Окружение и диагностика (поднимет port-forward, проверит переменные)
deploy/local/pilot/increment-1.sh env-check
deploy/local/pilot/increment-1.sh doctor

# 2. Intake матрицы задач (дедуп по id/external_ref — повтор безопасен)
deploy/local/pilot/increment-1.sh intake

# 3. Продвижение: одна команда = ровно одна стадия одного запуска
deploy/local/pilot/increment-1.sh advance chg_t043_p01

# цикл по всем десяти (replay-first, повтор безопасен; exit 10/20 — норма)
deploy/local/pilot/increment-1.sh advance-all

# 4. Состояние
deploy/local/pilot/increment-1.sh status
deploy/local/pilot/increment-1.sh status chg_t043_p01
```

Коды выхода `advance` — контракт `docs/descriptions/cli.md`: 0 — стадия
исполнена/replay, 10 — waiting (human-гейт или CI), 20 — blocked, 1/2 — ошибка.

## Human-гейты (решения оператора)

Гейты approval/merge — только человек (ADR-011). Решение подаётся через API
(Console или командой; токен оператора — из `.env`):

```sh
set -a; . ./.env; set +a
deploy/local/pilot/pilot_api.py approve \
  --change-id chg_t043_p01 --gate <gate> --outcome approved \
  --subject-revision <revision> [--comment "..."]
```

Значения `gate` и `subject_revision` берутся из `run status`/`next_action`
карточки запуска. Агенты не аппрувят и не мержат.

## Ограничения

- Скрипт читает `.env` репозитория и передаёт секреты процессам через
  окружение; значения никогда не печатаются (ADR-009).
- `approve` — осознанное действие человека; скрипт только оформляет запрос.
- Foreground-процессы агентов идут на машине оператора (worktrees из
  локального зеркала); параллельные прогоны одного change недопустимы
  (эксклюзивное исполнение, ADR-019).
