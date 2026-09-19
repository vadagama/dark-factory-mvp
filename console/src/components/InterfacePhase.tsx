import { useEffect, useState } from "react";
import type { ApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import type { ArtifactTreeView, CommentView, UiScreen, UiSpecView } from "../api/types";
import { UI_STATE_KINDS, UI_STATE_LABELS, artifactName, previewHref, shortRevision } from "../lib/artifacts";
import { LoadingState, Notice } from "./Section";
import { StatusBadge } from "./StatusBadge";

type UiTab = "scenarios" | "screens" | "links";

const UI_TAB_LABELS: Record<UiTab, string> = {
  scenarios: "Сценарии",
  screens: "Экраны",
  links: "Связи",
};

export interface InterfacePhaseProps {
  changeId: string;
  api: ApiClient;
  tree: ArtifactTreeView | null;
  /** The 503 (or other) detail of the artifacts read — shown honestly, no fake data. */
  treeError: string | null;
  /** Comments of the phase: per-element counts and the explicit «привязка потеряна» marks. */
  comments: CommentView[];
  /** Bumped by the parent after every write: the UI spec is re-read. */
  reloadKey: number;
  onOpenArtifact: (path: string) => void;
  /** «Комментарий» on an element selects `screen.path + EL-*` for the context panel composer. */
  onSelectFragment: (artifact: string, anchorId: string | null) => void;
}

/**
 * «Результат» of the interface phase (T096, M3): the UI spec as three
 * sub-tabs `Сценарии / Экраны / Связи`, «Сценарии» by default. Scenarios are
 * cards with steps linking to their screen; the screen gallery shows every
 * screen with its five states (a missing one is an explicit «не описано»),
 * its elements (a «Комментарий» per element anchors a remark to `EL-*`), and
 * «Открыть на dev» only when a preview URL exists — otherwise the honest
 * «dev-окружение появится после доставки». A detached comment anchor is
 * marked and never moved (ADR-034 p.1).
 */
export function InterfacePhase({ changeId, api, tree, treeError, comments, reloadKey, onOpenArtifact, onSelectFragment }: InterfacePhaseProps) {
  const [tab, setTab] = useState<UiTab>("scenarios");
  const [focusedScreen, setFocusedScreen] = useState<string | null>(null);
  const ui = useAsync(() => api.getUiSpec(changeId), [changeId, reloadKey]);

  useEffect(() => {
    if (tab !== "screens" || focusedScreen === null) {
      return;
    }
    const target = document.getElementById(`screen-${focusedScreen}`);
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [tab, focusedScreen]);

  const focusScreen = (screenId: string) => {
    setFocusedScreen(screenId);
    setTab("screens");
  };

  if (treeError) {
    return (
      <div className="stack" data-testid="interface-phase">
        <Notice tone="warning">Артефакты недоступны: {treeError}</Notice>
      </div>
    );
  }
  if (tree !== null && tree.revision === null) {
    return (
      <div className="stack" data-testid="interface-phase">
        <p className="state state--empty" role="status" data-testid="interface-no-branch">
          Ветки изменения ещё нет: агент не создал ни одной ревизии. UI-спека появится после запуска фазы.
        </p>
      </div>
    );
  }

  const spec = ui.data;
  return (
    <div className="stack" data-testid="interface-phase">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="tabs tabs--sub" role="tablist" aria-label="Разделы UI-спеки">
          {(Object.keys(UI_TAB_LABELS) as UiTab[]).map((candidate) => (
            <button
              key={candidate}
              type="button"
              role="tab"
              aria-selected={tab === candidate}
              className={`tab${tab === candidate ? " tab--active" : ""}`}
              onClick={() => setTab(candidate)}
              data-testid={`ui-tab-${candidate}`}
            >
              {UI_TAB_LABELS[candidate]}
              {spec ? (
                <span className="muted">
                  {" "}
                  {candidate === "scenarios" ? spec.scenarios.length : candidate === "screens" ? spec.screens.length : spec.links.length}
                </span>
              ) : null}
            </button>
          ))}
        </div>
        {spec ? (
          <span className="muted mono" title={spec.revision ?? undefined}>
            ревизия {shortRevision(spec.revision)}
          </span>
        ) : null}
      </div>

      {ui.loading && !spec ? <LoadingState /> : null}
      {ui.error && !spec ? <Notice tone="warning">UI-спека недоступна: {ui.error.detail}</Notice> : null}
      {spec?.errors.length ? <Notice tone="warning">Не разобраны: {spec.errors.join("; ")}</Notice> : null}
      {spec && spec.scenarios.length === 0 && spec.screens.length === 0 ? (
        <p className="state state--empty" role="status" data-testid="interface-empty">
          UI-спеки на ветке пока нет: дизайнер ещё не описал сценарии и экраны.
        </p>
      ) : null}

      {spec ? (
        <div role="tabpanel" data-testid={`ui-tabpanel-${tab}`}>
          {tab === "scenarios" ? <ScenariosTab spec={spec} onOpenArtifact={onOpenArtifact} onFocusScreen={focusScreen} /> : null}
          {tab === "screens" ? (
            <ScreensTab spec={spec} comments={comments} focused={focusedScreen} onOpenArtifact={onOpenArtifact} onSelectFragment={onSelectFragment} />
          ) : null}
          {tab === "links" ? <LinksTab spec={spec} onFocusScreen={focusScreen} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function ScenariosTab({
  spec,
  onOpenArtifact,
  onFocusScreen,
}: {
  spec: UiSpecView;
  onOpenArtifact: (path: string) => void;
  onFocusScreen: (screenId: string) => void;
}) {
  if (spec.scenarios.length === 0) {
    return spec.screens.length > 0 ? <p className="muted">Сценариев нет, хотя экраны описаны.</p> : null;
  }
  return (
    <div className="stack" data-testid="ui-scenarios">
      {spec.scenarios.map((scenario) => (
        <article key={scenario.id} className="scenario-card" data-testid={`scenario-${scenario.id}`}>
          <div className="row">
            <h4 className="scenario-card__title">{scenario.title}</h4>
            <span className="mono muted">{scenario.id}</span>
            <button type="button" className="link-button" onClick={() => onOpenArtifact(scenario.path)} title={scenario.path}>
              {artifactName(scenario.path)}
            </button>
          </div>
          {scenario.summary ? <p className="scenario-card__summary">{scenario.summary}</p> : null}
          {scenario.steps.length === 0 ? (
            <p className="muted">Шаги не описаны.</p>
          ) : (
            <ol className="scenario-steps">
              {scenario.steps.map((step) => (
                <li key={step.id} data-testid={`step-${scenario.id}-${step.id}`}>
                  <span className="mono muted">{step.id}</span> {step.text}
                  {step.screen ? (
                    <>
                      {" "}
                      <button type="button" className="anchor-chip mono" onClick={() => onFocusScreen(step.screen!)} title="Показать экран в галерее">
                        {step.screen}
                      </button>
                    </>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </article>
      ))}
    </div>
  );
}

function ScreensTab({
  spec,
  comments,
  focused,
  onOpenArtifact,
  onSelectFragment,
}: {
  spec: UiSpecView;
  comments: CommentView[];
  focused: string | null;
  onOpenArtifact: (path: string) => void;
  onSelectFragment: (artifact: string, anchorId: string | null) => void;
}) {
  if (spec.screens.length === 0) {
    return spec.scenarios.length > 0 ? <p className="muted">Экраны ещё не описаны.</p> : null;
  }
  return (
    <div className="stack">
      <div className="ui-gallery" data-testid="ui-gallery">
        {spec.screens.map((screen) => (
          <ScreenCard
            key={screen.id}
            screen={screen}
            devUrl={spec.dev_url}
            comments={comments.filter((comment) => comment.anchor.artifact === screen.path)}
            focused={focused === screen.id}
            onOpenArtifact={onOpenArtifact}
            onSelectFragment={onSelectFragment}
          />
        ))}
      </div>
      {spec.components.length > 0 ? (
        <p className="muted" data-testid="ui-components">
          Компоненты UIKit:{" "}
          {spec.components.map((use) => `${use.name} (${use.screens.join(", ")})`).join(" · ")}
        </p>
      ) : null}
    </div>
  );
}

function ScreenCard({
  screen,
  devUrl,
  comments,
  focused,
  onOpenArtifact,
  onSelectFragment,
}: {
  screen: UiScreen;
  devUrl: string | null;
  comments: CommentView[];
  focused: boolean;
  onOpenArtifact: (path: string) => void;
  onSelectFragment: (artifact: string, anchorId: string | null) => void;
}) {
  const href = previewHref(screen.preview_url, devUrl);
  const elementIds = new Set(screen.elements.map((element) => element.id));
  const live = comments.filter((comment) => comment.status !== "closed");
  const orphaned = live.filter(
    (comment) => comment.anchor_state === "detached" && (comment.anchor.anchor_id === null || !elementIds.has(comment.anchor.anchor_id)),
  );
  return (
    <article
      id={`screen-${screen.id}`}
      className={`screen-card${focused ? " screen-card--focused" : ""}`}
      data-testid={`screen-${screen.id}`}
      aria-label="Экран"
    >
      <div className="row">
        <h4 className="screen-card__title">{screen.title}</h4>
        <span className="mono muted">{screen.id}</span>
        {screen.route ? <span className="mono">{screen.route}</span> : <span className="muted">маршрут не задан</span>}
      </div>
      {screen.purpose ? <p className="screen-card__purpose">{screen.purpose}</p> : <p className="muted">Назначение не описано.</p>}

      <h5 className="screen-card__caption">Состояния</h5>
      <ul className="screen-states" data-testid={`states-${screen.id}`}>
        {UI_STATE_KINDS.map((kind) => {
          const state = screen.states.find((candidate) => candidate.kind === kind) ?? null;
          const described = state !== null && state.description !== null && state.description.trim().length > 0;
          return (
            <li key={kind} data-testid={`state-${screen.id}-${kind}`}>
              {described ? (
                <>
                  <StatusBadge label={UI_STATE_LABELS[kind]} tone="neutral" /> <span>{state.description}</span>
                </>
              ) : (
                <>
                  <StatusBadge label={`${UI_STATE_LABELS[kind]}: не описано`} tone="warning" />
                </>
              )}
            </li>
          );
        })}
      </ul>

      <h5 className="screen-card__caption">Элементы</h5>
      {screen.elements.length === 0 ? (
        <p className="muted">Элементы не описаны.</p>
      ) : (
        <ul className="screen-elements" data-testid={`elements-${screen.id}`}>
          {screen.elements.map((element) => {
            const own = live.filter((comment) => comment.anchor.anchor_id === element.id);
            const detached = own.some((comment) => comment.anchor_state === "detached");
            return (
              <li key={element.id} className="screen-element" data-testid={`element-${screen.id}-${element.id}`}>
                <span className="mono">{element.id}</span>
                {element.kind ? <span className="muted">{element.kind}</span> : null}
                {element.label ? <span>{element.label}</span> : null}
                {element.component ? <span className="badge badge--neutral">{element.component}</span> : null}
                {own.length > 0 ? <span className="muted">замечаний: {own.length}</span> : null}
                {detached ? <StatusBadge label="привязка потеряна" tone="danger" /> : null}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onSelectFragment(screen.path, element.id)}
                  data-testid={`comment-${screen.id}-${element.id}`}
                  title="Оставить замечание к элементу"
                >
                  Комментарий
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {orphaned.length > 0 ? (
        <p className="field__hint" data-testid={`orphaned-${screen.id}`}>
          <StatusBadge label="привязка потеряна" tone="danger" /> элемент исчез из экрана, замечание не переносится:{" "}
          {orphaned.map((comment) => `${comment.id} (${comment.anchor.anchor_id ?? "весь экран"})`).join(", ")}
        </p>
      ) : null}

      <div className="row">
        <button type="button" className="button" onClick={() => onOpenArtifact(screen.path)} data-testid={`open-screen-${screen.id}`}>
          Открыть документ
        </button>
        {href ? (
          <a href={href} target="_blank" rel="noreferrer" className="button" data-testid={`dev-link-${screen.id}`}>
            Открыть на dev
          </a>
        ) : (
          <span className="muted" data-testid={`dev-missing-${screen.id}`}>
            dev-окружение появится после доставки
          </span>
        )}
      </div>
    </article>
  );
}

function LinksTab({ spec, onFocusScreen }: { spec: UiSpecView; onFocusScreen: (screenId: string) => void }) {
  const titleOf = (id: string) => spec.screens.find((screen) => screen.id === id)?.title ?? null;
  if (spec.links.length === 0) {
    return spec.screens.length > 0 ? <p className="muted">Связей между экранами не описано.</p> : null;
  }
  return (
    <table className="table" data-testid="ui-links">
      <thead>
        <tr>
          <th>Откуда</th>
          <th>Куда</th>
          <th>Действие</th>
          <th>Условие</th>
        </tr>
      </thead>
      <tbody>
        {spec.links.map((link) => (
          <tr key={link.id} data-testid={`link-${link.id}`}>
            <td>
              <button type="button" className="anchor-chip mono" onClick={() => onFocusScreen(link.from_screen)}>
                {link.from_screen}
              </button>{" "}
              {titleOf(link.from_screen) ?? <span className="muted">экран не описан</span>}
            </td>
            <td>
              <button type="button" className="anchor-chip mono" onClick={() => onFocusScreen(link.to_screen)}>
                {link.to_screen}
              </button>{" "}
              {titleOf(link.to_screen) ?? <span className="muted">экран не описан</span>}
            </td>
            <td>{link.trigger ?? <span className="muted">—</span>}</td>
            <td>{link.condition ?? <span className="muted">—</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
