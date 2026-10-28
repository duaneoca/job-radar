import { Search } from "lucide-react";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";
import { Label } from "./ui/label";
import type { PendingReplace } from "../lib/companySite";

/** Shown on Add Job after "Find on company site": offers to update that job with
 *  what's being imported instead of adding a second copy (lib/companySite.ts). */
export function ReplaceJobBanner({ pending, enabled, onChange, onDismiss }: {
  pending: PendingReplace;
  enabled: boolean;
  onChange: (on: boolean) => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 space-y-3">
      <div className="flex items-start gap-2">
        <Search className="h-4 w-4 text-primary shrink-0 mt-0.5" />
        <div className="text-sm space-y-1">
          <p className="font-medium">
            Is this <span className="text-foreground">{pending.title}</span> at {pending.company}?
          </p>
          <p className="text-muted-foreground">
            You went looking for it on the company's site. Saving can update that job instead of
            adding a new one: it gets this page's link and posting, it's scored again, and if it
            had expired it comes back to your list.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Switch id="replace-job" checked={enabled} onCheckedChange={onChange} />
          <Label htmlFor="replace-job" className="text-sm">Update that job</Label>
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>Not that job</Button>
      </div>
    </div>
  );
}
