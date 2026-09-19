"""CI stage catalog: the switchable stages of the factory pipeline (T058/T059, ADR-026/ADR-027).

The factory pipeline (``.github/workflows/ci.yml``) is a set of twenty
independently switchable stages (T058): every job carries a repository-variable
toggle ``CI_SKIP_<JOB>`` and the stage is skipped when that variable equals
``true`` (ADR-026). This module is the single source of truth for the *catalog*:

- the API serves it to the console and refuses to touch a variable outside it
  (a request can never name an arbitrary repository variable);
- ``tests/test_ci_stage_toggles.py`` keeps it in sync with the workflow, the
  workflow header and the operator instruction;
- ``console/tools/export_meta.py`` renders the same data into the console
  snapshot (ADR-021 p.5), so the console and the API never disagree about what
  a stage is.

Invariants enforced by tests (not by convention alone):

- the catalog covers exactly the jobs of ``ci.yml`` — no more, no less;
- ``title`` equals the ``name:`` of that job and the toggle ``variable`` equals
  the ``CI_SKIP_*`` variable the job's ``if`` condition reads;
- every provider call in the API goes through :func:`stage_by_job`, so an
  unknown job is rejected before any repository variable is touched.

The field vocabulary mirrors the operator instruction
(``docs/instructions/manage-ci-stages.md``): ``summary`` is what the stage
checks, ``local_command`` is how to run the same check by hand, ``weight`` is
the order of magnitude of its duration (a hint for the console badge, never a
promise about wall-clock time), and ``group`` is the console section.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class CiStageGroup(StrEnum):
    """Console section of a stage (the catalog is ordered by this grouping)."""

    PYTHON = "python"
    FACTORY = "factory"
    CONSOLE = "console"
    UIKIT = "uikit"
    IMAGE = "image"


class CiStageWeight(StrEnum):
    """Order of magnitude of a stage's duration, for the console badge."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


TOGGLE_VARIABLE_PREFIX: Final[str] = "CI_SKIP_"
"""Repository-variable prefix of every stage toggle (ADR-026)."""

SKIP_VALUE: Final[str] = "true"
"""Variable value that switches a stage off (ADR-026; written lower case by the API)."""


def variable_for_job(job: str) -> str:
    """Toggle variable of a stage: ``CI_SKIP_`` plus the job id (``-`` → ``_``, upper case)."""
    return f"{TOGGLE_VARIABLE_PREFIX}{job.upper().replace('-', '_')}"


def is_skipped_value(value: str | None) -> bool:
    """Whether a stored toggle value switches the stage off.

    Mirrors the workflow condition ``vars.CI_SKIP_<JOB> != 'true'``: GitHub
    ignores case when comparing strings in expressions, so any spelling of
    ``true`` switches the stage off, while anything else (an absent variable,
    ``false``, ``yes``, a typo) keeps it enabled — the fail-safe direction
    (ADR-026). The comparison is deliberately exact about whitespace: a padded
    value is *not* ``true`` for the workflow either, and the console must not
    report a state the pipeline would disagree with.
    """
    return value is not None and value.lower() == SKIP_VALUE


@dataclass(frozen=True, slots=True)
class CiStage:
    """One switchable CI stage of the factory pipeline."""

    job: str
    """Job id in ``.github/workflows/ci.yml`` (also the console route parameter)."""

    title: str
    """Human title; must equal the job's ``name:`` (drift-tested)."""

    group: CiStageGroup
    """Console section the stage belongs to."""

    summary: str
    """What the stage checks, in one sentence (Russian, operator-facing)."""

    local_command: str
    """How to run the same check by hand (the instruction's local equivalent)."""

    weight: CiStageWeight
    """Order of magnitude of the stage's duration."""

    @property
    def variable(self) -> str:
        """Repository variable that switches this stage off (ADR-026)."""
        return variable_for_job(self.job)


