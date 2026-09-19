import { useState } from "react";
import type { RefObject } from "react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { ArtifactDocumentView, ArtifactTreeView, Question, ReworkOrder } from "../api/types";
import { artifactName, shortRevision } from "../lib/artifacts";
import { formatDateTime } from "../lib/format";
import { Notice } from "./Section";
import { StatusBadge } from "./StatusBadge";

const REWORK_STATUS_TONES = {
  pending: "warning",
  in_progress: "info",
  done: "success",
  escalated: "danger",
} as const;

const REWORK_STATUS_LABELS = {
  pending: "ожидает раунда",
  in_progress: "выполняется",
  done: "выполнено",
  escalated: "эскалация",
} as const;

export interface RequirementsPhaseProps {
  changeId: string;
  api: ApiClient;
  hasToken: boolean;
  onTokenRequired: () => void;
  /** The artifact tree, or null while loading / when the contour has no repository. */
  tree: ArtifactTreeView | null;
  /** The 503 (or other) detail of the artifacts read — shown honestly, no fake data. */
  treeError: string | null;
  /** Spec documents of the tree that could be read (anchors, counts, viewed). */
  documents: ArtifactDocumentView[];
  questions: Question[];
  orders: ReworkOrder[];
  /** After any write: the parent re-reads the workspace (gate, guidance, counts). */
  onChanged: () => void;
  onOpenArtifact: (path: string) => void;
  /** A click on an anchor chip selects the fragment for the context panel. */
  onSelectFragment: (artifact: string, anchorId: string | null) => void;
  /** The guidance primary «Ответить на вопросы» scrolls here. */
  questionsRef?: RefObject<HTMLElement | null>;
}

/**
 * «Результат» of the requirements phase (T089, ADR-034): the requirements
 * delta — the `spec` artifacts on the change branch with their stable ids —
 * the agent's blocking questions answered in one click, its assumptions
 * (non-blocking questions) with «Подтвердить / Исправить / Варианты», and the
 * agent's summary «что изменил / что осталось» after a rework round. The
 * baseline is what the product repository already accepted (ADR-037 p.7);
 * everything listed here is proposed, not accepted. «Просмотрено» marks the
 * revision as viewed and never says approved (ADR-034 p.2).
 */
