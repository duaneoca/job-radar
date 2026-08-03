// Titles and post-nominals are not part of a name. Without this,
// "Dr. Alan Turing PhD" picks first-and-last and yields "Dr_PhD".
const _HONORIFICS = new Set(["mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame", "rev"]);
const _SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "phd", "md", "mba", "msc", "bsc", "ba", "bs", "ma", "esq", "cpa", "pe", "rn", "pmp"]);

/** A résumé name as a filename stem: "Ada M. Lovelace" → "Ada_Lovelace".
 *
 *  Middle names drop out so the result stays predictable. Letters are kept as
 *  they are — accents and non-Latin scripts included — because this is the
 *  user's own name and every current filesystem handles them; only characters a
 *  path can't contain are removed, since the browser puts this straight into a
 *  save dialog without sanitising it. */
export function filenameFromName(raw: string): string {
  const cleaned = (raw || "")
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f/\\:*?"<>|]+/g, " ")   // path- and shell-hostile only
    .replace(/[.,]+/g, " ")                                // "Ada M. Lovelace" → drop the dot
    .trim();
  let parts = cleaned.split(/\s+/).filter(Boolean);

  // Strip a leading title and any trailing post-nominals, but never everything:
  // someone actually named e.g. "Miss" keeps their name over an empty result.
  while (parts.length > 1 && _HONORIFICS.has(parts[0].toLowerCase())) parts = parts.slice(1);
  while (parts.length > 1 && _SUFFIXES.has(parts[parts.length - 1].toLowerCase())) parts = parts.slice(0, -1);

  if (parts.length === 0) return "Resume";
  const picked = parts.length === 1 ? parts : [parts[0], parts[parts.length - 1]];
  return picked.join("_").replace(/^[-_]+|[-_]+$/g, "") || "Resume";
}
