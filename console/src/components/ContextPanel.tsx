import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { ArtifactDocumentView, ArtifactTreeView, CommentView, Phase, Question, ReworkOrder } from "../api/types";
import { artifactName, shortRevision } from "../lib/artifacts";
import { formatDateTime } from "../lib/format";
import { Notice } from "./Section";
import { StatusBadge } from "./StatusBadge";

/** The fragment the discussion is about: an artifact and optionally one of its anchors. */
export interface Fragment {
  artifact: string;
  anchorId: string | null;
}

/**
 * A comment the editor started from a text selection: prefilled artifact,
 * anchor and the quote. `id` grows with every new seed so the composer adopts
 * each one exactly once.
 */
export interface CommentSeed extends Fragment {
  id: number;
  quote: string | null;
}

export interface ContextPanelProps {
  changeId: string;
  api: ApiClient;
  phase: Phase;
  hasToken: boolean;
  onTokenRequired: () => void;
  tree: ArtifactTreeView | null;
  documents: ArtifactDocumentView[];
  comments: CommentView[];
  questions: Question[];
  orders: ReworkOrder[];
  fragment: Fragment | null;
  onFragmentChange: (fragment: Fragment | null) => void;
  /** Set by the editor's «Комментировать»; adopted once per `seed.id`. */
  seed: CommentSeed | null;
  /** «На доработку» form visibility — the guidance primary/secondary opens it. */
  reworkOpen: boolean;
  onReworkOpenChange: (open: boolean) => void;
  onChanged: () => void;
}

const COMMENT_TONES = { open: "warning", addressed: "info", closed: "muted" } as const;
const COMMENT_LABELS = { open: "открыто", addressed: "исправлено агентом", closed: "закрыто" } as const;

/**
 * Right context panel of the ChangeSet workspace (T088/T089, ADR-037 p.4):
 * the discussion of the selected fragment — comments anchored to
 * `artifact + anchor_id + revision`, the composer for a new remark, the
 * rework orders of the phase and the «На доработку» form. A comment never
 * starts a round by itself (ADR-034 p.2): the order is the explicit action.
 * A `detached` anchor is shown as such and never moved; «исправлено» is the
 * agent's claim and only the operator closes.
 */
