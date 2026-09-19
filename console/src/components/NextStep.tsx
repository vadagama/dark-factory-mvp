import type { Guidance } from "../api/types";
import { actorLabel, parseGuidanceApi } from "../lib/guidance";
import type { ConsoleAction } from "../lib/guidance";

export interface NextStepProps {
  guidance: Guidance;
  /** Performs a Console-known primary action (validate, navigate, focus the brief editor). */
  onPerform: (action: ConsoleAction) => void;
  /** True while the primary action is in flight (button disabled, label kept). */
  busy?: boolean;
}

/**
 * Renders a server-computed `Guidance` block (T074/T075, ADR-033): headline,
 * why, exactly ONE primary action, secondary actions, attributed blockers
 * and "after". The Console never derives a step of its own: when the
 * primary `api` is something the Console can perform, it is a button; when
 * it is not (run advance/withdraw — M4), the server's CLI command is shown
 * so the CLI and the Console name the same step. A disabled primary keeps
 * its label and shows the `reason` — no dead ends (ADR-033 p.4). No
 * percentage progress is ever shown.
 */
export function NextStep({ guidance, onPerform, busy = false }: NextStepProps) {
  const { primary } = guidance;
  const action = parseGuidanceApi(primary.api, guidance.subject);
  const performable = action !== null;

  return (
    <section className="card next-step" data-testid="next-step" aria-labelledby="next-step-headline">
      <p className="next-step__caption">
        Следующий шаг
        {guidance.phase ? (
          <span className="mono" data-testid="next-step-phase">
            {" "}
            · фаза: {guidance.phase}
          </span>
        ) : null}
      </p>
      <h3 id="next-step-headline" className="next-step__headline">
        {guidance.headline}
      </h3>
      <p className="next-step__why">{guidance.why}</p>

      <div className="next-step__primary">
        {!primary.enabled ? (
          <>
            <button type="button" className="button button--primary" disabled data-testid="next-step-primary">
              {primary.label}
            </button>
            {primary.reason ? (
              <span className="field__hint" data-testid="next-step-reason">
                {primary.reason}
              </span>
            ) : null}
          </>
        ) : performable ? (
          <button
            type="button"
            className="button button--primary"
            data-testid="next-step-primary"
            disabled={busy}
            onClick={() => onPerform(action)}
          >
            {busy ? `${primary.label}…` : primary.label}
          </button>
        ) : primary.cli ? (
          <div className="next-step__cli" data-testid="next-step-cli">
            <span className="next-step__cli-label">{primary.label}</span>
            <span className="field__hint">в CLI:</span>
            <code className="next-step__code mono">{primary.cli}</code>
          </div>
        ) : (
          <div className="next-step__cli" data-testid="next-step-cli">
            <span className="next-step__cli-label">{primary.label}</span>
            <span className="field__hint">
              {primary.api ? `выполняется через API: ${primary.api}` : "в Console пока не выполняется"}
            </span>
          </div>
        )}
      </div>

      {guidance.secondary.length > 0 ? (
        <ul className="next-step__secondary" data-testid="next-step-secondary">
          {guidance.secondary.map((item, index) => (
            <li key={`${item.label}-${index}`}>
              <span>{item.label}</span>
              {item.cli ? <code className="mono">{item.cli}</code> : null}
              {!item.cli && item.api ? <code className="mono">{item.api}</code> : null}
              {!item.enabled && item.reason ? <span className="muted"> — {item.reason}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {guidance.blockers.length > 0 ? (
        <div className="next-step__blockers">
          <h4>Что мешает</h4>
          <ul data-testid="next-step-blockers">
            {guidance.blockers.map((blocker, index) => (
              <li key={`${blocker.what}-${index}`}>
                <strong>{blocker.what}</strong> — снимает: {actorLabel(blocker.who)} — как: {blocker.how}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {guidance.after ? (
        <p className="muted next-step__after" data-testid="next-step-after">
          {guidance.after}
        </p>
      ) : null}
    </section>
  );
}
