import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { AlphaCell } from "../../lib/AlphaCell";
import { useToast } from "../../lib/toast";

const num = (v: any) => (v == null ? "—" : (+v).toFixed(3));
type TransferConfig={universes:string[];neutralizations:string[]};

// Simulation + the success gate. Alphas run across the chosen universes/neutralizations;
// each is judged against every gate metric (delay 0 is strict on Sharpe/Fitness). Tags are
// the user's own. Correlations require the (optional) submission check.
//
// All configuration is persisted, so leaving the screen and returning (or a full reload)
// keeps every setting — it never resets to the data-fetch context. A running job is
// reconnected on mount so its live progress reappears instead of vanishing.
export function Simulation() {
  const R = useResearch();
  const { toast, toastErr } = useToast();

  const [expr, setExpr] = usePersistentState("sim:expr", (R.pending || []).join("\n"));
  const [unis, setUnis] = usePersistentState<string[]>("sim:unis", [R.ctx.universe]);
  const [neuts, setNeuts] = usePersistentState<string[]>("sim:neuts", ["INDUSTRY"]);
  const [decay, setDecay] = usePersistentState("sim:decay", 4);
  const [trunc, setTrunc] = usePersistentState("sim:trunc", 0.08);
  const [conc, setConc] = usePersistentState("sim:conc", 3);
  const [multi, setMulti] = usePersistentState("sim:multi", 10);
  const [testPeriod, setTestPeriod] = usePersistentState("sim:testperiod", "P0Y0M0D");
  const [gate, setGate] = usePersistentState("sim:gate", { sharpe: 1.58, fitness: 1.0, max_turnover: 0.7, max_corr: 0.7 });
  const [winAbove, setWinAbove] = usePersistentState("sim:winabove", 2.0);
  const [checkSub, setCheckSub] = usePersistentState("sim:checksub", false);
  const [rangeFrom, setRangeFrom] = usePersistentState("sim:rangefrom-zero-based", 0);
  const [rangeTo, setRangeTo] = usePersistentState("sim:rangeto", 0);   // 0 = to the end
  const [rnd, setRnd] = usePersistentState("sim:rnd", false);           // randomize configs before range
  // engine settings — every one is toggleable and persisted
  const [pasteur, setPasteur] = usePersistentState("sim:pasteur", "ON");
  const [nanH, setNanH] = usePersistentState("sim:nan", "OFF");
  const [maxTrade, setMaxTrade] = usePersistentState("sim:maxtrade", "OFF");
  // tags live in Settings' localStorage keys (shared). Read on mount (picks up Settings edits when
  // navigating here) and written back on change, so Settings and Simulation stay in sync and the
  // saved tag is always what's applied on simulate.
  const [tag, setTag] = useState(() => localStorage.getItem("ace2-tag") || "");
  const [winnerTag, setWinnerTag] = useState(() => localStorage.getItem("ace2-winnertag") || "");
  useEffect(() => { localStorage.setItem("ace2-tag", tag); }, [tag]);
  useEffect(() => { localStorage.setItem("ace2-winnertag", winnerTag); }, [winnerTag]);

  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState<{ done: number; total: number; msg: string } | null>(null);
  const [res, setRes] = usePersistentState<any | null>("sim:res", null);
  const [jobId, setJobId] = usePersistentState<string>("sim:job", "");
  const [estimate,setEstimate]=useState<any>(null);
  const transferStudy=R.transferStudy;
  const [transferChoices,setTransferChoices]=useState<Record<string,TransferConfig>>({});
  const [transferOptions,setTransferOptions]=useState<any[]>([]);
  const [transferSheetOpen,setTransferSheetOpen]=useState(false);
  const jobRef = useRef<string>(jobId);
  jobRef.current = jobId;

  const universes = R.universes(R.ctx.instrument, R.ctx.region, R.ctx.delay);
  const neutOpts = useMemo(() => {
    const recs = (R.options || []).filter((o) => o.instrument === R.ctx.instrument && o.region === R.ctx.region);
    return [...new Set(recs.flatMap((o) => o.neutralizations))];
  }, [R.options, R.ctx]);
  useEffect(()=>{if(transferStudy)api.get<any>("/regions/info").then(d=>setTransferOptions(d.options||[]))},[transferStudy?.id]);
  const optionRecords=useMemo(()=>{const merged=new Map<string,any>();for(const raw of [...R.options,...transferOptions]){const instrument=String(raw.instrument||"").trim().toUpperCase(),region=String(raw.region||"").trim().toUpperCase(),delay=Number(raw.delay),key=`${instrument}|${region}|${delay}`,old=merged.get(key)||{instrument,region,delay,universes:[],neutralizations:[]};merged.set(key,{...old,universes:[...new Set([...(old.universes||[]),...(raw.universes||[])])],neutralizations:[...new Set([...(old.neutralizations||[]),...(raw.neutralizations||[])])]})}return[...merged.values()]},[R.options,transferOptions]);
  const transferKey=(region:string,delay:number)=>`${region}|${delay}`;
  const contextUniverses=(region:string,delay:number)=>[...new Set(optionRecords.filter(o=>o.instrument===String(transferStudy?.instrument||R.ctx.instrument).trim().toUpperCase()&&o.region===String(region).trim().toUpperCase()&&Number(o.delay)===delay).flatMap(o=>o.universes||[]))];
  const contextNeuts=(region:string,delay:number)=>[...new Set(optionRecords.filter(o=>o.instrument===String(transferStudy?.instrument||R.ctx.instrument).trim().toUpperCase()&&o.region===String(region).trim().toUpperCase()&&Number(o.delay)===delay).flatMap(o=>o.neutralizations||[]))];
  useEffect(()=>{if(!transferStudy||!optionRecords.length)return;let stored:Record<string,TransferConfig>={};try{stored=JSON.parse(localStorage.getItem(`ace2-transfer-sim:${transferStudy.id}`)||"{}")}catch{}const next:Record<string,TransferConfig>={};for(const ctx of transferStudy.contexts){const key=transferKey(ctx.region,ctx.delay),availableU=contextUniverses(ctx.region,ctx.delay),availableN=contextNeuts(ctx.region,ctx.delay),old=stored[key];const universes=(old?.universes||[ctx.universe]).filter(x=>availableU.includes(x));const neutralizations=(old?.neutralizations||ctx.neutralizations||[]).filter(x=>availableN.includes(x));next[key]={universes:universes.length?universes:[availableU.includes(ctx.universe)?ctx.universe:availableU[0]].filter(Boolean),neutralizations:neutralizations.length?neutralizations:[availableN.includes("INDUSTRY")?"INDUSTRY":availableN[0]].filter(Boolean)}}setTransferChoices(next)},[transferStudy?.id,optionRecords.length]);
  useEffect(()=>{if(transferStudy&&Object.keys(transferChoices).length)localStorage.setItem(`ace2-transfer-sim:${transferStudy.id}`,JSON.stringify(transferChoices))},[transferStudy?.id,transferChoices]);
  const toggleTransfer=(ctxKey:string,kind:keyof TransferConfig,value:string)=>setTransferChoices(prev=>{const row=prev[ctxKey]||{universes:[],neutralizations:[]},current=row[kind],next=current.includes(value)?current.filter(x=>x!==value):[...current,value];return{...prev,[ctxKey]:{...row,[kind]:next}}});
  const transferSettings=transferStudy?.contexts.map(ctx=>({region:ctx.region,delay:ctx.delay,...(transferChoices[transferKey(ctx.region,ctx.delay)]||{universes:[ctx.universe],neutralizations:ctx.neutralizations||[]})}))||[];

  // Gate defaults follow delay (2.69/1.5 at delay 0) — but only re-fetch when the delay
  // actually CHANGES, so a persisted custom gate isn't clobbered on every mount.
  const gateDelay = useRef<number | null>(null);
  useEffect(() => {
    if (gateDelay.current === R.ctx.delay) return;
    gateDelay.current = R.ctx.delay;
    api.get<any>(`/simulate/gate?delay=${R.ctx.delay}`).then((g) =>
      setGate({ sharpe: g.sharpe, fitness: g.fitness, max_turnover: g.max_turnover, max_corr: g.max_corr }));
  }, [R.ctx.delay]);

  // Apply a handoff from Template Studio / Generation ONLY when it actually changes — never
  // re-overwrite the user's edits on a plain remount.
  const lastPending = useRef<string>("");
  useEffect(() => {
    const key = (R.pending || []).join("\n");
    if (key && key !== lastPending.current) { lastPending.current = key; setExpr(key); }
  }, [R.pending]);

  // Keep chosen universes valid for the current region without wiping the user's selection:
  // drop any that no longer exist; if none remain, fall back to the context universe.
  useEffect(() => {
    setUnis((prev) => {
      const keep = prev.filter((u) => universes.includes(u));
      return keep.length ? keep : [universes.includes(R.ctx.universe) ? R.ctx.universe : (universes[0] || "TOP3000")];
    });
  }, [R.ctx.region]);

  // Same for neutralizations: a persisted choice like COUNTRY carries over when you switch to a
  // region that doesn't offer it (e.g. USA), and BRAIN then rejects the whole batch with a 400.
  // Drop any neutralization not offered by the current region/instrument so it can NEVER be sent.
  useEffect(() => {
    if (!neutOpts.length) return;                 // options not loaded yet — don't wipe the choice
    setNeuts((prev) => {
      const keep = prev.filter((n) => neutOpts.includes(n));
      return keep.length ? keep : [neutOpts.includes("INDUSTRY") ? "INDUSTRY" : neutOpts[0]];
    });
  }, [neutOpts]);

  // Reconnect to a job that's still running (survives navigation + reload).
  useEffect(() => {
    if (jobId) { setBusy(true); poll(jobId); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) =>
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const flip = (v: string, on: string, off: string) => (v === on ? off : on);

  const allExprs = expr.split("\n").map((s) => s.trim()).filter(Boolean);
  const exprs = allExprs;   // range now applies at the CONFIG level (backend), not to expressions
  const transferConfigCount = transferSettings.reduce((sum,x)=>sum+x.universes.length*x.neutralizations.length,0);
  const totalConfigs = allExprs.length * Math.max(1, transferStudy?transferConfigCount:unis.length*Math.max(1,neuts.length));
  const previousTotal=useRef(0);
  useEffect(()=>{const before=previousTotal.current;if(totalConfigs>0&&(rangeTo<=0||rangeTo===before))setRangeTo(totalConfigs);previousTotal.current=totalConfigs},[totalConfigs]);

  async function poll(id: string) {
    let s: any = {};
    for (; ;) {
      s = await api.get(`/simulate/jobs/${id}`);
      if (s.error && s.status === undefined) break;   // job gone (e.g. backend restarted)
      if (s.total || s.done) setProg({ done: s.done || 0, total: s.total || 0, msg: s.message || "" });
      if (s.status !== "running") break;
      await new Promise((r) => setTimeout(r, 1400));
    }
    setBusy(false); setProg(null); setJobId("");
    if (s.status === "error") return toastErr(s.error);
    if (!s.result) return;   // job vanished; nothing to show
    setRes(s.result);
    if (s.result.passed) toast(`${s.result.passed} of ${s.result.simulated} passed the gate · ${s.result.tagged || 0} tagged.`, "ok");
    else toast(`${s.result.simulated} simulated · none passed the gate yet.`, "warn");
  }

  function retryFailed() {
    const failed = [...new Set((res?.results || []).filter((r: any) => !r.passed).map((r: any) => String(r.expr || "")).filter(Boolean))] as string[];
    if (!failed.length) return toast("No failed configs to retry.", "warn");
    setExpr(failed.join("\n"));
    run(failed);
  }
  async function estimateRun(){if(!exprs.length)return toast("Add expressions first.","warn");if(transferStudy&&transferSettings.some(x=>!x.universes.length||!x.neutralizations.length))return toast("Select at least one universe and neutralization for every transfer context.","warn");const path=transferStudy?"/simulate/transfer/estimate":"/simulate/estimate";const payload=transferStudy?{study_id:transferStudy.id,expressions:exprs,context_settings:transferSettings,neutralizations:neuts,decay,truncation:trunc,test_period:testPeriod,concurrency:conc,limit_of_multi:multi}:{expressions:exprs,region:R.ctx.region,delay:R.ctx.delay,instrument:R.ctx.instrument,universes:unis,neutralizations:neuts,decay,truncation:trunc,test_period:testPeriod,concurrency:conc,limit_of_multi:multi};const d=await api.post<any>(path,payload);if(d.error)return toastErr(d.error);setEstimate(d)}

  async function run(override?: string[]) {
    const list = override && override.length ? override : exprs;
    if (!list.length) return toast("Add expressions (from Generation, or paste).", "warn");
    if (!transferStudy&&(!unis.length || !neuts.length)) return toast("Pick at least one universe and neutralization.", "warn");
    if (transferStudy&&transferSettings.some(x=>!x.universes.length||!x.neutralizations.length)) return toast("Pick at least one universe and neutralization for every transfer context.","warn");
    // Quota estimate: configs = expr × universes × neutralizations, then the config range/randomize.
    const total = list.length * Math.max(1,transferStudy?transferConfigCount:unis.length*neuts.length);
    const eff = Math.max(0, Math.min(rangeTo > 0 ? rangeTo : total, total) - Math.max(0, rangeFrom));
    if(!eff)return toast("The config range is empty. Set To above From.","warn");
    if (eff > 300 && !window.confirm(`This will run about ${eff} simulations (of ${total} configs${rnd ? ", randomized" : ""}). That can take a while and use BRAIN quota. Continue?`)) return;
    localStorage.setItem("ace2-tag", tag); localStorage.setItem("ace2-winnertag", winnerTag);
    setBusy(true); setRes(null); setProg({ done: 0, total: 0, msg: "starting…" });
    const endpoint=transferStudy?"/simulate/transfer/run":"/simulate/run";
    const payload=transferStudy?{study_id:transferStudy.id,expressions:list,context_settings:transferSettings,neutralizations:neuts,decay,truncation:trunc,test_period:testPeriod,concurrency:conc,limit_of_multi:multi,randomize:rnd,range_from:rangeFrom,range_to:rangeTo,pasteurization:pasteur,nan_handling:nanH,max_trade:maxTrade,unit_handling:"VERIFY",min_sharpe:gate.sharpe,min_fitness:gate.fitness,max_turnover:gate.max_turnover,max_corr:gate.max_corr,tag,winner_tag:winnerTag,check_submission:checkSub}:{expressions:list,region:R.ctx.region,delay:R.ctx.delay,instrument:R.ctx.instrument,universes:unis,neutralizations:neuts,decay,truncation:trunc,test_period:testPeriod,concurrency:conc,limit_of_multi:multi,randomize:rnd,range_from:rangeFrom,range_to:rangeTo,pasteurization:pasteur,nan_handling:nanH,max_trade:maxTrade,unit_handling:"VERIFY",min_sharpe:gate.sharpe,min_fitness:gate.fitness,max_turnover:gate.max_turnover,max_corr:gate.max_corr,tag,winner_tag:winnerTag,tag_winners_above:winAbove,check_submission:checkSub};
    const start = await api.post<{ job_id: string; error?: string }>(endpoint,payload);
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setJobId(start.job_id);
    jobRef.current=start.job_id;
    window.dispatchEvent(new CustomEvent("ace-job-started",{detail:start.job_id}));
    poll(start.job_id);
  }

  async function stop() { const id=jobRef.current||jobId;if(id){const d=await api.post<any>(`/meta/jobs/${id}/cancel`);if(d.error)toastErr(d.error);else toast("Stopping simulation…","warn");} }

  // ── cross-region sweep ──────────────────────────────────────────────────────────────
  const allRegions = R.regions(R.ctx.instrument);
  // Walk-forward runs the SAME region at delay 1 AND delay 0, so it's only meaningful when this
  // region actually offers both delays.
  const regionDelays = R.delays(R.ctx.instrument, R.ctx.region);
  const hasBothDelays = regionDelays.includes(0) && regionDelays.includes(1);
  const [sweepRegions, setSweepRegions] = usePersistentState<string[]>("sim:sweepregions", []);
  const [transferContexts,setTransferContexts]=useState<any[]>([]);
  const [sweepBusy, setSweepBusy] = useState(false);
  const [sweepProg, setSweepProg] = useState("");
  const [sweepRes, setSweepRes] = usePersistentState<any[] | null>("sim:sweepres", null);
  useEffect(()=>{const q=`dataset_ids=${encodeURIComponent(R.selDatasets.join(","))}&field_ids=${encodeURIComponent(R.selFields.join(","))}&instrument=${encodeURIComponent(R.ctx.instrument)}`;api.get<any>(`/catalog/transfer?${q}`).then(d=>!d.error&&setTransferContexts(d.contexts||[]))},[R.selDatasets.join("|"),R.selFields.join("|"),R.ctx.instrument]);
  const compatibleRegions=[...new Set(transferContexts.map((x:any)=>x.region))].filter(x=>x!==R.ctx.region) as string[];

  async function runSweep() {
    if (!exprs.length) return toast("Add expressions to sweep.", "warn");
    if (!sweepRegions.length) return toast("Pick target regions to sweep.", "warn");
    setSweepBusy(true); setSweepRes(null); setSweepProg("starting…");
    const start = await api.post<any>("/simulate/sweep", {
      expressions: exprs, regions: sweepRegions, delay: R.ctx.delay, instrument: R.ctx.instrument,
      neutralizations: neuts, decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, tag, winner_tag: winnerTag,
      home_region: R.ctx.region,
    });
    if (start.error || !start.job_id) { setSweepBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {};
    for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.message) setSweepProg(`${s.message}${s.total ? ` · ${s.done}/${s.total}` : ""}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1600)); }
    setSweepBusy(false); setSweepProg("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    setSweepRes(s.result?.regions || []);
    const passed = (s.result?.regions || []).reduce((a: number, r: any) => a + (r.passed || 0), 0);
    toast(`Swept ${sweepRegions.length} region(s) · ${passed} passed the gate.`, passed ? "ok" : "warn");
  }

  // ── batch queue (run several expression sets sequentially, unattended) ───────────────
  const [queue, setQueue] = usePersistentState<{ label: string; expressions: string[] }[]>("sim:queue", []);
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueRes, setQueueRes] = usePersistentState<any[] | null>("sim:queueres", null);
  function enqueue() {
    if (!exprs.length) return toast("Nothing to queue.", "warn");
    setQueue((q) => [...q, { label: `${R.ctx.region} · ${exprs.length} expr · #${q.length + 1}`, expressions: exprs }]);
    toast("Added to the batch queue.");
  }
  async function runQueue() {
    if (!queue.length) return toast("The queue is empty.", "warn");
    setQueueBusy(true); setQueueRes(null);
    const start = await api.post<any>("/simulate/batch", {
      batches: queue, region: R.ctx.region, delay: R.ctx.delay, universes: unis, neutralizations: neuts,
      decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, min_sharpe: gate.sharpe, min_fitness: gate.fitness, tag, winner_tag: winnerTag,
    });
    if (start.error || !start.job_id) { setQueueBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 2000)); }
    setQueueBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setQueueRes(s.result?.batches || []);
    toast("Batch queue complete.", "ok");
  }

  const [wfBusy, setWfBusy] = useState(false);
  const [wfRes, setWfRes] = usePersistentState<any[] | null>("sim:wfres", null);
  async function runWalkForward() {
    if (!exprs.length) return toast("Add expressions first.", "warn");
    setWfBusy(true); setWfRes(null);
    const start = await api.post<any>("/simulate/walkforward", {
      expressions: exprs, regions: [R.ctx.region], instrument: R.ctx.instrument, neutralizations: neuts,
      decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, tag, winner_tag: winnerTag,
    });
    if (start.error || !start.job_id) { setWfBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1600)); }
    setWfBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setWfRes(s.result?.delays || []);
    toast("Walk-forward complete (delay 1 & 0).", "ok");
  }

  const Pill = ({ on, label, onClick, title }: { on: boolean; label: string; onClick: () => void; title?: string }) =>
    <span className={"pill" + (on ? " on" : "")} onClick={onClick} title={title}>{label}</span>;

  return (
    <div className="dx-split simulation-page route-workspace" style={{ flex: 1, minHeight: 0 }}>
      {/* settings */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Simulation</b>
          <span className="mut">{transferStudy?`${transferStudy.contexts.length}-context transfer study`: `${R.ctx.region} · D${R.ctx.delay}`} · {exprs.length}{exprs.length !== allExprs.length ? ` of ${allExprs.length}` : ""} expr</span>
          <button className="btn ghost sm" style={{marginLeft:"auto"}} onClick={estimateRun}>Estimate & inspect queue</button>
          {busy ? <span className="badge ok" style={{ marginLeft: "auto" }}>running</span> : null}</div>
        {transferStudy?<div className="cost-readout"><b>{transferStudy.mode==="regions"?"Cross-region":"D0/D1"} automatic mode · {transferStudy.contexts.length} contexts · {transferStudy.selected_field_ids.length} common fields</b><span>Choose the simulation universes and neutralizations independently below. The shared fields remain fixed.</span></div>:null}
        {estimate?<div className="cost-readout"><b>{estimate.expressions} expressions × {transferStudy?estimate.context_configurations:`${unis.length} universes × ${neuts.length} neutralizations`} = {estimate.configurations} configurations</b><span>{estimate.new_configurations??0} new · {estimate.reusable??0} exact local matches · {estimate.batches??0} batches</span></div>:null}
        <label className="fld"><span>Expressions (One Per Line — From Generation)</span>
          <textarea value={expr} onChange={(e) => setExpr(e.target.value)} style={{ minHeight: 92 }} /></label>

        {!transferStudy?<><label style={{ fontSize: 11, color: "var(--mut)", marginTop: 8, display: "block" }}>Universes</label><div className="dx-filters wrap">{universes.map((u) => <span key={u} className={"pill" + (unis.includes(u) ? " on" : "")} onClick={() => toggle(unis, u, setUnis)}>{u}</span>)}</div><label style={{ fontSize: 11, color: "var(--mut)" }}>Neutralizations</label><div className="dx-filters wrap">{neutOpts.map((nu) =><span key={nu} className={"pill" + (neuts.includes(nu) ? " on" : "")} onClick={() => toggle(neuts, nu, setNeuts)}>{nu}</span>)}</div></>:<><div className="transfer-config-summary"><div>{transferSettings.map(x=><button type="button" key={transferKey(x.region,x.delay)} onClick={()=>setTransferSheetOpen(true)} title={`Configure ${x.region} D${x.delay}`}><b>{x.region} D{x.delay}</b>{x.universes.length}U × {x.neutralizations.length}N <i>›</i></button>)}</div><button className="btn sm" onClick={()=>setTransferSheetOpen(true)}>Configure all markets ({transferStudy.contexts.length})</button></div>{transferSheetOpen?<div className="transfer-sheet-backdrop" onMouseDown={()=>setTransferSheetOpen(false)}><aside className="transfer-sheet" onMouseDown={e=>e.stopPropagation()}><header><div><b>Transfer simulation configurations</b><span>Select one or more universes and neutralizations for every region or delay. Choices save automatically.</span></div><button className="btn ghost sm" onClick={()=>setTransferSheetOpen(false)}>Done</button></header><div className="transfer-sim-grid">{transferStudy.contexts.map((ctx:any,i:number)=>{const key=transferKey(ctx.region,ctx.delay),choice=transferChoices[key]||{universes:[ctx.universe],neutralizations:ctx.neutralizations||[]},availableU=contextUniverses(ctx.region,ctx.delay),availableN=contextNeuts(ctx.region,ctx.delay);return <section key={key}><header><i>{i+1}</i><b>{ctx.region} · D{ctx.delay}</b><span>{choice.universes.length*choice.neutralizations.length} configs / expression</span></header><label>Universes</label><div className="sheet-select-actions"><button onClick={()=>setTransferChoices(v=>({...v,[key]:{...choice,universes:availableU}}))}>All</button><button onClick={()=>setTransferChoices(v=>({...v,[key]:{...choice,universes:[ctx.universe]}}))}>Reference only</button></div><div className="dx-filters wrap">{availableU.map(u=><span key={u} className={"pill"+(choice.universes.includes(u)?" on":"")} onClick={()=>toggleTransfer(key,"universes",u)}>{u}</span>)}</div><label>Neutralizations</label><div className="sheet-select-actions"><button onClick={()=>setTransferChoices(v=>({...v,[key]:{...choice,neutralizations:availableN}}))}>All</button></div><div className="dx-filters wrap">{availableN.map(n=><span key={n} className={"pill"+(choice.neutralizations.includes(n)?" on":"")} onClick={()=>toggleTransfer(key,"neutralizations",n)}>{n}</span>)}</div>{!availableU.length||!availableN.length?<small className="bad">Account options are unavailable for this context. Refresh the BRAIN session and reopen this sheet.</small>:null}</section>})}</div></aside></div>:null}</>}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginTop: 8 }}>
          <label className="fld"><span>Decay</span><NumberInput min={0} fallback={4} value={decay} onChange={setDecay} /></label>
          <label className="fld"><span>Truncation</span><NumberInput step="0.01" min={0} fallback={0.08} value={trunc} onChange={setTrunc} /></label>
          <label className="fld"><span>Concurrency (1–8)</span><NumberInput min={1} max={8} fallback={3} value={conc} onChange={setConc} /></label>
          <label className="fld"><span>Multi-batch (2–10)</span><NumberInput min={2} max={10} fallback={10} value={multi} onChange={setMulti} /></label>
          <label className="fld" title="In-sample test period. Accepts P0Y0M0D, shorthand like P1Y or P0Y6M, or a bare number as years (1 → P1Y0M0D).">
            <span>Test period</span>
            <select value={testPeriod} onChange={(e) => setTestPeriod(e.target.value)}><option value="P0Y0M0D">Full period (baseline)</option><option value="P1Y0M0D">Hold out 1 year</option><option value="P2Y0M0D">Hold out 2 years</option><option value="P3Y0M0D">Hold out 3 years</option><option value="P4Y0M0D">Hold out 4 years</option><option value="P5Y0M0D">Hold out 5 years</option><option value="P6Y0M0D">Hold out 6 years</option></select></label>
          <label className="fld"><span>Sharpe ≥ {R.ctx.delay === 0 ? "(D0)" : ""}</span><NumberInput step="0.01" min={0} fallback={0} value={gate.sharpe} onChange={(v) => setGate({ ...gate, sharpe: v })} /></label>
          <label className="fld"><span>Fitness ≥</span><NumberInput step="0.1" min={0} fallback={0} value={gate.fitness} onChange={(v) => setGate({ ...gate, fitness: v })} /></label>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>Engine Settings</div>
        <div className="dx-filters wrap">
          <Pill on={pasteur === "ON"} label={`Pasteurization: ${pasteur}`} onClick={() => setPasteur((v) => flip(v, "ON", "OFF"))}
            title="Aligns each alpha to the universe used at simulation time." />
          <Pill on={nanH === "ON"} label={`NaN handling: ${nanH}`} onClick={() => setNanH((v) => flip(v, "ON", "OFF"))}
            title="ON treats NaN as 0; OFF leaves gaps as missing." />
          <Pill on={maxTrade === "ON"} label={`Max trade: ${maxTrade}`} onClick={() => setMaxTrade((v) => flip(v, "ON", "OFF"))}
            title="Caps per-instrument trading when ON." />
          <span className="pill on" style={{ cursor: "default" }} title="Unit handling is always VERIFY (enforces dimensional consistency).">Unit handling: VERIFY</span>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>Tags (Your Own) & Checks</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <label className="fld"><span>Tag Every Alpha</span><input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="e.g. my_news" /></label>
          <label className="fld"><span>Winner Tag</span><input value={winnerTag} onChange={(e) => setWinnerTag(e.target.value)} placeholder="e.g. my_winner" /></label>
          <label className="fld"><span>Winner |fit| ≥</span><NumberInput step="0.1" min={0} fallback={2} value={winAbove} onChange={setWinAbove} style={{ width: 80 }} /></label>
        </div>
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <span className={"pill" + (checkSub ? " on" : "")} onClick={() => setCheckSub((v) => !v)}
            title="Runs BRAIN's submission checks so self/prod/powerpool correlations join the gate. Slower.">Correlation & Submission Checks</span>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>
          Simulate A Range Of Configs (Of {totalConfigs} = {allExprs.length} expr × {transferStudy?`${transferConfigCount} transfer settings`:`${unis.length} univ × ${neuts.length} neut`})</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end" }}>
          <label className="fld"><span>From index (0-based)</span><NumberInput min={0} fallback={0} value={rangeFrom} onChange={setRangeFrom} /></label>
          <label className="fld"><span>To index (exclusive)</span><NumberInput min={1} max={Math.max(1,totalConfigs)} fallback={Math.max(1,totalConfigs)} value={rangeTo||totalConfigs} onChange={setRangeTo} /></label>
          <button className="btn ghost sm" onClick={() => { setRangeFrom(0); setRangeTo(totalConfigs); }}>All</button>
        </div>
        <div className="dx-filters" style={{ marginTop: 6 }}>
          <span className={"pill" + (rnd ? " on" : "")} onClick={() => setRnd((v) => !v)}
            title="Shuffle ALL configs (expr × universe × neutralization) before applying the range — so the range is a RANDOM sample of the whole config space, not the first N in order.">Randomize configs{rnd ? " ✓" : ""}</span>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button className="btn" style={{ flex: 1 }} onClick={() => run()} disabled={busy}>
            {busy ? <><span className="spin" /> Simulating…</> : "▶ Run Simulation"}</button>
          {busy ? <button className="btn ghost" onClick={stop}>Stop</button> : null}
        </div>
        {prog ? <div className="mut" style={{ fontSize: 12, marginTop: 8 }}>
          <span className="spin" /> {prog.msg} {prog.total ? `· ${prog.done}/${prog.total}` : ""}</div> : null}
      </div>

      {/* results */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Results</b>
          {res ? <span className="mut">{res.passed}/{res.simulated} passed the gate</span> : null}
          {res && res.results?.some((r: any) => !r.passed) ?
            <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={retryFailed} disabled={busy}>
              Retry {[...new Set(res.results.filter((r: any) => !r.passed).map((r: any) => r.expr))].length} failed</button> : null}
          {res ? <button className="btn ghost sm" style={res.results?.some((r: any) => !r.passed) ? undefined : { marginLeft: "auto" }}
            onClick={() => { setRes(null); toast("Cleared the results panel."); }} disabled={busy}
            title="Clear these results from this panel only — nothing on BRAIN or your saved data is touched">Clear</button> : null}</div>
        <div className="panel-scroll">
          {res && res.errors?.length ? (
            <div style={{ fontSize: 11, color: "var(--bad)", background: "var(--bad-weak)", borderRadius: 6, padding: "6px 9px", marginBottom: 8 }}>
              <b>{res.configs && res.simulated != null ? res.configs - res.simulated : res.errors.length} config(s) errored on BRAIN.</b> Reasons:
              <ul style={{ margin: "3px 0 0 16px", padding: 0 }}>
                {res.errors.slice(0, 6).map((e: string, i: number) => <li key={i}>{e}</li>)}
              </ul>
            </div>) : null}
          {!res ? <div className="empty">Run a simulation. Each alpha is judged against every gate metric; a green ✓ means it passed all of them.</div> :
            !res.results?.length ? <div className="empty">Nothing simulated (check the log / session).</div> :
              <table><thead><tr><th></th><th>Alpha</th><th>Sharpe</th><th>Fit</th><th>Turn</th><th>Uni / Neut</th><th>Why Not</th></tr></thead>
                <tbody>{res.results.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>{r.passed ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--bad)" }}>✗</span>}</td>
                    <td>{r.alpha_id ? <a href={`https://platform.worldquantbrain.com/alpha/${r.alpha_id}`} target="_blank" rel="noopener"><code>{r.alpha_id}</code></a> : "—"}
                      <div className="sub" style={{ marginTop: 2 }}><AlphaCell expr={r.expr} max={44} /></div></td>
                    <td>{num(r.sharpe)}</td><td>{num(r.fitness)}</td><td>{r.turnover == null ? "—" : (r.turnover * 100).toFixed(0) + "%"}</td>
                    <td className="mut" style={{ fontSize: 11 }}>{r.universe}<br />{r.neutralization}</td>
                    <td className="mut" style={{ fontSize: 11 }}>{(r.reasons || []).join(", ")}</td>
                  </tr>))}</tbody></table>}
          {res?.transfer_evidence?.length?<><div className="section" style={{marginTop:12}}>Transferability evidence</div><table><thead><tr><th>Expression</th><th>Contexts</th><th>Passed</th><th>Direction</th><th>Sharpe range</th></tr></thead><tbody>{res.transfer_evidence.map((x:any)=><tr key={x.expression}><td><AlphaCell expr={x.expression} max={42}/></td><td>{x.contexts_tested}/{transferStudy?.contexts.length}</td><td>{x.contexts_passed}</td><td className={x.direction_consistent?"good":"bad"}>{x.direction_consistent?"consistent":"sign changed"}</td><td>{num(x.min_sharpe)} → {num(x.max_sharpe)}</td></tr>)}</tbody></table></>:null}

          {!transferStudy?<>
          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Cross-Region Sweep</div>
          <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>Run the same {exprs.length} expression(s) across other regions to diversify fast. Each region first has its <b>datafields verified against BRAIN</b> (in a valid universe); regions missing a field are skipped, not guessed.</div>
          {compatibleRegions.length?<div className="dx-head" style={{marginBottom:6}}><span className="badge ok">{compatibleRegions.length} catalog-compatible region(s)</span><button className="btn ghost sm" onClick={()=>setSweepRegions(compatibleRegions)}>Select compatible</button></div>:<div className="mut" style={{fontSize:11,marginBottom:6}}>Sync the selected datasets and fields in other regions to identify exact local transfer candidates.</div>}
          <div className="dx-filters wrap">
            {allRegions.filter((rg) => rg !== R.ctx.region).map((rg) =>
              <span key={rg} title={compatibleRegions.includes(rg)?"Selected datasets and fields exist here in the local catalog":"BRAIN will verify fields before simulation"} className={"pill" + (sweepRegions.includes(rg) ? " on" : "")} onClick={() => setSweepRegions((x) => x.includes(rg) ? x.filter((y) => y !== rg) : [...x, rg])}>{rg}{compatibleRegions.includes(rg)?" ✓":""}</span>)}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn ghost sm" onClick={runSweep} disabled={sweepBusy || busy}>{sweepBusy ? <><span className="spin" /> Sweeping…</> : "▶ Run Cross-Region Sweep"}</button>
          </div>
          {sweepProg ? <div className="mut" style={{ fontSize: 12, marginTop: 6 }}><span className="spin" /> {sweepProg}</div> : null}
          {sweepRes ? <table style={{ marginTop: 8 }}><thead><tr><th>Region</th><th>Universe / neutralization</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{sweepRes.map((rg: any, i: number) => (
              <tr key={i}><td><b>{rg.region}</b></td><td className="mut">{rg.universe || "—"}<br/>{(rg.neutralizations||[]).join(", ")}</td>
                <td style={{ color: rg.passed ? "var(--ok)" : undefined }}>{rg.error || rg.skipped ? "—" : rg.passed}</td>
                <td className="mut" style={{ fontSize: 11 }}>
                  {rg.error ? <span style={{ color: "var(--bad)" }}>{rg.error}</span>
                    : rg.skipped ? <span style={{ color: "var(--warn)" }}>skipped · missing {(rg.missing || []).join(", ")}</span>
                      : rg.simulated}</td></tr>))}</tbody></table> : null}

          </>:null}
          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Batch Queue (Run Sequentially, Unattended)</div>
          <div className="dx-filters">
            <button className="btn ghost sm" onClick={enqueue}>+ Add Current ({exprs.length})</button>
            <button className="btn ghost sm" onClick={() => { setQueue([]); setQueueRes(null); }} disabled={!queue.length}>Clear Queue</button>
            <button className="btn sm" onClick={runQueue} disabled={queueBusy || busy || !queue.length}>{queueBusy ? <><span className="spin" /> Running…</> : `▶ Run Queue (${queue.length})`}</button>
          </div>
          {queue.length ? <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>{queue.map((b, i) => <div key={i}>{i + 1}. {b.label}</div>)}</div> : null}
          {queueRes ? <table style={{ marginTop: 6 }}><thead><tr><th>Batch</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{queueRes.map((b: any, i: number) => (
              <tr key={i}><td>{b.label}</td><td style={{ color: b.passed ? "var(--ok)" : undefined }}>{b.error || b.skipped ? "—" : b.passed}</td>
                <td className="mut">{b.error ? <span style={{ color: "var(--bad)", fontSize: 11 }}>{b.error}</span> : b.skipped || b.simulated}</td></tr>))}</tbody></table> : null}

          {!transferStudy&&hasBothDelays ? <>
          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Walk-Forward (Both Delays)</div>
          <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>Run the expressions at delay 1 AND delay 0 in {R.ctx.region} — an alpha that passes both is far more robust.</div>
          <button className="btn ghost sm" onClick={runWalkForward} disabled={wfBusy || busy}>{wfBusy ? <><span className="spin" /> Running…</> : "▶ Walk-Forward Check"}</button>
          {wfRes ? <table style={{ marginTop: 8 }}><thead><tr><th>Delay</th><th>Universe</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{wfRes.map((d: any, i: number) => (
              <tr key={i}><td><b>D{d.delay}</b></td><td className="mut">{d.universe || "—"}</td>
                <td style={{ color: d.passed ? "var(--ok)" : undefined }}>{d.error || d.skipped ? "—" : d.passed}</td>
                <td className="mut">{d.error ? <span style={{ color: "var(--bad)", fontSize: 11 }}>{d.error}</span> : d.skipped ? <span style={{ fontSize: 11 }}>{d.skipped}</span> : d.simulated}</td></tr>))}</tbody></table> : null}
          </> : null}
        </div>
      </div>
    </div>
  );
}
