import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Button } from "./ui/button";

/**
 * Proton-style link guard: shows the FULL destination URL as plain text and
 * makes the user confirm before a new tab is opened. The URL is rendered as
 * inert, escaped text (never as an <a>), so this dialog itself can't be a
 * vector. [C2]
 */
export function ExternalLinkConfirmDialog({
  url, onConfirm, onCancel,
}: {
  url: string | null;                         // non-null ⇒ open
  onConfirm: (dontAskAgain: boolean) => void;
  onCancel: () => void;
}) {
  const [dontAsk, setDontAsk] = useState(false);
  useEffect(() => { setDontAsk(false); }, [url]); // reset per prompt

  return (
    <Dialog open={!!url} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ExternalLink className="h-4 w-4" /> Link confirmation
          </DialogTitle>
        </DialogHeader>

        <p className="text-sm text-muted-foreground">
          You are about to open another browser tab and visit:
        </p>
        <div className="rounded-md border bg-muted/50 px-3 py-2 text-sm font-mono break-all select-all max-h-40 overflow-auto">
          {url}
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <label className="mr-auto flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={dontAsk}
              onChange={(e) => setDontAsk(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            Don't ask again
          </label>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button onClick={() => onConfirm(dontAsk)}>Confirm</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
