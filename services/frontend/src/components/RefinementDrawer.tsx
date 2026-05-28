import { useState, useEffect, useRef } from "react";
import { Loader2, Send, Sparkles, CheckCheck, Mic2, FileText } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "./ui/sheet";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Separator } from "./ui/separator";
import { jobsApi } from "../lib/api";
import { toast } from "../hooks/useToast";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

type MergeType = "voice" | "application";

interface MergeState {
  type: MergeType;
  loading: boolean;
  current: string;       // existing text before merge
  proposed: string;      // editable proposed merged text
}

interface Props {
  open: boolean;
  onClose: () => void;
  reviewId: string;
  templateLabel: string;
  templateIdx: number;
  /** Current displayed answer (may be an unsaved draft). */
  currentAnswer: string;
  /** Current voice guidelines text (may be empty/null). */
  currentVoiceGuidelines: string | null;
  /** Current application template prompt text. */
  currentPrompt: string;
  /** Replace the answer field in the parent with a refined version. */
  onUseAnswer: (answer: string) => void;
  /** Save a fully-merged replacement to voice guidelines. */
  onUpdateVoiceGuidelines: (merged: string) => Promise<void>;
  /** Save a fully-merged replacement to the template prompt. */
  onUpdateApplicationPrompt: (merged: string) => Promise<void>;
}

// ─── Helper ───────────────────────────────────────────────────────────────────

function apiErrorMessage(err: any): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: any) => d.msg ?? JSON.stringify(d)).join("; ");
  if (err?.message) return err.message;
  return "Unexpected error — check the browser console for details.";
}

// ─── Component ────────────────────────────────────────────────────────────────

