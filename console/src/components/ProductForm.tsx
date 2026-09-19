import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { createApiClient } from "../api/client";
import { getToken } from "../api/token";
import { newProductId } from "../lib/id";
import { Notice, Section } from "./Section";
import type { Product, ProductCreateRequest, Provider } from "../api/types";

const PROVIDERS: Provider[] = ["github", "gitlab"];

export interface ProductFormProps {
  onCreated?: (product: Product) => void;
  /** Fail-closed UX (ADR-021 p.4): called when submitting without a token. */
  onTokenRequired: () => void;
}

interface ProductFields {
  id: string;
  name: string;
  provider: Provider;
  slug: string;
  description: string;
  repository_url: string;
  baseline_ref: string;
  dev_env_ref: string;
}

function emptyFields(): ProductFields {
  return {
    id: newProductId(),
    name: "",
    provider: "github",
    slug: "",
    description: "",
    repository_url: "",
    baseline_ref: "",
    dev_env_ref: "",
  };
}

function orNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/**
 * «Добавить продукт» (T066/T075, ADR-030 p.1): the id is generated
 * (`prd_<hex>`) but editable — the server replays the same id instead of
 * creating a duplicate. Disabled without a token (products:write, operator
 * role) — no silent failures.
 */
export function ProductForm({ onCreated, onTokenRequired }: ProductFormProps) {
  const api = useMemo(() => createApiClient(), []);
  const [fields, setFields] = useState<ProductFields>(emptyFields);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasToken = getToken() !== null;
  const update = (patch: Partial<ProductFields>) => setFields((prev) => ({ ...prev, ...patch }));

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setSubmitting(true);
    const body: ProductCreateRequest = {
      id: fields.id.trim(),
      name: fields.name.trim(),
      repository: { provider: fields.provider, slug: fields.slug.trim() },
      description: orNull(fields.description),
      repository_url: orNull(fields.repository_url),
      baseline_ref: orNull(fields.baseline_ref),
      dev_env_ref: orNull(fields.dev_env_ref),
    };
    try {
      const stored = await api.createProduct(body);
      setFields(emptyFields());
      onCreated?.(stored);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Section
      title="Добавить продукт"
      description="POST /products с токеном products:write (роль operator). id выбирается клиентом: повтор того же id вернёт существующий продукт, а не создаст второй."
    >
      {!hasToken ? (
        <p className="muted" data-testid="product-token-hint">
          Мутации недоступны: введите токен оператора (products:write) на экране «Служебное → Настройки».
        </p>
      ) : null}
      {error ? <Notice tone="error">Ошибка регистрации: {error}</Notice> : null}
      <form onSubmit={(event) => void onSubmit(event)} data-testid="product-form">
        <fieldset disabled={!hasToken} style={{ border: "none", padding: 0, margin: 0 }}>
          <div className="form-grid">
            <div className="field">
              <label className="field__label" htmlFor="product-id">
                ID
              </label>
              <input
                id="product-id"
                type="text"
                required
                className="mono"
                value={fields.id}
                onChange={(event) => update({ id: event.target.value })}
              />
              <span className="field__hint">Сгенерирован автоматически, можно изменить.</span>
            </div>
            <div className="field">
              <label className="field__label" htmlFor="product-name">
                Название
              </label>
              <input
                id="product-name"
                type="text"
                required
                value={fields.name}
                onChange={(event) => update({ name: event.target.value })}
              />
            </div>
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field__label" htmlFor="product-provider">
                Провайдер
              </label>
              <select
                id="product-provider"
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
              <label className="field__label" htmlFor="product-slug">
                Репозиторий (slug)
              </label>
              <input
                id="product-slug"
                type="text"
                required
                placeholder="acme/demo-service"
                value={fields.slug}
                onChange={(event) => update({ slug: event.target.value })}
              />
            </div>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="product-description">
              Описание (необязательно)
            </label>
            <textarea
              id="product-description"
              value={fields.description}
              onChange={(event) => update({ description: event.target.value })}
            />
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field__label" htmlFor="product-repository-url">
                URL репозитория (необязательно)
              </label>
              <input
                id="product-repository-url"
                type="url"
                placeholder="https://github.com/acme/demo-service"
                value={fields.repository_url}
                onChange={(event) => update({ repository_url: event.target.value })}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="product-baseline-ref">
                Baseline ref (необязательно)
              </label>
              <input
                id="product-baseline-ref"
                type="text"
                value={fields.baseline_ref}
                onChange={(event) => update({ baseline_ref: event.target.value })}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="product-dev-env-ref">
                Dev env ref (необязательно)
              </label>
              <input
                id="product-dev-env-ref"
                type="text"
                value={fields.dev_env_ref}
                onChange={(event) => update({ dev_env_ref: event.target.value })}
              />
            </div>
          </div>
          <div className="form-actions">
            <button type="submit" className="button button--primary" disabled={submitting}>
              {submitting ? "Отправка…" : "Добавить продукт"}
            </button>
            <span className="field__hint">Idempotency-Key добавляется автоматически</span>
          </div>
        </fieldset>
      </form>
    </Section>
  );
}
