import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router";
import { createApiClient, ApiError } from "../api/client";
import { useAsync } from "../api/hooks";
import { getToken } from "../api/token";
import { EmptyState, ErrorState, LoadingState, Notice, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import { formatDateTime, formatGate } from "../lib/format";
import { deriveExpectedStateRevision } from "../lib/meta";
import { statusTone } from "../lib/statusTone";
import type { Decision, DecisionOutcome, Finding, Gate, GateResult } from "../api/types";

export const APPROVAL_GATES: Gate[] = [
  "specification",
  "planning",
  "code",
  "ui",
  "review",
  "verification",
  "release",
];
const OUTCOMES: DecisionOutcome[] = ["approved", "rejected", "waived"];

export interface GatesModel {
  runs: { runId: string }[];
  gatesByRun: Map<string, GateResult[]>;
  blockersByRun: Map<string, Finding[]>;
  approvals: Decision[];
  decisionsCount: number;
  changeTitle: string;
}

/**
 * Submit one version-bound approval (ADR-009 p.7, ADR-021 p.4):
 * - reuses the given Idempotency-Key for the automatic 409 retry (a 409 means
 *   nothing was written, so the key stays replay-safe);
 * - on 409 re-reads the card, re-derives expected_state_revision and retries
 *   once; a second 409 surfaces the error to the operator.
 * Extracted from the component for testability.
 */
export async function submitApproval(
  api: ReturnType<typeof createApiClient>,
  changeId: string,
  body: {
    gate: Gate;
    outcome: DecisionOutcome;
    subject_revision: string;
    comment: string | null;
    expected_state_revision: number | null;
  },
  options: { idempotencyKey: string },
): Promise<{ decision: Decision; retried: boolean }> {
  const send = (expected: number | null) =>
    api.recordApproval(
      changeId,
      {
        gate: body.gate,
        outcome: body.outcome,
        subject_revision: body.subject_revision,
        comment: body.comment,
        expected_state_revision: expected,
      },
      { idempotencyKey: options.idempotencyKey },
    );
  try {
    return { decision: await send(body.expected_state_revision), retried: false };
  } catch (cause) {
    if (!(cause instanceof ApiError) || !cause.isStateRevisionConflict) {
      throw cause;
    }
    // 409 (ADR-006 p.4): re-read the card, recompute the optimistic lock, retry once.
    const card = await api.getChange(changeId);
    const fresh = deriveExpectedStateRevision(card.decisions_count);
    return { decision: await send(fresh), retried: true };
  }
}

export function GatesPage() {
  const { changeId = "" } = useParams();
  const api = useMemo(() => createApiClient(), []);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  const state = useAsync<GatesModel>(async () => {
    const [card, approvals] = await Promise.all([
      api.getChange(changeId),
      api.getChangeApprovals(changeId),
    ]);
    const runIds = card.runs.map((run) => run.run_id);
    const [gates, blockers] = await Promise.all([
      Promise.all(runIds.map((runId) => api.getRunGates(runId))),
      Promise.all(
        runIds.map((runId) => api.getRunFindings(runId, { severity: "blocker", status: "open" })),
      ),
    ]);
    return {
      runs: runIds.map((runId) => ({ runId })),
      gatesByRun: new Map(runIds.map((runId, index) => [runId, gates[index]])),
      blockersByRun: new Map(runIds.map((runId, index) => [runId, blockers[index]])),
      approvals,
      decisionsCount: card.decisions_count,
      changeTitle: card.title,
    };
  }, [changeId, reloadNonce]);

  return (
    <>
      <Section
        title="Гейты и согласования"
        description="Последний результат по каждому гейту прогона, открытые blocker-находки и version-bound решения оператора (ADR-009 п.7, ADR-011 п.2)."
      >
        <div className="row">
          <Link to={`/changes/${encodeURIComponent(changeId)}`} className="mono">
            {changeId}
          </Link>
          {state.data ? <span className="muted">{state.data.changeTitle}</span> : null}
        </div>
        {state.loading ? <LoadingState /> : null}
        {state.error ? <ErrorState message={state.error.detail} /> : null}
        {state.data && state.data.runs.length === 0 ? (
          <EmptyState label="У изменения нет прогонов — гейты появятся после запуска." />
        ) : null}
        {state.data?.runs.map(({ runId }) => (
          <div key={runId} className="stack">
            <h4 className="mono" style={{ marginBottom: 0 }}>
              {runId}
            </h4>
            <table className="table" data-testid={`gates-${runId}`}>
              <thead>
                <tr>
                  <th>Гейт</th>
                  <th>Статус</th>
                  <th>SHA</th>
                  <th>Резюме</th>
                </tr>
              </thead>
              <tbody>
                {(state.data?.gatesByRun.get(runId) ?? []).map((gate) => (
                  <tr key={gate.gate}>
                    <td>{formatGate(gate.gate)}</td>
                    <td>
                      <StatusBadge label={gate.status} tone={statusTone(gate.status, "gate")} />
                    </td>
                    <td className="mono">{gate.sha ?? "—"}</td>
                    <td>{gate.summary ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(state.data?.blockersByRun.get(runId) ?? []).length > 0 ? (
              <div>
                <h4>Открытые blocker-находки</h4>
                <ul data-testid={`blockers-${runId}`}>
                  {(state.data?.blockersByRun.get(runId) ?? []).map((finding) => (
                    <li key={finding.id}>
                      <span className="mono">{finding.id}</span> —{" "}
                      {finding.required_action ?? "требует действия"}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ))}
      </Section>

      <Section title="Решения" description="История approvals: решение привязано к ревизии (commit_sha) — новая версия требует нового решения.">
        {state.data && state.data.approvals.length === 0 ? (
          <EmptyState label="Решений ещё нет." />
        ) : null}
        {state.data && state.data.approvals.length > 0 ? (
          <table className="table" data-testid="approvals-table">
            <thead>
              <tr>
                <th>Гейт</th>
                <th>Решение</th>
                <th>Кем</th>
                <th>Ревизия</th>
                <th>Когда</th>
                <th>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {state.data.approvals.map((decision) => (
                <tr key={decision.id}>
                  <td>{formatGate(decision.gate)}</td>
                  <td>
                    <StatusBadge
                      label={decision.outcome}
                      tone={statusTone(decision.outcome, "decision")}
                    />
                  </td>
                  <td>{decision.decided_by}</td>
                  <td className="mono">{decision.commit_sha ?? "—"}</td>
                  <td className="muted">{formatDateTime(decision.decided_at)}</td>
                  <td>{decision.comment ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Section>

      {/* expected_state_revision re-derives when the card (re)loads: while
          the card is loading decisionsCount is 0, and the initial state must
          not stick to that. The form itself stays mounted so the success
          notice survives the post-submission reload. */}
      <ApprovalForm
        changeId={changeId}
        decisionsCount={state.data?.decisionsCount ?? 0}
        onTokenRequired={() => setDialogOpen(true)}
        onRecorded={() => setReloadNonce((value) => value + 1)}
      />
      <TokenDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        reason="Запись решения требует Bearer-токен с approvals:write и роль operator (сервисная роль не согласовывает)."
      />
    </>
  );
}

interface ApprovalFormProps {
  changeId: string;
  decisionsCount: number;
  onTokenRequired: () => void;
  onRecorded: () => void;
}

export function ApprovalForm({ changeId, decisionsCount, onTokenRequired, onRecorded }: ApprovalFormProps) {
  const api = useMemo(() => createApiClient(), []);
  const [gate, setGate] = useState<Gate>("review");
  const [outcome, setOutcome] = useState<DecisionOutcome>("approved");
  const [subjectRevision, setSubjectRevision] = useState("");
  const [comment, setComment] = useState("");
  const [expectedRevision, setExpectedRevision] = useState<string>(String(deriveExpectedStateRevision(decisionsCount)));
  const [syncedDecisionsCount, setSyncedDecisionsCount] = useState(decisionsCount);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The card reloads after each recorded decision; keep the optimistic lock
  // in sync with the fresh decisions_count without remounting the form.
  // Adjusting the state during render — the pattern react.dev documents in
  // "You Might Not Need an Effect" — refreshes the displayed value on a new
  // count while leaving the field editable afterwards (the guard keeps the
  // user's own edits from being overwritten on unrelated re-renders).
  if (syncedDecisionsCount !== decisionsCount) {
    setSyncedDecisionsCount(decisionsCount);
    setExpectedRevision(String(deriveExpectedStateRevision(decisionsCount)));
  }

  const hasToken = getToken() !== null;

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setSubmitting(true);
    const body = {
      gate,
      outcome,
      subject_revision: subjectRevision.trim(),
      comment: comment.trim() ? comment.trim() : null,
      expected_state_revision: expectedRevision.trim() ? Number(expectedRevision.trim()) : null,
    };
    try {
      // One Idempotency-Key per user submission (including the 409 retry).
      const { decision, retried } = await submitApproval(api, changeId, body, {
        idempotencyKey: crypto.randomUUID(),
      });
      setMessage(
        `Решение записано: ${decision.id}${retried ? " (после перечитывания карточки — 409)" : ""}`,
      );
      setSubjectRevision("");
      setComment("");
      onRecorded();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Section
      title="Новое решение (version-bound)"
      description="POST /changes/{id}/approvals с токеном approvals:write и ролью operator. subject_revision обязателен: решение авторизует именно эту ревизию."
    >
      {!hasToken ? (
        <p className="muted" data-testid="approval-token-hint">
          Запись решения недоступна: нужен токен оператора (approvals:write, роль operator) на экране «Настройки».
        </p>
      ) : null}
      {message ? <Notice tone="success">{message}</Notice> : null}
      {error ? <Notice tone="error">Ошибка: {error}</Notice> : null}
      <form onSubmit={(event) => void onSubmit(event)} data-testid="approval-form">
        <div className="form-grid">
          <div className="field">
            <label className="field__label" htmlFor="approval-gate">
              Гейт
            </label>
            <select
              id="approval-gate"
              value={gate}
              onChange={(event) => setGate(event.target.value as Gate)}
              disabled={!hasToken}
            >
              {APPROVAL_GATES.map((value) => (
                <option key={value} value={value}>
                  {formatGate(value)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="approval-outcome">
              Решение
            </label>
            <select
              id="approval-outcome"
              value={outcome}
              onChange={(event) => setOutcome(event.target.value as DecisionOutcome)}
              disabled={!hasToken}
            >
              {OUTCOMES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="approval-revision">
              subject_revision
            </label>
            <input
              id="approval-revision"
              type="text"
              required
              placeholder="abc1234"
              value={subjectRevision}
              onChange={(event) => setSubjectRevision(event.target.value)}
              disabled={!hasToken}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="approval-expected">
              expected_state_revision
            </label>
            <input
              id="approval-expected"
              type="number"
              min={1}
              value={expectedRevision}
              onChange={(event) => setExpectedRevision(event.target.value)}
              disabled={!hasToken}
            />
            <span className="field__hint">
              Производится из карточки (1 + число решений); при 409 консоль перечитает карточку и повторит.
            </span>
          </div>
        </div>
        <div className="field">
          <label className="field__label" htmlFor="approval-comment">
            Комментарий (необязательно)
          </label>
          <textarea
            id="approval-comment"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            disabled={!hasToken}
          />
        </div>
        <div className="form-actions">
          <button type="submit" className="button button--primary" disabled={submitting || !hasToken}>
            {submitting ? "Запись…" : "Записать решение"}
          </button>
        </div>
      </form>
    </Section>
  );
}
