import { useMemo, useState } from "react";
import type { FormEvent, RefObject } from "react";
import { createApiClient } from "../api/client";
import { getToken } from "../api/token";
import { briefAuthor, briefToFields, fieldsToBrief } from "../lib/brief";
import type { BriefFields } from "../lib/brief";
import { statusTone } from "../lib/statusTone";
import { Notice, Section } from "./Section";
import { StatusBadge } from "./StatusBadge";
import type { IntakeBrief } from "../api/types";

export interface BriefSectionProps {
  changeId: string;
  brief: IntakeBrief | null;
  /** Called after a successful PUT so the page can re-read the change and its guidance. */
  onSaved: () => void;
  /** Fail-closed UX (ADR-021 p.4): called when saving without a token. */
  onTokenRequired: () => void;
  /** The `problem` field — the guidance primary «Дополнить бриф» focuses it. */
  problemRef?: RefObject<HTMLTextAreaElement | null>;
}

const AUTHOR_LABELS = { operator: "оператор", agent: "агент" } as const;

/**
 * «Бриф» of a change (T071/T075): the structured fields, their status and
 * author, the operator's original text (collapsed) and the last agent error;
 * below, an inline editor that replaces the brief via PUT /changes/{id}/brief.
 * The editor remounts (key) only when the server brief actually changes.
 */
export function BriefSection({ changeId, brief, onSaved, onTokenRequired, problemRef }: BriefSectionProps) {
  // The editor remounts on a server-side brief change (its key below), which
  // is exactly what a successful save causes — so the success notice lives
  // here, in the parent that survives the remount.
  const [saved, setSaved] = useState(false);
  const briefKey = JSON.stringify(brief);
  return (
    <Section
      title="Бриф"
      description="Проблема, цель, ограничения и то, что вне объёма. Статус выводится сервером: complete — когда заполнены problem и goal."
    >
      <div className="stack" data-testid="brief-section">
        {brief === null ? (
          <p className="muted" data-testid="brief-absent">
            У изменения нет брифа (создано до T071). Заполните поля ниже — сохранение создаст бриф.
          </p>
        ) : (
          <>
            <div className="row">
              <StatusBadge label={brief.status} tone={statusTone(brief.status, "brief")} />
              <span className="muted">
                сформулировал: {brief.formulated_by ? AUTHOR_LABELS[brief.formulated_by] : "—"}
              </span>
            </div>
            {brief.error ? (
              <Notice tone="warning">Бриф остался черновиком: {brief.error}</Notice>
            ) : null}
            <dl className="kv">
              <dt>Проблема</dt>
              <dd data-testid="brief-problem">{brief.problem ?? <span className="muted">не заполнена</span>}</dd>
              <dt>Цель</dt>
              <dd data-testid="brief-goal">{brief.goal ?? <span className="muted">не заполнена</span>}</dd>
              <dt>Ограничения</dt>
              <dd>
                {brief.constraints.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {brief.constraints.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt>Вне объёма</dt>
              <dd>
                {brief.out_of_scope.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {brief.out_of_scope.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </dd>
            </dl>
            {brief.source_text ? (
              <details data-testid="brief-source">
                <summary className="muted">Исходный текст оператора</summary>
                <p className="brief-source">{brief.source_text}</p>
              </details>
            ) : null}
          </>
        )}
        {saved ? <Notice tone="success">Бриф сохранён.</Notice> : null}
        <BriefEditor
          key={briefKey}
          changeId={changeId}
          brief={brief}
          onSaved={() => {
            setSaved(true);
            onSaved();
          }}
          onDirty={() => setSaved(false)}
          onTokenRequired={onTokenRequired}
          problemRef={problemRef}
        />
      </div>
    </Section>
  );
}

interface BriefEditorProps extends BriefSectionProps {
  /** Called on any edit, so a stale success notice disappears. */
  onDirty: () => void;
}

function BriefEditor({ changeId, brief, onSaved, onDirty, onTokenRequired, problemRef }: BriefEditorProps) {
  const api = useMemo(() => createApiClient(), []);
  const initial = useMemo(() => briefToFields(brief), [brief]);
  const [fields, setFields] = useState<BriefFields>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasToken = getToken() !== null;

  const update = (patch: Partial<BriefFields>) => {
    onDirty();
    setFields((prev) => ({ ...prev, ...patch }));
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setSaving(true);
    // The agent's wording survives as `agent` only while untouched.
    const agentFields = brief?.formulated_by === "agent" ? initial : null;
    const body = fieldsToBrief(fields, {
      source_text: brief?.source_text ?? null,
      formulated_by: briefAuthor(fields, agentFields),
    });
    try {
      await api.updateChangeBrief(changeId, body);
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={(event) => void onSubmit(event)} data-testid="brief-form">
      <h4 style={{ margin: "0 0 8px" }}>Редактировать бриф</h4>
      {!hasToken ? (
        <p className="muted" data-testid="brief-token-hint">
          Сохранение недоступно: введите токен оператора (changes:write) на экране «Служебное → Настройки».
        </p>
      ) : null}
      {error ? <Notice tone="error">Ошибка сохранения брифа: {error}</Notice> : null}
      <fieldset disabled={!hasToken} style={{ border: "none", padding: 0, margin: 0 }}>
        <div className="form-grid">
          <div className="field">
            <label className="field__label" htmlFor="brief-problem-input">
              Проблема
            </label>
            <textarea
              id="brief-problem-input"
              ref={problemRef}
              value={fields.problem}
              onChange={(event) => update({ problem: event.target.value })}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="brief-goal-input">
              Цель
            </label>
            <textarea id="brief-goal-input" value={fields.goal} onChange={(event) => update({ goal: event.target.value })} />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="brief-constraints-input">
              Ограничения (по одному в строке)
            </label>
            <textarea
              id="brief-constraints-input"
              value={fields.constraints}
              onChange={(event) => update({ constraints: event.target.value })}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="brief-out-of-scope-input">
              Вне объёма (по одному в строке)
            </label>
            <textarea
              id="brief-out-of-scope-input"
              value={fields.out_of_scope}
              onChange={(event) => update({ out_of_scope: event.target.value })}
            />
          </div>
        </div>
        <div className="form-actions">
          <button type="submit" className="button button--primary" disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить бриф"}
          </button>
          <span className="field__hint">PUT /changes/{changeId}/brief · Idempotency-Key добавляется автоматически</span>
        </div>
      </fieldset>
    </form>
  );
}
