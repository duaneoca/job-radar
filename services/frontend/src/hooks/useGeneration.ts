import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jobsApi } from "../lib/api";
import type { GenerationJob, GenerationKind, GenerationStatusOut } from "../lib/types";

/** True while a run is queued or running. */
export function isGenerating(job?: GenerationJob | null): boolean {
  return !!job && (job.status === "queued" || job.status === "running");
}

type Watch = { onDone?: () => void; onFailed?: (error: string) => void };

const KINDS: GenerationKind[] = ["research", "tailor"];

/**
 * Research and résumé tailoring run in the background: POST returns 202 with a
 * job row and the result lands on the job later. This polls the rows every 2 s
 * while a run is active and fires onDone / onFailed once when one finishes —
 * including a run started from another tab, or by an MCP client.
 */
export function useGeneration(
  reviewId: string | undefined,
  watch: Partial<Record<GenerationKind, Watch>> = {},
) {
  const query = useQuery<GenerationStatusOut>({
    queryKey: ["generation", reviewId],
    queryFn: () => jobsApi.get(`/jobs/${reviewId}/generation`).then((r) => r.data),
    enabled: !!reviewId,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (isGenerating(d.research) || isGenerating(d.tailor)) ? 2000 : false;
    },
  });

  // Fire once per transition out of "active". Keyed by job id + status so a
  // re-render with unchanged data is silent and a new run resets the gate.
  const seen = useRef<Partial<Record<GenerationKind, string>>>({});
  const watchRef = useRef(watch);
  watchRef.current = watch;
  useEffect(() => {
    const data = query.data;
    if (!data) return;
    for (const kind of KINDS) {
      const job = data[kind];
      if (!job) continue;
      const prev = seen.current[kind];
      const now = `${job.id}:${job.status}`;
      seen.current[kind] = now;
      if (prev === undefined || prev === now) continue;
      const [prevId, prevStatus] = prev.split(":");
      if (prevId !== job.id || !(prevStatus === "queued" || prevStatus === "running")) continue;
      if (job.status === "done") watchRef.current[kind]?.onDone?.();
      if (job.status === "failed") watchRef.current[kind]?.onFailed?.(job.error ?? "Generation failed.");
    }
  }, [query.data]);

  return query;
}

const PATH: Record<GenerationKind, string> = { research: "research", tailor: "tailor-resume" };

/** Start a run. On success the new row is placed in the generation query at
 *  once, so polling begins before the first refetch. */
export function useStartGeneration(
  reviewId: string | undefined,
  kind: GenerationKind,
  opts: { onError?: (err: unknown) => void } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => jobsApi.post<GenerationJob>(`/jobs/${reviewId}/${PATH[kind]}`).then((r) => r.data),
    onSuccess: (job) => {
      qc.setQueryData<GenerationStatusOut>(["generation", reviewId], (old) => ({
        research: null, tailor: null, ...old, [kind]: job,
      }));
    },
    onError: opts.onError,
  });
}