export function ContextPanel({
  changeId,
  api,
  phase,
  hasToken,
  onTokenRequired,
  tree,
  documents,
  comments,
  questions,
  orders,
  fragment,
  onFragmentChange,
  seed,
  reworkOpen,
  onReworkOpenChange,
  onChanged,
}: ContextPanelProps) {
  const [artifact, setArtifact] = useState(fragment?.artifact ?? "");
  const [anchorId, setAnchorId] = useState(fragment?.anchorId ?? "");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [transitionError, setTransitionError] = useState<string | null>(null);

  // Props → state adjustments during render (no effects): a fragment selected
  // elsewhere (anchor chip, question) prefills the composer; the editor's
  // «Комментировать» seed also brings the quoted selection.
  const [adoptedFragment, setAdoptedFragment] = useState(fragment);
  if (fragment !== adoptedFragment) {
    setAdoptedFragment(fragment);
    if (fragment) {
      setArtifact(fragment.artifact);
      setAnchorId(fragment.anchorId ?? "");
    }
  }
  // Starts at null so a seed present at mount is adopted on the first render too.
  const [adoptedSeedId, setAdoptedSeedId] = useState<number | null>(null);
  if ((seed?.id ?? null) !== adoptedSeedId) {
    setAdoptedSeedId(seed?.id ?? null);
    if (seed) {
      setArtifact(seed.artifact);
      setAnchorId(seed.anchorId ?? "");
      setBody(seed.quote ? `> ${seed.quote.trim().replaceAll("\n", "\n> ")}\n\n` : "");
    }
  }

  const artifacts = tree?.nodes ?? [];
  const selectedDoc = documents.find((doc) => doc.path === artifact) ?? null;
  const anchors = selectedDoc?.anchors ?? [];
  const visible = fragment
    ? comments.filter(
        (comment) =>
          comment.anchor.artifact === fragment.artifact &&
          (fragment.anchorId === null || comment.anchor.anchor_id === fragment.anchorId),
      )
    : comments;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    if (!artifact || !body.trim()) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await api.addComment(changeId, {
        artifact,
        anchor_id: anchorId || null,
        revision: selectedDoc?.revision ?? tree?.revision ?? null,
        body: body.trim(),
        phase,
      });
      setBody("");
      setNotice(`Замечание ${created.id} записано. Доработка не запускается сама — используйте «На доработку».`);
      onChanged();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const transition = async (comment: CommentView, action: "close" | "reopen") => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setTransitionError(null);
    try {
      if (action === "close") {
        await api.closeComment(changeId, comment.id);
      } else {
        await api.reopenComment(changeId, comment.id);
      }
      onChanged();
    } catch (cause) {
      setTransitionError(cause instanceof ApiError ? cause.detail : String(cause));
    }
  };

  return (
    <div className="stack" data-testid="context-panel">
      <section>
        <h4 className="context__title">Фрагмент</h4>
        {fragment ? (
          <div className="row" data-testid="context-fragment">
            <span className="mono" title={fragment.artifact}>
              {artifactName(fragment.artifact)}
            </span>
            <span className="mono">{fragment.anchorId ?? "весь документ"}</span>
            <button type="button" className="link-button" onClick={() => onFragmentChange(null)}>
              сбросить
            </button>
          </div>
        ) : (
          <p className="muted">Фрагмент не выбран — показаны все замечания фазы.</p>
        )}
      </section>

      <section data-testid="context-comments">
        <h4 className="context__title">Замечания ({visible.length})</h4>
        {visible.length === 0 ? <p className="muted">Замечаний нет.</p> : null}
        <div className="stack">
          {visible.map((comment) => (
            <article key={comment.id} className="comment-card" data-testid={`comment-${comment.id}`}>
              <div className="row">
                <StatusBadge label={COMMENT_LABELS[comment.status]} tone={COMMENT_TONES[comment.status]} />
                {comment.anchor_state === "detached" && comment.status !== "closed" ? (
                  <StatusBadge label="якорь потерян" tone="danger" />
                ) : null}
                <button
                  type="button"
                  className="anchor-chip mono"
                  onClick={() => onFragmentChange({ artifact: comment.anchor.artifact, anchorId: comment.anchor.anchor_id })}
                  title={comment.anchor.artifact}
                >
                  {artifactName(comment.anchor.artifact)}
                  {comment.anchor.anchor_id ? ` · ${comment.anchor.anchor_id}` : ""}
                </button>
                <span className="muted mono" title={comment.anchor.revision ?? undefined}>
                  @{shortRevision(comment.anchor.revision)}
                </span>
              </div>
              <p className="comment-card__body">{comment.body}</p>
              {comment.anchor_state === "detached" && comment.status !== "closed" ? (
                <p className="field__hint" data-testid={`detached-${comment.id}`}>
                  Фрагмент «{comment.anchor.anchor_id ?? "документ"}» в текущей ревизии не найден; замечание не
                  переносится на другой элемент — проверьте и закройте или переоткройте.
                </p>
              ) : null}
              {comment.addressed_note ? (
                <p className="muted">Агент: {comment.addressed_note}</p>
              ) : null}
              <div className="row">
                <span className="muted">
                  {comment.author} · {formatDateTime(comment.created_at)}
                </span>
                {comment.rework_order_id ? <span className="muted mono">{comment.rework_order_id}</span> : null}
                {comment.status !== "closed" ? (
                  <button type="button" className="button" onClick={() => void transition(comment, "close")} data-testid={`close-${comment.id}`}>
                    Закрыть
                  </button>
                ) : null}
                {comment.status === "addressed" ? (
                  <button type="button" className="button" onClick={() => void transition(comment, "reopen")} data-testid={`reopen-${comment.id}`}>
                    Переоткрыть
                  </button>
                ) : null}
              </div>
            </article>
          ))}
        </div>
        {transitionError ? <Notice tone="error">{transitionError}</Notice> : null}
      </section>

      <section>
        <h4 className="context__title">Новое замечание</h4>
        <form onSubmit={(event) => void submit(event)} className="stack" data-testid="comment-form">
          <fieldset className="stack" disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
            <label className="field">
              <span className="field__label">Артефакт</span>
              <select
                value={artifact}
                onChange={(event) => {
                  setArtifact(event.target.value);
                  setAnchorId("");
                }}
                data-testid="comment-artifact"
              >
                <option value="">— выберите —</option>
                {artifacts.map((node) => (
                  <option key={node.path} value={node.path}>
                    {artifactName(node.path)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">Фрагмент</span>
              <select value={anchorId} onChange={(event) => setAnchorId(event.target.value)} data-testid="comment-anchor">
                <option value="">весь документ</option>
                {anchors.map((anchor) => (
                  <option key={anchor} value={anchor}>
                    {anchor}
                  </option>
                ))}
                {anchorId && !anchors.includes(anchorId) ? <option value={anchorId}>{anchorId}</option> : null}
              </select>
            </label>
            <label className="field">
              <span className="field__label">Текст</span>
              <textarea value={body} onChange={(event) => setBody(event.target.value)} data-testid="comment-body" rows={3} />
            </label>
          </fieldset>
          <div className="form-actions">
            <button type="submit" className="button button--primary" disabled={busy || !artifact || body.trim().length === 0} data-testid="comment-submit">
              Оставить замечание
            </button>
            {!hasToken ? <span className="field__hint">требует токен оператора</span> : null}
          </div>
          {error ? <Notice tone="error">Замечание не записано: {error}</Notice> : null}
          {notice ? <Notice tone="success">{notice}</Notice> : null}
        </form>
      </section>

      <section data-testid="context-rework">
        <div className="row">
          <h4 className="context__title" style={{ margin: 0 }}>
            Поручения ({orders.length})
          </h4>
          <button type="button" className="button" onClick={() => onReworkOpenChange(!reworkOpen)} data-testid="rework-toggle">
            На доработку
          </button>
        </div>
        {reworkOpen ? (
          <ReworkForm
            changeId={changeId}
            api={api}
            phase={phase}
            hasToken={hasToken}
            onTokenRequired={onTokenRequired}
            comments={comments.filter((comment) => comment.status !== "closed")}
            questions={questions.filter((question) => question.status === "answered")}
            expectedRevision={tree?.revision ?? null}
            onIssued={() => {
              onReworkOpenChange(false);
              onChanged();
            }}
            onCancel={() => onReworkOpenChange(false)}
          />
        ) : null}
        <ul className="rework-list">
          {orders.map((order) => (
            <li key={order.id}>
              <span className="mono">{order.id}</span> <StatusBadge label={order.status} tone={order.status === "done" ? "success" : order.status === "escalated" ? "danger" : "warning"} />
              {order.round !== null ? <span className="muted"> раунд {order.round}</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export interface ReworkFormProps {
  changeId: string;
  api: ApiClient;
  phase: Phase;
  hasToken: boolean;
  onTokenRequired: () => void;
  comments: CommentView[];
  questions: Question[];
  expectedRevision: string | null;
  onIssued: (order: ReworkOrder) => void;
  onCancel: () => void;
}

/**
 * «На доработку» (ADR-034 p.2/p.3): pick the open comments and the answered
 * questions the agent should take, add an instruction, send. The order is
 * bound to the current head (`expected_revision`); 409 (a round is already
 * pending/running, or the head moved) and 422 (empty order) show the server
 * detail.
 */
export function ReworkForm({
  changeId,
  api,
  phase,
  hasToken,
  onTokenRequired,
  comments,
  questions,
  expectedRevision,
  onIssued,
  onCancel,
}: ReworkFormProps) {
  const [commentIds, setCommentIds] = useState<string[]>(comments.filter((comment) => comment.status === "open").map((comment) => comment.id));
  const [questionIds, setQuestionIds] = useState<string[]>(questions.map((question) => question.id));
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (list: string[], id: string, set: (next: string[]) => void) => {
    set(list.includes(id) ? list.filter((item) => item !== id) : [...list, id]);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const order = await api.createReworkOrder(changeId, {
        phase,
        comment_ids: commentIds,
        question_ids: questionIds,
        instruction: instruction.trim() || null,
        expected_revision: expectedRevision,
      });
      onIssued(order);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const empty = commentIds.length === 0 && questionIds.length === 0 && instruction.trim().length === 0;

  return (
    <form className="rework-form stack" onSubmit={(event) => void submit(event)} data-testid="rework-form">
      <p className="muted" style={{ margin: 0 }}>
        Поручение расходует раунд доработки (не более 3 на прогон). Привязано к ревизии{" "}
        <span className="mono">{shortRevision(expectedRevision)}</span>.
      </p>
      <fieldset className="stack" disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <div>
          <span className="field__label">Замечания</span>
          {comments.length === 0 ? <p className="muted">Открытых замечаний нет.</p> : null}
          {comments.map((comment) => (
            <label key={comment.id} className="check">
              <input
                type="checkbox"
                checked={commentIds.includes(comment.id)}
                onChange={() => toggle(commentIds, comment.id, setCommentIds)}
              />
              <span>
                <span className="mono">{comment.id}</span> — {comment.body}
                {comment.status === "addressed" ? <span className="muted"> (исправлено агентом)</span> : null}
              </span>
            </label>
          ))}
        </div>
        <div>
          <span className="field__label">Ответы на вопросы</span>
          {questions.length === 0 ? <p className="muted">Отвеченных вопросов нет.</p> : null}
          {questions.map((question) => (
            <label key={question.id} className="check">
              <input
                type="checkbox"
                checked={questionIds.includes(question.id)}
                onChange={() => toggle(questionIds, question.id, setQuestionIds)}
              />
              <span>
                <span className="mono">{question.id}</span> — {question.text}
                {question.answer ? <span className="muted"> → {question.answer.value}</span> : null}
              </span>
            </label>
          ))}
        </div>
        <label className="field">
          <span className="field__label">Инструкция агенту</span>
          <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={3} data-testid="rework-instruction" />
        </label>
      </fieldset>
      <div className="form-actions">
        <button type="submit" className="button button--primary" disabled={busy || empty} data-testid="rework-submit">
          {busy ? "Отправляю…" : "Отправить агенту"}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        {!hasToken ? <span className="field__hint">требует токен оператора</span> : null}
      </div>
      {error ? <Notice tone="error">Поручение не создано: {error}</Notice> : null}
    </form>
  );
}
