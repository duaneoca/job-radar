// The order a résumé's sections are drawn in, and what each is called. ONE
// source for every renderer — the tailor page and both PDF templates. Each used
// to hardcode its own order and labels, which is why the screen and the PDF
// disagreed and neither matched the résumé.
//
// Two kinds of section:
//   * the five TYPED ones (summary, skills, experience, education, projects),
//     whose layout and honesty checks depend on the type;
//   * CUSTOM ones ("custom:<slug>") — anything else, under its own heading,
//     drawn as titled entries with bullets.
// Headings come from the résumé itself (`section_titles` / custom titles).
//
// Mirrors normalize_section_order on the backend, which is the authority; this
// only has to cope with responses that predate the fields.

export type BuiltinKey = "summary" | "skills" | "experience" | "education" | "projects";
export type SectionKey = BuiltinKey | `custom:${string}`;

export const SECTION_KEYS: BuiltinKey[] = ["summary", "skills", "experience", "education", "projects"];

// Used when a résumé's own order is unknown — the order the Classic PDF always used.
export const DEFAULT_SECTION_ORDER: BuiltinKey[] = ["summary", "skills", "projects", "experience", "education"];

export const SECTION_LABELS: Record<BuiltinKey, string> = {
  summary: "Summary",
  skills: "Skills",
  experience: "Experience",
  education: "Education",
  projects: "Projects",
};

export function isCustom(key: string): key is `custom:${string}` {
  return key.startsWith("custom:");
}

/** The résumé's custom sections, each with its index (diff paths use it). */
export function customSections(data: any): { key: `custom:${string}`; index: number; section: any }[] {
  return (data?.custom_sections ?? [])
    .map((section: any, index: number) => ({ key: section?.id, index, section }))
    .filter((s: any) => typeof s.key === "string" && isCustom(s.key));
}

/** The heading to print for a section: the résumé's own, else the standard label. */
export function sectionTitle(data: any, key: SectionKey): string {
  if (isCustom(key)) return customSections(data).find((s) => s.key === key)?.section?.title || "Section";
  return data?.section_titles?.[key] || SECTION_LABELS[key];
}

/** A complete section order for a structured résumé: its own, cleaned, else the default. */
export function sectionOrder(data: any): SectionKey[] {
  const custom = customSections(data).map((s) => s.key);
  const valid = new Set<string>([...SECTION_KEYS, ...custom]);
  const raw = Array.isArray(data?.section_order) ? data.section_order : [];
  const out: SectionKey[] = [];
  for (const k of raw) {
    if (typeof k === "string" && valid.has(k) && !out.includes(k as SectionKey)) out.push(k as SectionKey);
  }
  return [
    ...out,
    ...DEFAULT_SECTION_ORDER.filter((k) => !out.includes(k)),
    ...custom.filter((k) => !out.includes(k)),
  ];
}

/** Whether a section has anything to draw. Empty sections are skipped everywhere. */
export function hasSection(data: any, key: SectionKey): boolean {
  if (isCustom(key)) return (customSections(data).find((s) => s.key === key)?.section?.entries?.length ?? 0) > 0;
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
