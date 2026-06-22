import { Check, RotateCcw, Star } from "lucide-react";
import { cn } from "../../lib/utils";
import {
  ACCENTS,
  DEFAULT_SETTINGS,
  DENSITY,
  FONT_MAX,
  FONT_MIN,
  MARGIN_MAX,
  MARGIN_MIN,
  type Density,
  type ResumeSettings,
} from "../../lib/resumeSettings";

// Print-formatting controls ("knobs"). Live: each change drives the CSS vars that the
// PagedPreview re-renders with. `showMargin` is false for full-bleed templates (Modern).
export function ResumeKnobs({
  settings,
  onChange,
  showMargin,
  onSetDefault,
  savedDefault,
}: {
  settings: ResumeSettings;
  onChange: (next: ResumeSettings) => void;
  showMargin: boolean;
  onSetDefault?: () => void;
  savedDefault?: boolean;
}) {
  const set = (patch: Partial<ResumeSettings>) => onChange({ ...settings, ...patch });

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
      <label className="flex items-center gap-2">
        <span className="text-muted-foreground">Font</span>
        <input
          type="range" min={FONT_MIN} max={FONT_MAX} step={0.1} value={settings.fontPt}
          onChange={(e) => set({ fontPt: parseFloat(e.target.value) })}
          className="w-24 accent-[#1f3a5f]"
        />
        <span className="tabular-nums w-10 text-muted-foreground">{settings.fontPt.toFixed(1)}pt</span>
      </label>

      <div className="flex items-center gap-1">
        <span className="text-muted-foreground mr-1">Density</span>
        {(Object.keys(DENSITY) as Density[]).map((d) => (
          <button
            key={d} type="button" onClick={() => set({ density: d })}
            className={cn(
              "rounded px-2 py-0.5 border capitalize transition-colors",
              settings.density === d ? "bg-accent text-accent-foreground border-accent" : "hover:bg-accent/50",
            )}
          >
            {d}
          </button>
        ))}
      </div>

      {showMargin && (
        <label className="flex items-center gap-2">
          <span className="text-muted-foreground">Margins</span>
          <input
            type="range" min={MARGIN_MIN} max={MARGIN_MAX} step={0.05} value={settings.marginIn}
            onChange={(e) => set({ marginIn: parseFloat(e.target.value) })}
            className="w-20 accent-[#1f3a5f]"
          />
          <span className="tabular-nums w-9 text-muted-foreground">{settings.marginIn.toFixed(2)}&quot;</span>
        </label>
      )}

      <div className="flex items-center gap-1.5">
        <span className="text-muted-foreground mr-1">Accent</span>
        {ACCENTS.map((a) => (
          <button
            key={a.label} type="button" title={a.label} onClick={() => set({ accent: a.value })}
            className={cn(
              "h-5 w-5 rounded-full border flex items-center justify-center text-[9px] text-muted-foreground",
              settings.accent === a.value ? "ring-2 ring-offset-1 ring-foreground" : "",
            )}
            style={{ background: a.value ?? "transparent" }}
          >
            {a.value === null && "A"}
          </button>
        ))}
      </div>

      <button
        type="button" onClick={() => onChange({ ...DEFAULT_SETTINGS, template: settings.template })}
        className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
        title="Reset formatting to defaults (keeps the chosen template)"
      >
        <RotateCcw className="h-3.5 w-3.5" /> Reset
      </button>

      {onSetDefault && (
        <button
          type="button" onClick={onSetDefault}
          className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
          title="Use these settings as the default for every résumé"
        >
          {savedDefault ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Star className="h-3.5 w-3.5" />}
          {savedDefault ? "Saved" : "Set as default"}
        </button>
      )}
    </div>
  );
}
