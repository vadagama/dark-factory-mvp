import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { Decision, Gate, PhaseGate } from "../api/types";
import { shortRevision } from "../lib/artifacts";
import { deriveExpectedStateRevision } from "../lib/meta";
import { Notice } from "./Section";

export interface ApprovalFormProps {
  changeId: string;
  api: ApiClient;
  gate: PhaseGate;
  /** The gate the approval records against (`specification` for requirements). */
  gateName: Gate;
  /** From the change card: the optimistic lock is derived as 1 + decisions_count. */
  decisionsCount: number;
  hasToken: boolean;
  onTokenRequired: () => void;
  /** The server's action label («Согласовать требования») — the form never invents its own. */
  label: string;
  onRecorded: (decision: Decision) => void;
  onCancel: () => void;
  /** After a 409 the operator asked to reload the workspace and decide on what they see. */
  onReload: () => void;
}

/**
 * The decision of a phase gate (T088 bottom panel, ADR-009 p.7, ADR-032 p.5):
 * version-bound to `current_revision` of the phase gate — the operator sees
 * the revision they approve and cannot type another one. «Пропустить фазу»
 * is the explicit `waived` with a mandatory reason. A 409 (gate closed
 * meanwhile, or the revision moved) shows the server detail and offers a
 * reload; nothing is retried blindly.
 */
export function ApprovalForm({
  changeId,
  api,
  gate,
  gateName,
  decisionsCount,
  hasToken,
  onTokenRequired,
  label,
  onRecorded,
  onCancel,
  onReload,
}: ApprovalFormProps) {
  const [outcome, setOutcome] = useState<"approved" | "waived">("approved");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ detail: string; conflict: boolean } | null>(null);
  const revision = gate.current_revision;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    if (revision === null) {
      return;
    }
    if (outcome === "waived" && comment.trim().length === 0) {
      setError({ detail: "Пропуск фазы требует основания — заполните комментарий (ADR-032 п.5).", conflict: false });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const decision = await api.recordApproval(changeId, {
        gate: gateName,
        outcome,
        subject_revision: revision,
        comment: comment.trim() || null,
        expected_state_revision: deriveExpectedStateRevision(decisionsCount),
      });
      onRecorded(decision);
    } catch (cause) {
      const detail = cause instanceof ApiError ? cause.detail : String(cause);
      setError({ detail, conflict: cause instanceof ApiError && cause.isStateRevisionConflict });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="approval-form stack" onSubmit={(event) => void submit(event)} data-testid="approval-form">
      <p style={{ margin: 0 }}>
        {label}: решение записывается на ревизии{" "}
        <strong className="mono" data-testid="approval-revision" title={revision ?? undefined}>
          {shortRevision(revision)}
        </strong>
        {revision === null ? <span className="muted"> — ревизии нет, согласовывать нечего</span> : null}.
      </p>
      <div className="radio-group">
        <label>
          <input type="radio" name="outcome" value="approved" checked={outcome === "approved"} onChange={() => setOutcome("approved")} disabled={busy} />
          <span>Согласовать (approved)</span>
        </label>
        {gate.skippable ? (
          <label>
            <input type="radio" name="outcome" value="waived" checked={outcome === "waived"} onChange={() => setOutcome("waived")} disabled={busy} />
            <span>Пропустить фазу с основанием (waived)</span>
          </label>
        ) : null}
      </div>
      <label className="field">
        <span className="field__label">{outcome === "waived" ? "Основание пропуска (обязательно)" : "Комментарий (необязательно)"}</span>
        <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={2} disabled={busy} data-testid="approval-comment" />
      </label>
      <div className="form-actions">
        <button type="submit" className="button button--primary" disabled={busy || revision === null} data-testid="approval-submit">
          {busy ? "Записываю…" : "Записать решение"}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        {!hasToken ? <span className="field__hint">требует токен оператора (approvals:write)</span> : null}
      </div>
      {error ? (
        <div className="stack">
          <Notice tone="error">Решение не записано: {error.detail}</Notice>
          {error.conflict ? (
            <button type="button" className="button" onClick={onReload} data-testid="approval-reload">
              Обновить и решить заново
            </button>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}
