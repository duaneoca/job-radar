// Modern résumé template — navy sidebar (contact + skills) + main column. One-page
// design (a full-bleed sidebar can't span printed pages). CSS ported from the
// browser-validated prototype; vertical padding scales with --scale so autofit
// reaches a single page.
const CSS = `
.rt-modern{
  --ink:#23272e; --muted:#5c6573; --sidebar:#16314f; --sidebar-soft:#aebfd4;
  --accent:#2f6db0; --scale:1;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  color:var(--ink); line-height:1.42; font-size:calc(10.2pt * var(--scale));
  display:grid; grid-template-columns:2.55in 1fr; width:8.5in; background:#fff;
  -webkit-print-color-adjust:exact; print-color-adjust:exact;
}
.rt-modern .side{ background:var(--sidebar); color:#fff; padding:calc(.5in * var(--scale)) .34in; }
.rt-modern .side h1{ font-size:1.85em; line-height:1.05; margin:0 0 .1em; font-weight:700; }
.rt-modern .side .role{ color:var(--sidebar-soft); font-size:.88em; margin-bottom:1.1em; }
.rt-modern .side h3{ font-size:.84em; text-transform:uppercase; letter-spacing:1.6px; color:#fff;
  border-bottom:1px solid rgba(255,255,255,.25); padding-bottom:.2em; margin:1.2em 0 .55em; break-after:avoid; }
.rt-modern .side .row{ font-size:.86em; color:#e7edf5; margin:.25em 0; word-break:break-word; }
.rt-modern .side .skill{ margin:.5em 0; font-size:.86em; break-inside:avoid; }
.rt-modern .side .skill b{ display:block; color:#fff; font-size:.84em; }
.rt-modern .side .skill span{ color:var(--sidebar-soft); }
.rt-modern .main{ padding:calc(.5in * var(--scale)) .46in; }
.rt-modern .main h2{ font-size:1.08em; text-transform:uppercase; letter-spacing:1.3px; color:var(--sidebar);
  margin:0 0 .55em; padding-bottom:.2em; border-bottom:2px solid var(--accent); break-after:avoid; }
.rt-modern section{ margin-bottom:.8em; }
.rt-modern p{ margin:0; }
.rt-modern .entry{ margin-bottom:.7em; break-inside:avoid; }
.rt-modern .entry-head{ display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.rt-modern .org{ font-weight:700; font-size:1.04em; }
.rt-modern .dates{ color:var(--muted); font-size:.86em; white-space:nowrap; }
.rt-modern .titles{ font-style:italic; color:var(--muted); font-size:.88em; margin:.05em 0 .35em; }
.rt-modern .phase{ font-weight:600; color:var(--accent); font-size:.91em; margin:.5em 0 .15em; break-after:avoid; }
.rt-modern ul{ margin:.25em 0 .35em; padding-left:1.3em; }
.rt-modern li{ margin:.18em 0; break-inside:avoid; }
.rt-modern .note{ font-size:.84em; color:var(--muted); margin:.25em 0 0; } .rt-modern .note b{ color:var(--ink); }
@media print{ @page{ size:letter; margin:0; } }
`;

export function ModernTemplate({ data }: { data: any }) {
  const c = data?.contact ?? {};
  return (
    <div className="rt-modern" data-resume-doc data-fit="one-page">
      <style>{CSS}</style>
      <aside className="side">
        <h1>{c.name || "Your Name"}</h1>
        {(data?.experience?.[0]?.titles?.[0]) && <div className="role">{data.experience[0].titles[0]}</div>}
        <h3>Contact</h3>
        {[c.phone, c.location, c.email, ...(c.links ?? [])].filter(Boolean).map((r: string, i: number) => (
          <div className="row" key={i}>{r}</div>
        ))}
        {data?.skills?.length > 0 && (
          <>
            <h3>Skills</h3>
            {data.skills.map((g: any, i: number) => (
              <div className="skill" key={i}><b>{g.label}</b><span>{(g.items ?? []).join(" · ")}</span></div>
            ))}
          </>
        )}
      </aside>

      <main className="main">
        {data?.summary && (<section><h2>Summary</h2><p>{data.summary}</p></section>)}

        {data?.projects?.length > 0 && (
          <section>
            <h2>Projects</h2>
            {data.projects.map((pr: any, i: number) => (
              <div className="entry" key={i}>
                {pr.title && <div className="org" style={{ fontSize: "1em" }}>{pr.title}</div>}
                {pr.bullets?.length > 0 && <ul>{pr.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
              </div>
            ))}
          </section>
        )}

        {data?.experience?.length > 0 && (
          <section>
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
          </section>
        )}

        {data?.education?.length > 0 && (
          <section>
            <h2>Education</h2>
            {data.education.map((ed: any, i: number) => (
              <div className="entry-head" key={i}>
                <span className="org">{ed.degree}</span>
                {ed.school && <span className="dates">{ed.school}</span>}
              </div>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
