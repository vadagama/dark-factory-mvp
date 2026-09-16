# Git-цикл задачи

Штатный порядок выполнения любой задачи, меняющей файлы репозитория (код, тесты, документация, конфиги). Цикл обрамляет реализацию: роль `ci-cd` подключается в начале (ветка) и в конце (MR), а merge остаётся за человеком. Короткое описание — в `AGENTS.md`, поведение оркестратора — в `.agents/skills/dark-factory/SKILL.md`.

## Шаги

| # | Шаг | Роль | Действие | Результат |
|---|---|---|---|---|
| 1 | Ветка | `ci-cd` | Синхронизировать `main` и создать от него ветку задачи, запушить | Ветка задачи в origin |
| 2 | Реализация | `develop` | Внести минимальные изменения в ветке задачи | Коммиты в ветке |
| 3 | Проверка | `develop` | Фактически запустить диагностику, тесты, lint/typecheck по изменённым файлам | Зелёные проверки + evidence |
| 4 | MR | `ci-cd` | Закоммитить, запушить и открыть MR против `main` с описанием и evidence | Открытый MR |
| 5 | Merge | человек | Ревью и merge (ADR-011) | Merge в `main` |

Проверка (шаг 3) выполняется до MR и повторяется в CI на самом MR (`.github/workflows/ci.yml`). Реализация не начинается до создания ветки (шаг 1) и не считается завершённой до открытия MR (шаг 4).

## Именование веток

`<type>/t-<NNN>-<slug>`, где `<NNN>` — номер задачи из `specs/001-dark-factory-mvp/tasks.md`, `<slug>` — краткий английский идентификатор:

- `feat/t-003-domain-model` — новая функциональность;
- `fix/t-042-fix-gate-status` — исправление;
- `chore/…`, `docs/…`, `refactor/…`, `test/…` — по характеру задачи.

`main` всегда стабилен: push в него напрямую запрещён.

## Команды

**Шаг 1 — ветка (ci-cd):**

```sh
git fetch origin --prune
git checkout main
git pull --rebase origin main
git checkout -b feat/t-<NNN>-<slug>
git push -u origin feat/t-<NNN>-<slug>
```

**Шаг 4 — MR (ci-cd):**

```sh
git add <файлы задачи>
git commit -m "feat: <описание> (T-<NNN>)"
git push -u origin HEAD
gh pr create --base main --title "<type>: <описание> (T-<NNN>)" --body "<summary + evidence>"
```

В коммит включаются только файлы задачи — несвязанные изменения в рабочем дереве не трогаются.

Провайдер задаётся на уровне репозитория (ADR-019): для GitHub — `gh`/PR, для GitLab — `glab mr create --base main`/MR. Доменная модель оперирует единым `ChangeRequestRef`, а не терминами провайдера.

## Evidence в MR

Описание MR обязано фиксировать: что изменено, какие проверки запущены и с каким результатом (команды и итог), что осталось. Самооценка без фактического прогона проверок не принимается.

## Правила

- Коммиты, push и MR — только в ветку задачи; в `main` — никогда.
- Merge в `main` — только человек (ADR-011): агенты MR не мержат.
- Conventional Commits на английском: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Секреты и ключи — никогда в коде, коммитах и git.
- Вне git-цикла задачи коммитить и пушить без явной просьбы пользователя нельзя.

## Ссылки

- `AGENTS.md` — правила агентов, конвейер, DoD.
- `docs/adr/ADR-011-risk-based-merge-release-policy.md` — merge/release policy: merge — человек, deploy в dev — автоматически.
- `docs/adr/ADR-019-multi-provider-sc-ci-github-first.md` — провайдеры SC/CI, `ChangeRequestRef`, GitHub первым.
- `.agents/skills/dark-factory/SKILL.md` — оркестратор: маршрут ролей и git-цикл задачи.
