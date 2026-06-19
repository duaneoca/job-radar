// Classic résumé template — single column, navy accents. Most ATS-safe, multi-page
// friendly. CSS ported from the browser-validated prototype (@page margins on every
// page, keep-together rules, em-based spacing driven by --scale for autofit).
const CSS = `
.rt-classic{
  --ink:#1a1a1a; --muted:#555; --accent:#1f3a5f; --rule:#c9d3df; --scale:1;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  color:var(--ink); line-height:1.4; font-size:calc(10.3pt * var(--scale));
  width:7.5in; margin:0 auto; overflow-wrap:break-word; background:#fff;
}
.rt-classic header{ text-align:center; border-bottom:2px solid var(--accent); padding-bottom:.6em; margin-bottom:.9em; }
.rt-classic h1{ font-size:2.2em; letter-spacing:.5px; margin:0 0 .15em; color:var(--accent); font-weight:700; }
.rt-classic .contact{ font-size:.9em; color:var(--muted); }
.rt-classic h2{ font-size:1.02em; text-transform:uppercase; letter-spacing:1.4px; color:var(--accent);
  border-bottom:1px solid var(--rule); padding-bottom:.2em; margin:1.1em 0 .55em; break-after:avoid; page-break-after:avoid; }
.rt-classic p{ margin:0; }
.rt-classic .skill{ margin:.24em 0; } .rt-classic .skill b{ color:var(--accent); }
.rt-classic .entry{ margin-bottom:.7em; break-inside:avoid; page-break-inside:avoid; }
.rt-classic .entry-head{ display:flex; justify-content:space-between; align-items:baseline; gap:10px; }
.rt-classic .org{ font-weight:700; font-size:1.06em; }
.rt-classic .dates{ color:var(--muted); font-size:.9em; white-space:nowrap; }
.rt-classic .titles{ font-style:italic; color:var(--muted); font-size:.92em; margin:.05em 0 .35em; }
.rt-classic .phase{ font-weight:600; color:var(--accent); font-size:.95em; margin:.5em 0 .15em; break-after:avoid; }
.rt-classic ul{ margin:.25em 0 .35em; padding-left:1.4em; }
.rt-classic li{ margin:.18em 0; break-inside:avoid; }
.rt-classic .lead{ margin:0 0 .2em; break-after:avoid; }
.rt-classic .note{ font-size:.86em; color:var(--muted); margin:.25em 0 0; } .rt-classic .note b{ color:var(--ink); }
@media print{ .rt-classic{ width:auto; } @page{ size:letter; margin:.5in; } }
`;

function contactLine(c: any): string {
  return [c?.phone, c?.location, c?.email, ...(c?.links ?? [])]
    .filter(Boolean).join(" · ");
}

export function ClassicTemplate({ data }: { data: any }) {
  const c = data?.contact ?? {};
  return (
    <div className="rt-classic" data-resume-doc>
      <style>{CSS}</style>
      <header>
        <h1>{c.name || "Your Name"}</h1>
        <div className="contact">{contactLine(c)}</div>
      </header>

      {data?.summary && (<><h2>Summary</h2><p>{data.summary}</p></>)}

      {data?.skills?.length > 0 && (
        <>
          <h2>Skills</h2>
          {data.skills.map((g: any, i: number) => (
            <div className="skill" key={i}><b>{g.label}:</b> {(g.items ?? []).join(" · ")}</div>
          ))}
        </>
      )}

      {data?.projects?.length > 0 && (
        <>
          <h2>Projects</h2>
          {data.projects.map((pr: any, i: number) => (
            <div className="entry" key={i}>
              {pr.title && <div className="org" style={{ fontSize: "1em" }}>{pr.title}</div>}
              {pr.bullets?.length > 0 && <ul>{pr.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
            </div>
          ))}
        </>
      )}

      {data?.experience?.length > 0 && (
        <>
          <h2>Experience</h2>
          {data.experience.map((e: any, i: number) => (
            <div className="entry" key={i}>
              <div className="entry-head">
                <span className="org">{e.company}</span>
                {(e.start || e.end) && <span className="dates">{e.start}{e.start && e.end ? " – " : ""}{e.end}</span>}
              </div>
              {e.titles?.length > 0 && <div className="titles">{e.titles.join(" → ")}</div>}
              {e.bullets?.length > 0 && <ul>{e.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
              {e.phases?.map((p: any, k: number) => (
                <div key={k}>
                  {(p.label || p.start) && <div className="phase">{p.label}{p.start ? ` (${p.start}${p.end ? ` – ${p.end}` : ""})` : ""}</div>}
                  {p.bullets?.length > 0 && <ul>{p.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
                </div>
              ))}
              {e.notable?.length > 0 && <p className="note"><b>Notable:</b> {e.notable.join(", ")}</p>}
            </div>
          ))}
        </>
      )}

      {data?.education?.length > 0 && (
        <>
          <h2>Education</h2>
          {data.education.map((ed: any, i: number) => (
            <div className="entry-head" key={i}>
              <span className="org">{ed.degree}</span>
              {ed.school && <span className="dates">{ed.school}</span>}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
