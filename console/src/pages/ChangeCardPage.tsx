import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { createApiClient } from "../api/client";
import { POLL_MS, useAsync } from "../api/hooks";
import { BriefSection } from "../components/BriefSection";
import { NextStep } from "../components/NextStep";
import { EmptyState, ErrorState, LoadingState, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import { formatCost, formatDateTime, formatNumber, formatProduct, formatStage, formatTime } from "../lib/format";
import type { ConsoleAction } from "../lib/guidance";
import { statusTone } from "../lib/statusTone";
import type { Evidence, Finding, IntakeBrief, RunCard, Scenario, SpendLimit } from "../api/types";

const SCENARIO_LABELS: Record<Scenario, string> = {
  specs_only: "только спецификации — до согласования требований/архитектуры/UI",
  full: "полный — до доставки в dev",
};

/** Per-run payload shown on the card: usage, gates, evidence, open blockers. */
interface RunDetails {
  run: RunCard;
  evidence: Evidence[];
  blockers: Finding[];
}

interface ChangeDetailModel {
  changeId: string;
  runs: RunDetails[];
  decisionsCount: number;
  change: {
    id: string;
    title: string;
    description: string | null;
    source: string;
    externalRef: string | null;
    productProvider: string;
    productSlug: string;
    riskClass: string;
    createdAt: string;
    productId: string | null;
    brief: IntakeBrief | null;
    scenario: Scenario;
    spendLimit: SpendLimit | null;
  };
  trace: {
    runId: string;
    status: string;
    chain: { stage: string; status: string; attemptNumber: number; producedAt: string; artifactUris: string[] }[];
  }[];
}

export function ChangeCardPage() {
  const { changeId = "" } = useParams();
  const api = useMemo(() => createApiClient(), []);
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const problemRef = useRef<HTMLTextAreaElement | null>(null);

  // The next step is its own read model (T074): polled next to the card so a
  // brief edit or a run transition shows up without a manual reload.
  const guidance = useAsync(() => api.getChangeGuidance(changeId), [changeId, reloadNonce], { pollMs: POLL_MS });

  const reloadAll = () => {
    setReloadNonce((value) => value + 1);
  };

  const perform = (action: ConsoleAction) => {
    switch (action.kind) {
      case "edit_brief": {
        const field = problemRef.current;
        if (field) {
          if (typeof field.scrollIntoView === "function") {
            field.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          field.focus();
        }
        return;
      }
      case "open_change":
      case "open_run":
        reloadAll();
        return;
      case "approve_phase":
      case "rework":
      case "focus_questions":
      case "open_artifacts":
        // The decision lives in the ChangeSet workspace (T088); the card is the legacy view.
        navigate(`/changes/${encodeURIComponent(action.changeId)}`);
        return;
      case "new_change":
        navigate(
          state.data?.change.productId
            ? `/products/${encodeURIComponent(state.data.change.productId)}/new-change`
            : "/",
        );
        return;
      case "validate_product":
      case "reload_product":
        navigate(`/products/${encodeURIComponent(action.productId)}`);
        return;
    }
  };

  const state = useAsync<ChangeDetailModel>(async () => {
    const card = await api.getChange(changeId);
    const [trace, ...runDetails] = await Promise.all([
      api.getChangeTrace(changeId),
      ...card.runs.map(async (runRef): Promise<RunDetails> => {
        const run = await api.getRun(runRef.run_id);
        const evidence = await api.getRunEvidence(runRef.run_id);
        const blockers =
          run.open_blockers > 0
            ? await api.getRunFindings(runRef.run_id, { severity: "blocker", status: "open" })
            : [];
        return { run, evidence, blockers };
      }),
    ]);
    return {
      changeId: card.id,
      decisionsCount: card.decisions_count,
      change: {
        id: card.id,
        title: card.title,
        description: card.description,
        source: card.source,
        externalRef: card.external_ref,
        productProvider: card.product.provider,
        productSlug: card.product.slug,
        riskClass: card.risk_class,
        createdAt: card.created_at,
        productId: card.product_id,
        brief: card.brief,
        scenario: card.scenario,
        spendLimit: card.spend_limit,
      },
      runs: runDetails,
      trace: trace.runs.map((runTrace) => ({
        runId: runTrace.run_id,
        status: runTrace.status,
        chain: runTrace.chain.map((stage) => ({
          stage: stage.stage,
          status: stage.status,
          attemptNumber: stage.attempt_number,
          producedAt: stage.produced_at,
          artifactUris: stage.artifacts.map((artifact) => artifact.uri),
        })),
      })),
    };
  }, [changeId, reloadNonce], { pollMs: POLL_MS });

  return (
    <>
      {guidance.data ? <NextStep guidance={guidance.data} onPerform={perform} /> : null}
      {guidance.error && !guidance.data ? (
        <Section title="Следующий шаг">
          <ErrorState message={guidance.error.detail} />
        </Section>
      ) : null}

      <Section title="Изменение">
        {state.updatedAt !== null ? (
          <p className="muted" data-testid="updated-at">
            обновлено {formatTime(state.updatedAt)}
          </p>
        ) : null}
        {state.loading ? <LoadingState /> : null}
        {state.error ? <ErrorState message={state.error.detail} /> : null}
        {state.data ? (
          <div className="stack">
            <h3 style={{ margin: 0 }}>{state.data.change.title}</h3>
            {state.data.change.description ? <p>{state.data.change.description}</p> : null}
            <div className="row">
              <span className="mono">{state.data.change.id}</span>
              <StatusBadge label={state.data.change.riskClass} tone="info" />
              <span className="muted">источник: {state.data.change.source}</span>
              <span className="muted">
                продукт: {formatProduct(state.data.change.productProvider, state.data.change.productSlug)}
              </span>
              {state.data.change.externalRef ? (
                <span className="mono">external_ref: {state.data.change.externalRef}</span>
              ) : null}
              <span className="muted">создано: {formatDateTime(state.data.change.createdAt)}</span>
              <span className="muted">решений: {state.data.decisionsCount}</span>
            </div>
            <div className="row" data-testid="change-intake-line">
              <span className="muted">сценарий: {SCENARIO_LABELS[state.data.change.scenario] ?? state.data.change.scenario}</span>
              <span className="muted">
                лимит:{" "}
                {state.data.change.spendLimit
                  ? `${state.data.change.spendLimit.cost_budget_usd} USD${
                      state.data.change.spendLimit.token_budget !== null
                        ? `, ${formatNumber(state.data.change.spendLimit.token_budget)} токенов`
                        : ""
                    }`
                  : "не задан"}
              </span>
              {state.data.change.productId ? (
                <Link to={`/products/${encodeURIComponent(state.data.change.productId)}`} className="mono">
                  продукт: {state.data.change.productId}
                </Link>
              ) : (
                <span className="muted">продукт: не привязан</span>
              )}
            </div>
            <div className="row">
              <Link to={`/changes/${encodeURIComponent(state.data.changeId)}/gates`} className="button">
                Гейты и согласования
              </Link>
            </div>
          </div>
        ) : null}
      </Section>

      {state.data ? (
        <BriefSection
          changeId={state.data.changeId}
          brief={state.data.change.brief}
          onSaved={reloadAll}
          onTokenRequired={() => setDialogOpen(true)}
          problemRef={problemRef}
        />
      ) : null}

      {state.data && state.data.runs.length === 0 ? (
        <Section title="Прогоны">
          <EmptyState label="У изменения ещё нет запусков." />
        </Section>
      ) : null}

      {state.data?.runs.map(({ run, evidence, blockers }) => (
        <Section
          key={run.run_id}
          title={`Прогон ${run.run_id}`}
          description={`Маршрут: ${run.route} · провайдер: ${run.provider} · state_revision: ${run.state_revision}`}
        >
          <div className="row">
            <StatusBadge label={run.status} tone={statusTone(run.status, "run")} />
            <span className="muted">
              открытых блокеров: <strong data-testid={`open-blockers-${run.run_id}`}>{run.open_blockers}</strong>
            </span>
            <span className="muted">
              токены: {formatNumber(run.usage.prompt_tokens + run.usage.completion_tokens)} (prompt{" "}
              {formatNumber(run.usage.prompt_tokens)} / completion {formatNumber(run.usage.completion_tokens)})
            </span>
            <span className="muted">стоимость: {formatCost(run.usage.cost)}</span>
            <span className="muted">
              ручных вмешательств: {formatNumber(run.usage.manual_interventions)}
            </span>
          </div>
          <h4>Стадии</h4>
          <table className="table" data-testid={`stages-${run.run_id}`}>
            <thead>
              <tr>
                <th>Стадия</th>
                <th>Статус</th>
                <th>Попыток</th>
                <th>Входная ревизия</th>
              </tr>
            </thead>
            <tbody>
              {run.stages.map((stage) => (
                <tr key={`${stage.stage}-${stage.input_revision ?? ""}`}>
                  <td>{formatStage(stage.stage)}</td>
                  <td>
                    <StatusBadge label={stage.status} tone={statusTone(stage.status, "stage")} />
                  </td>
                  <td>{stage.attempt_count}</td>
                  <td className="mono">{stage.input_revision ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {blockers.length > 0 ? (
            <>
              <h4>Открытые блокеры</h4>
              <ul>
                {blockers.map((finding) => (
                  <li key={finding.id}>
                    <span className="mono">{finding.id}</span> — {finding.required_action ?? "требует действия"}
                    {finding.file ? <span className="muted"> ({finding.file})</span> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <h4>Evidence</h4>
          {evidence.length === 0 ? (
            <p className="muted">Нет evidence в этом прогоне.</p>
          ) : (
            <ul data-testid={`evidence-${run.run_id}`}>
              {evidence.map((item) => (
                <li key={item.id}>
                  <a href={item.uri} target="_blank" rel="noreferrer" className="mono">
                    {item.id}
                  </a>{" "}
                  <span className="muted">
                    ({item.type}
                    {item.required ? ", required" : ""}
                    {item.available ? "" : ", unavailable"})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      ))}

      {state.data && state.data.trace.length > 0 ? (
        <Section
          title="Цепочка стадий (SC-007)"
          description="Канонический порядок: specification → planning → construction → review_verification → release."
        >
          {state.data.trace.map((runTrace) => (
            <div key={runTrace.runId} className="stack">
              <div className="row">
                <span className="mono">{runTrace.runId}</span>
                <StatusBadge label={runTrace.status} tone={statusTone(runTrace.status, "run")} />
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Стадия</th>
                    <th>Статус</th>
                    <th>Попытка</th>
                    <th>Артефакты</th>
                  </tr>
                </thead>
                <tbody>
                  {runTrace.chain.map((stage) => (
                    <tr key={`${runTrace.runId}-${stage.stage}`}>
                      <td>{formatStage(stage.stage)}</td>
                      <td>
                        <StatusBadge label={stage.status} tone={statusTone(stage.status, "stage")} />
                      </td>
                      <td>{stage.attemptNumber}</td>
                      <td>
                        {stage.artifactUris.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          stage.artifactUris.map((uri) => (
                            <div key={uri}>
                              <a href={uri} target="_blank" rel="noreferrer" className="mono">
                                {uri}
                              </a>
                            </div>
                          ))
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </Section>
      ) : null}
      <TokenDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
}
