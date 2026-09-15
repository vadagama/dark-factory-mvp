import { useMemo } from "react";
import { Link } from "react-router";
import { createApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import { EmptyState, ErrorState, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { formatCost, formatDateTime, formatNumber, formatStage } from "../lib/format";
import { meta } from "../lib/meta";
import { statusTone } from "../lib/statusTone";
import type { RunCard } from "../api/types";

/** Share of a used budget against its configured limit; null when unset. */
export function budgetShare(used: number, budget: number | null): number | null {
  if (budget === null || budget <= 0) {
    return null;
  }
  return Math.min(100, Math.round((used / budget) * 100));
}

/**
 * Screen 4: configured limits come from the generated snapshot (meta.json,
 * ADR-021 p.5); actual spend comes from the API (RunCard.usage over the
 * attempts). The latest 20 runs are shown (the API list cap is 200).
 */
export function BudgetsPage() {
  const api = useMemo(() => createApiClient(), []);
  const state = useAsync(async () => {
    const summaries = await api.listRuns({ limit: 20 });
    const runs = await Promise.all(summaries.map((summary) => api.getRun(summary.run_id)));
    return runs;
  }, []);

  const limits = meta.limits;

  return (
    <>
      <Section
        title="Настроенные лимиты"
        description="Источник: снапшот из правил фабрики (console/src/generated/meta.json ← src/dark_factory/rules + BudgetSnapshot). null = лимит не задан."
      >
        <table className="table" data-testid="limits-table">
          <thead>
            <tr>
              <th>Лимит</th>
              <th>Значение</th>
              <th>Смысл</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Rework (раунды)</td>
              <td>
                <strong>{limits.max_rework_rounds}</strong>
              </td>
              <td className="muted">Максимум раундов переработки на прогон (FR-008)</td>
            </tr>
            <tr>
              <td>Токены</td>
              <td>{limits.token_budget === null ? "не задан" : formatNumber(limits.token_budget)}</td>
              <td className="muted">Бюджет токенов на прогон (FR-018)</td>
            </tr>
            <tr>
              <td>Стоимость</td>
              <td>{limits.cost_budget === null ? "не задан" : `$${limits.cost_budget}`}</td>
              <td className="muted">Бюджет стоимости на прогон (FR-018)</td>
            </tr>
            <tr>
              <td>Дедлайн</td>
              <td>{limits.deadline === null ? "не задан" : formatDateTime(limits.deadline)}</td>
              <td className="muted">Крайний срок автономной работы</td>
            </tr>
          </tbody>
        </table>
      </Section>

      <Section
        title="Фактический расход по прогонам"
        description="usage из RunCard: сумма по всем попыткам (FR-018/FR-024, SC-003). Доля от лимита — когда лимит задан."
      >
        {state.loading ? <p className="state">Загрузка…</p> : null}
        {state.error ? <ErrorState message={state.error.detail} /> : null}
        {state.data && state.data.length === 0 ? (
          <EmptyState label="Прогонов ещё нет." />
        ) : null}
        {state.data && state.data.length > 0 ? (
          <table className="table" data-testid="usage-table">
            <thead>
              <tr>
                <th>Прогон</th>
                <th>Изменение</th>
                <th>Статус</th>
                <th>Токены (prompt+completion)</th>
                <th>Доля токенов</th>
                <th>Стоимость</th>
                <th>Доля стоимости</th>
                <th>Ручных вмешательств</th>
                <th>Попыток всего</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((run: RunCard) => {
                const attempts = run.stages.reduce((sum, stage) => sum + stage.attempt_count, 0);
                const tokenShare = budgetShare(
                  run.usage.prompt_tokens + run.usage.completion_tokens,
                  limits.token_budget,
                );
                const costShare = budgetShare(
                  run.usage.cost === null ? 0 : Number(run.usage.cost),
                  limits.cost_budget === null ? null : Number(limits.cost_budget),
                );
                return (
                  <tr key={run.run_id}>
                    <td>
                      <Link to={`/changes/${encodeURIComponent(run.change_id)}`} className="mono">
                        {run.run_id}
                      </Link>
                    </td>
                    <td className="mono">{run.change_id}</td>
                    <td>
                      <StatusBadge label={run.status} tone={statusTone(run.status, "run")} />
                    </td>
                    <td>
                      {formatNumber(run.usage.prompt_tokens)} + {formatNumber(run.usage.completion_tokens)}
                    </td>
                    <td>{tokenShare === null ? <span className="muted">—</span> : `${tokenShare}%`}</td>
                    <td>{formatCost(run.usage.cost)}</td>
                    <td>{costShare === null ? <span className="muted">—</span> : `${costShare}%`}</td>
                    <td>{formatNumber(run.usage.manual_interventions)}</td>
                    <td title={run.stages.map((stage) => `${formatStage(stage.stage)}: ${stage.attempt_count}`).join(", ")}>
                      {attempts}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </Section>
    </>
  );
}
