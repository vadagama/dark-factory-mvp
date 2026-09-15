# dark-factory-runs

Компактный immutable индекс доказательств Software Dark Factory (ADR-015 п.4,
ADR-006 п.2): `RunManifest`, итоговый `StageResult`, approvals/decisions, ссылки,
digest и provenance. Это **не** execution journal — операционный журнал живёт в
PostgreSQL (ADR-004), тяжёлая evidence (логи, отчёты, скриншоты, SBOM) — в CI
artifacts провайдера через `ArtifactStorePort` (ADR-009, ADR-015 п.4).

> Этот файл — часть seed-контента (T-061): репозиторий ещё не создан, его
> создание — решение человека. Процедура bootstrap — в конце файла. Схемы в
> `schema/` сгенерированы из versioned-контрактов и проверяются дрейф-тестом
> `tests/test_deploy_runs_schema.py`.

## Правила репозитория

1. **Только через MR.** Прямой push в `main` запрещён (branch protection:
   required PR, squash, запрет force-push). Append-only trunk: исправление
   принятого record — новая запись/ревизия, а не скрытое переписывание истории.
2. **Никаких секретов.** Пароли, токены, ключи, приватные данные в репозиторий
   не попадают (ADR-015 п.4: sanitization). Публикатор проверяет запись
   fail-closed: совпадение с шаблоном секрета (ключ, GitHub-токен, JWT, `Bearer`,
   AWS access key, `password=...`, credentials в URI) блокирует публикацию.
3. **Только immutable ссылки.** Никаких `latest` и плавающих ссылок — только
   конкретные revisions, digest'ы и semver (ADR-015 п.5). URI evidence с
   сегментом `latest` блокируется: без этого run не воспроизводим.
4. **Никаких бинарных и тяжёлых данных.** Скриншоты, большие логи, build output,
   модели и архивы в репозитории не помещаются: в записи остаются только URI и
   checksum. Общий размер одной записи ограничен (`MAX_RUN_RECORD_BYTES`,
   512 KiB); превышение блокирует публикацию.
5. **Компактность и читаемость.** Запись адресуется идемпотентно по
   `change_id` + `run_id`, поэтому её можно найти без обхода всей истории
   (git-structure §8).

## Структура

```text
schema/
  run-manifest.schema.json    # JSON Schema контракта RunManifest (ADR-015 п.5)
  stage-result.schema.json    # JSON Schema контракта StageResult (ADR-015 п.3)
runs/
  <YYYY>/<MM>/                # partitioning: год и месяц создания run в UTC
    <change-id>/
      <run-id>/
        manifest.yaml         # RunManifest: точные ревизии всех входов
        snapshot.json         # самодостаточный RunRecord — первичный артефакт записи
        stages/
          <stage>-<attempt>.json  # по файлу на каждый immutable StageResult
        usage.json            # сводка usage/cost и бюджет run
        decisions.md          # таблица approvals/decisions (только если они есть)
        evidence-index.json   # компактный индекс evidence и artifacts
retention/                    # зарезервировано: политики retention (P1)
```

`<change-id>` и `<run-id>` — это идентификаторы, приведённые к одному безопасному
элементу пути (`:` и прочие непортируемые символы заменяются на `-`), поэтому
partition всегда плоский и не зависит от платформы. Идентификатор восстанавливается
из `snapshot.json`, а не из имени каталога: коллизия слагов не может подменить
запись.

Пустые секции не создаются: `decisions.md` появляется только при наличии решений,
каталог `stages/` — только при наличии результатов стадий.

## Протокол записи (ADR-015 п.4)

| Пункт протокола | Реализация в T-061 (P0-минимум) | Осталось (P1) |
|---|---|---|
| один commit на run или стадию | запись одного run = один каталог; коммит делает CI/CD | автоматизация commit/push в этот репозиторий |
| partitioning, например `YYYY/MM/project/run-id` | `<YYYY>/<MM>/<change-id>/<run-id>`, год/месяц — из `created_at` в UTC | — |
| single-writer либо MR-free append | идемпотентная запись + запрет перезаписи (immutability) | протокол single-writer/append, снимающий гонку записи |
| максимальный размер записи | `MAX_RUN_RECORD_BYTES` (512 KiB) на запись | — |
| retention и архивирование | классы retention у evidence (`audit` / `standard`) | исполнение политик retention и архивация |
| sanitization секретов и ПДн | шаблонный скрин сериализованной записи, fail-closed | расширение шаблонов по мере появления полей |
| immutability завершённого record | повторная публикация того же содержимого — no-op; иное содержимое по тому же адресу отклоняется | — |
| подпись manifest и проверка digest evidence | проверка checksum evidence, разрешимой локально | подпись manifest |

Класс retention: evidence с `required: true` — `audit` (срок хранения обязан
перекрывать максимальное окно approval/retry/аудита, ADR-009 п.9), остальная — 
`standard` (наследует retention соответствующего CI artifact).

## Публикация записи

Публикатор — `factory run publish` (`dark_factory.cli.runs`); он читает
`run_record.json`, который уже создали `factory stage run --evidence-dir` (T011)
или `factory release verify --evidence-dir` (T034/T-045), проверяет запись и
раскладывает её в checkout этого репозитория.

```bash
# runs-root — checkout dark-factory-runs; переменная окружения DARK_FACTORY_RUNS_ROOT задаёт его по умолчанию
uv run factory run publish \
  --record evidence/run_01H/run_record.json \
  --runs-root ../dark-factory-runs
```

Коды выхода: `0` — запись создана или уже была (unchanged); `1` — запись
отклонена (секрет, размер, неполная цепочка evidence, конфликт immutability);
`2` — невалидный вход или не задан runs-root (ничего не записано).

Публикация отделена от исполнения стадии намеренно: стадии идут в sandboxed
подах с агентной работой (ADR-018), а запись в аудит-репозиторий — действие
доверенного публикатора; это то же разделение, что между агентом и финализатором
в merge-политике (T-026).

## Bootstrap репозитория (выполняет человек; команды НЕ исполняются из T-061)

```bash
# 1. Создать репозиторий (решение человека; по умолчанию private):
gh repo create vadagama/dark-factory-runs --private \
  --description "Compact immutable evidence index of dark factory runs" \
  --disable-wiki --disable-issues

# 2. Развернуть seed и запушить:
git init -b main dark-factory-runs
cp -R deploy/runs/. dark-factory-runs/
cd dark-factory-runs
git add -A
git commit -m "chore: seed runs repository (T-061)"
git remote add origin git@github.com:vadagama/dark-factory-runs.git
git push -u origin main
```

## Ограничения P0

Коммит и push записи в этот репозиторий выполняет CI/CD-контур (доверенный
уровень), а не публикатор: он только раскладывает запись в checkout. Протокол
single-writer / merge-request-free append, исполнение retention и архивация, а
также подпись manifest — P1 (ADR-015 п.4) и зафиксированы в `docs/tech-dept.md`.
