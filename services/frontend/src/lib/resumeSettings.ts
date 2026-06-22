// User-tunable print "knobs" for the résumé templates (Phase 4). Stored per-browser
// in localStorage for now; server-side profile defaults + per-résumé overrides are a
// follow-up. The values are applied as CSS variables (--rt-*) onto the template root.

export type Density = "compact" | "normal" | "roomy";

export interface ResumeSettings {
  fontPt: number; // base font in points (autofit's --scale still multiplies this)
  density: Density; // line-height + vertical rhythm
  marginIn: number; // page margin in inches (Classic; Modern is full-bleed)
  accent: string | null; // accent colour, or null = keep the template's own default(s)
}

export const DEFAULT_SETTINGS: ResumeSettings = {
  fontPt: 10,
  density: "normal",
  marginIn: 0.5,
  accent: null,
};

// Bounds for the sliders.
export const FONT_MIN = 8;
export const FONT_MAX = 12;
export const MARGIN_MIN = 0.35;
export const MARGIN_MAX = 0.85;

// Density preset → line-height + a multiplier applied to section/entry/bullet margins.
export const DENSITY: Record<Density, { line: number; gap: number }> = {
  compact: { line: 1.25, gap: 0.8 },
  normal: { line: 1.4, gap: 1.0 },
  roomy: { line: 1.6, gap: 1.25 },
};

// Accent swatches. `null` = "Template default" (preserves Modern's two-tone).
export const ACCENTS: { label: string; value: string | null }[] = [
  { label: "Template default", value: null },
  { label: "Navy", value: "#1f3a5f" },
  { label: "Charcoal", value: "#2b2f36" },
  { label: "Teal", value: "#1f5f5b" },
  { label: "Burgundy", value: "#6e2433" },
  { label: "Plum", value: "#4a2a5a" },
];

const KEY = "jr-resume-settings";

export function loadSettings(): ResumeSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    // ignore malformed / unavailable storage — fall through to defaults
  }
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(s: ResumeSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // ignore storage failures (private mode, quota) — settings just won't persist
  }
}