export function RequirementsPhase({
  changeId,
  api,
  hasToken,
  onTokenRequired,
  tree,
  treeError,
  documents,
  questions,
  orders,
  onChanged,
  onOpenArtifact,
  onSelectFragment,
  questionsRef,
}: RequirementsPhaseProps) {
  const [viewError, setViewError] = useState<string | null>(null);
  const specNodes = tree?.nodes.filter((node) => node.kind === "spec") ?? [];
  const blocking = questions.filter((question) => question.blocking && question.status !== "stale");
  const assumptions = questions.filter((question) => !question.blocking && question.status !== "stale");
  const stale = questions.filter((question) => question.status === "stale");
  const summarised = [...orders].sort((a, b) => b.created_at.localeCompare(a.created_at));

  const markViewed = async (doc: ArtifactDocumentView) => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setViewError(null);
    try {
      await api.markArtifactViewed(changeId, doc.path, doc.revision);
      onChanged();
    } catch (cause) {
      setViewError(cause instanceof ApiError ? cause.detail : String(cause));
    }
  };

  return (
    <div className="stack" data-testid="requirements-phase">
      <section className="card" data-testid="requirements-delta">
        <h3 className="card__title">Дельта требований</h3>
        <p className="card__description">
          Предлагаемые спецификации на ветке изменения; baseline продукта не меняется до доставки.
        </p>
        {treeError ? (
          <Notice tone="warning">Артефакты недоступны: {treeError}</Notice>
        ) : tree === null ? (
          <p className="muted">Загрузка артефактов…</p>
        ) : tree.revision === null ? (
          <p className="muted" data-testid="requirements-no-branch">
            Ветки изменения ещё нет: агент не создал ни одной ревизии. Дельта появится после запуска фазы.
          </p>
        ) : specNodes.length === 0 ? (
          <p className="muted" data-testid="requirements-empty-branch">
            Ветка <span className="mono">{tree.branch}</span> есть (ревизия {shortRevision(tree.revision)}), но
            спецификаций в ней пока нет.
          </p>
        ) : (
          <ul className="delta-list">
            {specNodes.map((node) => {
              const doc = documents.find((candidate) => candidate.path === node.path) ?? null;
              const hasDraft = tree.drafts.includes(node.path);
              return (
                <li key={node.path} className="delta-item" data-testid={`delta-${artifactName(node.path)}`}>
                  <div className="row">
                    <button type="button" className="link-button" onClick={() => onOpenArtifact(node.path)}>
                      {artifactName(node.path)}
                    </button>
                    <span className="mono muted" title={node.revision ?? undefined}>
                      {shortRevision(node.revision)}
                    </span>
                    {hasDraft ? <StatusBadge label="черновик" tone="warning" /> : null}
                    {doc ? (
                      doc.viewed ? (
                        <StatusBadge label="просмотрено" tone="neutral" />
                      ) : (
                        <button
                          type="button"
                          className="button"
                          onClick={() => void markViewed(doc)}
                          data-testid={`view-${artifactName(node.path)}`}
                          title="Отметка просмотра этой ревизии — не согласование"
                        >
                          Просмотрено
                        </button>
                      )
                    ) : null}
                    {doc ? (
                      <span className="muted">
                        вопросов: {doc.open_questions} · замечаний: {doc.open_comments}
                      </span>
                    ) : null}
                  </div>
                  <div className="muted mono delta-item__path">{node.path}</div>
                  {doc ? (
                    doc.anchors.length > 0 ? (
                      <div className="row anchors" data-testid={`anchors-${artifactName(node.path)}`}>
                        {doc.anchors.map((anchor) => (
                          <button
                            key={anchor}
                            type="button"
                            className="anchor-chip mono"
                            onClick={() => onSelectFragment(node.path, anchor)}
                            title="Выбрать фрагмент для обсуждения"
                          >
                            {anchor}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <span className="muted">стабильных идентификаторов нет</span>
                    )
                  ) : (
                    <span className="muted">документ не прочитан</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {viewError ? <Notice tone="error">Отметка не записана: {viewError}</Notice> : null}
      </section>

      <section className="card" data-testid="requirements-questions" ref={questionsRef}>
        <h3 className="card__title">Вопросы агента</h3>
        <p className="card__description">Открытый блокирующий вопрос закрывает гейт; ответ — решение человека, не правка.</p>
        {blocking.length === 0 ? (
          <p className="muted">Блокирующих вопросов нет.</p>
        ) : (
          <div className="stack">
            {blocking.map((question) => (
              <QuestionCard
                key={question.id}
                changeId={changeId}
                api={api}
                question={question}
                hasToken={hasToken}
                onTokenRequired={onTokenRequired}
                onAnswered={onChanged}
                onSelectFragment={onSelectFragment}
              />
            ))}
          </div>
        )}
      </section>

      <section className="card" data-testid="requirements-assumptions">
        <h3 className="card__title">Допущения агента</h3>
        <p className="card__description">Не блокируют гейт; подтвердите, исправьте или выберите вариант.</p>
        {assumptions.length === 0 ? (
          <p className="muted">Допущений нет.</p>
        ) : (
          <div className="stack">
            {assumptions.map((question) => (
              <QuestionCard
                key={question.id}
                changeId={changeId}
                api={api}
                question={question}
                hasToken={hasToken}
                onTokenRequired={onTokenRequired}
                onAnswered={onChanged}
                onSelectFragment={onSelectFragment}
                assumption
              />
            ))}
          </div>
        )}
        {stale.length > 0 ? (
          <p className="muted" data-testid="requirements-stale-questions">
            Неактуальных вопросов (фрагмент исчез до ответа): {stale.length}.
          </p>
        ) : null}
      </section>

      <section className="card" data-testid="requirements-rework">
        <h3 className="card__title">Доработки</h3>
        <p className="card__description">Сводка агента после раунда: что изменил, что осталось.</p>
        {summarised.length === 0 ? (
          <p className="muted">Поручений на доработку не было.</p>
        ) : (
          <div className="stack">
            {summarised.map((order) => (
              <ReworkSummaryCard key={order.id} order={order} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export interface QuestionCardProps {
  changeId: string;
  api: ApiClient;
  question: Question;
  hasToken: boolean;
  onTokenRequired: () => void;
  onAnswered: () => void;
  onSelectFragment: (artifact: string, anchorId: string | null) => void;
  /** Render as an assumption card: «Подтвердить / Исправить / Варианты». */
  assumption?: boolean;
}

/**
 * One question of the agent (ADR-034 p.1). A `choice` is answered with one
 * click on an option; `text`/`number` with a small input and «Ответить».
 * An assumption is a non-blocking question: «Подтвердить» answers the text
 * "confirmed" (text kind only — a number or a choice cannot be "confirmed"
 * literally), «Исправить» opens the free-text input, «Варианты» shows the
 * options of a choice.
 */
export function QuestionCard({
  changeId,
  api,
  question,
  hasToken,
  onTokenRequired,
  onAnswered,
  onSelectFragment,
  assumption = false,
}: QuestionCardProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showInput, setShowInput] = useState(!assumption && question.kind !== "choice");
  const [showOptions, setShowOptions] = useState(!assumption && question.kind === "choice");
  const open = question.status === "open";

  const answer = async (answerValue: string) => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    const trimmed = answerValue.trim();
    if (!trimmed) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.answerQuestion(changeId, question.id, { value: trimmed });
      onAnswered();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      className={`question-card${assumption ? " question-card--assumption" : ""}`}
      data-testid={`question-${question.id}`}
      aria-label={assumption ? "Допущение" : "Вопрос"}
    >
      <div className="row">
        <StatusBadge label={question.status} tone={question.status === "open" ? "warning" : question.status === "answered" ? "success" : "muted"} />
        {assumption ? <StatusBadge label="допущение" tone="info" /> : <StatusBadge label={question.blocking ? "блокирует гейт" : "не блокирует"} tone={question.blocking ? "danger" : "neutral"} />}
        {question.asked_by ? <span className="muted">спросил: {question.asked_by}</span> : null}
        {question.anchor ? (
          <button
            type="button"
            className="anchor-chip mono"
            onClick={() => onSelectFragment(question.anchor!.artifact, question.anchor!.anchor_id)}
            title={question.anchor.artifact}
          >
            {artifactName(question.anchor.artifact)}
            {question.anchor.anchor_id ? ` · ${question.anchor.anchor_id}` : ""}
          </button>
        ) : null}
        <span className="muted">{formatDateTime(question.asked_at)}</span>
      </div>
      <p className="question-card__text">{question.text}</p>

      {question.answer ? (
        <p className="question-card__answer" data-testid={`answer-${question.id}`}>
          Ответ: <strong>{question.answer.value}</strong>
          {question.answer.comment ? <span className="muted"> — {question.answer.comment}</span> : null}
          <span className="muted"> ({question.answer.answered_by}, {formatDateTime(question.answer.answered_at)})</span>
        </p>
      ) : null}

      {open ? (
        <div className="stack">
          {assumption ? (
            <div className="form-actions">
              {question.kind === "text" ? (
                <button type="button" className="button button--primary" disabled={busy} onClick={() => void answer("confirmed")}>
                  Подтвердить
                </button>
              ) : null}
              <button type="button" className="button" disabled={busy} onClick={() => setShowInput((shown) => !shown)}>
                Исправить
              </button>
              {question.kind === "choice" ? (
                <button type="button" className="button" disabled={busy} onClick={() => setShowOptions((shown) => !shown)}>
                  Варианты
                </button>
              ) : null}
            </div>
          ) : null}

          {showOptions && question.kind === "choice" ? (
            <div className="form-actions" data-testid={`options-${question.id}`}>
              {question.options.map((option) => (
                <button key={option} type="button" className="button" disabled={busy} onClick={() => void answer(option)}>
                  {option}
                </button>
              ))}
            </div>
          ) : null}

          {showInput && (question.kind !== "choice" || assumption) ? (
            <form
              className="form-actions"
              onSubmit={(event) => {
                event.preventDefault();
                void answer(value);
              }}
            >
              <input
                type={question.kind === "number" && !assumption ? "number" : "text"}
                aria-label={assumption ? "Исправление" : "Ответ"}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                disabled={busy}
                step={question.kind === "number" ? "any" : undefined}
              />
              <button type="submit" className="button button--primary" disabled={busy || value.trim().length === 0}>
                Ответить
              </button>
            </form>
          ) : null}
          {!hasToken ? <span className="field__hint">ответ требует токен оператора</span> : null}
          {error ? <Notice tone="error">Ответ не записан: {error}</Notice> : null}
        </div>
      ) : null}
    </article>
  );
}

/** The agent's summary of one rework order: status, what changed, what remains (ADR-034). */
export function ReworkSummaryCard({ order }: { order: ReworkOrder }) {
  return (
    <article className="rework-card" data-testid={`rework-${order.id}`}>
      <div className="row">
        <span className="mono">{order.id}</span>
        <StatusBadge label={REWORK_STATUS_LABELS[order.status]} tone={REWORK_STATUS_TONES[order.status]} />
        {order.round !== null ? <span className="muted">раунд {order.round}</span> : null}
        <span className="muted">{formatDateTime(order.created_at)}</span>
        <span className="muted">
          замечаний: {order.comment_ids.length} · ответов: {order.question_ids.length}
        </span>
      </div>
      {order.instruction ? <p className="rework-card__instruction">Поручение: {order.instruction}</p> : null}
      {order.status === "pending" ? <p className="muted">Раунд ещё не запущен — сводки пока нет.</p> : null}
      {order.status === "in_progress" ? <p className="muted">Агент работает над поручением.</p> : null}
      {order.escalation_reason ? <Notice tone="error">Эскалация: {order.escalation_reason}</Notice> : null}
      {order.summary ? (
        <div className="rework-card__summary" data-testid={`summary-${order.id}`}>
          <div>
            <h5>Что изменил</h5>
            {order.summary.changed.length === 0 ? (
              <p className="muted">—</p>
            ) : (
              <ul>
                {order.summary.changed.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h5>Что осталось</h5>
            {order.summary.remaining.length === 0 ? (
              <p className="muted">ничего</p>
            ) : (
              <ul>
                {order.summary.remaining.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
          {order.summary.addressed_comment_ids.length > 0 ? (
            <p className="muted">
              Отмечены как исправленные (ждут вашей проверки):{" "}
              <span className="mono">{order.summary.addressed_comment_ids.join(", ")}</span>
            </p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