CI_STAGES: Final[tuple[CiStage, ...]] = (
    CiStage(
        job="lint",
        title="Ruff lint + format",
        group=CiStageGroup.PYTHON,
        summary=("Стиль, импорты и форматирование Python-кода (ruff check и ruff format --check)."),
        local_command="uv run ruff check . && uv run ruff format --check .",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="typecheck",
        title="Mypy typecheck",
        group=CiStageGroup.PYTHON,
        summary="Типы всего Python-кода в strict-режиме (mypy).",
        local_command="uv run mypy .",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="test",
        title="Pytest",
        group=CiStageGroup.PYTHON,
        summary=(
            "Полный набор тестов фабрики против PostgreSQL (интеграционные тесты не скипаются)."
        ),
        local_command=(
            "docker run --rm -d -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test "
            "-e POSTGRES_DB=dark_factory_test postgres:16-alpine && "
            "DARK_FACTORY_TEST_DATABASE_URL="
            "postgresql+psycopg://test:test@localhost:55432/dark_factory_test uv run pytest"
        ),
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="build",
        title="Build distribution",
        group=CiStageGroup.PYTHON,
        summary="Сборка дистрибутива Python: sdist и wheel собираются (упаковка не сломана).",
        local_command="uv build",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="security",
        title="Dependency audit",
        group=CiStageGroup.PYTHON,
        summary="Аудит зависимостей из uv.lock на известные уязвимости (pip-audit).",
        local_command=(
            "uv export --frozen --no-emit-project --no-dev > pip-audit-requirements.txt && "
            "uvx pip-audit -r pip-audit-requirements.txt"
        ),
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="factory-us1-parity",
        title="US1 walking skeleton (doctor + stage run)",
        group=CiStageGroup.FACTORY,
        summary=(
            "Проверка окружения (doctor) и детерминированный прогон стадии construction "
            "по smoke-фикстуре."
        ),
        local_command=(
            "uv run factory doctor --json && uv run factory stage run "
            "--change ./fixtures/chg_smoke.yaml --stage construction --json --non-interactive"
        ),
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="factory-stage-smoke",
        title="Factory stage template (construction on chg_smoke)",
        group=CiStageGroup.FACTORY,
        summary=(
            "Тот же smoke-прогон через переиспользуемый workflow — контракт stage-job'ов фабрики."
        ),
        local_command=(
            "uv run factory stage run --change ./fixtures/chg_smoke.yaml --stage construction "
            "--json --non-interactive && uv run python -m dark_factory.cli.ci_job stage_result.json"
        ),
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="console-lint",
        title="Console ESLint",
        group=CiStageGroup.CONSOLE,
        summary="Линт операторской консоли (ESLint).",
        local_command="cd console && npm ci && npm run lint",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="console-typecheck",
        title="Console tsc --noEmit",
        group=CiStageGroup.CONSOLE,
        summary="Типы консоли (TypeScript strict, tsc --noEmit).",
        local_command="cd console && npm run typecheck",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="console-test",
        title="Console vitest",
        group=CiStageGroup.CONSOLE,
        summary="Unit- и компонентные тесты консоли (vitest).",
        local_command="cd console && npm run test",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="console-build",
        title="Console build (vite)",
        group=CiStageGroup.CONSOLE,
        summary="Сборка продакшн-бандла консоли (vite build).",
        local_command="cd console && npm run build",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="console-e2e",
        title="Console Playwright smoke",
        group=CiStageGroup.CONSOLE,
        summary="Сквозной smoke консоли в браузере (Playwright, синтетический API).",
        local_command="cd console && npx playwright install --with-deps chromium && npm run e2e",
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="uikit-lint",
        title="UIKit ESLint + stylelint",
        group=CiStageGroup.UIKIT,
        summary="Линт UI-кита и продуктовая политика, дисциплина токенов (stylelint).",
        local_command="cd packs/ui/blueprint/ui && npm ci && npm run lint",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="uikit-typecheck",
        title="UIKit tsc --noEmit",
        group=CiStageGroup.UIKIT,
        summary="Типы UI-кита (TypeScript strict).",
        local_command="cd packs/ui/blueprint/ui && npm run typecheck",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="uikit-test",
        title="UIKit vitest (components + axe + tokens)",
        group=CiStageGroup.UIKIT,
        summary="Тесты компонентов, доступности (axe) и токенов UI-кита.",
        local_command="cd packs/ui/blueprint/ui && npm run test",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="uikit-gates",
        title="UIKit gate self-test",
        group=CiStageGroup.UIKIT,
        summary="Самотест UI-гейтов: нарушения блокируются, чистый код проходит.",
        local_command="cd packs/ui/blueprint/ui && npm run test:gates",
        weight=CiStageWeight.LIGHT,
    ),
    CiStage(
        job="uikit-storybook",
        title="UIKit Storybook build",
        group=CiStageGroup.UIKIT,
        summary="Сборка Storybook — исполняемой UI-спецификации кита.",
        local_command="cd packs/ui/blueprint/ui && npm run storybook:build",
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="uikit-visual",
        title="UIKit visual regression (pinned browser)",
        group=CiStageGroup.UIKIT,
        summary=(
            "Визуальная регрессия по закоммиченным baseline'ам в закреплённом браузере "
            "(включает сборку Storybook)."
        ),
        local_command="cd packs/ui/blueprint/ui && npm run test:visual",
        weight=CiStageWeight.MEDIUM,
    ),
    CiStage(
        job="factory-image",
        title="Factory OCI image (trusted build)",
        group=CiStageGroup.IMAGE,
        summary=(
            "Доверенная сборка образа фабрики: secrets/SAST/SCA, multi-arch публикация "
            "immutable sha-<sha>, сканы и SBOM."
        ),
        local_command="./deploy/ci/scripts/build-image.sh",
        weight=CiStageWeight.HEAVY,
    ),
    CiStage(
        job="console-image",
        title="Console OCI image (trusted build)",
        group=CiStageGroup.IMAGE,
        summary=(
            "Доверенная сборка образа консоли: secrets-скан, бандл, multi-arch публикация "
            "immutable sha-<sha>, сканы и SBOM."
        ),
        local_command=(
            "cd console && npm ci && npm run build && docker build -t dark-factory-console:local ."
        ),
        weight=CiStageWeight.HEAVY,
    ),
)

_STAGES_BY_JOB: Final[Mapping[str, CiStage]] = {stage.job: stage for stage in CI_STAGES}


def stage_by_job(job: str) -> CiStage | None:
    """Catalog entry of a stage job id, or ``None`` when the job is not a stage.

    Every provider call resolves the job through this function first: an
    unknown job never reaches a repository variable.
    """
    return _STAGES_BY_JOB.get(job)
