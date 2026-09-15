import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { createApiClient } from "../api/client";
import { getToken } from "../api/token";
import { newChangeId } from "../lib/id";
import { Notice, Section } from "./Section";
import type { Change, Provider, RiskClass } from "../api/types";

const RISK_CLASSES: RiskClass[] = ["R0", "R1", "R2", "R3", "R4"];
const PROVIDERS: Provider[] = ["github", "gitlab"];

export interface IntakeFormProps {
  onCreated?: (change: Change) => void;
  /** Fail-closed UX (ADR-021 p.4): called when submitting without a token. */
  onTokenRequired: () => void;
}

interface IntakeFields {
  title: string;
  provider: Provider;
  slug: string;
  risk_class: RiskClass;
  description: string;
}

const EMPTY_FIELDS: IntakeFields = {
  title: "",
  provider: "github",
  slug: "",
  risk_class: "R1",
  description: "",
};

/**
 * Minimal intake form (FR-001): title, product, risk class; source is always
 * "console". Disabled without a token (changes:write) — no silent failures.
 */
export function IntakeForm({ onCreated, onTokenRequired }: IntakeFormProps) {
  const api = useMemo(() => createApiClient(), []);
  const [fields, setFields] = useState<IntakeFields>(EMPTY_FIELDS);
  const [submitting, setSubmitting] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const hasToken = getToken() !== null;

  const update = (patch: Partial<IntakeFields>) => setFields((prev) => ({ ...prev, ...patch }));

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setSubmitting(true);
    const change: Change = {
      id: newChangeId(),
      title: fields.title.trim(),
      description: fields.description.trim() ? fields.description.trim() : null,
      source: "console",
      external_ref: null,
      product: { provider: fields.provider, slug: fields.slug.trim() },
      risk_class: fields.risk_class,
      change_request: null,
      created_at: new Date().toISOString(),
    };
    try {
      // Idempotency-Key is generated inside the client (UUID v4, ADR-021 p.4).
      const stored = await api.createChange(change);
      setCreatedId(stored.id);
      setFields(EMPTY_FIELDS);
      onCreated?.(stored);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Section
      title="Приёмка изменения"
      description="POST /changes с токеном changes:write. Дедупликация по id — повторная отправка того же ключа вернёт существующее изменение."
    >
      {!hasToken ? (
        <p className="muted" data-testid="intake-token-hint">
          Мутации недоступны: введите токен оператора (changes:write) на экране «Настройки».
        </p>
      ) : null}
      {createdId ? (
        <Notice tone="success">Изменение создано: {createdId}</Notice>
      ) : null}
      {error ? <Notice tone="error">Ошибка приёмки: {error}</Notice> : null}
      <form onSubmit={(event) => void onSubmit(event)} data-testid="intake-form">
        <fieldset disabled={!hasToken} style={{ border: "none", padding: 0, margin: 0 }}>
          <div className="field">
            <label className="field__label" htmlFor="intake-title">
              Название
            </label>
            <input
              id="intake-title"
              type="text"
              required
              value={fields.title}
              onChange={(event) => update({ title: event.target.value })}
            />
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field__label" htmlFor="intake-provider">
                Провайдер
              </label>
              <select
                id="intake-provider"
                value={fields.provider}
                onChange={(event) => update({ provider: event.target.value as Provider })}
              >
                {PROVIDERS.map((provider) => (
                  <option key={provider} value={provider}>
                    {provider}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field__label" htmlFor="intake-slug">
                Репозиторий (slug)
              </label>
              <input
                id="intake-slug"
                type="text"
                required
                placeholder="acme/demo-service"
                value={fields.slug}
                onChange={(event) => update({ slug: event.target.value })}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="intake-risk">
                Класс риска
              </label>
              <select
                id="intake-risk"
                value={fields.risk_class}
                onChange={(event) => update({ risk_class: event.target.value as RiskClass })}
              >
                {RISK_CLASSES.map((risk) => (
                  <option key={risk} value={risk}>
                    {risk}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="intake-description">
              Описание (необязательно)
            </label>
            <textarea
              id="intake-description"
              value={fields.description}
              onChange={(event) => update({ description: event.target.value })}
            />
          </div>
          <div className="form-actions">
            <button type="submit" className="button button--primary" disabled={submitting}>
              {submitting ? "Отправка…" : "Создать изменение"}
            </button>
            <span className="field__hint">Источник: console · Idempotency-Key добавляется автоматически</span>
          </div>
        </fieldset>
      </form>
    </Section>
  );
}
