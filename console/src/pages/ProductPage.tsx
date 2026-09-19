import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError, createApiClient } from "../api/client";
import { POLL_MS, useAsync } from "../api/hooks";
import { getToken } from "../api/token";
import { NextStep } from "../components/NextStep";
import { EmptyState, ErrorState, LoadingState, Notice, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import { formatCost, formatDateTime, formatNumber, formatProduct, formatTime } from "../lib/format";
import type { ConsoleAction } from "../lib/guidance";
import { statusTone } from "../lib/statusTone";
import type { Change, Guidance, GuidanceAction, Product, RepositoryValidation } from "../api/types";

export interface ProductModel {
  product: Product;
  guidance: Guidance;
  changes: Change[];
}

const SCENARIO_LABELS = { specs_only: "только спецификации", full: "полный" } as const;

/** The «Новая фича» action as the server lists it (primary or secondary), if at all. */
export function findNewChangeAction(guidance: Guidance | null): GuidanceAction | null {
  if (!guidance) {
    return null;
  }
  return [guidance.primary, ...guidance.secondary].find((action) => action.api === "POST /changes") ?? null;
}

/**
 * Product page (ADR-037, M1): header with readiness, the server-computed next
 * step, then stacked sections — overview, changes of the product, baseline and
 * deliveries (the last two are honest placeholders until M2/M5).
 */
export function ProductPage() {
  const { productId = "" } = useParams();
  const api = useMemo(() => createApiClient(), []);
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<RepositoryValidation | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  const state = useAsync<ProductModel>(
    async () => {
      const [product, guidance, changes] = await Promise.all([
        api.getProduct(productId),
        api.getProductGuidance(productId),
        api.listChanges({ product_id: productId, limit: 200 }),
      ]);
      return { product, guidance, changes };
    },
    [productId],
    { pollMs: POLL_MS },
  );

  const hasToken = getToken() !== null;
  const newChangePath = `/products/${encodeURIComponent(productId)}/new-change`;

  const validate = async () => {
    setValidationError(null);
    setValidation(null);
    if (!hasToken) {
      setDialogOpen(true);
      return;
    }
    setValidating(true);
    try {
      const view = await api.validateProduct(productId);
      setValidation(view.validation);
      state.reload();
    } catch (cause) {
      // 503 = provisioning not configured on this contour: the server detail is the message.
      setValidationError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setValidating(false);
    }
  };

  const perform = (action: ConsoleAction) => {
    switch (action.kind) {
      case "validate_product":
        void validate();
        return;
      case "reload_product":
        state.reload();
        return;
      case "new_change":
        navigate(newChangePath);
        return;
      case "open_change":
      case "approve_phase":
      case "rework":
      case "focus_questions":
      case "open_artifacts":
        navigate(`/changes/${encodeURIComponent(action.changeId)}`);
        return;
      case "edit_brief":
        navigate(`/changes/${encodeURIComponent(action.changeId)}/card`);
        return;
      case "open_run":
        state.reload();
        return;
    }
  };

  const newChangeAction = findNewChangeAction(state.data?.guidance ?? null);
  const newChangeDisabled = newChangeAction !== null && !newChangeAction.enabled;

  return (
    <>
      <Section title="Продукт">
        {state.updatedAt !== null ? (
          <p className="muted" data-testid="updated-at">
            обновлено {formatTime(state.updatedAt)}
          </p>
        ) : null}
        {state.loading ? <LoadingState /> : null}
        {state.error ? <ErrorState message={state.error.detail} /> : null}
        {state.data ? (
          <div className="page-header" data-testid="product-header">
            <div className="row">
              <h2>{state.data.product.name}</h2>
              <StatusBadge
                label={state.data.product.status}
                tone={statusTone(state.data.product.status, "product")}
              />
            </div>
            {state.data.product.status_reason ? (
              <Notice tone="error">{state.data.product.status_reason}</Notice>
            ) : null}
            <div className="row">
              <span className="mono">{state.data.product.id}</span>
              <span className="mono">
                {formatProduct(state.data.product.repository.provider, state.data.product.repository.slug)}
              </span>
              {state.data.product.repository_url ? (
                <a href={state.data.product.repository_url} target="_blank" rel="noreferrer" className="mono">
                  {state.data.product.repository_url}
                </a>
              ) : null}
            </div>
            <div className="row">
              {newChangeDisabled ? (
                <>
                  <button type="button" className="button button--primary" disabled data-testid="new-change-button">
                    Новая фича
                  </button>
                  <span className="field__hint">{newChangeAction?.reason}</span>
                </>
              ) : (
                <Link to={newChangePath} className="button button--primary" data-testid="new-change-button">
                  Новая фича
                </Link>
              )}
              <button
                type="button"
                className="button"
                onClick={() => void validate()}
                disabled={validating}
                data-testid="validate-button"
              >
                {validating ? "Проверка…" : "Проверить репозиторий"}
              </button>
              {!hasToken ? (
                <span className="field__hint" data-testid="product-token-hint">
                  проверка требует токен оператора
                </span>
              ) : null}
            </div>
            {validation ? (
              <Notice tone="success">
                Репозиторий проверен: состояние{" "}
                <StatusBadge label={validation.state} tone={statusTone(validation.state, "repository")} />
                {validation.default_branch ? ` · ветка ${validation.default_branch}` : ""}
                {validation.head_revision ? ` · HEAD ${validation.head_revision}` : ""}
              </Notice>
            ) : null}
            {validationError ? <Notice tone="error">Проверка не выполнена: {validationError}</Notice> : null}
          </div>
        ) : null}
      </Section>

      {state.data ? <NextStep guidance={state.data.guidance} onPerform={perform} busy={validating} /> : null}

      {state.data ? (
        <>
          <Section title="Обзор">
            <dl className="kv" data-testid="product-overview">
              <dt>ID</dt>
              <dd className="mono">{state.data.product.id}</dd>
              <dt>Название</dt>
              <dd>{state.data.product.name}</dd>
              <dt>Описание</dt>
              <dd>{state.data.product.description ?? <span className="muted">—</span>}</dd>
              <dt>Репозиторий</dt>
              <dd className="mono">
                {formatProduct(state.data.product.repository.provider, state.data.product.repository.slug)}
              </dd>
              <dt>URL</dt>
              <dd className="mono">{state.data.product.repository_url ?? <span className="muted">—</span>}</dd>
              <dt>Статус</dt>
              <dd>
                <StatusBadge
                  label={state.data.product.status}
                  tone={statusTone(state.data.product.status, "product")}
                />
                {state.data.product.status_reason ? ` — ${state.data.product.status_reason}` : ""}
              </dd>
              <dt>Baseline ref</dt>
              <dd className="mono">{state.data.product.baseline_ref ?? <span className="muted">не задан</span>}</dd>
              <dt>Dev env ref</dt>
              <dd className="mono">{state.data.product.dev_env_ref ?? <span className="muted">не задан</span>}</dd>
              <dt>state_revision</dt>
              <dd>{state.data.product.state_revision}</dd>
              <dt>Создан</dt>
              <dd>{formatDateTime(state.data.product.created_at)}</dd>
            </dl>
          </Section>

          <Section title="Изменения" description="Задачи этого продукта (GET /changes?product_id=…).">
            {state.data.changes.length === 0 ? (
              <EmptyState label="У продукта ещё нет задач — начните с «Новая фича»." />
            ) : (
              <table className="table" data-testid="product-changes-table">
                <thead>
                  <tr>
                    <th>Задача</th>
                    <th>Сценарий</th>
                    <th>Бриф</th>
                    <th>Лимит</th>
                    <th>Риск</th>
                    <th>Создано</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.changes.map((change) => (
                    <tr key={change.id}>
                      <td>
                        <Link to={`/changes/${encodeURIComponent(change.id)}`}>{change.title}</Link>
                        <div className="mono muted">{change.id}</div>
                      </td>
                      <td>{SCENARIO_LABELS[change.scenario] ?? change.scenario}</td>
                      <td>
                        {change.brief ? (
                          <StatusBadge label={change.brief.status} tone={statusTone(change.brief.status, "brief")} />
                        ) : (
                          <span className="muted">нет</span>
                        )}
                      </td>
                      <td>
                        {change.spend_limit ? (
                          <>
                            {formatCost(change.spend_limit.cost_budget_usd)}
                            {change.spend_limit.token_budget !== null
                              ? ` · ${formatNumber(change.spend_limit.token_budget)} токенов`
                              : ""}
                          </>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        <StatusBadge label={change.risk_class} tone="info" />
                      </td>
                      <td className="muted">{formatDateTime(change.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <Section title="База" description="Baseline-пакеты продукта (ADR-031): что фабрика считает основой репозитория.">
            <p data-testid="product-baseline">
              Baseline ref:{" "}
              {state.data.product.baseline_ref ? (
                <span className="mono">{state.data.product.baseline_ref}</span>
              ) : (
                <span className="muted">не задан</span>
              )}
            </p>
            <p className="muted" style={{ marginBottom: 0 }}>
              Просмотр документов baseline появится в M2.
            </p>
          </Section>

          <Section title="Доставки" description="Dev-окружение продукта и история доставок в него.">
            <p data-testid="product-deliveries">
              Dev env ref:{" "}
              {state.data.product.dev_env_ref ? (
                <span className="mono">{state.data.product.dev_env_ref}</span>
              ) : (
                <span className="muted">не задан</span>
              )}
            </p>
            <p className="muted" style={{ marginBottom: 0 }}>
              История доставок появится в M5.
            </p>
          </Section>
        </>
      ) : null}

      <TokenDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        reason="Проверка репозитория — операторское действие (products:write): нужен Bearer-токен оператора."
      />
    </>
  );
}
