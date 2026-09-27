import type { CSSProperties } from "react";
import type { RingState } from "@/lib/session/types";

/** The one memorable element: a CSS ring whose look is the call state (spec §1, §3.5). */
export function Ring({ state, level, className = "" }: { state?: RingState; level?: number; className?: string }) {
  const style = level !== undefined ? ({ "--lvl": level.toFixed(3) } as CSSProperties) : undefined;
  return <div className={`ring ${state ?? ""} ${className}`.trim()} style={style} aria-hidden="true" data-ring={state ?? "default"} />;
}
