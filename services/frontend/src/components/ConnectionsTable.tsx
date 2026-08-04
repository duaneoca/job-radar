import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronsUpDown, ExternalLink, Loader2, Search } from "lucide-react";
import { Input } from "./ui/input";
import { connectionsApi } from "../lib/api";
import { cn } from "../lib/utils";
import type { LinkedInConnection } from "../lib/types";

type SortKey = "name" | "company" | "position" | "connected";
type Direction = "asc" | "desc";

/** Sort value for a row. Strings are lower-cased so ordering isn't case-driven;
 *  the date column sorts on the parsed ISO value, never the display text — as
 *  text, "07 Apr 2026" sorts before "08 Feb 2019". */
function sortValue(c: LinkedInConnection, key: SortKey): string {
  switch (key) {
    case "name":
      return `${c.last_name ?? ""} ${c.first_name ?? ""}`.trim().toLowerCase();
    case "company":
      return (c.company ?? "").toLowerCase();
    case "position":
      return (c.position ?? "").toLowerCase();
    case "connected":
      return c.connected_at ?? "";
  }
}

const COLUMNS: { key: SortKey; label: string; className?: string }[] = [
  { key: "name",      label: "Name" },
  { key: "company",   label: "Company" },
  { key: "position",  label: "Position",  className: "hidden md:table-cell" },
  { key: "connected", label: "Connected", className: "hidden sm:table-cell w-28" },
];

function SortHeader({
  column, sort, direction, onSort,
}: {
  column: (typeof COLUMNS)[number];
  sort: SortKey;
  direction: Direction;
  onSort: (key: SortKey) => void;
}) {
  const active = sort === column.key;
  const Icon = !active ? ChevronsUpDown : direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={cn("text-left px-3 py-2.5 font-medium", column.className)}>
      <button
        type="button"
        onClick={() => onSort(column.key)}
        aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
        className={cn(
          "inline-flex items-center gap-1 hover:text-foreground transition-colors",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {column.label}
        <Icon className={cn("h-3.5 w-3.5", active ? "opacity-100" : "opacity-40")} />
      </button>
    </th>
  );
}

export function ConnectionsTable() {
  const { data: connections = [], isLoading } = useQuery<LinkedInConnection[]>({
    queryKey: ["connections"],
    queryFn: () => connectionsApi.get("/connections").then((r) => r.data),
  });

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("name");
  const [direction, setDirection] = useState<Direction>("asc");

  function toggleSort(key: SortKey) {
    if (key === sort) setDirection((d) => (d === "asc" ? "desc" : "asc"));
    else { setSort(key); setDirection("asc"); }
  }

  const rows = useMemo(() => {
    const query = search.trim().toLowerCase();
    // Search spans every field, including the two that aren't sortable — you
    // may well remember someone's email or their profile slug and nothing else.
    const matched = query
      ? connections.filter((c) =>
          [c.first_name, c.last_name, c.company, c.position, c.email,
           c.connected_on, c.profile_url]
            .some((v) => v && v.toLowerCase().includes(query)))
      : connections;

    return [...matched].sort((a, b) => {
      const av = sortValue(a, sort);
      const bv = sortValue(b, sort);
      // Blanks always sink, in both directions. Flipping the sort to surface a
      // wall of empty cells is never what anyone wanted — position is missing
      // on some rows and connection dates don't always parse.
      if (!av && !bv) return 0;
      if (!av) return 1;
      if (!bv) return -1;
      const cmp = av.localeCompare(bv);
      return direction === "asc" ? cmp : -cmp;
    });
  }, [connections, search, sort, direction]);

  if (isLoading) {
    return (
      <div className="flex justify-center py-16 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (connections.length === 0) {
    return (
      <div className="rounded-lg border bg-muted/20 px-4 py-10 text-center text-sm text-muted-foreground">
        <p>No connections imported yet.</p>
        <p className="mt-1">
          Upload your LinkedIn <code className="text-xs bg-muted px-1 rounded">Connections.csv</code> under{" "}
          <Link to="/profile?tab=connections" className="underline font-medium">Profile → Connections</Link>.
        </p>
      </div>
    );
  }

  const noLinks = connections.every((c) => !c.profile_url);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search name, company, position, email…"
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="text-sm text-muted-foreground">
          {rows.length === connections.length
            ? `${connections.length} connections`
            : `${rows.length} of ${connections.length}`}
        </span>
      </div>

      {noLinks && (
        // The URL column was discarded by the importer until v1.13. Existing
        // rows have no link and nothing can reconstruct one, so say so rather
        // than showing a column of blanks with no explanation.
        <p className="text-xs text-muted-foreground">
          Profile links are missing because they weren't captured when you last imported.{" "}
          <Link to="/profile?tab=connections" className="underline">Re-upload your CSV</Link> to add them.
        </p>
      )}

      <div className="rounded-lg border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              {COLUMNS.map((col) => (
                <SortHeader key={col.key} column={col} sort={sort}
                            direction={direction} onSort={toggleSort} />
              ))}
              {/* Not sortable: a link is a yes/no, and email is blank on most
                  rows because LinkedIn only exports it with permission. */}
              <th className="text-center px-3 py-2.5 font-medium w-10">Link</th>
              <th className="text-left px-3 py-2.5 font-medium hidden lg:table-cell">Email</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-12 text-muted-foreground">
                  No connections match "{search.trim()}".
                </td>
              </tr>
            ) : (
              rows.map((c) => (
                <tr key={c.id} className="border-b last:border-0 hover:bg-muted/30">
                  <td className="px-3 py-2 font-medium">
                    {[c.first_name, c.last_name].filter(Boolean).join(" ") || "—"}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{c.company || "—"}</td>
                  <td className="px-3 py-2 text-muted-foreground hidden md:table-cell">
                    {c.position || "—"}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground hidden sm:table-cell whitespace-nowrap">
                    {c.connected_on || "—"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {c.profile_url ? (
                      <a
                        href={c.profile_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`Open ${[c.first_name, c.last_name].filter(Boolean).join(" ")} on LinkedIn`}
                        className="text-muted-foreground hover:text-foreground inline-block"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    ) : (
                      <span className="text-muted-foreground/40">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground hidden lg:table-cell">
                    {c.email || "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
