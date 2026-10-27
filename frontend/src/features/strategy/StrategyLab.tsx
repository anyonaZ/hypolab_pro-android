import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";

// Strategy Lab (replaces Knowledge Graph + Strategy Atlas): semantic + AI analysis of the SELECTED
// datafields. For each field (single) or field pairing (multi) it proposes an economically-sound
// hypothesis and the exact operators to build it, then composes a prompt constrained to ONLY those
// fields + operators and auto-loads it into Alpha Lab ② Generate. Not a twin-finder.
interface Item { fields: string[]; idea: string; mechanism: string; operators: string[]; sign: string; horizon: string; information_arrival?:string; regime?:string; transformation?:string; neutralization_rationale?:string; falsification?:string; baseline?:string; alternative_explanation?:string; }

export function StrategyLab() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();

  const [mode, setMode] = usePersistentState<"single" | "multi">("slab:mode", "single");
  const [n, setN] = usePersistentState("slab:n", 8);
  const [maxOps, setMaxOps] = usePersistentState("slab:maxops", 4);
  const [items, setItems] = usePersistentState<Item[]>("slab:items", []);
  const [ops, setOps] = usePersistentState<string[]>("slab:ops", []);
  const [drop, setDrop] = usePersistentState<Record<number, boolean>>("slab:drop", {});   // excluded items
  const [busy, setBusy] = useState(false);

  const selFields = R.fields.filter((f) => R.selFields.includes(f.id));
  const dsById = Object.fromEntries(R.datasets.map((d) => [d.id, d]));
  const fieldCategory: Record<string, string> = {};
  selFields.forEach((f) => { const c = f.dataset_id && dsById[f.dataset_id]?.category_id; if (c) fieldCategory[f.id] = c; });
  const nCats = new Set(Object.values(fieldCategory)).size;
  const chosen = items.filter((_, i) => !drop[i]);
  const chosenOps = [...new Set(chosen.flatMap((it) => it.operators))].sort();

  async function analyze() {
    if (!selFields.length) return toast("Select datafields in the Data Explorer first.", "warn");
    setBusy(true); setItems([]); setOps([]); setDrop({});
    const start = await api.post<any>("/strategy/field-intel", {
      fields: selFields.map((f) => ({ id: f.id, type: f.type, description: f.description, dataset_id: f.dataset_id })),
      categories: fieldCategory, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      universe: R.ctx.universe, mode, n, max_operators: maxOps,
      transfer_context:R.transferStudy?.contexts||[],
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/strategy/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    const r = s.result || {};
    setItems(r.items || []); setOps(r.operators || []);
    toast(`${(r.items || []).length} hypotheses across ${new Set((r.items || []).flatMap((x: Item) => x.fields)).size} field(s) via ${r.provider}.`);
  }

  // Compose the chosen hypotheses into a prompt constrained to ONLY their fields + operators, then
  // auto-load it into Alpha Lab ② Generate (like the research auto-flow).
  function compose() {
    if (!chosen.length) return toast("Keep at least one hypothesis to compose.", "warn");
    const usedFields = [...new Set(chosen.flatMap((it) => it.fields))];
    const body = (R.transferStudy?`TRANSFER STUDY: preserve economic meaning and expected sign across ${R.transferStudy.contexts.map(x=>`${x.region} D${x.delay} ${x.universe}`).join(", ")}. Use only the verified common fields.\n\n`:"")+
      "Build WorldQuant FastExpr alphas that TEST each hypothesis below. Use ONLY the operators listed " +
      "and ONLY the provided datafields — do not introduce any other operator or field.\n\n" +
      chosen.map((it, i) =>
        `${i + 1}. ${it.idea} — ${it.mechanism} (sign ${it.sign || "?"}, ~${it.horizon || "?"}d). ` +
        `Fields: ${it.fields.join(", ")}. Operators: ${it.operators.join(", ")}.`).join("\n") +
      (chosenOps.length ? `\n\nALLOWED OPERATORS (use ONLY these): ${chosenOps.join(", ")}.` : "") +
      `\nFields in play: ${usedFields.join(", ")}.`;
    localStorage.setItem("ace2:gen:prompt", JSON.stringify(body));
    localStorage.setItem("ace2:gen:allowedops", JSON.stringify(chosenOps));
    localStorage.setItem("ace2:gen:reqds", JSON.stringify([]));
    localStorage.setItem("ace2:gen:mode", JSON.stringify(mode));
    localStorage.setItem("ace2:lab:stage", JSON.stringify("generate"));
    toast(`Composed a constrained prompt (${chosen.length} hypotheses · ${chosenOps.length} operators) → Alpha Lab.`, "ok");
    nav("/generate");
  }
  async function saveAll(){let saved=0;for(const it of chosen){const d=await api.post<any>("/research/hypotheses",{hypothesis:it,context:{...R.ctx,fields:it.fields,operators:it.operators,source:"strategy-lab"}});if(!d.error&&!d.duplicate)saved++}toast(`Saved ${saved} new reusable hypotheses; duplicates were retained once.`,`ok`)}

  return (
    <div className="dx-split strategy-page route-workspace" style={{ flex: 1, minHeight: 0 }}>
      {/* setup */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Strategy Lab</b>
          <span className="mut">{selFields.length} field(s) · {nCats} cat · {R.ctx.region} D{R.ctx.delay}</span></div>
        <div className="mut" style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 10 }}>
          AI Analysis of your selected datafields — a hypothesis and the exact operators for each,
          composed into a prompt that uses <b>only</b> those fields and operators.
        </div>

        <div className="seg" style={{ marginBottom: 10 }}>
          <button className={"seg-btn" + (mode === "single" ? " on" : "")} onClick={() => setMode("single")}>Single field</button>
          <button className={"seg-btn" + (mode === "multi" ? " on" : "")} onClick={() => setMode("multi")}>Multi field</button>
        </div>
        {mode === "multi" && (
          <div className="mut" style={{ fontSize: 11, marginBottom: 8 }}>
            {nCats >= 2 ? "Combines two different categories per hypothesis." : "Combines 2+ fields from your selection per hypothesis."}
          </div>)}

        <div style={{ display: "flex", gap: 8, alignItems: "end" }}>
          <label className="fld" style={{ width: 92 }}><span>Hypotheses</span>
            <NumberInput min={1} max={50} fallback={8} value={n} onChange={setN} /></label>
          <label className="fld" style={{ width: 84 }}><span>Max Ops</span>
            <NumberInput min={1} max={12} fallback={4} value={maxOps} onChange={setMaxOps} /></label>
          <button className="btn" style={{ flex: 1 }} onClick={analyze} disabled={busy}>
            {busy ? <><span className="spin" /> Analysing…</> : "✦ Analyse Fields"}</button>
        </div>

        {ops.length ? (
          <div style={{ marginTop: 12 }}>
            <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 4 }}>
              Suggested operators ({chosenOps.length} in play)</div>
            <div className="dx-filters wrap">
              {ops.map((o) => <span key={o} className={"chip" + (chosenOps.includes(o) ? "" : " mut")} style={{ fontSize: 11 }}><code>{o}</code></span>)}
            </div>
          </div>) : null}
      </div>

      {/* results */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Hypotheses</b>
          {items.length ? <span className="mut">{chosen.length}/{items.length} kept</span> : null}
          {items.length ? <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={saveAll}>Save kept</button> : null}
          {items.length ? <button className="btn sm" onClick={compose}
            title="Compose the kept hypotheses into a constrained prompt and open Alpha Lab">Compose → Alpha Lab →</button> : null}
        </div>
        <div className="panel-scroll">
          {!items.length ? <div className="empty">Select datafields in the Data Explorer, then Analyse Fields. Each hypothesis is grounded in what a field measures, names the operators to build it, and composes into a prompt constrained to only your fields + those operators.</div> :
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {items.map((it, i) => (
                <div key={i} className="panel" style={{ padding: "10px 12px", boxShadow: "none", opacity: drop[i] ? 0.5 : 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <input type="checkbox" checked={!drop[i]} onChange={() => setDrop((d) => ({ ...d, [i]: !d[i] ? true : false }))}
                      style={{ width: "auto" }} title="Include this hypothesis" />
                    <b>{it.idea || `Hypothesis ${i + 1}`}</b>
                    {it.sign ? <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>sign {it.sign}</span> : null}
                    {it.horizon ? <span className="badge" style={{ background: "var(--faint)", color: "var(--mut)" }}>~{it.horizon}d</span> : null}
                  </div>
                  <div className="mut" style={{ fontSize: 12, marginTop: 4 }}>{it.mechanism}</div>
                  <div className="dx-filters wrap" style={{ marginTop: 6, gap: 4 }}>
                    {it.fields.map((f) => <span key={f} className="chip" style={{ fontSize: 11 }}><code>{f}</code></span>)}
                  </div>
                  {it.operators.length ? <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>
                    ops: {it.operators.join(", ")}</div> : null}
                  <div className="hypothesis-protocol"><span><b>Arrival</b>{it.information_arrival||"not specified"}</span><span><b>Regime</b>{it.regime||"all / test"}</span><span><b>Transform</b>{it.transformation||"baseline first"}</span><span><b>Neutralize</b>{it.neutralization_rationale||"test explicitly"}</span><span><b>Falsify</b>{it.falsification||"required before generation"}</span><span><b>Baseline</b>{it.baseline||"simplest valid expression"}</span><span><b>Alternative</b>{it.alternative_explanation||"not specified"}</span></div>
                </div>))}
            </div>}
        </div>
      </div>
    </div>
  );
}
