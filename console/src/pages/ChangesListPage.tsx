import { useMemo, useState } from "react";
import { Link } from "react-router";
import { createApiClient } from "../api/client";
import { useAsync } from "../api/hooks";
import { EmptyState, ErrorState, LoadingState, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import { formatDateTime, formatProduct } from "../lib/format";
import { statusTone } from "../lib/statusTone";
import type { Change, RunStatus } from "../api/types";
import { IntakeForm } from "../components/IntakeForm";

export interface ChangesListModel {
  changes: Change[];
  /** Latest run status per change ("" when the change has no runs yet). */
  statusByChange: Map<string, RunStatus>;
}

/**
 * Derives the per-change status from the runs list (one extra request —
 * GET /runs carries the status; fetching a card per change would be N+1).
 * Runs arrive sorted by (created_at, id), so the last one per change wins.
 */
export function deriveStatusByChange(
  changes: Change[],
  runs: { change_id: string; status: RunStatus }[],
): Map<string, RunStatus> {
  const byChange = new Map<string, RunStatus>();
  for (const run of runs) {
    byChange.set(run.change_id, run.status);
  }
  const known = new Set(changes.map((change) => change.id));
  return new Map([...byChange].filter(([changeId]) => known.has(changeId)));
}

export function ChangesListPage() {
  const api = useMemo(() => createApiClient(), []);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  const state = useAsync<ChangesListModel>(async () => {
    // limit=200 is the API maximum; enough for the MVP list.
    const [changes, runs] = await Promise.all([api.listChanges({ limit: 200 }), api.listRuns({ limit: 200 })]);
    return { changes, statusByChange: deriveStatusByChange(changes, runs) };
  }, [reloadNonce]);

  return (
    <>
      <Section
        title="Изменения"
        description="Список изменений фабрики со статусом последнего прогона. Приёмка новых изменений — форма ниже (нужен токен changes:write)."
      >
        {state.loading ? <LoadingState /> : null}
        {state.error ? (
          <ErrorState message={state.error.detail} />
        ) : null}
        {!state.loading && !state.error && state.data ? (
          state.data.changes.length === 0 ? (
            <EmptyState label="Пока нет изменений — создайте первое через форму приёмки." />
          ) : (
            <table className="table" data-testid="changes-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Название</th>
                  <th>Продукт</th>
                  <th>Риск</th>
                  <th>Источник</th>
                  <th>Статус</th>
                  <th>Создано</th>
                </tr>
              </thead>
              <tbody>
                {state.data.changes.map((change) => {
                  const status = state.data?.statusByChange.get(change.id);
                  return (
                    <tr key={change.id}>
                      <td>
                        <Link to={`/changes/${encodeURIComponent(change.id)}`} className="mono">
                          {change.id}
                        </Link>
                      </td>
                      <td>
                        <Link to={`/changes/${encodeURIComponent(change.id)}`}>{change.title}</Link>
                      </td>
                      <td className="mono">{formatProduct(change.product.provider, change.product.slug)}</td>
                      <td>
                        <StatusBadge label={change.risk_class} tone="info" />
                      </td>
                      <td>{change.source}</td>
                      <td>
                        {status ? (
                          <StatusBadge label={status} tone={statusTone(status, "run")} />
                        ) : (
                          <span className="muted">нет запусков</span>
                        )}
                      </td>
                      <td className="muted">{formatDateTime(change.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        ) : null}
      </Section>

      <IntakeForm
        onCreated={() => setReloadNonce((value) => value + 1)}
        onTokenRequired={() => setDialogOpen(true)}
      />
      <TokenDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
}

