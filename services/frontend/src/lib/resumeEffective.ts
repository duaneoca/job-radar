// The "effective" résumé from a tailor state: tailored content with every REJECTED
// change reverted. This is what gets rendered/printed. Pending + accepted changes
// keep the tailored text.
//
// The server now computes this and returns it as `state.effective`, because a
// rejected re-ordering has to restore the original ORDER while independently
// accepted rewordings survive — which the per-path revert below cannot express.
// The local version stays as a fallback for responses cached before that shipped.
import type { TailorState } from "./types";

// Navigate a path like "experience/0/bullets/2" to read the value.
function getPath(obj: any, path: string): any {
  return path.split("/").reduce((acc, tok) => {
    if (acc == null) return undefined;
    const key = /^\d+$/.test(tok) ? Number(tok) : tok;
    return acc[key];
  }, obj);
}

// Set the value at a path, navigating into objects/arrays (numeric token = index).
function setPath(obj: any, path: string, value: any): void {
  const toks = path.split("/");
  let cur = obj;
  for (let i = 0; i < toks.length - 1; i++) {
    const key = /^\d+$/.test(toks[i]) ? Number(toks[i]) : toks[i];
    if (cur[key] == null) return; // structure missing — skip defensively
    cur = cur[key];
  }
  const last = toks[toks.length - 1];
  cur[/^\d+$/.test(last) ? Number(last) : last] = value;
}

export function effectiveResume(
  state: Pick<TailorState, "original" | "tailored" | "changes"> & { effective?: Record<string, any> },
): any {
  if (state.effective) return state.effective;
  const eff = structuredClone(state.tailored);
  for (const c of state.changes) {
    if (c.decision === "rejected") {
      setPath(eff, c.path, getPath(state.original, c.path));
    }
  }
  return eff;
}
