import { rmSync } from "node:fs";
import { join } from "node:path";
import { OUT, TARGET } from "./audit";

// Fresh result rows per run (only this target's; the other target's docs JSON is kept).
export default function globalSetup() {
  rmSync(join(OUT, "rows"), { recursive: true, force: true });
  console.log(`[audit] target=${TARGET}`);
}
