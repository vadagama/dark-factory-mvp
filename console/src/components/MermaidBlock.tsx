import { useEffect, useState } from "react";

type RenderResult = { kind: "rendered"; svg: string } | { kind: "failed"; message: string };

let sequence = 0;

/**
 * One ```mermaid fence of a design document (M3, T095). The `mermaid` package
 * is loaded lazily on first use (`import("mermaid")` → its own chunk), so the
 * workspace bundle does not carry the diagram engine. The source is always on
 * screen until the SVG replaces it, and stays on screen — with a visible
 * note — when the diagram cannot be rendered: never an empty box.
 */
export function MermaidBlock({ source }: { source: string }) {
  // The result is keyed by its source: a new source is "pending" until its own
  // render lands, without resetting state inside the effect.
  const [result, setResult] = useState<{ source: string; outcome: RenderResult } | null>(null);
  const outcome: RenderResult | { kind: "pending" } = result !== null && result.source === source ? result.outcome : { kind: "pending" };

  useEffect(() => {
    let cancelled = false;
    sequence += 1;
    const id = `mermaid-${sequence}`;
    import("mermaid")
      .then(async (module) => {
        const mermaid = module.default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "inherit" });
        const { svg } = await mermaid.render(id, source);
        if (!cancelled) {
          setResult({ source, outcome: { kind: "rendered", svg } });
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setResult({ source, outcome: { kind: "failed", message: message.split("\n")[0] } });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  if (outcome.kind === "rendered") {
    // The SVG comes from mermaid with securityLevel "strict" (labels are sanitised).
    return <div className="mermaid-block" data-testid="mermaid-rendered" dangerouslySetInnerHTML={{ __html: outcome.svg }} />;
  }
  return (
    <div className={`mermaid-block mermaid-block--${outcome.kind}`} data-testid={`mermaid-${outcome.kind}`}>
      <p className="field__hint mermaid-block__note" role="status">
        {outcome.kind === "failed" ? `Схема не отрисована: ${outcome.message}. Ниже — её исходник.` : "Схема отрисовывается… ниже — её исходник."}
      </p>
      <pre className="mermaid-block__source">{source}</pre>
    </div>
  );
}
