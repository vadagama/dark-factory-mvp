import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { ArtifactDiff, ArtifactDocumentView, ArtifactRevision, ArtifactWriteView } from "../api/types";
import { anchorForSelection, coercePropertyValue, isScalarProperty, shortRevision, splitFrontmatter } from "../lib/artifacts";
import { formatClock, formatDateTime } from "../lib/format";
import { Notice } from "./Section";

export type EditorMode = "document" | "markdown" | "reading";

const MODE_LABELS: Record<EditorMode, string> = {
  document: "Документ",
  markdown: "Markdown",
  reading: "Чтение",
};

type SaveState =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

type CommitState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "conflict"; detail: string }
  | { kind: "error"; detail: string };

export interface MarkdownEditorProps {
  changeId: string;
  /** The document at the revision the operator edits from; remount (key) on a new revision. */
  document: ArtifactDocumentView;
  api: ApiClient;
  hasToken: boolean;
  /** Fail-closed (ADR-021 p.4): a write without a token opens the token dialog. */
  onTokenRequired: () => void;
  /** After a commit: the parent re-reads the document and the discussion (staleness). */
  onSaved: (written: ArtifactWriteView) => void;
  /** After a 409 the operator asked to reload — never merge silently (ADR-035 p.3). */
  onReload: () => void;
  /** The operator selected text and pressed «Комментировать» (anchor from the selection, else whole document). */
  onComment?: (anchorId: string | null, selection: string) => void;
  /** Debounce of the draft autosave; tests shorten it. */
  autosaveDelayMs?: number;
  initialMode?: EditorMode;
}

/**
 * Markdown editor of one ChangeSet artifact (T090, ADR-035 p.4–p.6, p.8).
 *
 * The single source of truth in the component is the raw `content` string:
 * «Markdown» edits it in a textarea, «Документ» renders it (react-markdown +
 * GFM) and edits *only* the frontmatter through the properties panel — the
 * edits travel as `properties` in the PUT and are applied by the server;
 * «Чтение» renders it read-only. No mode ever rewrites `content`, so unknown
 * constructs survive verbatim and switching modes is a no-op on the text.
 *
 * Draft autosave (ADR-035 p.4) goes to `PUT /artifact-drafts/{path}` outside
 * git with `base_revision` = the loaded revision, ~1.5 s after the last
 * keystroke; the save state is always visible. «Сохранить в git» is the
 * explicit commit (`PUT /artifacts/{path}`); a 409 shows the server detail
 * and offers a reload — nothing is overwritten. Versions and the diff between
 * two revisions come from `artifact-versions` / `artifact-diff`.
 */
