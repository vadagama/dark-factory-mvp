import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError, createApiClient } from "../api/client";
import type { ApiClient } from "../api/client";
import { POLL_MS, useAsync } from "../api/hooks";
import { getToken } from "../api/token";
import type {
  ArtifactDocumentView,
  ArtifactTreeView,
  ChangeCard,
  CommentView,
  Decision,
  Phase,
  PhaseGate,
  Question,
  ReworkOrder,
  RunCard,
  Scenario,
} from "../api/types";
import { ApprovalForm } from "../components/ApprovalForm";
import { BriefSection } from "../components/BriefSection";
import { ContextPanel } from "../components/ContextPanel";
import type { CommentSeed, Fragment } from "../components/ContextPanel";
import { MarkdownEditor } from "../components/MarkdownEditor";
import { NextStep } from "../components/NextStep";
import { PhaseGatePanel } from "../components/PhaseGatePanel";
import { RequirementsPhase } from "../components/RequirementsPhase";
import { ErrorState, LoadingState, Notice } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import {
  ARTIFACT_KIND_LABELS,
  PHASES,
  PHASE_GATE,
  PHASE_MILESTONE,
  PHASE_STATE_LABELS,
  artifactName,
  budgetFact,
  phaseIndexLabel,
  phaseLabel,
  phaseState,
  shortRevision,
} from "../lib/artifacts";
import type { PhaseState } from "../lib/artifacts";
import { formatCost, formatDateTime, formatNumber, formatStage, formatTime } from "../lib/format";
import type { ConsoleAction } from "../lib/guidance";
import { statusTone } from "../lib/statusTone";
import type { StatusTone } from "../lib/statusTone";

type Tab = "result" | "changes" | "checks" | "history";

const TAB_LABELS: Record<Tab, string> = {
  result: "Результат",
  changes: "Изменения",
  checks: "Проверки",
  history: "История",
};

const SCENARIO_LABELS: Record<Scenario, string> = {
  specs_only: "только спецификации",
  full: "полный",
};

const PHASE_STATE_TONES: Record<PhaseState, StatusTone> = {
  approved: "success",
  decision: "warning",
  active: "info",
  rework: "warning",
  passed: "neutral",
  pending: "muted",
  skipped: "muted",
};

export interface WorkspaceModel {
  card: ChangeCard;
  runs: RunCard[];
  approvals: Decision[];
  gates: Partial<Record<Phase, PhaseGate>>;
  questions: Question[];
  comments: CommentView[];
  orders: ReworkOrder[];
}

export interface ArtifactsModel {
  tree: ArtifactTreeView | null;
  /** The server detail when the tree could not be read (503 = no repository on the contour). */
  error: string | null;
  documents: ArtifactDocumentView[];
}

/**
 * ChangeSet workspace (T088, ADR-037 p.4): the main screen of a change.
 * Top panel — title, id, current phase and its state, budget (fact / limit /
 * forecast), blockers; left — the phases F0–F7 with state, open counts and
 * the iteration number; centre — the selected phase with the tabs
 * Результат · Изменения · Проверки · История (or the markdown editor of an
 * opened artifact); right — the collapsible context panel (discussion of the
 * selected fragment, rework orders); bottom — the decision panel rendered
 * only from `Guidance` with exactly one CTA (`NextStep`). Phases other than
 * F0/F1 are honest placeholders naming their milestone. Nothing here shows
 * a percentage of completion (ADR-037 p.6).
 */
