import { useMemo, useState } from "react";
import { ApiError, createApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import { ErrorState, LoadingState, Notice, Section } from "../components/Section";
import { TokenDialog } from "../components/TokenDialog";
import type { CiStage, CiStageGroup, CiStageWeight, CiStages } from "../api/types";

/** Render order of the server-side stage groups; the server order wins inside a group. */
const GROUP_ORDER: CiStageGroup[] = ["python", "factory", "console", "uikit", "image"];
const GROUP_TITLES: Record<CiStageGroup, string> = {
  python: "Python-гейты",
  factory: "Factory-гейты (dogfooding)",
  console: "Console-гейты",
  uikit: "UIKit-гейты",
  image: "Доверенные сборки образов",
};
const WEIGHT_LABELS: Record<CiStageWeight, string> = {
  light: "лёгкий",
  medium: "средний",
  heavy: "тяжёлый",
};

export interface CiStageGroupBucket {
  group: CiStageGroup;
  title: string;
  stages: CiStage[];
}

/** Buckets stages by group, preserving the server order inside each group. */
export function groupStages(stages: CiStage[]): CiStageGroupBucket[] {
  return GROUP_ORDER.map((group) => ({
    group,
    title: GROUP_TITLES[group],
    stages: stages.filter((stage) => stage.group === group),
  })).filter((bucket) => bucket.stages.length > 0);
}

/** Visible state next to the switch, so the state is never colour-only. */
export function enabledLabel(enabled: boolean | null): string {
  if (enabled === null) {
    return "неизвестно";
  }
  return enabled ? "включён" : "выключен";
}

/** Maps the toggle statuses of the contract to operator-readable Russian. */
export function describeToggleError(error: ApiError, job: string): string {
  switch (error.status) {
    case 401:
      return "Нужен токен оператора с scope ci:write: введите его на экране «Настройки».";
    case 403:
      return "Токену не хватает прав: нужен scope ci:write и роль operator.";
    case 404:
      return `Этап «${job}» неизвестен API.`;
    case 422:
      return `API отклонил тело запроса (422): ожидается {"enabled": true|false}.`;
    case 502:
      return "GitHub не ответил: переменную репозитория изменить не удалось (502).";
    case 503:
      return "Переключатели не сконфигурированы на этом контуре (503).";
    default:
      return error.detail;
  }
}

/**
 * Screen 6 (T059): switches the CI stages of the factory repository on and off.
 * Each switch is a `CI_SKIP_<JOB>` GitHub repository variable (T058/ADR-026);
 * the API is fail-closed when its GitHub credentials are absent
 * (`available: false`, every `enabled` is null).
 */
export function CiStagesPage() {
  const api = useMemo(() => createApiClient(), []);
  const state = useAsync(async () => api.listCiStages(), []);

  // Results of successful PUTs, keyed by job. A fresh GET already contains
  // every toggle, so it supersedes the patches (see the render-time reset).
  const [patched, setPatched] = useState<Record<string, CiStage>>({});
  const [patchedFor, setPatchedFor] = useState<CiStages | null>(null);
  const [pending, setPending] = useState<string[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkMessage, setBulkMessage] = useState<{ tone: "success" | "error"; text: string } | null>(
    null,
  );
  const [dialogOpen, setDialogOpen] = useState(false);

  // Adjusting state during render (the react.dev pattern already used by
  // GatesPage) drops patches and row errors the moment a reloaded list lands,
  // without showing a frame of stale toggle states.
  if (state.data !== null && state.data !== patchedFor) {
    setPatchedFor(state.data);
    setPatched({});
    setRowErrors({});
  }

  const list = state.data;
  const stages = (list?.stages ?? []).map((stage) => patched[stage.job] ?? stage);
  const available = list?.available ?? false;
  const offJobs = stages.filter((stage) => stage.enabled === false).map((stage) => stage.job);

  const toggle = async (stage: CiStage) => {
    const next = stage.enabled !== true;
    setPending((jobs) => [...jobs, stage.job]);
    setRowErrors((errors) =>
      Object.fromEntries(Object.entries(errors).filter(([job]) => job !== stage.job)),
    );
    try {
      const updated = await api.setCiStageEnabled(stage.job, next);
      setPatched((current) => ({ ...current, [updated.job]: updated }));
    } catch (cause) {
      const error = cause instanceof ApiError ? cause : new ApiError(0, null, String(cause));
      setRowErrors((errors) => ({ ...errors, [stage.job]: describeToggleError(error, stage.job) }));
      if (error.isUnauthorized) {
        setDialogOpen(true);
      }
    } finally {
      setPending((jobs) => jobs.filter((job) => job !== stage.job));
    }
  };

  const enableAll = async () => {
    const jobs = [...offJobs];
    setBulkRunning(true);
    setBulkMessage(null);
    const failures: string[] = [];
    for (const job of jobs) {
      try {
        await api.setCiStageEnabled(job, true);
      } catch (cause) {
        const error = cause instanceof ApiError ? cause : new ApiError(0, null, String(cause));
        failures.push(`${job}: ${describeToggleError(error, job)}`);
      }
    }
    setBulkRunning(false);
    setBulkMessage(
      failures.length === 0
        ? { tone: "success", text: `Включены все этапы (${jobs.length}).` }
        : {
            tone: "error",
            text: `Не удалось включить ${failures.length} из ${jobs.length} — ${failures.join("; ")}`,
          },
    );
    // The sequential PUTs return single stages; re-read the list to settle on
    // the server state (and to drop the per-row patches).
    state.reload();
  };

  return (
    <>
      <Section
        title="Этапы CI"
        description="Переключатели пайплайна CI фабрики — переменные репозитория CI_SKIP_<JOB> (T058, ADR-026). Выключенный этап пропускается в GitHub Actions; по умолчанию включены все этапы."
      >
        {state.loading ? <LoadingState /> : null}
        {state.error ? (
          <div className="stack">
            <ErrorState message={state.error.detail} />
            <div className="form-actions">
              <button type="button" className="button" onClick={state.reload}>
                Повторить
              </button>
            </div>
          </div>
        ) : null}

        {list ? (
          <>
            <p className="row" data-testid="ci-repository">
              <span className="muted">Репозиторий:</span>
              <span className="mono">{list.repository ?? "недоступен"}</span>
            </p>

            {available ? (
              <Notice tone="warning">
                <span data-testid="ci-safety-banner">
                  Переключатели — удобство разработки, а не способ получить зелёный CI. Перед merge верните все этапы
                  включёнными: merge — решение человека, проверки должны быть зелёными (ADR-011).
                </span>
              </Notice>
            ) : (
              <Notice tone="warning">
                <span data-testid="ci-unavailable-banner">
                  Управление этапами недоступно на этом контуре: {list.reason ?? "нет доступа к GitHub"}. Пока API не
                  сконфигурирован учётными данными GitHub, переключатели выключены, а состояние этапов неизвестно.
                </span>
              </Notice>
            )}

            <div className="ci-toolbar">
              <button
                type="button"
                className="button"
                disabled={!available || bulkRunning || offJobs.length === 0}
                onClick={() => void enableAll()}
              >
                {bulkRunning ? "Включение…" : "Включить все этапы"}
              </button>
              <span className="muted" data-testid="ci-off-count">
                Выключено этапов: {offJobs.length}
              </span>
            </div>

            {bulkMessage ? (
              <Notice tone={bulkMessage.tone === "error" ? "error" : "success"}>
                {bulkMessage.text}
              </Notice>
            ) : null}

            <div className="stack" data-testid="ci-stages">
              {groupStages(stages).map((bucket) => (
                <div key={bucket.group}>
                  <h3 className="stage-group__title">{bucket.title}</h3>
                  <div className="stage-list">
                    {bucket.stages.map((stage) => {
                      const isPending = pending.includes(stage.job);
                      const rowError = rowErrors[stage.job];
                      return (
                        <div
                          key={stage.job}
                          className="stage-row"
                          data-testid={`ci-stage-${stage.job}`}
                        >
                          <div className="stage-row__main">
                            <div className="row">
                              <strong>{stage.title}</strong>
                              <span className="mono muted">{stage.job}</span>
                              <code className="mono muted">{stage.variable}</code>
                              <span className="badge badge--neutral">
                                {WEIGHT_LABELS[stage.weight]}
                              </span>
                            </div>
                            <p className="muted stage-row__summary">{stage.summary}</p>
                            <code className="mono stage-row__command">{stage.local_command}</code>
                            {rowError ? <Notice tone="error">{rowError}</Notice> : null}
                          </div>
                          <div className="stage-row__control">
                            {/* A button[role=switch] is what Radix renders too, without the
                                extra dependency; aria-checked carries the state. */}
                            <button
                              type="button"
                              role="switch"
                              aria-checked={stage.enabled === null ? "mixed" : stage.enabled}
                              aria-label={`Переключатель этапа ${stage.job}`}
                              className="switch"
                              disabled={!available || isPending || bulkRunning}
                              onClick={() => void toggle(stage)}
                            />
                            <span className="muted" data-testid={`ci-stage-state-${stage.job}`}>
                              {enabledLabel(stage.enabled)}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </Section>

      <TokenDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        reason="Переключение этапов CI требует Bearer-токен с scope ci:write и роль operator. Токен вводится на экране «Настройки»."
      />
    </>
  );
}
