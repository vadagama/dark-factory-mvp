import { isValidElement, useState } from "react";
import type { ReactNode, RefObject } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import type { ArtifactDocumentView, ArtifactTreeView, CommentView, DecisionCard, DecisionCardStatus, ReworkOrder } from "../api/types";
import { artifactName, shortRevision } from "../lib/artifacts";
import type { StatusTone } from "../lib/statusTone";
import { MermaidBlock } from "./MermaidBlock";
import { ReworkSummaryCard } from "./RequirementsPhase";
import { LoadingState, Notice } from "./Section";
import { StatusBadge } from "./StatusBadge";

const DECISION_STATUS_LABELS: Record<DecisionCardStatus, string> = {
  proposed: "предложено",
  accepted: "принято",
  needs_revision: "требует пересмотра",
  superseded: "заменено",
};

const DECISION_STATUS_TONES: Record<DecisionCardStatus, StatusTone> = {
  proposed: "info",
  accepted: "success",
  needs_revision: "warning",
  superseded: "muted",
};

/** A ```mermaid fence becomes a diagram; every other block stays a plain <pre>. */
const OVERVIEW_COMPONENTS: Components = {
  pre(props) {
    const child = Array.isArray(props.children) ? props.children[0] : props.children;
    if (isValidElement<{ className?: string; children?: ReactNode }>(child) && /\blanguage-mermaid\b/.test(child.props.className ?? "")) {
      return <MermaidBlock source={String(child.props.children ?? "").replace(/\n$/, "")} />;
    }
    return <pre className={props.className}>{props.children}</pre>;
  },
};

export interface ArchitecturePhaseProps {
  changeId: string;
  api: ApiClient;
  hasToken: boolean;
  onTokenRequired: () => void;
  tree: ArtifactTreeView | null;
  /** The 503 (or other) detail of the artifacts read — shown honestly, no fake data. */
  treeError: string | null;
  /** `design/overview.md` of the change branch when it exists and could be read. */
  overview: ArtifactDocumentView | null;
  /** Comments of the phase; the open ones are selectable in the alternative form. */
  comments: CommentView[];
  /** Rework orders of the phase — the finished ones carry the agent's summary per decision. */
  orders: ReworkOrder[];
  /** Bumped by the parent after every write: the decisions overview is re-read. */
  reloadKey: number;
  onChanged: () => void;
  onOpenArtifact: (path: string) => void;
  /** The guidance «Запросить альтернативу» scrolls here. */
  decisionsRef?: RefObject<HTMLElement | null>;
}

/**
 * «Результат» of the architecture phase (T095, M3): the design overview
 * (`design/overview.md`, read-only with its mermaid diagram; the editor is
 * one click away) and the decisions overview from `GET /changes/{id}/decisions`
 * — one card per ADR with proposal, rationale, alternatives, consequences,
 * impact and the derived status. «Запросить альтернативу» is an inline form
 * on the card: a rework order bound to that decision, never a page change
 * (T093). Approval is the guidance CTA, not a button here.
 */
