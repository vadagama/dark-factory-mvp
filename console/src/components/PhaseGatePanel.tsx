import type { PhaseGate } from "../api/types";
import { shortRevision } from "../lib/artifacts";
import { statusTone } from "../lib/statusTone";
import { StatusBadge } from "./StatusBadge";

const REVISION_STATE_LABELS = {
  current: "на текущей ревизии",
  stale: "неактуально",
  unbound: "без ревизии",
} as const;

const REVISION_STATE_TONES = { current: "success", stale: "warning", unbound: "muted" } as const;

/**
 * «Проверки» of a phase (T087/T088): the phase gate as the server computed
 * it — available or not with every reason and its unblocking action, the
 * counts of questions/comments, the rework rounds used of the maximum and
 * every recorded approval with its revision state. A stale approval is shown
 * as stale, never as a green status (ADR-035 p.7); no percentage anywhere.
 */
export function PhaseGatePanel({ gate }: { gate: PhaseGate | null }) {
  if (gate === null) {
    return <p className="muted">Состояние гейта загружается…</p>;
  }
  return (
    <div className="stack" data-testid="phase-gate">
      <div className="row">
        <span>Гейт: {gate.gate ? <span className="mono">{gate.gate}</span> : <span className="muted">нет (решение не требуется)</span>}</span>
        {gate.gate ? (
          <StatusBadge label={gate.available ? "доступен" : "закрыт"} tone={gate.available ? "success" : "warning"} />
        ) : null}
        {gate.approved ? <StatusBadge label="согласовано на текущей ревизии" tone="success" /> : null}
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
