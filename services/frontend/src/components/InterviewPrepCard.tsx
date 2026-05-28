import { useState } from "react";
import { Pencil, Trash2, ChevronDown, ChevronUp, Check, X, Lightbulb, BookOpen } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Badge } from "./ui/badge";
import type { InterviewQuestion } from "../lib/types";

const CATEGORY_STYLES: Record<string, string> = {
  "Behavioral":        "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300",
  "Technical":         "bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300",
  "Situational":       "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300",
  "Culture/Motivation":"bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300",
  "General":           "bg-muted text-muted-foreground",
};

interface Props {
  question: InterviewQuestion;
  isNew?: boolean;
  onSave: (updated: InterviewQuestion) => void;
  onDelete: () => void;
}

export function InterviewPrepCard({ question, isNew = false, onSave, onDelete }: Props) {
  const [editing, setEditing] = useState(isNew);
  const [notesOpen, setNotesOpen] = useState(false);
  const [draft, setDraft] = useState<InterviewQuestion>(question);

  function commitEdit() {
    if (!draft.question.trim()) return;
    onSave(draft);
    setEditing(false);
  }

  function cancelEdit() {
    if (isNew) {
      onDelete(); // removing a card that was never saved
    } else {
      setDraft(question);
      setEditing(false);
    }
  }

  const categoryClass = CATEGORY_STYLES[question.category] ?? CATEGORY_STYLES["General"];

  return (
    <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
      {/* Card header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b bg-muted/30">
        <Badge className={`text-xs font-medium shrink-0 border-0 ${categoryClass}`}>
          {question.category}
        </Badge>
        <div className="flex-1 min-w-0" />
        {!editing && (
          <>
            <Button variant="ghost" size="sm" className="h-7 px-2 text-muted-foreground" onClick={() => setEditing(true)}>
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost" size="sm"
              className="h-7 px-2 text-destructive hover:bg-destructive/10"
              onClick={onDelete}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </>
        )}
      </div>

      <div className="p-4 space-y-3">
        {editing ? (
          /* ── Edit mode ── */
          <>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Category</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.keys(CATEGORY_STYLES).filter(k => k !== "General").map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setDraft((d) => ({ ...d, category: cat as InterviewQuestion["category"] }))}
                    className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                      draft.category === cat
                        ? CATEGORY_STYLES[cat] + " border-transparent"
                        : "border-border text-muted-foreground hover:bg-muted/50"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Question</p>
              <Textarea
                rows={2}
                className="text-sm"
                value={draft.question}
                onChange={(e) => setDraft((d) => ({ ...d, question: e.target.value }))}
                autoFocus
              />
            </div>

            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Coaching note</p>
              <Textarea
                rows={4}
                className="text-sm"
                value={draft.coaching}
                onChange={(e) => setDraft((d) => ({ ...d, coaching: e.target.value }))}
              />
            </div>

            <div className="flex gap-2 justify-end pt-1">
              <Button size="sm" variant="ghost" onClick={cancelEdit}>
                <X className="h-3.5 w-3.5 mr-1" />
                Cancel
              </Button>
              <Button size="sm" onClick={commitEdit} disabled={!draft.question.trim()}>
                <Check className="h-3.5 w-3.5 mr-1" />
                Save
              </Button>
            </div>
          </>
        ) : (
          /* ── Read mode ── */
          <>
            {/* Question */}
            <p className="text-sm font-medium leading-snug">{question.question}</p>

            {/* Coaching */}
            {question.coaching && (
              <div className="rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200/60 dark:border-amber-800/40 px-3 py-2.5 space-y-1">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-400">
                  <Lightbulb className="h-3.5 w-3.5" />
                  Coaching
                </div>
                <p className="text-xs text-amber-900 dark:text-amber-200 leading-relaxed">
                  {question.coaching}
                </p>
                {question.story_refs && question.story_refs.length > 0 && question.story_refs[0] && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {question.story_refs.filter(Boolean).map((ref, i) => (
                      <span key={i} className="inline-flex items-center gap-1 text-xs bg-amber-100 dark:bg-amber-900/50 text-amber-800 dark:text-amber-300 rounded px-1.5 py-0.5">
                        <BookOpen className="h-3 w-3" />
                        {ref}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Notes toggle */}
            <div>
              <button
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setNotesOpen((o) => !o)}
              >
                {notesOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                {question.notes ? "My notes" : "Add notes"}
              </button>
              {notesOpen && (
                <Textarea
                  rows={3}
                  className="mt-2 text-sm"
                  placeholder="Your own notes for this question — reminders, talking points, things to avoid…"
                  value={draft.notes}
                  onChange={(e) => {
                    setDraft((d) => ({ ...d, notes: e.target.value }));
                    onSave({ ...question, notes: e.target.value });
                  }}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
