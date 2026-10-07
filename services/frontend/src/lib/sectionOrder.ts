// The order a résumé's sections are drawn in. ONE source for every renderer —
// the tailor page and both PDF templates. Each used to hardcode its own order,
// which is why the screen and the PDF disagreed and neither matched the résumé.
//
// Mirrors schemas.normalize_section_order on the backend, which is the authority;
// this only has to cope with responses that predate the field.

export type SectionKey = "summary" | "skills" | "experience" | "education" | "projects";

export const SECTION_KEYS: SectionKey[] = ["summary", "skills", "experience", "education", "projects"];

// Used when a résumé's own order is unknown — the order the Classic PDF always used.
export const DEFAULT_SECTION_ORDER: SectionKey[] = ["summary", "skills", "projects", "experience", "education"];

export const SECTION_LABELS: Record<SectionKey, string> = {
  summary: "Summary",
  skills: "Skills",
  experience: "Experience",
  education: "Education",
  projects: "Projects",
};

/** A complete section order for a structured résumé: its own, cleaned, else the default. */
export function sectionOrder(data: any): SectionKey[] {
  const raw = Array.isArray(data?.section_order) ? data.section_order : [];
  const out: SectionKey[] = [];
  for (const k of raw) {
    if ((SECTION_KEYS as string[]).includes(k) && !out.includes(k)) out.push(k);
  }
  if (out.length === 0) return [...DEFAULT_SECTION_ORDER];
  return [...out, ...DEFAULT_SECTION_ORDER.filter((k) => !out.includes(k))];
}

/** Whether a section has anything to draw. Empty sections are skipped everywhere. */
export function hasSection(data: any, key: SectionKey): boolean {
  return key === "summary" ? !!data?.summary : (data?.[key]?.length ?? 0) > 0;
}

/** `order` with `key` swapped past its next VISIBLE neighbour up (-1) or down (+1),
 *  so an arrow never appears to do nothing by stepping over an empty section.
 *  Unchanged at the ends. */
export function moveSection(order: SectionKey[], key: SectionKey, dir: -1 | 1,
                            visible: (k: SectionKey) => boolean = () => true): SectionKey[] {
  const i = order.indexOf(key);
  if (i < 0) return order;
  let j = i + dir;
  while (j >= 0 && j < order.length && !visible(order[j])) j += dir;
  if (j < 0 || j >= order.length) return order;
  const next = [...order];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}
