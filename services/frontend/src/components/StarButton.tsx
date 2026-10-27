import { Star } from "lucide-react";
import { cn } from "../lib/utils";

/** The user's own "come back to this" marker. Deliberately not the AI's
 *  recommendation, which is shown with sparkles instead. Shared by the jobs
 *  table, its side panel and the job detail page. */
export function StarButton({ starred, onToggle, className, iconClassName }: {
  starred: boolean; onToggle: () => void; className?: string; iconClassName?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={starred}
      aria-label={starred ? "Remove star" : "Star this job"}
      title={starred ? "Remove star" : "Star this job"}
      className={cn(
        "inline-flex items-center justify-center rounded p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        starred ? "text-amber-500" : "text-muted-foreground/40 hover:text-amber-500",
        className,
      )}
    >
      <Star className={cn("h-4 w-4", starred && "fill-current", iconClassName)} />
    </button>
  );
}
