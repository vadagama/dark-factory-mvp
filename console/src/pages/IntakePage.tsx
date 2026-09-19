import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { createApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import { getToken } from "../api/token";
import { ErrorState, LoadingState, Notice, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import {
  EMPTY_BRIEF_FIELDS,
  briefAuthor,
  briefToFields,
  deriveBriefStatus,
  fieldsToBrief,
  parseCostBudget,
  parseTokenBudget,
} from "../lib/brief";
import type { BriefFields } from "../lib/brief";
import { formatProduct } from "../lib/format";
import { newChangeId } from "../lib/id";
import { statusTone } from "../lib/statusTone";
import type { Change, RiskClass, Scenario } from "../api/types";

const RISK_CLASSES: RiskClass[] = ["R0", "R1", "R2", "R3", "R4"];
const SCENARIOS: { value: Scenario; label: string; hint: string }[] = [
  {
    value: "specs_only",
    label: "Только спецификации",
    hint: "до согласования требований/архитектуры/UI",
  },
  { value: "full", label: "Полный", hint: "до доставки в dev" },
];

/**
 * «Новая фича» (T076, ADR-037): the operator describes the intent in free
 * text, the agent formulates the brief (POST /briefs/formulate) — or fails
 * observably and the operator fills the four fields by hand — then the
 * scenario and the spend limit are chosen and the change is created
 * (POST /changes) with `brief`, `scenario`, `spend_limit` and `product_id`.
 * The forecast block is honest: there is no forecast data before a run.
 */
export function IntakePage() {
  const { productId = "" } = useParams();
  const api = useMemo(() => createApiClient(), []);
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);

  const product = useAsync(() => api.getProduct(productId), [productId]);

  const [title, setTitle] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [fields, setFields] = useState<BriefFields>(EMPTY_BRIEF_FIELDS);
  /** The agent's wording as returned by formulate — `formulated_by: agent` only while unchanged. */
  const [agentFields, setAgentFields] = useState<BriefFields | null>(null);
  const [formulateError, setFormulateError] = useState<string | null>(null);
  const [formulating, setFormulating] = useState(false);
  const [scenario, setScenario] = useState<Scenario>("full");
  const [costBudget, setCostBudget] = useState("");
  const [tokenBudget, setTokenBudget] = useState("");
  const [riskClass, setRiskClass] = useState<RiskClass>("R1");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const hasToken = getToken() !== null;
  const update = (patch: Partial<BriefFields>) => setFields((prev) => ({ ...prev, ...patch }));
  const author = briefAuthor(fields, agentFields);
  const briefStatus = deriveBriefStatus(fields);
  const costParsed = parseCostBudget(costBudget);

  const formulate = async () => {
    setFormulateError(null);
    if (!hasToken) {
      setDialogOpen(true);
      return;
    }
    const text = sourceText.trim();
    if (!text) {
      return;
    }
    setFormulating(true);
    try {
      const brief = await api.formulateBrief({ source_text: text });
      const formulated = briefToFields(brief);
      setFields(formulated);
      setAgentFields(brief.formulated_by === "agent" ? formulated : null);
      if (brief.error) {
        // T072 DoD: the brief stays a draft and the cause is observable; the fields stay editable.
        setFormulateError(brief.error);
      }
    } catch (cause) {
      setAgentFields(null);
      setFormulateError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setFormulating(false);
    }
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitError(null);
    if (!hasToken) {
      setDialogOpen(true);
      return;
    }
    if (!product.data) {
      return;
    }
    if (costParsed === null) {
      setSubmitError("Лимит в USD должен быть положительным числом (до 4 знаков после точки).");
      return;
    }
    const tokens = parseTokenBudget(tokenBudget);
    if (tokens === undefined) {
      setSubmitError("Лимит токенов должен быть целым числом не меньше 1 (или пустым).");
      return;
    }
    setSubmitting(true);
    const change: Change = {
      id: newChangeId(),
      title: title.trim(),
      description: null,
      source: "console",
      external_ref: null,
      product: product.data.repository,
      product_id: product.data.id,
      risk_class: riskClass,
      change_request: null,
      created_at: new Date().toISOString(),
      brief: fieldsToBrief(fields, { source_text: sourceText, formulated_by: author }),
      scenario,
      spend_limit: { cost_budget_usd: costParsed, token_budget: tokens },
    };
    try {
      const stored = await api.createChange(change);
      navigate(`/changes/${encodeURIComponent(stored.id)}`);
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Section title="Новая фича">
        {product.loading ? <LoadingState /> : null}
        {product.error ? <ErrorState message={product.error.detail} /> : null}
        {product.data ? (
          <div className="page-header" data-testid="intake-product-header">
            <div className="row">
              <h2>
                <Link to={`/products/${encodeURIComponent(product.data.id)}`}>{product.data.name}</Link>
              </h2>
              <StatusBadge label={product.data.status} tone={statusTone(product.data.status, "product")} />
              <span className="mono">
                {formatProduct(product.data.repository.provider, product.data.repository.slug)}
              </span>
            </div>
            {product.data.status !== "ready" ? (
              <p className="muted">
                Продукт ещё не готов ({product.data.status}): задачу можно создать, но фаза «Требования» не начнётся,
                пока репозиторий не проверен.
              </p>
            ) : null}
          </div>
        ) : null}
      </Section>

      <Section
        title="Задача"
        description="POST /changes с токеном changes:write. Бриф формулирует агент по вашему тексту; если не смог — заполните поля вручную."
      >
        {!hasToken ? (
          <p className="muted" data-testid="intake-token-hint">
            Мутации недоступны: введите токен оператора (changes:write) на экране «Служебное → Настройки».
          </p>
        ) : null}
        {submitError ? <Notice tone="error">Ошибка создания задачи: {submitError}</Notice> : null}
        <form onSubmit={(event) => void onSubmit(event)} data-testid="intake-form">
          <fieldset disabled={!hasToken || !product.data} style={{ border: "none", padding: 0, margin: 0 }}>
            <div className="field">
              <label className="field__label" htmlFor="intake-title">
                Название
              </label>
              <input id="intake-title" type="text" required value={title} onChange={(event) => setTitle(event.target.value)} />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="intake-source-text">
                Опишите своими словами
              </label>
              <textarea
                id="intake-source-text"
                rows={5}
                placeholder="Что болит, что должно стать правдой, чего нельзя трогать…"
                value={sourceText}
                onChange={(event) => setSourceText(event.target.value)}
              />
              <div className="form-actions">
                <button
                  type="button"
                  className="button"
                  data-testid="formulate-button"
                  disabled={formulating || sourceText.trim().length === 0}
                  onClick={() => void formulate()}
                >
                  {formulating ? "Формулирую…" : "Помоги сформулировать"}
                </button>
                <span className="field__hint">POST /briefs/formulate — агент разложит текст на проблему, цель, ограничения и вне объёма</span>
              </div>
            </div>

            {formulateError ? (
              <Notice tone="warning">Бриф остался черновиком: {formulateError}</Notice>
            ) : null}

            <div className="row" style={{ marginBottom: 8 }}>
              <span className="muted">Бриф:</span>
              <StatusBadge label={briefStatus} tone={statusTone(briefStatus, "brief")} />
              <span className="muted" data-testid="brief-author">
                сформулировал: {author === "agent" ? "агент" : "оператор"}
              </span>
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field__label" htmlFor="intake-problem">
                  Проблема
                </label>
                <textarea id="intake-problem" value={fields.problem} onChange={(event) => update({ problem: event.target.value })} />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="intake-goal">
                  Цель
                </label>
                <textarea id="intake-goal" value={fields.goal} onChange={(event) => update({ goal: event.target.value })} />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="intake-constraints">
                  Ограничения (по одному в строке)
                </label>
                <textarea
                  id="intake-constraints"
                  value={fields.constraints}
                  onChange={(event) => update({ constraints: event.target.value })}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="intake-out-of-scope">
                  Вне объёма (по одному в строке)
                </label>
                <textarea
                  id="intake-out-of-scope"
                  value={fields.out_of_scope}
                  onChange={(event) => update({ out_of_scope: event.target.value })}
                />
              </div>
            </div>

            <div className="field">
              <span className="field__label">Сценарий</span>
              <div className="radio-group" role="radiogroup" aria-label="Сценарий">
                {SCENARIOS.map((option) => (
                  <label key={option.value}>
                    <input
                      type="radio"
                      name="scenario"
                      value={option.value}
                      checked={scenario === option.value}
                      onChange={() => setScenario(option.value)}
                    />
                    <span>
                      {option.label} <span className="muted">— {option.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field__label" htmlFor="intake-cost-budget">
                  Лимит, USD
                </label>
                <input
                  id="intake-cost-budget"
                  type="text"
                  inputMode="decimal"
                  required
                  placeholder="25.00"
                  value={costBudget}
                  onChange={(event) => setCostBudget(event.target.value)}
                />
                <span className="field__hint">Жёсткий лимит расхода задачи; копируется в бюджет прогона.</span>
              </div>
              <div className="field">
                <label className="field__label" htmlFor="intake-token-budget">
                  Лимит токенов (необязательно)
                </label>
                <input
                  id="intake-token-budget"
                  type="text"
                  inputMode="numeric"
                  placeholder="150000"
                  value={tokenBudget}
                  onChange={(event) => setTokenBudget(event.target.value)}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="intake-risk">
                  Класс риска
                </label>
                <select id="intake-risk" value={riskClass} onChange={(event) => setRiskClass(event.target.value as RiskClass)}>
                  {RISK_CLASSES.map((risk) => (
                    <option key={risk} value={risk}>
                      {risk}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="forecast" data-testid="intake-forecast">
              Прогноз расхода появится после первого прогона
              {costParsed !== null ? `; лимит: ${costParsed} USD` : "; лимит не задан"}
            </div>

            <div className="form-actions">
              <button type="submit" className="button button--primary" disabled={submitting}>
                {submitting ? "Создание…" : "Создать задачу"}
              </button>
              <span className="field__hint">Источник: console · Idempotency-Key добавляется автоматически</span>
            </div>
          </fieldset>
        </form>
      </Section>
      <TokenDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
}