export function RefinementDrawer({
  open,
  onClose,
  reviewId,
  templateLabel,
  templateIdx,
  currentAnswer,
  currentVoiceGuidelines,
  currentPrompt,
  onUseAnswer,
  onUpdateVoiceGuidelines,
  onUpdateApplicationPrompt,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [mergeState, setMergeState] = useState<MergeState | null>(null);
  const [saving, setSaving] = useState(false);

  // Snapshot of the answer when the drawer opened
  const [snapshotAnswer, setSnapshotAnswer] = useState(currentAnswer);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Reset when drawer opens
  useEffect(() => {
    if (open) {
      setMessages([]);
      setInput("");
      setMergeState(null);
      setSnapshotAnswer(currentAnswer);
      setTimeout(() => textareaRef.current?.focus(), 300);
    }
  }, [open]); // snapshot once on open, intentionally ignoring currentAnswer changes

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const lastAssistantMessage =
    [...messages].reverse().find((m) => m.role === "assistant")?.content ?? null;
  const hasConversation = messages.length > 0;

  // ── Send ───────────────────────────────────────────────────────────────────

  async function sendMessage() {
    const text = input.trim();
    if (!text || sending) return;

    const newMessages: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(newMessages);
    setInput("");
    setSending(true);
    setMergeState(null); // collapse any open merge panel when chatting

    try {
      const res = await jobsApi.post(`/jobs/${reviewId}/refine`, {
        messages: newMessages,
        template_idx: templateIdx,
        current_answer: snapshotAnswer || null,
      });
      const reply = res.data.response as string;
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (err: any) {
      console.error("Refinement error:", err?.response ?? err);
      setMessages(messages); // roll back optimistic message
      toast({
        title: "Refinement failed",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  // ── Extract & merge ────────────────────────────────────────────────────────

  async function requestMerge(type: MergeType) {
    const currentContent =
      type === "voice" ? (currentVoiceGuidelines ?? "") : currentPrompt;

    setMergeState({ type, loading: true, current: currentContent, proposed: "" });

    try {
      const res = await jobsApi.post(`/jobs/${reviewId}/extract-changes`, {
        messages,
        change_type: type,
        current_content: currentContent,
        template_idx: templateIdx,
      });
      setMergeState({ type, loading: false, current: currentContent, proposed: res.data.proposed });
    } catch (err: any) {
      console.error("Extract-changes error:", err?.response ?? err);
      setMergeState(null);
      toast({
        title: "Extraction failed",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    }
  }

  async function confirmMerge() {
    if (!mergeState || !mergeState.proposed.trim()) return;
    setSaving(true);
    try {
      if (mergeState.type === "voice") {
        await onUpdateVoiceGuidelines(mergeState.proposed);
        toast({ title: "Voice guidelines updated" });
      } else {
        await onUpdateApplicationPrompt(mergeState.proposed);
        toast({ title: "Application prompt updated" });
      }
      setMergeState(null);
    } catch {
      toast({ title: "Failed to save", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────

  const mergeTypeLabel = mergeState?.type === "voice" ? "voice guidelines" : "application prompt";

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent side="right" className="w-full sm:max-w-xl flex flex-col p-0 gap-0">

        {/* Header */}
        <SheetHeader className="px-5 pt-5 pb-3 shrink-0">
          <SheetTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            Refine: {templateLabel}
          </SheetTitle>
          <SheetDescription>
            Chat to adjust tone, length, or content. Then use the buttons below to apply changes.
          </SheetDescription>
        </SheetHeader>

        <Separator />

        {/* Starting-from card */}
        <div className="px-5 py-3 shrink-0 bg-muted/40 border-b">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5">
            Starting from
          </p>
          {snapshotAnswer ? (
            <p className="text-sm leading-relaxed line-clamp-4 text-foreground/80">{snapshotAnswer}</p>
          ) : (
            <p className="text-sm text-muted-foreground italic">
              No draft yet — chat to create one from scratch.
            </p>
          )}
        </div>

        {/* Chat area */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0">
          {messages.length === 0 && (
            <div className="text-center text-sm text-muted-foreground py-8">
              <Sparkles className="h-6 w-6 mx-auto mb-2 opacity-40" />
              <p>Tell the AI what to change.</p>
              <p className="mt-1 text-xs opacity-70">
                Try: "Make it shorter", "Remove the m-dashes", "Emphasize my leadership background"
              </p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted border"
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {sending && (
            <div className="flex justify-start">
              <div className="bg-muted border rounded-xl px-4 py-2.5">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="shrink-0 border-t px-5 pt-3 pb-2 space-y-3">
          <div className="flex gap-2">
            <Textarea
              ref={textareaRef}
              rows={2}
              placeholder="Make it more concise… (Enter to send, Shift+Enter for newline)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={sending}
              className="resize-none text-sm"
            />
            <Button
              size="icon"
              onClick={sendMessage}
              disabled={!input.trim() || sending}
              className="shrink-0 self-end"
            >
              {sending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Send className="h-4 w-4" />}
            </Button>
          </div>

          {/* Action row */}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={!lastAssistantMessage}
              onClick={() => {
                if (lastAssistantMessage) {
                  onUseAnswer(lastAssistantMessage);
                  toast({ title: "Answer updated — don't forget to save." });
                  onClose();
                }
              }}
            >
              <CheckCheck className="h-3.5 w-3.5 mr-1" />
              Use this answer
            </Button>

            <Button
              size="sm"
              variant="outline"
              disabled={!hasConversation || sending}
              onClick={() => requestMerge("voice")}
            >
              <Mic2 className="h-3.5 w-3.5 mr-1" />
              Update voice guidelines
            </Button>

            <Button
              size="sm"
              variant="outline"
              disabled={!hasConversation || sending}
              onClick={() => requestMerge("application")}
            >
              <FileText className="h-3.5 w-3.5 mr-1" />
              Update application prompt
            </Button>
          </div>

          {/* Merge review panel */}
          {mergeState && (
            <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
              <div className="px-4 py-2.5 bg-muted/60 border-b flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Review changes — {mergeTypeLabel}
                </p>
                <button
                  className="text-muted-foreground hover:text-foreground text-xs"
                  onClick={() => setMergeState(null)}
                >
                  Dismiss
                </button>
              </div>

              <div className="p-4 space-y-3">
                {/* Before */}
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Before</p>
                  <div className="rounded border bg-muted/40 px-3 py-2 text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap max-h-28 overflow-y-auto">
                    {mergeState.current || <em>Empty</em>}
                  </div>
                </div>

                {/* After */}
                <div>
                  <p className="text-xs font-medium text-foreground mb-1">After (editable)</p>
                  {mergeState.loading ? (
                    <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Analysing conversation…
                    </div>
                  ) : (
                    <Textarea
                      rows={5}
                      className="text-xs resize-none"
                      value={mergeState.proposed}
                      onChange={(e) =>
                        setMergeState((s) => s ? { ...s, proposed: e.target.value } : s)
                      }
                    />
                  )}
                </div>

                {/* Confirm / Cancel */}
                {!mergeState.loading && (
                  <div className="flex gap-2 justify-end">
                    <Button size="sm" variant="ghost" onClick={() => setMergeState(null)}>
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      onClick={confirmMerge}
                      disabled={!mergeState.proposed.trim() || saving}
                    >
                      {saving && <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />}
                      Save to {mergeTypeLabel}
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="pb-1" /> {/* bottom breathing room */}
        </div>
      </SheetContent>
    </Sheet>
  );
}