export function MarkdownEditor({
  changeId,
  document: doc,
  api,
  hasToken,
  onTokenRequired,
  onSaved,
  onReload,
  onComment,
  autosaveDelayMs = 1500,
  initialMode = "document",
}: MarkdownEditorProps) {
  const [mode, setMode] = useState<EditorMode>(initialMode);
  const [content, setContent] = useState(doc.content);
  const [propertyEdits, setPropertyEdits] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [commitState, setCommitState] = useState<CommitState>({ kind: "idle" });
  const [commitMessage, setCommitMessage] = useState("");
  const [draftOffer, setDraftOffer] = useState(doc.draft !== null && doc.draft.content !== doc.content);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ text: string; anchor: string | null } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [versions, setVersions] = useState<ArtifactRevision[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [fromRevision, setFromRevision] = useState("");
  const [toRevision, setToRevision] = useState("");
  const [diff, setDiff] = useState<ArtifactDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  // The content the server draft already holds: autosave fires only on a real change.
  const lastSavedRef = useRef<string>(doc.content);

  const dirty = content !== doc.content;
  const hasPropertyEdits = Object.keys(propertyEdits).length > 0;
  const { body } = useMemo(() => splitFrontmatter(content), [content]);
  const values = doc.properties?.values ?? {};
  const protectedKeys = doc.properties?.protected ?? [];

  // Autosave: one timer, re-armed on every keystroke; nothing runs without a token.
  useEffect(() => {
    if (content === lastSavedRef.current || !hasToken) {
      return;
    }
    setSaveState({ kind: "pending" });
    const timer = setTimeout(() => {
      setSaveState({ kind: "saving" });
      api
        .putArtifactDraft(changeId, doc.path, { content, base_revision: doc.revision })
        .then((draft) => {
          lastSavedRef.current = content;
          setSaveState({ kind: "saved", at: Date.parse(draft.updated_at) || Date.now() });
        })
        .catch((cause: unknown) => {
          setSaveState({ kind: "error", message: cause instanceof ApiError ? cause.detail : String(cause) });
        });
    }, autosaveDelayMs);
    return () => clearTimeout(timer);
  }, [content, hasToken, api, changeId, doc.path, doc.revision, autosaveDelayMs]);

  const continueDraft = () => {
    if (doc.draft) {
      lastSavedRef.current = doc.draft.content;
      setContent(doc.draft.content);
      setSaveState({ kind: "saved", at: Date.parse(doc.draft.updated_at) || Date.now() });
      setMode("markdown");
    }
    setDraftOffer(false);
  };

  const discardDraft = async () => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setDraftError(null);
    try {
      await api.deleteArtifactDraft(changeId, doc.path);
      setDraftOffer(false);
    } catch (cause) {
      setDraftError(cause instanceof ApiError ? cause.detail : String(cause));
    }
  };

  const commit = async () => {
    if (!hasToken) {
      onTokenRequired();
      return;
    }
    setCommitState({ kind: "busy" });
    const properties = hasPropertyEdits
      ? Object.fromEntries(
          Object.entries(propertyEdits).map(([key, raw]) => [key, coercePropertyValue(values[key], raw)]),
        )
      : null;
    try {
      const written = await api.putArtifact(changeId, doc.path, {
        content,
        base_revision: doc.revision,
        message: commitMessage.trim() || null,
        properties,
      });
      setCommitState({ kind: "idle" });
      onSaved(written);
    } catch (cause) {
      if (cause instanceof ApiError && cause.isStateRevisionConflict) {
        setCommitState({ kind: "conflict", detail: cause.detail });
        return;
      }
      setCommitState({ kind: "error", detail: cause instanceof ApiError ? cause.detail : String(cause) });
    }
  };

  const openHistory = async () => {
    setHistoryOpen((open) => !open);
    if (versions !== null) {
      return;
    }
    try {
      const list = await api.getArtifactVersions(changeId, doc.path);
      setVersions(list);
      setToRevision(list[0]?.revision ?? doc.revision);
      setFromRevision(list[1]?.revision ?? list[0]?.revision ?? doc.revision);
    } catch (cause) {
      setVersionsError(cause instanceof ApiError ? cause.detail : String(cause));
    }
  };

  const showDiff = async () => {
    setDiffError(null);
    setDiff(null);
    try {
      setDiff(await api.getArtifactDiff(changeId, doc.path, { from_revision: fromRevision, to_revision: toRevision }));
    } catch (cause) {
      setDiffError(cause instanceof ApiError ? cause.detail : String(cause));
    }
  };

  const captureSelection = () => {
    const text = window.getSelection?.()?.toString() ?? "";
    if (text.trim().length === 0) {
      setSelection(null);
      return;
    }
    setSelection({ text, anchor: anchorForSelection(text, doc.anchors) });
  };

  const rendered = (
    <div className="editor__rendered" data-testid="editor-rendered" onMouseUp={captureSelection}>
      <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
    </div>
  );

  return (
    <div className="editor" data-testid="markdown-editor">
      <div className="editor__toolbar">
        <div className="editor__modes" role="group" aria-label="Режим редактора">
          {(Object.keys(MODE_LABELS) as EditorMode[]).map((candidate) => (
            <button
              key={candidate}
              type="button"
              className={`button${mode === candidate ? " button--primary" : ""}`}
              aria-pressed={mode === candidate}
              data-testid={`editor-mode-${candidate}`}
              onClick={() => setMode(candidate)}
            >
              {MODE_LABELS[candidate]}
            </button>
          ))}
        </div>
        <span className="muted mono" title={doc.revision}>
          ревизия {shortRevision(doc.revision)}
        </span>
        <span className="editor__save-state" data-testid="editor-save-state" role="status">
          {saveStateLabel(saveState, dirty, hasToken)}
        </span>
        <button type="button" className="button" onClick={() => void openHistory()} data-testid="editor-history-toggle">
          История версий
        </button>
      </div>

      {draftOffer && doc.draft ? (
        <div className="editor__draft" data-testid="editor-draft-banner">
          <span>
            Есть несохранённый черновик от {formatDateTime(doc.draft.updated_at)} ({doc.draft.saved_by})
            {doc.draft.stale ? " — основан на прежней ревизии" : ""}.
          </span>
          <button type="button" className="button button--primary" onClick={continueDraft}>
            Продолжить черновик
          </button>
          <button type="button" className="button" onClick={() => void discardDraft()}>
            Отбросить
          </button>
          {draftError ? <span className="field__hint">{draftError}</span> : null}
        </div>
      ) : null}

      {doc.frontmatter_error ? (
        <Notice tone="warning">
          Свойства не распознаны: {doc.frontmatter_error}. Исходник целиком доступен в режиме Markdown.
        </Notice>
      ) : null}

      {mode === "document" ? (
        <div className="editor__document">
          {doc.properties ? (
            <section className="editor__properties" data-testid="editor-properties" aria-label="Свойства документа">
              <h4>Свойства документа</h4>
              <dl className="kv">
                {Object.entries(values).map(([key, value]) => {
                  const isProtected = protectedKeys.includes(key);
                  const editable = !isProtected && isScalarProperty(value);
                  const id = `prop-${key}`;
                  return [
                    <dt key={`${key}-dt`}>
                      <label htmlFor={id}>{key}</label>
                      {isProtected ? <span className="badge badge--muted editor__lock">защищено</span> : null}
                    </dt>,
                    <dd key={`${key}-dd`}>
                      {editable ? (
                        typeof value === "boolean" ? (
                          <select
                            id={id}
                            value={propertyEdits[key] ?? String(value)}
                            onChange={(event) => setPropertyEdits({ ...propertyEdits, [key]: event.target.value })}
                          >
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : (
                          <input
                            id={id}
                            type="text"
                            value={propertyEdits[key] ?? String(value)}
                            onChange={(event) => setPropertyEdits({ ...propertyEdits, [key]: event.target.value })}
                          />
                        )
                      ) : (
                        <span className="mono" id={id} data-protected={isProtected || undefined}>
                          {isScalarProperty(value) ? String(value) : JSON.stringify(value)}
                        </span>
                      )}
                    </dd>,
                  ];
                })}
              </dl>
              <p className="field__hint">
                Системные идентификаторы защищены (ADR-035 п.5); полный YAML — в режиме Markdown.
              </p>
            </section>
          ) : (
            <p className="muted" data-testid="editor-no-properties">
              У документа нет frontmatter-свойств.
            </p>
          )}
          {rendered}
        </div>
      ) : null}

      {mode === "markdown" ? (
        <textarea
          className="editor__textarea"
          data-testid="editor-textarea"
          aria-label="Исходник Markdown"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          spellCheck={false}
        />
      ) : null}

      {mode === "reading" ? rendered : null}

      {selection && mode !== "markdown" ? (
        <div className="editor__selection" data-testid="editor-selection">
          <span className="muted">
            Выделено: «{selection.text.length > 80 ? `${selection.text.slice(0, 80)}…` : selection.text}» · якорь:{" "}
            <strong className="mono">{selection.anchor ?? "весь документ"}</strong>
          </span>
          {onComment ? (
            <button
              type="button"
              className="button"
              data-testid="editor-comment-selection"
              onClick={() => {
                onComment(selection.anchor, selection.text);
                setSelection(null);
              }}
            >
              Комментировать
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="editor__commit" data-testid="editor-commit">
        <input
          type="text"
          aria-label="Сообщение коммита (необязательно)"
          placeholder="Сообщение коммита (необязательно)"
          value={commitMessage}
          onChange={(event) => setCommitMessage(event.target.value)}
        />
        <button
          type="button"
          className="button button--primary"
          data-testid="editor-save-git"
          disabled={commitState.kind === "busy" || (!dirty && !hasPropertyEdits)}
          onClick={() => void commit()}
        >
          {commitState.kind === "busy" ? "Сохраняю в git…" : "Сохранить в git"}
        </button>
        {!hasToken ? <span className="field__hint">запись требует токен оператора</span> : null}
        {commitState.kind === "conflict" ? (
          <div className="notice notice--error editor__conflict" role="alert" data-testid="editor-conflict">
            Конфликт: {commitState.detail}
            <button type="button" className="button" onClick={onReload} data-testid="editor-reload">
              Перезагрузить документ
            </button>
          </div>
        ) : null}
        {commitState.kind === "error" ? <Notice tone="error">Не сохранено: {commitState.detail}</Notice> : null}
      </div>

      {historyOpen ? (
        <section className="editor__history" data-testid="editor-history" aria-label="История версий">
          <h4>История версий</h4>
          {versionsError ? <Notice tone="error">{versionsError}</Notice> : null}
          {versions === null && !versionsError ? <p className="muted">Загрузка…</p> : null}
          {versions !== null && versions.length === 0 ? <p className="muted">Ревизий нет.</p> : null}
          {versions !== null && versions.length > 0 ? (
            <>
              <table className="table">
                <thead>
                  <tr>
                    <th>Ревизия</th>
                    <th>Сообщение</th>
                    <th>Автор</th>
                    <th>Когда</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version.revision}>
                      <td className="mono" title={version.revision}>
                        {shortRevision(version.revision)}
                        {version.revision === doc.revision ? <span className="muted"> (открыта)</span> : null}
                      </td>
                      <td>{version.message || <span className="muted">—</span>}</td>
                      <td>{version.author ?? <span className="muted">—</span>}</td>
                      <td className="muted">{formatDateTime(version.authored_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="form-actions">
                <label className="field">
                  <span className="field__label">От ревизии</span>
                  <select value={fromRevision} onChange={(event) => setFromRevision(event.target.value)}>
                    {versions.map((version) => (
                      <option key={version.revision} value={version.revision}>
                        {shortRevision(version.revision)} — {version.message}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="field__label">К ревизии</span>
                  <select value={toRevision} onChange={(event) => setToRevision(event.target.value)}>
                    {versions.map((version) => (
                      <option key={version.revision} value={version.revision}>
                        {shortRevision(version.revision)} — {version.message}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="button" onClick={() => void showDiff()} data-testid="editor-show-diff">
                  Показать diff
                </button>
              </div>
              {diffError ? <Notice tone="error">{diffError}</Notice> : null}
              {diff ? (
                <div data-testid="editor-diff">
                  <p className="muted">
                    +{diff.added} / −{diff.removed}
                  </p>
                  {diff.unified ? (
                    <pre className="diff">{diff.unified}</pre>
                  ) : (
                    <p className="muted">Ревизии не различаются.</p>
                  )}
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function saveStateLabel(state: SaveState, dirty: boolean, hasToken: boolean): string {
  switch (state.kind) {
    case "saving":
      return "Сохраняю…";
    case "saved":
      return `Черновик сохранён ${formatClock(state.at)}`;
    case "error":
      return `Не сохранено: ${state.message}`;
    case "pending":
      return "Не сохранено";
    case "idle":
      if (dirty && !hasToken) {
        return "Не сохранено: нужен токен оператора";
      }
      return dirty ? "Не сохранено" : "Без изменений";
  }
}
