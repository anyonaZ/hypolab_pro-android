import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";

// Alpha Lab — the unified research→generation workspace (Research Lab merged in). One screen:
// ① IDEATE turns a goal / research paper into structured, validated hypotheses; a click folds
// them into ② GENERATE's instruction, which produces validated, deduped alpha expressions. No
// hopping between screens. All setup + results persist across navigation and reloads; running
// jobs reconnect.

interface Hyp {
  idea: string; mechanism: string; sign: string; horizon: string; expression: string;
  confidence?: number; expression_valid?: boolean; expression_issues?: string[];
  falsification?: string; field_requirements?: string;
}
interface SavedHyp { id: number; hypothesis: Hyp; context?: any; use_count?: number; }

const RESEARCH_MODES: { id: string; label: string; hint: string }[] = [
  { id: "single", label: "Single Field", hint: "One datafield per idea" },
  { id: "multi_single_dataset", label: "Multi · One Dataset", hint: "Combine fields from the same dataset" },
  { id: "multi_two_categories", label: "Multi · Two Categories", hint: "Combine fields from at most two categories" },
];

export function Generation() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();

  // which half of the setup panel is showing, and which output tab is active
  const [stage, setStage] = usePersistentState<"ideate" | "generate">("lab:stage", "generate");
  const [otab, setOtab] = usePersistentState<"hyps" | "expr">("lab:otab", "expr");

  // shared knobs (mean the same thing to both halves)
  const [econ, setEcon] = usePersistentState("gen:econ", true);
  const [maxOps, setMaxOps] = usePersistentState("gen:maxops", 4);

  // ── ideate (research) state ────────────────────────────────────────────────
  const [providers, setProviders] = useState<{ available: string[]; used: string[] }>({ available: [], used: [] });
  const [goal, setGoal] = usePersistentState("research:goal", "");
  const [goalBusy, setGoalBusy] = useState(false);
  const [rMode, setRMode] = usePersistentState("research:mode", "single");
  const [hypN, setHypN] = usePersistentState("research:n", 6);
  const [paperText, setPaperText] = usePersistentState("research:paperText", "");
  const [paperName, setPaperName] = usePersistentState("research:paperName", "");
  const [pages, setPages] = usePersistentState("research:pages", "");
  const [community, setCommunity] = usePersistentState("research:community", false);
  const [hyps, setHyps] = usePersistentState<Hyp[]>("research:hyps", []);
  const [rJob, setRJob] = usePersistentState<string>("research:job", "");
  const [rBusy, setRBusy] = useState(false);
  const [composing, setComposing] = useState(false);   // auto research→complex-prompt chain
  const [sortConf, setSortConf] = usePersistentState("research:sortconf", false);
  const [general, setGeneral] = usePersistentState("research:general", false);
  const [savedHyps, setSavedHyps] = useState<SavedHyp[]>([]);
  const [alphaId, setAlphaId] = usePersistentState("research:source-alpha", "");
  const [sourceCode, setSourceCode] = useState("");
  const [alphaBusy, setAlphaBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // ── generate state ─────────────────────────────────────────────────────────
  const [mode, setMode] = usePersistentState<"single" | "multi">("gen:mode", "single");
  const [prompt, setPrompt] = usePersistentState("gen:prompt", "");
  const [n, setN] = usePersistentState("gen:n", 12);
  const [valid, setValid] = usePersistentState<string[]>("gen:valid", []);
  const [rejected, setRejected] = usePersistentState<{ expr: string; issues: string[] }[]>("gen:rejected", []);
  const [meta, setMeta] = usePersistentState<{ provider?: string; used?: string[]; fields?: string[] }>("gen:meta", {});
  const [seen, setSeen] = usePersistentState<string[]>("gen:seen", []);
  const [gJob, setGJob] = usePersistentState<string>("gen:job", "");
  const [busy, setBusy] = useState(false);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [expl, setExpl] = useState<Record<number, string>>({});
  const [explBusy, setExplBusy] = useState<number | null>(null);
  const [libPrompts, setLibPrompts] = useState<{ id: number; name: string; body: string; datasets?: string[] }[]>([]);
  const [reqDatasets, setReqDatasets] = usePersistentState<string[]>("gen:reqds", []);
  // Operator constraint handed over by Strategy Lab: generation may use ONLY these operators.
  const [allowedOps, setAllowedOps] = usePersistentState<string[]>("gen:allowedops", []);

  useEffect(() => {
    api.get<any>("/generate/prompts?scope=generate").then((d) => setLibPrompts(d.prompts || []));
    api.get<any>("/research/providers").then(setProviders).catch(() => {});
    refreshSaved();
  }, []);
  // Reconnect running jobs on return / reload.
  useEffect(() => { if (gJob) { setBusy(true); pollGen(gJob); } if (rJob) { setRBusy(true); pollResearch(rJob); } /* eslint-disable-next-line */ }, []);

  // ── shared derived context ─────────────────────────────────────────────────
  const selFields = R.fields.filter((f) => R.selFields.includes(f.id));
  const dsById = Object.fromEntries(R.datasets.map((d) => [d.id, d]));
  const fieldCategory: Record<string, string> = {};
  selFields.forEach((f) => { const c = f.dataset_id && dsById[f.dataset_id]?.category_id; if (c) fieldCategory[f.id] = c; });
  const dsNames = R.datasets.filter((d) => R.selDatasets.includes(d.id)).map((d) => d.name || d.id);
  const nCats = new Set(Object.values(fieldCategory)).size;
  const category = [...new Set(Object.values(fieldCategory))].join(", ");
  const fetchedIds = R.datasets.map((d) => d.id.toLowerCase());
  const missingDs = reqDatasets.filter((d) => !fetchedIds.includes(d.toLowerCase()));
  const fieldPayload = () => selFields.map((f) => ({ id: f.id, type: f.type, description: f.description, dataset_id: f.dataset_id }));
  const transferNote=R.transferStudy?`TRANSFER STUDY: this idea must preserve its economic meaning and expected sign across ${R.transferStudy.contexts.map(x=>`${x.region} D${x.delay} ${x.universe}`).join(", ")}. Use only the verified common fields.`:"";

  async function refreshSaved() {
    const d = await api.get<any>("/research/hypotheses");
    if (!d.error) setSavedHyps(d.hypotheses || []);
  }
  async function saveHyp(h: Hyp) {
    const d = await api.post<any>("/research/hypotheses", { hypothesis: h, context: {
      region: R.ctx.region, delay: R.ctx.delay, datasets: dsNames, field_ids: selFields.map((f) => f.id) } });
    if (d.error) return toastErr(d.error);
    await refreshSaved(); toast(d.duplicate ? "That hypothesis is already saved." : `Saved hypothesis #${d.id}.`, "ok");
  }
  async function saveAllHyps() {
    if (!hyps.length) return;
    let added = 0;
    for (const h of hyps) {
      const d = await api.post<any>("/research/hypotheses", { hypothesis: h, context: {
        region: R.ctx.region, delay: R.ctx.delay, datasets: dsNames, field_ids: selFields.map((f) => f.id) } });
      if (!d.error && !d.duplicate) added++;
    }
    await refreshSaved(); toast(`${added} new hypotheses saved; duplicates were kept once.`, "ok");
  }
  async function fetchAlpha() {
    if (!alphaId.trim()) return toast("Enter a BRAIN alpha ID.", "warn");
    setAlphaBusy(true); const d = await api.get<any>(`/research/alpha/${encodeURIComponent(alphaId.trim())}`); setAlphaBusy(false);
    if (d.error) return toastErr(d.error); setSourceCode(d.expression || "");
    toast(d.expression ? "Fetched the source alpha code." : "Alpha found, but no code was returned.", d.expression ? "ok" : "warn");
  }
  async function replicateAlpha() {
    if (!selFields.length) return toast("Select target datafields first.", "warn");
    if (!alphaId.trim()) return toast("Enter a BRAIN alpha ID.", "warn");
    setAlphaBusy(true);
    const start = await api.post<any>("/research/replicate", { alpha_id: alphaId.trim(), fields: fieldPayload(),
      categories: fieldCategory, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument, goal:[goal,transferNote].filter(Boolean).join("\n"), n: hypN });
    if (start.error || !start.job_id) { setAlphaBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (;;) { s = await api.get(`/research/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setAlphaBusy(false); if (s.status !== "done") return toastErr(s.error || s.status);
    const list = s.result?.hypotheses || []; setSourceCode(s.result?.source?.expression || sourceCode); setHyps(list); setOtab("hyps");
    toast(`${list.length} transferable hypotheses curated from the source alpha.`, "ok");
  }

  // ── ideate (research) actions ──────────────────────────────────────────────
  async function uploadPaper(file: File) {
    const fd = new FormData(); fd.append("file", file); fd.append("pages", pages);
    toast("Reading PDF…");
    const d = await api.upload<{ text: string; chars: number; name: string; complete: boolean }>("/research/paper", fd);
    if (d.error) return toastErr(d.error);
    setPaperText(d.text); setPaperName(d.name);
    toast(`Loaded ${d.complete ? "the complete paper" : "the selected pages"} · ${d.chars.toLocaleString()} characters from ${d.name}.`, "ok");
  }
  function removePaper() { setPaperText(""); setPaperName(""); setPages(""); toast("Removed the uploaded paper."); }

  async function autoGoal() {
    if (!providers.available.length) return toast("No AI provider set up. Add a key in Settings.", "warn");
    if (!selFields.length && !paperText.trim()) return toast("Select some datafields (or upload a paper) first.", "warn");
    setGoalBusy(true);
    const d = await api.post<any>("/research/goal", {
      region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      dataset_names: dsNames, fields: selFields.map((f) => ({ id: f.id, type: f.type, description: f.description })),
      categories: fieldCategory, paper_text: paperText,
    });
    setGoalBusy(false);
    if (d.error) return toastErr(d.error);
    if (d.goal) { setGoal(d.goal); toast("Goal drafted — edit as you like.", "ok"); }
    else toast("Couldn't generate a goal.", "warn");
  }

  async function pollResearch(id: string) {
    let s: any = {};
    for (; ;) { s = await api.get(`/research/jobs/${id}`); if (s.error && s.status === undefined) break; if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setRBusy(false); setRJob("");
    if (s.status !== "done") { if (s.error) toastErr(s.error); return; }
    const r = s.result || {};
    const list: Hyp[] = r.hypotheses || [];
    setHyps(list);
    setOtab("hyps");
    toast(`${list.length} testable hypotheses via ${r.provider} — composing prompt…`);
    // Automatic: research → complex prompt → sitting in ② Generate, ready to run. No manual fold.
    if (list.length) await composeFromHyps(list);
  }

  // Compose the hypotheses into a rich, self-contained MASTER prompt (LLM) and drop it into the
  // Generate instruction — no navigation. Used automatically after research, and by the manual
  // "Recompose" button.
  async function composeFromHyps(list: Hyp[]) {
    const usable = list.filter((h) => h.idea);
    if (!usable.length) return;
    const body = (transferNote?transferNote+"\n\n":"")+
      "Build alphas that TEST each of the researched hypotheses below. Focus on the HYPOTHESIS " +
      "(the economic mechanism) — any example expression only ILLUSTRATES the idea and is NOT a field " +
      "restriction. Use whichever of the selected datafields best express each hypothesis, drawing on " +
      "ALL of them across the batch where they fit.\n\n" +
      usable.map((h, i) => `${i + 1}. ${h.idea} — ${h.mechanism} (sign ${h.sign || "?"}, ~${h.horizon || "?"}d).`
        + (h.expression && h.expression_valid !== false ? ` Illustrative form: ${h.expression}` : "")).join("\n");
    setComposing(true);
    let master = body;
    try {
      const start = await api.post<any>("/generate/rewrite", {
        prompt: body, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
        universe: R.ctx.universe, dataset_names: dsNames, fields: fieldPayload(),
        categories: fieldCategory, max_operators: maxOps, n,
      });
      if (start.job_id) {
        let s: any = {};
        for (; ;) { s = await api.get(`/generate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
        if (s.status === "done" && s.result?.prompt) master = s.result.prompt;
      }
    } catch { /* fall back to the plain composed body */ }
    setComposing(false);
    setPrompt(master);
    setReqDatasets([]);   // research is grounded in the selected fields already
    setStage("generate");
    toast("Complex prompt composed from your research — review and Generate.", "ok");
  }

  async function runResearch() {
    if (!providers.available.length) return toast("No AI provider set up. Add a key in Settings.", "warn");
    if (!general && !selFields.length) return toast("Select datafields, or enable General hypotheses.", "warn");
    setRBusy(true); setHyps([]);
    const start = await api.post<{ job_id: string; error?: string }>("/research/run", {
      category, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      dataset_names: dsNames, fields: selFields.map((f) => ({ id: f.id, type: f.type, description: f.description })),
      categories: fieldCategory, goal:[goal,transferNote].filter(Boolean).join("\n"), paper_text: paperText, paper_name: paperName, paper_is_community: community,
      mode: rMode, max_operators: maxOps, n: hypN, economic_review: econ, general,
    });
    if (start.error || !start.job_id) { setRBusy(false); return toastErr(start.error || "Could not start."); }
    setRJob(start.job_id); pollResearch(start.job_id);
  }

  // ── generate actions ───────────────────────────────────────────────────────
  async function pollGen(id: string) {
    let s: any = {};
    for (; ;) { s = await api.get(`/generate/jobs/${id}`); if (s.error && s.status === undefined) break; if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false); setGJob("");
    if (s.status !== "done") { if (s.error) toastErr(s.error); return; }
    const r = s.result || {};
    const all: string[] = r.valid || [];
    const fresh = all.filter((e) => !seen.includes(e));
    const dups = all.length - fresh.length;
    setValid(fresh); setRejected(r.rejected || []);
    setSeen([...new Set([...seen, ...all])].slice(-2000));
    setMeta({ provider: r.provider, used: r.operators_used, fields: r.fields_used });
    setOtab("expr");
    toast(`${fresh.length} new via ${r.provider}${dups ? ` · ${dups} duplicate(s) skipped` : ""} · ${r.operators_used?.length || 0} operators.`);
  }

  async function autoRewrite() {
    if (!selFields.length) return toast("Select datafields in the Data Explorer first.", "warn");
    setRewriteBusy(true);
    const start = await api.post<any>("/generate/rewrite", {
      prompt:[prompt,transferNote].filter(Boolean).join("\n\n"), region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument, universe: R.ctx.universe,
      dataset_names: dsNames, fields: fieldPayload(), categories: fieldCategory, max_operators: maxOps, n,
    });
    if (start.error || !start.job_id) { setRewriteBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/generate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setRewriteBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    if (s.result?.prompt) { setPrompt(s.result.prompt); toast("Rewrote into a rich instruction — datasets & operators are added at generation.", "ok"); }
  }

  async function explainOne(i: number, e: string) {
    if (expl[i]) { setExpl((m) => { const c = { ...m }; delete c[i]; return c; }); return; }
    setExplBusy(i);
    const r = await api.post<any>("/generate/explain", {
      expression: e, fields: fieldPayload(), region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument });
    setExplBusy(null);
    if (r.error) return toastErr(r.error);
    setExpl((m) => ({ ...m, [i]: r.explanation || "(no explanation)" }));
  }

  // Save the current instruction as a reusable Prompt Library entry, carrying its dataset knowledge
  // (the selected fields' datasets) and single/multi field mode, so it can be reopened later.
  async function saveToLibrary() {
    if (!prompt.trim()) return toast("Nothing to save — compose or write an instruction first.", "warn");
    setSaveBusy(true);
    const dsIds = [...new Set(selFields.map((f) => (f as any).dataset_id).filter(Boolean))] as string[];
    const start = await api.post<any>("/research/push", {
      scope: "generate", category: mode === "multi" ? "multi-field" : "single-field",
      region: R.ctx.region, body: prompt, dataset_names: dsIds, compose: false, source: "research",
    });
    if (start.error || !start.job_id) { setSaveBusy(false); return toastErr(start.error || "Could not save."); }
    let s: any = {}; for (; ;) { s = await api.get(`/research/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1200)); }
    setSaveBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    const r = s.result || {};
    toast(r.duplicate ? `Already in the Library as “${r.name}”.` : `Saved to Library as “${r.name}” (#${r.prompt_id}) with ${dsIds.length} dataset(s).`, "ok");
  }

  async function runGen() {
    if (!selFields.length) return toast("Select datafields in the Data Explorer first.", "warn");
    if (missingDs.length) return toast(`Fetch the required dataset(s) first: ${missingDs.join(", ")}.`, "warn");
    setBusy(true); setValid([]); setRejected([]);
    const start = await api.post<{ job_id: string; error?: string }>("/generate/run", {
      mode, prompt:[prompt,transferNote].filter(Boolean).join("\n\n"), region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      universe: R.ctx.universe, dataset_names: dsNames, fields: fieldPayload(),
      categories: fieldCategory, max_operators: maxOps, n, economic_review: econ,
      allowed_operators: allowedOps,
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setGJob(start.job_id); pollGen(start.job_id);
  }

  const ctxLine = `${selFields.length} field(s) · ${nCats} categor${nCats === 1 ? "y" : "ies"} · ${R.ctx.region} D${R.ctx.delay}`;

  return (
    <div className="dx-split generation-page route-workspace" style={{ flex: 1, minHeight: 0 }}>
      <header className="terminal-title compact-title generation-title"><div><span className="eyebrow">GROUNDED ALPHA RESEARCH</span><h1>Alpha Lab</h1><p>Move from selected data to economic hypotheses, validated expressions, and simulation-ready candidates.</p></div><div className="step-status"><span className={stage==="ideate"?"active":"done"}>1 Research</span><i>→</i><span className={stage==="generate"?"active":""}>2 Generate</span><i>→</i><span className={valid.length?"done":""}>3 Review</span></div></header>
      {/* ── setup ── */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head">
          <b>{stage === "ideate" ? "Research protocol" : "Candidate specification"}</b>
          <span className="mut">{ctxLine}</span>
          {(rBusy || busy) ? <span className="badge ok" style={{ marginLeft: "auto" }}>{rBusy ? "researching" : "generating"}</span> : null}
        </div>

        {/* stage switch */}
        <div className="seg" style={{ margin: "2px 0 12px" }}>
          <button className={"seg-btn" + (stage === "ideate" ? " on" : "")} onClick={() => setStage("ideate")}>① Ideate</button>
          <button className={"seg-btn" + (stage === "generate" ? " on" : "")} onClick={() => setStage("generate")}>② Generate</button>
        </div>

        <div className="panel-scroll" style={{ paddingRight: 2 }}>
          {stage === "ideate" ? (
            <>
              <div className="mut" style={{ fontSize: 11, marginBottom: 8 }}>
                {providers.available.length ? `AI via ${providers.used.join(", ") || providers.available.join(", ")}` : "No AI key — add one in Settings"}
                {category ? ` · ${category}` : ""}
              </div>

              <label className="fld"><span>Field combination</span>
                <select value={rMode} onChange={(e) => setRMode(e.target.value)}>
                  {RESEARCH_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select></label>
              <div className="dx-filters" style={{ marginTop: 8 }}>
                <span className={"pill" + (general ? " on" : "")} onClick={() => setGeneral((v) => !v)}
                  title="Create portable mechanisms now and bind them to selected fields later">General hypotheses{general ? " ✓" : ""}</span>
              </div>
              <div className="mut" style={{ fontSize: 11, marginTop: 3 }}>{RESEARCH_MODES.find((m) => m.id === rMode)?.hint}</div>

              <label className="fld" style={{ marginTop: 10 }}>
                <span style={{ display: "flex", alignItems: "center" }}>Goal (optional — more detail → richer hypotheses)
                  <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={autoGoal} disabled={goalBusy}
                    title="Let AI draft a goal from your selected data (or the uploaded paper)">
                    {goalBusy ? <span className="spin" /> : "✦ Auto"}</button></span>
                <textarea value={goal} onChange={(e) => setGoal(e.target.value)} style={{ minHeight: 46 }}
                  placeholder="e.g. short-horizon reaction to negative news in liquid names" /></label>

              {savedHyps.length ? <details className="panel" style={{ padding: 10, marginTop: 12 }}>
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>Saved hypotheses ({savedHyps.length})</summary>
                {savedHyps.slice(0, 20).map((x) => <div key={x.id} style={{ display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                  <span className="mut" style={{ flex: 1, fontSize: 12 }}>#{x.id} {x.hypothesis.idea}</span>
                  <button className="btn ghost sm" onClick={() => { setHyps([x.hypothesis]); composeFromHyps([x.hypothesis]); }}>Use</button>
                  <button className="btn ghost sm" onClick={() => { setHyps([x.hypothesis]); setAllowedOps([]); composeFromHyps([x.hypothesis]); toast("Choose operator constraints in Strategy Lab, or generate unconstrained variants."); }}>Explore ops</button>
                </div>)}
              </details> : null}

              <div className="dx-head" style={{ marginTop: 12 }}><b>Research paper</b>
                <span className="mut">optional — extract a mechanism to adapt</span></div>
              <div className="dx-filters" style={{ marginBottom: 6 }}>
                <span className={"pill" + (community ? " on" : "")} onClick={() => setCommunity((v) => !v)}
                  title="A WorldQuant community paper is mapped strictly to selected datasets and fields.">
                  WorldQuant Community Paper</span>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "end" }}>
                <label className="fld" style={{ width: 160 }}><span>Pages · optional</span>
                  <input value={pages} onChange={(e) => setPages(e.target.value)} placeholder="blank = complete paper" title="Leave blank to parse every page. Enter a range only when you intentionally restrict the source." /></label>
                <button className="btn ghost sm" onClick={() => fileRef.current?.click()}>Upload PDF</button>
                <input ref={fileRef} type="file" accept="application/pdf" style={{ display: "none" }}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadPaper(f); }} />
                {paperName ? <span className="badge ok">{paperName} · {pages.trim() ? `pages ${pages}` : "complete paper"}</span> : null}
                {(paperName || paperText) ? <button className="btn ghost sm" onClick={removePaper} title="Remove the uploaded paper">✕</button> : null}
              </div>
              <textarea value={paperText} onChange={(e) => setPaperText(e.target.value)} style={{ minHeight: 46, marginTop: 8 }}
                placeholder="…or paste the complete paper text here" />

              <details className="panel" style={{ padding: 10, marginTop: 12 }}>
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>Reuse a BRAIN alpha by its thesis</summary>
                <div className="mut" style={{ fontSize: 11, margin: "6px 0" }}>Fetch its code, extract the durable mechanism, then test transferability against your selected field descriptions. Blind field substitution is rejected.</div>
                <div style={{ display: "flex", gap: 8 }}>
                  <input value={alphaId} onChange={(e) => setAlphaId(e.target.value)} placeholder="BRAIN alpha ID" />
                  <button className="btn ghost sm" onClick={fetchAlpha} disabled={alphaBusy}>{alphaBusy ? <span className="spin" /> : "Fetch code"}</button>
                  <button className="btn sm" onClick={replicateAlpha} disabled={alphaBusy || !selFields.length}>{alphaBusy ? <span className="spin" /> : "Curate hypotheses"}</button>
                </div>
                {sourceCode ? <code style={{ display: "block", marginTop: 8, wordBreak: "break-all" }}>{sourceCode}</code> : null}
              </details>

              <div style={{ display: "flex", gap: 8, alignItems: "end", marginTop: 12 }}>
                <label className="fld" style={{ width: 90 }}><span>Hypotheses</span>
                  <NumberInput min={1} fallback={6} value={hypN} onChange={setHypN} /></label>
                <label className="fld" style={{ width: 84 }}><span>Max Ops</span>
                  <NumberInput min={1} max={12} fallback={6} value={maxOps} onChange={setMaxOps} /></label>
                <span className={"pill" + (econ ? " on" : "")} onClick={() => setEcon((v) => !v)}
                  title="An LLM drops hypotheses/expressions with no economic sense.">econ{econ ? " ✓" : ""}</span>
                <button className="btn" onClick={runResearch} disabled={rBusy || composing} style={{ flex: 1 }}>
                  {rBusy ? <><span className="spin" /> Researching…</> : composing ? <><span className="spin" /> Composing…</> : "✦ Run Research → Prompt"}</button>
              </div>
              <div className="mut" style={{ fontSize: 11, marginTop: 8 }}>
                {hyps.length
                  ? <>{hyps.length} hypotheses generated → a complex prompt was composed into <b>② Generate</b>. Review and Generate.</>
                  : <>Run Research and it will automatically compose a complex prompt into ② Generate — or skip straight to ② Generate and <b>Compose Complex Prompt</b> without ideation.</>}
              </div>
            </>
          ) : (
            <>
              <div className="dx-filters" style={{ marginBottom: 8 }}>
                <span className={"pill" + (mode === "single" ? " on" : "")} onClick={() => setMode("single")}>Single Field (Deep)</span>
                <span className={"pill" + (mode === "multi" ? " on" : "")} onClick={() => setMode("multi")}>Multi Field (≤2 Categories)</span>
                <span className={"pill" + (econ ? " on" : "")} onClick={() => setEcon((v) => !v)}
                  title="Before showing results, an LLM drops expressions with no economic sense so you don't waste simulations.">econ{econ ? " ✓" : ""}</span>
              </div>
              {mode === "multi" && nCats > 2 &&
                <div className="mut" style={{ fontSize: 12, color: "var(--warn)", marginBottom: 8 }}>
                  {nCats} categories selected — expressions restricted to at most 2 per expression.</div>}
              {allowedOps.length ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, fontSize: 11,
                  color: "var(--acc)", background: "var(--acc-weak)", border: "1px solid var(--acc-line)", borderRadius: 8, padding: "6px 10px" }}>
                  <span>🔒 Constrained to {allowedOps.length} operator(s) from Strategy Lab: <span className="mut" title={allowedOps.join(", ")}>{allowedOps.slice(0, 8).join(", ")}{allowedOps.length > 8 ? "…" : ""}</span></span>
                  <button className="pill sm" style={{ marginLeft: "auto" }} onClick={() => setAllowedOps([])} title="Remove the operator constraint">clear ✕</button>
                </div>) : null}

              <label className="fld"><span>Instruction (optional — fold hypotheses in, or write your own)</span>
                <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} style={{ minHeight: 96 }}
                  placeholder="Describe the economic ideas to explore, or leave blank for grounded auto-generation." /></label>

              {libPrompts.length > 0 &&
                <div className="dx-filters wrap" style={{ marginTop: 8 }}>
                  <span className="mut" style={{ fontSize: 11 }}>From Library:</span>
                  {libPrompts.slice(0, 8).map((p) => <span key={p.id} className="pill" title={p.name}
                    onClick={() => { setPrompt(p.body); setReqDatasets(p.datasets || []); toast(`Loaded “${p.name}” (#${p.id}).`); }}>
                    {p.name.length > 20 ? p.name.slice(0, 20) + "…" : p.name} <span className="mut">#{p.id}</span></span>)}
                </div>}
              {missingDs.length ? <div className="mut" style={{ fontSize: 12, color: "var(--warn)", marginTop: 8 }}>
                ⚠ This strategy needs data you haven't fetched: <b>{missingDs.join(", ")}</b>. Fetch it in the Data Explorer and select its fields first.
              </div> : null}

              <div className="dx-filters" style={{ marginTop: 8 }}>
                <button className="btn ghost sm" onClick={autoRewrite} disabled={rewriteBusy || busy}
                  title="Compose a rich, self-contained master prompt from whatever's here (or from scratch) — no ideation needed.">
                  {rewriteBusy ? <><span className="spin" /> Composing…</> : "✦ Compose Complex Prompt"}</button>
                <button className="btn ghost sm" onClick={saveToLibrary} disabled={saveBusy || !prompt.trim()}
                  title="Save this instruction to the Prompt Library with its dataset knowledge, reusable later.">
                  {saveBusy ? <><span className="spin" /> Saving…</> : "＋ Save to Library"}</button>
              </div>

              <div style={{ display: "flex", gap: 8, alignItems: "end", marginTop: 10 }}>
                <label className="fld" style={{ width: 84 }}><span>Max Ops</span>
                  <NumberInput min={1} max={12} fallback={4} value={maxOps} onChange={setMaxOps} /></label>
                <label className="fld" style={{ width: 84 }}><span>Count</span>
                  <NumberInput min={1} fallback={12} value={n} onChange={setN} /></label>
                <button className="btn" onClick={runGen} disabled={busy} style={{ flex: 1 }}>
                  {busy ? <><span className="spin" /> generating…</> : "✦ Generate"}</button>
              </div>
              {meta.used?.length ? <div className="mut" style={{ fontSize: 11, marginTop: 8 }}>operators this batch: {meta.used.join(", ")}</div> : null}
            </>
          )}
        </div>
      </div>

      {/* ── output ── */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head">
          <div className="seg">
            <button className={"seg-btn" + (otab === "hyps" ? " on" : "")} onClick={() => setOtab("hyps")}>Hypotheses{hyps.length ? ` ${hyps.length}` : ""}</button>
            <button className={"seg-btn" + (otab === "expr" ? " on" : "")} onClick={() => setOtab("expr")}>Expressions{valid.length ? ` ${valid.length}` : ""}</button>
          </div>
          {otab === "hyps" && hyps.length ? <>
            <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={saveAllHyps}>Save all uniquely</button>
            <span className={"pill sm" + (sortConf ? " on" : "")} onClick={() => setSortConf((v) => !v)} title="Sort by AI confidence">★ sort</span>
            <button className="btn sm" onClick={() => composeFromHyps(hyps)} disabled={composing}
              title="Re-compose these hypotheses into the Generate instruction">
              {composing ? <><span className="spin" /> Composing…</> : "Recompose → Instruction"}</button>
          </> : null}
          {otab === "expr" && valid.length ? <>
            <span className="mut" style={{ marginLeft: "auto" }}>{valid.length} valid · {rejected.length} rejected</span>
            <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(valid.join("\n")); toast("Copied."); }}>Copy</button>
            <button className="btn ghost sm" onClick={() => { R.setEvolutionPending(valid); nav("/evolution"); }}>Explore variants</button>
            <button className="btn sm" onClick={() => { R.setPending(valid); nav("/simulate"); }}>Send {valid.length} →</button>
          </> : null}
        </div>

        <div className="panel-scroll">
          {otab === "hyps" ? (
            !hyps.length ? <div className="empty">Set up ① Ideate and Run Research. Hypotheses are grounded in your selected fields and region, validated for field type & vector wrapping, then folded into the Generate instruction.</div> :
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(sortConf ? [...hyps].sort((a, b) => (b.confidence || 0) - (a.confidence || 0)) : hyps).map((h, i) => (
                  <div key={i} className="panel" style={{ padding: "10px 12px", boxShadow: "none" }}>
                    <div style={{ fontWeight: 600 }}>{i + 1}. {h.idea}</div>
                    <div className="mut" style={{ fontSize: 12, marginTop: 3 }}>{h.mechanism}</div>
                    <div className="dx-filters" style={{ marginTop: 6, marginBottom: 4 }}>
                      {h.confidence ? <span className="badge" style={{ background: "var(--ok-weak)", color: "var(--ok)" }}>{"★".repeat(h.confidence)} {h.confidence}/5</span> : null}
                      {h.sign ? <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>sign {h.sign}</span> : null}
                      {h.horizon ? <span className="badge" style={{ background: "var(--faint)", color: "var(--mut)" }}>~{h.horizon}d</span> : null}
                      <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => saveHyp(h)}>Save unique</button>
                      {h.expression ? (h.expression_valid === false
                        ? <span className="badge bad" title={(h.expression_issues || []).join(", ")}>invalid expr</span>
                        : <span className="badge ok">valid</span>) : null}
                    </div>
                    {h.falsification ? <div className="mut" style={{ fontSize: 11 }}><b>Falsify:</b> {h.falsification}</div> : null}
                    {h.field_requirements ? <div className="mut" style={{ fontSize: 11 }}><b>Needs:</b> {h.field_requirements}</div> : null}
                    {h.expression ? <code style={{ display: "block", background: "var(--surface-2)", padding: "6px 8px", borderRadius: 6 }}>{h.expression}</code> : null}
                  </div>
                ))}
              </div>
          ) : (
            !valid.length && !rejected.length ? <div className="empty">Fold hypotheses into the instruction (or write your own) in ② Generate, then Generate. Single-field extracts signal from one field every way; multi-field combines up to two categories.</div> :
              <>
                {(meta.used?.length || meta.fields?.length) ? (
                  <div className="dx-filters wrap" style={{ marginBottom: 6, fontSize: 11 }}>
                    <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>{meta.used?.length || 0} operators</span>
                    <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>{meta.fields?.length || 0} fields</span>
                    <span className="badge" style={{ background: "var(--faint)", color: "var(--mut)" }}>{seen.length} seen total</span>
                  </div>
                ) : null}
                {valid.map((e, i) => <div key={i} style={{ borderBottom: "1px solid var(--line)", padding: "6px 8px" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                    <code style={{ flex: 1, wordBreak: "break-all" }}>{e}</code>
                    <button className="btn ghost sm" style={{ flex: "0 0 auto" }} onClick={() => explainOne(i, e)} disabled={explBusy === i}
                      title="Explain the economic idea of this alpha">{explBusy === i ? <span className="spin" /> : (expl[i] ? "Hide" : "Explain")}</button>
                  </div>
                  {expl[i] ? <div className="mut" style={{ fontSize: 12, lineHeight: 1.5, marginTop: 4 }}>{expl[i]}</div> : null}
                </div>)}
                {rejected.length > 0 && <>
                  <div className="dx-head" style={{ marginTop: 10 }}><span className="mut">Rejected ({rejected.length})</span></div>
                  {rejected.slice(0, 8).map((r, i) => (
                    <div key={i} style={{ padding: "4px 8px", fontSize: 12 }}>
                      <code className="mut">{r.expr.slice(0, 60)}</code> <span style={{ color: "var(--bad)" }}>{r.issues.join(", ")}</span></div>))}
                </>}
              </>
          )}
        </div>
      </div>
    </div>
  );
}