export function ChangeSetPage() {
  const { changeId = "" } = useParams();
  const api = useMemo(() => createApiClient(), []);
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [chosenPhase, setChosenPhase] = useState<Phase | null>(null);
  const [tab, setTab] = useState<Tab>("result");
  const [contextOpen, setContextOpen] = useState(true);
  const [fragment, setFragment] = useState<Fragment | null>(null);
  const [commentSeed, setCommentSeed] = useState<CommentSeed | null>(null);
  const [reworkOpen, setReworkOpen] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [recorded, setRecorded] = useState<Decision | null>(null);
  const [editorPath, setEditorPath] = useState<string | null>(null);
  const questionsRef = useRef<HTMLElement | null>(null);
  const problemRef = useRef<HTMLTextAreaElement | null>(null);
  const hasToken = getToken() !== null;

  const reloadAll = useCallback(() => setReloadNonce((value) => value + 1), []);

  // The next step is its own read model (ADR-033), polled so an answer or a
  // rework round shows up without a manual reload.
  const guidance = useAsync(() => api.getChangeGuidance(changeId), [changeId, reloadNonce], { pollMs: POLL_MS });

  const workspace = useAsync<WorkspaceModel>(
    async () => {
      const card = await api.getChange(changeId);
      const [runs, approvals, questions, comments, orders, gates] = await Promise.all([
        Promise.all(card.runs.map((run) => api.getRun(run.run_id))),
        api.getChangeApprovals(changeId),
        api.listQuestions(changeId),
        api.listComments(changeId),
        api.listReworkOrders(changeId),
        Promise.all(PHASES.map((phase) => api.getPhaseGate(changeId, phase).catch(() => null))),
      ]);
      const byPhase: Partial<Record<Phase, PhaseGate>> = {};
      PHASES.forEach((phase, index) => {
        const gate = gates[index];
        if (gate) {
          byPhase[phase] = gate;
        }
      });
      return { card, runs, approvals, gates: byPhase, questions, comments, orders };
    },
    [changeId, reloadNonce],
    { pollMs: POLL_MS },
  );

  // Artifacts are read from git (ADR-035): not polled, re-read after every write.
  const artifacts = useAsync<ArtifactsModel>(async () => {
    try {
      const tree = await api.getArtifactTree(changeId);
      const specs = tree.nodes.filter((node) => node.kind === "spec");
      const documents = await Promise.all(
        specs.map((node) => api.getArtifact(changeId, node.path).catch(() => null)),
      );
      return { tree, error: null, documents: documents.filter((doc): doc is ArtifactDocumentView => doc !== null) };
    } catch (cause) {
      return { tree: null, error: cause instanceof ApiError ? cause.detail : String(cause), documents: [] };
    }
  }, [changeId, reloadNonce]);

  const currentPhase: Phase | null = guidance.data?.phase ?? null;
  const selectedPhase: Phase =
    chosenPhase ?? (currentPhase === null ? "initiative" : currentPhase === "done" ? "delivery" : currentPhase);
  const gate = workspace.data?.gates[selectedPhase] ?? null;
  const phaseQuestions = workspace.data?.questions.filter((question) => question.phase === selectedPhase) ?? [];
  const phaseComments = workspace.data?.comments.filter((comment) => comment.phase === selectedPhase) ?? [];
  const phaseOrders = workspace.data?.orders.filter((order) => order.phase === selectedPhase) ?? [];

  const selectPhase = (phase: Phase) => {
    setChosenPhase(phase);
    setEditorPath(null);
    setTab("result");
  };

  // The guidance speaks about the current phase: a decision action brings it into view.
  const focusCurrentPhase = () => {
    if (currentPhase && currentPhase !== "done") {
      setChosenPhase(currentPhase);
    }
  };

  const perform = (action: ConsoleAction) => {
    switch (action.kind) {
      case "approve_phase":
        focusCurrentPhase();
        setRecorded(null);
        setApprovalOpen(true);
        return;
      case "rework":
        focusCurrentPhase();
        setContextOpen(true);
        setReworkOpen(true);
        return;
      case "focus_questions": {
        focusCurrentPhase();
        setEditorPath(null);
        setTab("result");
        const target = questionsRef.current;
        if (target && typeof target.scrollIntoView === "function") {
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        return;
      }
      case "open_artifacts":
        setEditorPath(null);
        setTab("changes");
        return;
      case "edit_brief": {
        setChosenPhase("initiative");
        setEditorPath(null);
        setTab("result");
        const field = problemRef.current;
        if (field) {
          if (typeof field.scrollIntoView === "function") {
            field.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          field.focus();
        }
        return;
      }
      case "open_change":
      case "open_run":
        reloadAll();
        return;
      case "new_change":
        navigate(
          workspace.data?.card.product_id
            ? `/products/${encodeURIComponent(workspace.data.card.product_id)}/new-change`
            : "/",
        );
        return;
      case "validate_product":
      case "reload_product":
        navigate(`/products/${encodeURIComponent(action.productId)}`);
        return;
    }
  };

  const openFragment = (artifact: string, anchorId: string | null) => {
    setFragment({ artifact, anchorId });
    setContextOpen(true);
  };

  const card = workspace.data?.card ?? null;
  const fact = workspace.data ? budgetFact(workspace.data.runs.map((run) => run.usage.cost)) : null;
  const approvalGate = PHASE_GATE[selectedPhase];

  return (
    <div className="workspace" data-testid="changeset-workspace">
      <header className="workspace__top card" data-testid="workspace-top">
        {workspace.loading && !card ? <LoadingState /> : null}
        {workspace.error && !card ? <ErrorState message={workspace.error.detail} /> : null}
        {card ? (
          <>
            <div className="workspace__title">
              <h2>{card.title}</h2>
              <span className="mono">{card.id}</span>
              <StatusBadge label={card.risk_class} tone="info" />
              {card.product_id ? (
                <Link to={`/products/${encodeURIComponent(card.product_id)}`} className="mono">
                  продукт: {card.product_id}
                </Link>
              ) : null}
              <Link to={`/changes/${encodeURIComponent(card.id)}/card`} className="muted">
                карточка
              </Link>
              <Link to={`/changes/${encodeURIComponent(card.id)}/gates`} className="muted">
                гейты
              </Link>
              {workspace.updatedAt !== null ? (
                <span className="muted" data-testid="updated-at">
                  обновлено {formatTime(workspace.updatedAt)}
                </span>
              ) : null}
            </div>
            <dl className="workspace__facts">
              <div>
                <dt>Фаза</dt>
                <dd data-testid="workspace-phase">
                  {currentPhase === "done" ? (
                    <StatusBadge label="завершено" tone="success" />
                  ) : (
                    <>
                      {phaseLabel(currentPhase)}{" "}
                      {currentPhase ? (
                        <StatusBadge
                          label={PHASE_STATE_LABELS[phaseState(currentPhase, currentPhase, workspace.data?.gates[currentPhase] ?? null)]}
                          tone={PHASE_STATE_TONES[phaseState(currentPhase, currentPhase, workspace.data?.gates[currentPhase] ?? null)]}
                        />
                      ) : null}
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt>Бюджет</dt>
                <dd data-testid="workspace-budget">
                  факт {formatCost(fact)} · лимит{" "}
                  {card.spend_limit ? `${card.spend_limit.cost_budget_usd} USD` : "не задан"} · прогноз{" "}
                  <span className="muted" title="Прогноз появится с данными исполнения (M4)">
                    —
                  </span>
                </dd>
              </div>
              <div>
                <dt>Блокеры</dt>
                <dd data-testid="workspace-blockers">{guidance.data ? guidance.data.blockers.length : "—"}</dd>
              </div>
            </dl>
            <div className="row" data-testid="change-intake-line">
              <span className="muted">сценарий: {SCENARIO_LABELS[card.scenario] ?? card.scenario}</span>
              <span className="muted">
                лимит:{" "}
                {card.spend_limit
                  ? `${card.spend_limit.cost_budget_usd} USD${
                      card.spend_limit.token_budget !== null ? `, ${formatNumber(card.spend_limit.token_budget)} токенов` : ""
                    }`
                  : "не задан"}
              </span>
              <span className="muted">решений: {card.decisions_count}</span>
            </div>
          </>
        ) : null}
      </header>

      <div className={`workspace__body${contextOpen ? "" : " workspace__body--collapsed"}`}>
        <nav className="workspace__phases card" aria-label="Фазы" data-testid="workspace-phases">
          <ul>
            {PHASES.map((phase) => {
              const phaseGate = workspace.data?.gates[phase] ?? null;
              const state = phaseState(phase, currentPhase, phaseGate);
              return (
                <li key={phase}>
                  <button
                    type="button"
                    className={`phase-item${phase === selectedPhase ? " phase-item--selected" : ""}`}
                    aria-current={phase === selectedPhase ? "true" : undefined}
                    onClick={() => selectPhase(phase)}
                    data-testid={`phase-${phase}`}
                  >
                    <span className="phase-item__name">
                      <span className="muted">{phaseIndexLabel(phase)}</span> {phaseLabel(phase)}
                    </span>
                    <StatusBadge label={PHASE_STATE_LABELS[state]} tone={PHASE_STATE_TONES[state]} />
                    {phaseGate ? (
                      <span className="phase-item__counts muted">
                        вопросов {phaseGate.open_questions} · замечаний {phaseGate.open_comments} · итерация{" "}
                        {phaseGate.rework_rounds_used}
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <main className="workspace__center" data-testid="workspace-center">
          {editorPath ? (
            <ArtifactEditorPane
              key={editorPath}
              changeId={changeId}
              api={api}
              path={editorPath}
              hasToken={hasToken}
              onTokenRequired={() => setDialogOpen(true)}
              onBack={() => setEditorPath(null)}
              onSaved={reloadAll}
              onComment={(anchorId, quote) => {
                setFragment({ artifact: editorPath, anchorId });
                setCommentSeed({ id: (commentSeed?.id ?? 0) + 1, artifact: editorPath, anchorId, quote });
                setContextOpen(true);
              }}
            />
          ) : (
            <>
              <div className="workspace__center-head">
                <h3>
                  {phaseIndexLabel(selectedPhase)} · {phaseLabel(selectedPhase)}
                </h3>
                <div className="tabs" role="tablist" aria-label="Рабочая область фазы">
                  {(Object.keys(TAB_LABELS) as Tab[]).map((candidate) => (
                    <button
                      key={candidate}
                      type="button"
                      role="tab"
                      aria-selected={tab === candidate}
                      className={`tab${tab === candidate ? " tab--active" : ""}`}
                      onClick={() => setTab(candidate)}
                      data-testid={`tab-${candidate}`}
                    >
                      {TAB_LABELS[candidate]}
                    </button>
                  ))}
                </div>
              </div>
              <div role="tabpanel" data-testid={`tabpanel-${tab}`}>
                {tab === "result" ? (
                  selectedPhase === "requirements" ? (
                    <RequirementsPhase
                      changeId={changeId}
                      api={api}
                      hasToken={hasToken}
                      onTokenRequired={() => setDialogOpen(true)}
                      tree={artifacts.data?.tree ?? null}
                      treeError={artifacts.data?.error ?? null}
                      documents={artifacts.data?.documents ?? []}
                      questions={phaseQuestions}
                      orders={phaseOrders}
                      onChanged={reloadAll}
                      onOpenArtifact={setEditorPath}
                      onSelectFragment={openFragment}
                      questionsRef={questionsRef}
                    />
                  ) : selectedPhase === "initiative" ? (
                    card ? (
                      <BriefSection
                        changeId={card.id}
                        brief={card.brief}
                        onSaved={reloadAll}
                        onTokenRequired={() => setDialogOpen(true)}
                        problemRef={problemRef}
                      />
                    ) : (
                      <LoadingState />
                    )
                  ) : (
                    <PhasePlaceholder phase={selectedPhase} />
                  )
                ) : null}
                {tab === "changes" ? <ArtifactsTab model={artifacts.data} loading={artifacts.loading} onOpen={setEditorPath} /> : null}
                {tab === "checks" ? <PhaseGatePanel gate={gate} /> : null}
                {tab === "history" ? (
                  <HistoryTab approvals={workspace.data?.approvals ?? []} orders={workspace.data?.orders ?? []} runs={workspace.data?.runs ?? []} />
                ) : null}
              </div>
            </>
          )}
        </main>

        <aside className="workspace__context card" data-testid="workspace-context">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>Контекст</h3>
            <button
              type="button"
              className="button"
              onClick={() => setContextOpen((open) => !open)}
              aria-expanded={contextOpen}
              data-testid="context-toggle"
            >
              {contextOpen ? "Свернуть" : "Развернуть"}
            </button>
          </div>
          {contextOpen ? (
            <ContextPanel
              changeId={changeId}
              api={api}
              phase={selectedPhase}
              hasToken={hasToken}
              onTokenRequired={() => setDialogOpen(true)}
              tree={artifacts.data?.tree ?? null}
              documents={artifacts.data?.documents ?? []}
              comments={phaseComments}
              questions={phaseQuestions}
              orders={phaseOrders}
              fragment={fragment}
              onFragmentChange={setFragment}
              seed={commentSeed}
              reworkOpen={reworkOpen}
              onReworkOpenChange={setReworkOpen}
              onChanged={reloadAll}
            />
          ) : null}
        </aside>
      </div>

      <footer className="workspace__decision" data-testid="workspace-decision">
        {guidance.data ? <NextStep guidance={guidance.data} onPerform={perform} /> : null}
        {guidance.error && !guidance.data ? <ErrorState message={guidance.error.detail} /> : null}
        {recorded ? (
          <div data-testid="approval-recorded">
            <Notice tone="success">
              Решение записано: <span className="mono">{recorded.id}</span> ({recorded.outcome}, ревизия{" "}
              {shortRevision(recorded.commit_sha)}).
            </Notice>
          </div>
        ) : null}
        {approvalOpen && card && gate && approvalGate ? (
          <ApprovalForm
            changeId={changeId}
            api={api}
            gate={gate}
            gateName={approvalGate}
            decisionsCount={card.decisions_count}
            hasToken={hasToken}
            onTokenRequired={() => setDialogOpen(true)}
            label={guidance.data?.primary.label ?? "Согласовать"}
            onRecorded={(decision) => {
              setRecorded(decision);
              setApprovalOpen(false);
              reloadAll();
            }}
            onCancel={() => setApprovalOpen(false)}
            onReload={() => {
              setApprovalOpen(false);
              reloadAll();
            }}
          />
        ) : null}
        {approvalOpen && (!gate || !approvalGate) ? (
          <Notice tone="warning">У фазы «{phaseLabel(selectedPhase)}» нет гейта согласования.</Notice>
        ) : null}
      </footer>
      <TokenDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

/** Honest placeholder for a phase the Console gets in a later milestone (ADR-037 p.9). */
function PhasePlaceholder({ phase }: { phase: Phase }) {
  const milestone = PHASE_MILESTONE[phase];
  return (
    <p className="state state--empty" role="status" data-testid="phase-placeholder">
      Рабочая область фазы «{phaseLabel(phase)}» появится в {milestone ?? "следующем milestone"} (ADR-037 п.9); пока фаза
      ведётся через CLI, её состояние и решения видны в «Проверки» и «История».
    </p>
  );
}

function ArtifactsTab({ model, loading, onOpen }: { model: ArtifactsModel | null; loading: boolean; onOpen: (path: string) => void }) {
  if (loading && !model) {
    return <LoadingState />;
  }
  if (!model) {
    return null;
  }
  if (model.error) {
    return (
      <Notice tone="warning">
        Артефакты недоступны: {model.error}
      </Notice>
    );
  }
  const tree = model.tree;
  if (!tree) {
    return null;
  }
  if (tree.revision === null) {
    return (
      <p className="state state--empty" role="status" data-testid="artifacts-no-branch">
        Ветки изменения <span className="mono">{tree.branch}</span> ещё нет — артефакты появятся после первой ревизии агента.
      </p>
    );
  }
  return (
    <div className="stack" data-testid="artifacts-tab">
      <p className="muted" style={{ margin: 0 }}>
        Ветка <span className="mono">{tree.branch}</span> · ревизия <span className="mono">{shortRevision(tree.revision)}</span>
      </p>
      {tree.nodes.length === 0 ? (
        <p className="state state--empty" role="status">
          Ветка есть, артефактов в ней пока нет.
        </p>
      ) : (
        <table className="table" data-testid="artifacts-table">
          <thead>
            <tr>
              <th>Артефакт</th>
              <th>Тип</th>
              <th>Ревизия</th>
              <th>Черновик</th>
            </tr>
          </thead>
          <tbody>
            {tree.nodes.map((node) => (
              <tr key={node.path}>
                <td>
                  <button type="button" className="link-button" onClick={() => onOpen(node.path)}>
                    {artifactName(node.path)}
                  </button>
                  <div className="mono muted">{node.path}</div>
                </td>
                <td>
                  <StatusBadge label={ARTIFACT_KIND_LABELS[node.kind]} tone={node.kind === "spec" ? "info" : "neutral"} />
                </td>
                <td className="mono" title={node.revision ?? undefined}>
                  {shortRevision(node.revision)}
                </td>
                <td>{tree.drafts.includes(node.path) ? <StatusBadge label="есть" tone="warning" /> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function HistoryTab({ approvals, orders, runs }: { approvals: Decision[]; orders: ReworkOrder[]; runs: RunCard[] }) {
  return (
    <div className="stack" data-testid="history-tab">
      <h4 style={{ margin: 0 }}>Решения</h4>
      {approvals.length === 0 ? (
        <p className="muted">Решений ещё нет.</p>
      ) : (
        <table className="table" data-testid="history-decisions">
          <thead>
            <tr>
              <th>Решение</th>
              <th>Когда</th>
              <th>Гейт</th>
              <th>Исход</th>
              <th>Кто</th>
              <th>Ревизия</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {approvals.map((decision) => (
              <tr key={decision.id}>
                <td className="mono">{decision.id}</td>
                <td className="muted">{formatDateTime(decision.decided_at)}</td>
                <td>{decision.gate}</td>
                <td>
                  <StatusBadge label={decision.outcome} tone={statusTone(decision.outcome, "decision")} />
                </td>
                <td>{decision.decided_by}</td>
                <td className="mono" title={decision.commit_sha ?? undefined}>
                  {shortRevision(decision.commit_sha)}
                </td>
                <td>{decision.comment ?? <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h4 style={{ margin: 0 }}>Поручения на доработку</h4>
      {orders.length === 0 ? (
        <p className="muted">Поручений не было.</p>
      ) : (
        <ul>
          {orders.map((order) => (
            <li key={order.id}>
              <span className="mono">{order.id}</span> · {phaseLabel(order.phase)} · {order.status}
              {order.round !== null ? ` · раунд ${order.round}` : ""} · {formatDateTime(order.created_at)}
            </li>
          ))}
        </ul>
      )}
      <h4 style={{ margin: 0 }}>Прогоны</h4>
      {runs.length === 0 ? (
        <p className="muted">Запусков ещё не было.</p>
      ) : (
        <ul>
          {runs.map((run) => (
            <li key={run.run_id}>
              <span className="mono">{run.run_id}</span> <StatusBadge label={run.status} tone={statusTone(run.status, "run")} />{" "}
              <span className="muted">
                {run.stages.map((stage) => `${formatStage(stage.stage)}: ${stage.status}`).join(" · ")} · стоимость{" "}
                {formatCost(run.usage.cost)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface ArtifactEditorPaneProps {
  changeId: string;
  api: ApiClient;
  path: string;
  hasToken: boolean;
  onTokenRequired: () => void;
  onBack: () => void;
  onSaved: () => void;
  onComment: (anchorId: string | null, quote: string) => void;
}

/** Loads one artifact and hosts the editor; a new revision remounts the editor (key). */
function ArtifactEditorPane({ changeId, api, path, hasToken, onTokenRequired, onBack, onSaved, onComment }: ArtifactEditorPaneProps) {
  // Keyed by path in the parent: a new path is a fresh pane, so no reset effect is needed.
  const [nonce, setNonce] = useState(0);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const doc = useAsync(() => api.getArtifact(changeId, path), [changeId, path, nonce]);

  return (
    <div className="stack" data-testid="artifact-editor-pane">
      <div className="row">
        <button type="button" className="button" onClick={onBack} data-testid="editor-back">
          ← К результату фазы
        </button>
        <span className="mono">{path}</span>
        {doc.data ? <StatusBadge label={ARTIFACT_KIND_LABELS[doc.data.kind]} tone="info" /> : null}
        {doc.data?.viewed ? <StatusBadge label="просмотрено" tone="neutral" /> : null}
      </div>
      {doc.loading && !doc.data ? <LoadingState /> : null}
      {doc.error && !doc.data ? <ErrorState message={doc.error.detail} /> : null}
      {savedNotice ? <Notice tone="success">{savedNotice}</Notice> : null}
      {doc.data ? (
        <MarkdownEditor
          key={doc.data.revision}
          changeId={changeId}
          document={doc.data}
          api={api}
          hasToken={hasToken}
          onTokenRequired={onTokenRequired}
          onSaved={(written) => {
            setSavedNotice(
              `Сохранено в git: ревизия ${shortRevision(written.revision)}${
                written.stale_questions.length > 0 ? `; вопросов стало неактуально: ${written.stale_questions.length}` : ""
              }${written.detached_comments.length > 0 ? `; замечаний отвязано: ${written.detached_comments.length}` : ""}.`,
            );
            setNonce((value) => value + 1);
            onSaved();
          }}
          onReload={() => setNonce((value) => value + 1)}
          onComment={onComment}
        />
      ) : null}
    </div>
  );
}
