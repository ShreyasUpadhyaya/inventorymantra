"use client";

import { useEffect, useId, useState } from "react";

/** Mermaid diagram (process/lifecycle answers). Loaded only when needed: mermaid is large. */
export function MermaidFlow({ code }: { code: string }) {
  const id = `flow-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    import("mermaid")
      .then(async ({ default: mermaid }) => {
        const dark = document.documentElement.classList.contains("dark");
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral" });
        const { svg } = await mermaid.render(id, code);
        if (!cancelled) setSvg(svg);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [code, id]);

  if (failed) return <pre className="overflow-auto rounded-lg bg-muted p-3 text-xs">{code}</pre>;
  if (!svg) return <div className="h-40 animate-pulse rounded-lg bg-muted" />;
  // Mermaid output with securityLevel "strict" (no scripts, no HTML labels); the code is
  // built by the backend from fixed labels and counts (viz.po_flow_mermaid).
  return <div className="overflow-auto [&_svg]:mx-auto" dangerouslySetInnerHTML={{ __html: svg }} />;
}