export function ArchitecturePhase({
  changeId,
  api,
  hasToken,
  onTokenRequired,
  tree,
  treeError,
  overview,
  comments,
  orders,
  reloadKey,
  onChanged,
  onOpenArtifact,
  decisionsRef,
}: ArchitecturePhaseProps) {
  const decisions = useAsync(() => api.getDecisions(changeId), [changeId, reloadKey]);
  const designNodes = tree?.nodes.filter((node) => node.kind === "design" || node.kind === "adr") ?? [];

  return (
    <div className="stack" data-testid="architecture-phase">
      <section className="card" data-testid="design-overview">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 className="card__title" style={{ margin: 0 }}>
            Обзор проектирования
          </h3>
          {overview ? (
            <div className="row">
              <span className="muted mono" title={overview.revision}>
                ревизия {shortRevision(overview.revision)}
              </span>
              <button type="button" className="button" onClick={() => onOpenArtifact(overview.path)} data-testid="open-overview">
                Открыть в редакторе
              </button>
            </div>
          ) : null}
        </div>
        <p className="card__description">Предложение архитектора на ветке изменения; baseline продукта не меняется до доставки.</p>
        {treeError ? (
          <Notice tone="warning">Артефакты недоступны: {treeError}</Notice>
        ) : tree === null ? (
          <p className="muted">Загрузка артефактов…</p>
        ) : tree.revision === null ? (
          <p className="muted" data-testid="architecture-no-branch">
            Ветки изменения ещё нет: агент не создал ни одной ревизии. Обзор появится после запуска фазы.
          </p>
        ) : overview === null ? (
          <p className="muted" data-testid="architecture-no-overview">
            Ветка <span className="mono">{tree.branch}</span> есть (ревизия {shortRevision(tree.revision)}), но{" "}
            <span className="mono">design/overview.md</span> в ней пока нет
            {designNodes.length > 0 ? `; артефактов проектирования: ${designNodes.length}` : ""}.
          </p>
        ) : (
          <div className="editor__rendered design-overview" data-testid="design-overview-body">
            <Markdown remarkPlugins={[remarkGfm]} components={OVERVIEW_COMPONENTS}>
              {overview.body}
            </Markdown>
          </div>
        )}
      </section>

      <section className="card" data-testid="decisions-overview" ref={decisionsRef}>
        <h3 className="card__title">Обзор решений</h3>
        <p className="card__description">
          Карточка — read-model над ADR в git; статус производный: «принято» только после согласования фазы на текущей ревизии.
        </p>
        {decisions.loading && !decisions.data ? <LoadingState /> : null}
        {decisions.error && !decisions.data ? (
          <Notice tone="warning">Решения недоступны: {decisions.error.detail}</Notice>
        ) : null}
        {decisions.data ? (
          <div className="stack">
            {decisions.data.errors.length > 0 ? (
              <Notice tone="warning">
                Не разобраны: {decisions.data.errors.join("; ")}
              </Notice>
            ) : null}
            <p className="muted" style={{ margin: 0 }} data-testid="decisions-revision">
              ревизия <span className="mono">{shortRevision(decisions.data.revision)}</span> ·{" "}
              {decisions.data.approved ? "фаза согласована на этой ревизии" : "фаза не согласована"} · решений: {decisions.data.decisions.length}
            </p>
            {decisions.data.decisions.length === 0 ? (
              <p className="muted" data-testid="decisions-empty">
                {tree?.revision === null ? "Ветки изменения ещё нет — решений пока нет." : "ADR на ветке пока нет: архитектор ещё не оформил решения."}
              </p>
            ) : (
              decisions.data.decisions.map((card) => (
                <DecisionCardView
                  key={card.id}
                  changeId={changeId}
                  api={api}
                  card={card}
                  comments={comments}
                  orders={orders}
                  hasToken={hasToken}
                  onTokenRequired={onTokenRequired}
                  onChanged={onChanged}
                  onOpenArtifact={onOpenArtifact}
                />
              ))
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}

interface DecisionCardViewProps {
  changeId: string;
  api: ApiClient;
  card: DecisionCard;
  comments: CommentView[];
  orders: ReworkOrder[];
  hasToken: boolean;
  onTokenRequired: () => void;
  onChanged: () => void;
  onOpenArtifact: (path: string) => void;
}

/** One decision card (T093): the ADR sections, the derived status and the inline «Запросить альтернативу». */
function DecisionCardView({ changeId, api, card, comments, orders, hasToken, onTokenRequired, onChanged, onOpenArtifact }: DecisionCardViewProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [issued, setIssued] = useState<ReworkOrder | null>(null);
  const related = orders
    .filter((order) => order.decision_ids.includes(card.id))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const lastDone = related.find((order) => order.status === "done") ?? null;
  const pending = card.pending_alternative;

  return (
    <article className="decision-card" data-testid={`decision-${card.id}`} aria-label="Решение">
      <div className="row decision-card__head">
        <h4 className="decision-card__title">{card.title}</h4>
        <span className="mono muted">{card.id}</span>
        <StatusBadge label={DECISION_STATUS_LABELS[card.status]} tone={DECISION_STATUS_TONES[card.status]} />
        {card.document_status && card.document_status !== card.status ? (
          <span className="muted" title="Статус, записанный агентом во frontmatter">
            в документе: {card.document_status}
          </span>
        ) : null}
        <span className="muted mono" title={card.revision ?? undefined}>
          ревизия {shortRevision(card.revision)}
        </span>
      </div>
      {card.impact.length > 0 ? (
        <div className="row" data-testid={`impact-${card.id}`}>
          <span className="muted">влияние:</span>
          {card.impact.map((area) => (
            <span key={area} className="badge badge--neutral">
              {area}
            </span>
          ))}
        </div>
      ) : null}

      <DecisionSection title="Предложение" text={card.proposal} />
      <DecisionSection title="Обоснование" text={card.rationale} />
      <div>
        <h5 className="decision-card__caption">Альтернативы</h5>
        {card.alternatives.length === 0 ? (
          <p className="muted">Альтернативы не описаны.</p>
        ) : (
          <table className="table" data-testid={`alternatives-${card.id}`}>
            <thead>
              <tr>
                <th>Вариант</th>
                <th>Суть</th>
                <th>Почему не выбран</th>
              </tr>
            </thead>
            <tbody>
              {card.alternatives.map((alternative) => (
                <tr key={alternative.title}>
                  <td>{alternative.title}</td>
                  <td>{alternative.summary ?? <span className="muted">—</span>}</td>
                  <td>{alternative.rejected_because ?? <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <DecisionSection title="Последствия" text={card.consequences} />

      {pending ? (
        <div className="stack" data-testid={`pending-${card.id}`}>
          <p className="muted" style={{ margin: 0 }}>
            По решению открыто поручение — альтернатива запрашивается у агента.
          </p>
          <ReworkSummaryCard order={pending} />
        </div>
      ) : lastDone ? (
        <div className="stack" data-testid={`revised-${card.id}`}>
          <p className="muted" style={{ margin: 0 }}>
            Решение пересмотрено по поручению <span className="mono">{lastDone.id}</span>.
          </p>
          <ReworkSummaryCard order={lastDone} />
        </div>
      ) : null}
      {card.affected_artifacts.length > 0 ? (
        <div className="row" data-testid={`affected-${card.id}`}>
          <span className="muted">затронутые артефакты:</span>
          {card.affected_artifacts.map((path) => (
            <button key={path} type="button" className="link-button" onClick={() => onOpenArtifact(path)} title={path}>
              {artifactName(path)}
            </button>
          ))}
        </div>
      ) : null}

      <div className="form-actions">
        <button type="button" className="button" onClick={() => onOpenArtifact(card.path)} data-testid={`open-adr-${card.id}`}>
          Открыть ADR
        </button>
        {pending === null ? (
          <button
            type="button"
            className="button"
            onClick={() => setFormOpen((open) => !open)}
            aria-expanded={formOpen}
            data-testid={`alternative-toggle-${card.id}`}
          >
            Запросить альтернативу
          </button>
        ) : (
          <span className="field__hint">пока поручение открыто, новую альтернативу запросить нельзя</span>
        )}
      </div>
      {formOpen && pending === null ? (
        <AlternativeForm
          changeId={changeId}
          api={api}
          decision={card}
          comments={comments.filter((comment) => comment.status !== "closed")}
          hasToken={hasToken}
          onTokenRequired={onTokenRequired}
          onIssued={(order) => {
            setIssued(order);
            setFormOpen(false);
            onChanged();
          }}
          onCancel={() => setFormOpen(false)}
        />
      ) : null}
      {issued ? (
        <Notice tone="success">
          Поручение <span className="mono">{issued.id}</span> создано: агент подготовит альтернативу к «{card.title}»; статус — в карточке.
        </Notice>
      ) : null}
    </article>
  );
}

function DecisionSection({ title, text }: { title: string; text: string | null }) {
  return (
    <div>
      <h5 className="decision-card__caption">{title}</h5>
      {text ? (
        <div className="decision-card__text">
          <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
        </div>
      ) : (
        <p className="muted">не описано</p>
      )}
    </div>
  );
}

interface AlternativeFormProps {
  changeId: string;
  api: ApiClient;
  decision: DecisionCard;
  comments: CommentView[];
  hasToken: boolean;
  onTokenRequired: () => void;
  onIssued: (order: ReworkOrder) => void;
  onCancel: () => void;
}

/**
 * «Запросить альтернативу» (T093): an instruction plus optional open comments
 * → `POST /decisions/{id}/alternative`. The order is bound to this decision
 * and spends a rework round; 409 (another order of the phase is pending) and
 * 404 show the server detail — nothing leaves the screen.
 */
export function AlternativeForm({ changeId, api, decision, comments, hasToken, onTokenRequired, onIssued, onCancel }: AlternativeFormProps) {
  const [instruction, setInstruction] = useState("");
  const [commentIds, setCommentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const order = await api.requestAlternative(changeId, decision.id, { instruction: instruction.trim(), comment_ids: commentIds });
      onIssued(order);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="rework-form stack"
      data-testid={`alternative-form-${decision.id}`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="muted" style={{ margin: 0 }}>
        Поручение по решению <span className="mono">{decision.id}</span> расходует раунд доработки; отказ относится к этому решению, не к фазе целиком.
      </p>
      <fieldset className="stack" disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <label className="field">
          <span className="field__label">Что не устраивает и какую альтернативу рассмотреть</span>
          <textarea
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            rows={3}
            data-testid={`alternative-instruction-${decision.id}`}
          />
        </label>
        <div>
          <span className="field__label">Замечания фазы, которые агент должен учесть</span>
          {comments.length === 0 ? <p className="muted">Открытых замечаний нет.</p> : null}
          {comments.map((comment) => (
            <label key={comment.id} className="check">
              <input
                type="checkbox"
                checked={commentIds.includes(comment.id)}
                onChange={() =>
                  setCommentIds((selected) => (selected.includes(comment.id) ? selected.filter((id) => id !== comment.id) : [...selected, comment.id]))
                }
              />
              <span>
                <span className="mono">{comment.id}</span> — {comment.body}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="form-actions">
        <button
          type="submit"
          className="button button--primary"
          disabled={busy || instruction.trim().length === 0}
          data-testid={`alternative-submit-${decision.id}`}
        >
          {busy ? "Отправляю…" : "Отправить агенту"}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        {!hasToken ? <span className="field__hint">требует токен оператора</span> : null}
      </div>
      {error ? <Notice tone="error">Альтернатива не запрошена: {error}</Notice> : null}
    </form>
  );
}
