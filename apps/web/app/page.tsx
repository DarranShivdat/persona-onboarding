import { App } from "@/components/App";
import { isStateName } from "@/lib/session/fixtures";

// FE-001: mock session driver. `?state=<name>` replays a fixed spec state (qa:visual).
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const s = typeof sp.state === "string" ? sp.state : undefined;
  const initial = isStateName(s) ? s : "landing";
  return <App key={initial} initialState={initial} capture={sp.capture === "1"} />;
}
