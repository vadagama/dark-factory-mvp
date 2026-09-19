import { useState } from "react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { PhaseGate, PlannedCheckStatus, UiRequirement } from "../api/types";
import { phaseLabel, shortRevision } from "../lib/artifacts";
import { deriveExpectedStateRevision } from "../lib/meta";
import { statusTone } from "../lib/statusTone";
import type { StatusTone } from "../lib/statusTone";
import { Notice } from "./Section";
import { StatusBadge } from "./StatusBadge";

const REVISION_STATE_LABELS = {
  current: "на текущей ревизии",
  stale: "неактуально",
  unbound: "без ревизии",
} as const;

const REVISION_STATE_TONES = { current: "success", stale: "warning", unbound: "muted" } as const;

/** `planned` is never green: the check has not run (T097). */
const CHECK_STATUS_LABELS: Record<PlannedCheckStatus, string> = {
  planned: "запланировано на исполнении",
  not_required: "не требуется",
  passed: "пройдено",
  failed: "не пройдено",
};

const CHECK_STATUS_TONES: Record<PlannedCheckStatus, StatusTone> = {
  planned: "warning",
  not_required: "muted",
  passed: "success",
  failed: "danger",
};

const UI_SOURCE_LABELS: Record<UiRequirement["source"], string> = {
  route: "маршрут",
  agent: "архитектор",
  operator: "оператор",
  default: "по умолчанию",
};

export interface PhaseGatePanelProps {
  gate: PhaseGate | null;
  changeId: string;
  api: ApiClient;
  hasToken: boolean;
  /** From the change card: the optimistic lock is derived as 1 + decisions_count. */
  decisionsCount: number;
  /** After the waiver is recorded the parent re-reads the workspace. */
  onChanged: () => void;
}

/**
 * «Проверки» of a phase (T087/T088, M3 T097): the phase gate as the server
 * computed it — available or not with every reason and its unblocking action,
 * the counts of questions/comments, the rework rounds used of the maximum,
 * the non-automatable checks (`planned` is shown as planned, never green),
 * the UI requirement with «Подтвердить пропуск UI» when the architect proposed
 * `not_required`, and every recorded approval with its revision state. A
 * stale approval is shown as stale, never as a green status (ADR-035 p.7);
 * no percentage anywhere.
 */
export function PhaseGatePanel({ gate, changeId, api, hasToken, decisionsCount, onChanged }: PhaseGatePanelProps) {
  const [busy, setBusy] = useState(false);
  const [waiveError, setWaiveError] = useState<string | null>(null);

  if (gate === null) {
    return <p className="muted">Состояние гейта загружается…</p>;
  }
  const requirement = gate.ui_requirement;
  // A waiver is about the phase, not a document: one without a revision (`unbound`) is in effect too.
  const waivedCurrent = gate.approvals.some(
    (approval) => approval.outcome === "waived" && (approval.state === "current" || approval.state === "unbound"),
  );
  const revision = gate.current_revision;

  const waive = async () => {
    if (requirement === null) {
      return;
    }
    setBusy(true);
    setWaiveError(null);
    try {
      await api.recordApproval(changeId, {
        gate: gate.gate ?? "ui",
        phase: gate.phase,
        outcome: "waived",
        // Sent only when the phase has a revision; a waiver needs none (M3).
        ...(revision !== null ? { subject_revision: revision } : {}),
        comment: requirement.reason ?? "UI не требуется",
        expected_state_revision: deriveExpectedStateRevision(decisionsCount),
      });
      onChanged();
    } catch (cause) {
      setWaiveError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" data-testid="phase-gate">
      <div className="row">
        <span>
          Гейт:{" "}
          {gate.gate ? (
            <>
              <span className="mono">{gate.gate}</span> <span className="muted">· фаза: {phaseLabel(gate.phase)}</span>
            </>
          ) : (
            <span className="muted">нет (решение не требуется)</span>
          )}
        </span>
        {gate.gate ? (
          <StatusBadge label={gate.available ? "доступен" : "закрыт"} tone={gate.available ? "success" : "warning"} />
        ) : null}
        {gate.approved ? <StatusBadge label="согласовано на текущей ревизии" tone="success" /> : null}
        {waivedCurrent && !gate.approved ? <StatusBadge label="пропущена с основанием" tone="neutral" /> : null}
        <span className="muted mono" title={gate.current_revision ?? undefined}>
          ревизия {shortRevision(gate.current_revision)}
        </span>
      </div>
      {gate.reasons.length > 0 ? (
        <ul data-testid="phase-gate-reasons">
          {gate.reasons.map((reason) => (
            <li key={reason.what}>
              <strong>{reason.what}</strong> — как снять: {reason.how}
            </li>
          ))}
        </ul>
      ) : null}

      {requirement !== null ? (
        <section className="gate-ui" data-testid="phase-gate-ui-requirement">
          <h4 style={{ margin: 0 }}>Нужен ли UI</h4>
          {requirement.required ? (
            <p style={{ margin: 0 }}>
              <StatusBadge label="UI требуется" tone="info" /> <span className="muted">источник: {UI_SOURCE_LABELS[requirement.source]}</span>
              {requirement.reason ? <span> — {requirement.reason}</span> : null}
            </p>
          ) : (
            <div className="stack">
              <p style={{ margin: 0 }} data-testid="phase-gate-ui-not-required">
                <StatusBadge label="Не требуется" tone="neutral" /> UI не требуется: {requirement.reason ?? "основание не указано"}{" "}
                <span className="muted">(источник: {UI_SOURCE_LABELS[requirement.source]})</span>
              </p>
              {waivedCurrent ? (
                <p className="muted" style={{ margin: 0 }}>
                  Пропуск подтверждён оператором{revision !== null ? " на текущей ревизии" : ""}.
                </p>
              ) : (
                <div className="form-actions">
                  <button
                    type="button"
                    className="button button--primary"
                    disabled={!hasToken || busy}
                    onClick={() => void waive()}
                    data-testid="waive-ui"
                    title="Записывает решение waived на гейте ui с основанием архитектора"
                  >
                    {busy ? "Записываю…" : "Подтвердить пропуск UI"}
                  </button>
                  {!hasToken ? <span className="field__hint">требует токен оператора (approvals:write)</span> : null}
                  {revision === null ? <span className="field__hint">у фазы нет ревизии — пропуск записывается без привязки к документу</span> : null}
                </div>
              )}
              {waiveError ? <Notice tone="error">Пропуск не записан: {waiveError}</Notice> : null}
            </div>
          )}
        </section>
      ) : null}

      {gate.checks.length > 0 ? (
        <section data-testid="phase-gate-checks">
          <h4 style={{ margin: "0 0 8px" }}>Проверки фазы</h4>
          <ul className="check-list">
            {gate.checks.map((check) => (
              <li key={check.id} data-testid={`check-${check.id}`}>
                <StatusBadge label={CHECK_STATUS_LABELS[check.status]} tone={CHECK_STATUS_TONES[check.status]} /> <strong>{check.label}</strong>
                {check.note ? <span className="muted"> — {check.note}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <dl className="kv" data-testid="phase-gate-counts">
        <dt>Блокирующих вопросов</dt>
        <dd>{gate.blocking_questions}</dd>
        <dt>Вопросов без ответа</dt>
        <dd>{gate.open_questions}</dd>
        <dt>Отвеченных вопросов</dt>
        <dd>{gate.answered_questions}</dd>
        <dt>Открытых замечаний</dt>
        <dd>{gate.open_comments}</dd>
        <dt>Ждут повторной проверки</dt>
        <dd>{gate.addressed_comments}</dd>
        <dt>Отвязанных замечаний</dt>
        <dd>{gate.detached_comments}</dd>
        <dt>Раундов доработки</dt>
        <dd>
          {gate.rework_rounds_used} из {gate.rework_rounds_max}
          {gate.rework_pending ? " · поручение ожидает раунда" : ""}
          {gate.rework_in_progress ? " · идёт доработка" : ""}
        </dd>
      </dl>
      <h4 style={{ margin: 0 }}>Решения по гейту</h4>
      {gate.approvals.length === 0 ? (
        <p className="muted">Решений ещё нет.</p>
      ) : (
        <table className="table" data-testid="phase-gate-approvals">
          <thead>
            <tr>
              <th>Решение</th>
              <th>Фаза</th>
              <th>Исход</th>
              <th>Ревизия</th>
              <th>Актуальность</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {gate.approvals.map((approval) => (
              <tr key={approval.decision_id}>
                <td className="mono">{approval.decision_id}</td>
                <td>{approval.phase ? phaseLabel(approval.phase) : <span className="muted" title="сервер не указал фазу решения">—</span>}</td>
                <td>
                  <StatusBadge label={approval.outcome} tone={statusTone(approval.outcome, "decision")} />
                </td>
                <td className="mono" title={approval.revision ?? undefined}>
                  {shortRevision(approval.revision)}
                </td>
                <td>
                  <StatusBadge label={REVISION_STATE_LABELS[approval.state]} tone={REVISION_STATE_TONES[approval.state]} />
                </td>
                <td>{approval.comment ?? <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
