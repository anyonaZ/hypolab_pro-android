import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch, type Dataset } from "../../lib/store";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";
import { usePersistentState } from "../../lib/persist";
import "./data.css";

// Delay 0 is a real, valid delay but 0 is falsy — parse without the `|| default` trap.
const intOr = (v: any, d: number) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : d; };

// The category tag comes ONLY from the real fetched category (never guessed from the id, which
// doesn't reliably encode it) — better no tag than a wrong one.
const catOf = (d: Dataset) => d.category_id || d.category_name || "";

// Coarse data-nature label (mirrors the backend heuristic) so you can see at a glance whether a
// field is continuous/ratio/count/event/group/vector — and pick operators that fit it.
function fieldNature(f: any): string {
  if (String(f.type).toUpperCase() === "VECTOR") return "vector";
  const d = ((f.description || "") + " " + (f.id || "")).toLowerCase();
  const has = (...ws: string[]) => ws.some((w) => d.includes(w));
  if (has("flag", "indicator", "dummy", "binary", "1 if", "event", "announc", "rating", "recommendation", "upgrade", "downgrade", "surprise", "beat", "miss")) return "event";
  if (has("number of", "count", "# of", "days since", "days to", "days from", "quarters", "how many")) return "count";
  if (has("ratio", "percent", "%", "yield", "margin", "growth", " rate", "per share", "return", "score")) return "ratio";
  if (has("sector", "industry", "country", "currency", "exchange", "classification", "grouping")) return "group";
  return "continuous";
}
function fieldModeEstimate(fields:any[]):number { return Math.max(4,new Set(fields.map(fieldNature)).size*6); }

// Tapping anywhere on a dataset/field row toggles it (easier than the small checkbox on a
// tablet). Taps on the row's own controls and links keep their normal behaviour.
function rowTap(e: { target: EventTarget | null }, toggle: () => void) {
  const el = e.target as HTMLElement | null;
  if (el?.closest("input, button, a, select, textarea, label")) return;
  if (window.getSelection()?.toString()) return;   // don't toggle while selecting text
  toggle();
}

export function DataExplorer() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [busyDs, setBusyDs] = useState(false);
  const [busyF, setBusyF] = useState(false);
  const datasetRequest = useRef(0);
  const fieldRequest = useRef(0);
  const [catalogCategories, setCatalogCategories] = useState<string[]>([]);
  const [catalogScope,setCatalogScope]=useState<any>(null);

  // Guided flow: carry a pushed strategy's prompt into Generation once its data is ready.
  function continueToGeneration() {
    localStorage.setItem("ace2:gen:prompt", JSON.stringify(R.flow.promptBody || ""));
    localStorage.setItem("ace2:gen:reqds", JSON.stringify(R.flow.datasets || []));
    R.clearFlow();
    nav("/generate");
  }

  // dataset panel state
  const [dsFilter, setDsFilter] = usePersistentState("data:dataset-filter", "");
  const [dsCat, setDsCat] = usePersistentState("data:dataset-category", "");
  const [covMin, setCovMin] = usePersistentState("data:coverage-min", 0);
  const [valMin, setValMin] = usePersistentState("data:value-min", 0);
  // field panel state
  const [fQuery, setFQuery] = usePersistentState("data:field-filter", "");
  const [fType, setFType] = usePersistentState<"ALL" | "VECTOR" | "MATRIX" | "GROUP">("data:field-type", "ALL");
  const [fVirgin, setFVirgin] = usePersistentState("data:virgin", false);
  const [fPrefix, setFPrefix] = usePersistentState<string[]>("data:prefixes", []);
  const [dateCovMin, setDateCovMin] = usePersistentState("data:date-coverage-min", 0);
  const [alphaMin, setAlphaMin] = usePersistentState("data:alpha-min", 0);
  const [alphaMax, setAlphaMax] = usePersistentState("data:alpha-max", 0);
  const [sampleN, setSampleN] = usePersistentState("data:sample", 50);
  const [compare, setCompare] = useState(false);
  const [collectionName, setCollectionName] = useState("");

  const insts = R.instruments();
  const regions = R.regions(R.ctx.instrument);
  const delays = R.delays(R.ctx.instrument, R.ctx.region);
  const universes = R.universes(R.ctx.instrument, R.ctx.region, R.ctx.delay);
  const contextKey = `${R.ctx.instrument}|${R.ctx.region}|${R.ctx.delay}|${R.ctx.universe}`;

  // SQLite is the durable metadata source. Mount a previously fetched context immediately when
  // the user changes market coordinates, without spending another BRAIN request. The request id
  // prevents a slower response for the previous region from replacing the current region's rows.
  useEffect(() => {
    const requestId = ++datasetRequest.current;
    setBusyDs(true);
    R.setFields([]);
    const q = new URLSearchParams({
      instrument: R.ctx.instrument, region: R.ctx.region,
      delay: String(R.ctx.delay), universe: R.ctx.universe,
    });
    api.get<{datasets:Dataset[];catalog?:any;error?:string}>(`/catalog/datasets?${q}`).then((d) => {
      if (requestId !== datasetRequest.current) return;
      setBusyDs(false);
      if (d.error) return;
      setCatalogScope(d.catalog||null);
      R.setDatasets(d.datasets || []);
      const present=new Set((d.datasets||[]).map(x=>x.id));
      R.setSelDatasets(R.selDatasets.filter(id=>present.has(id)));
    });
  // setDatasets is a stable React state setter exposed by the shared store.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey]);

  useEffect(() => {
    api.get<any>("/catalog/health").then((d) => {
      if (!d.error) setCatalogCategories((d.categories || []).map((x:any) => String(x.category)).filter(Boolean));
    });
  }, []);

  // Change context but always keep delay/universe VALID for the chosen instrument+region, so a
  // fetch never sends a mismatched combination (e.g. JPN with TOP3000).
  function setCtxValid(patch: { instrument?: string; region?: string; delay?: number }) {
    const inst = patch.instrument ?? R.ctx.instrument;
    const region = patch.region ?? R.ctx.region;
    const ds = R.delays(inst, region);
    let delay = patch.delay ?? R.ctx.delay;
    if (ds.length && !ds.includes(delay)) delay = ds[0];
    const us = R.universes(inst, region, delay);
    let universe = R.ctx.universe;
    if (us.length && !us.includes(universe)) universe = us[0];
    R.setCtx({ instrument: inst, region, delay, universe });
  }

  // Once options load, snap any stale/invalid delay+universe (e.g. a persisted JPN + TOP3000) to a
  // real one for the region, so the fetch request itself sends the correct universe.
  useEffect(() => {
    if (!R.options.length) return;
    const ds = R.delays(R.ctx.instrument, R.ctx.region);
    const delay = (ds.length && !ds.includes(R.ctx.delay)) ? ds[0] : R.ctx.delay;
    const us = R.universes(R.ctx.instrument, R.ctx.region, delay);
    const universe = (us.length && !us.includes(R.ctx.universe)) ? us[0] : R.ctx.universe;
    if (delay !== R.ctx.delay || universe !== R.ctx.universe) R.setCtx({ delay, universe });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [R.options.length, R.ctx.instrument, R.ctx.region]);

  // Selecting datasets mounts their saved fields immediately. No BRAIN request occurs here.
  useEffect(() => {
    const requestId=++fieldRequest.current;
    if (!R.selDatasets.length) { R.setFields([]); setBusyF(false); return; }
    setBusyF(true);
    const q = `dataset_ids=${encodeURIComponent(R.selDatasets.join(","))}&instrument=${encodeURIComponent(R.ctx.instrument)}&region=${encodeURIComponent(R.ctx.region)}&delay=${R.ctx.delay}&universe=${encodeURIComponent(R.ctx.universe)}`;
    api.get<any>(`/catalog/fields?${q}`).then((d)=>{ if(requestId===fieldRequest.current){setBusyF(false);if(!d.error){R.setFields(d.fields||[]);setFPrefix([])}} });
  }, [R.selDatasets.join("|"),R.ctx.instrument,R.ctx.region,R.ctx.delay,R.ctx.universe]);

  // ── dataset list (text filter + category + coverage/value floors) ───────────────────
  const dsCategories = useMemo(() => [...new Set([...catalogCategories, ...R.datasets.map((d) => catOf(d)).filter(Boolean)])].sort(), [R.datasets,catalogCategories]);
  const shownDatasets = R.datasets.filter((x) => {
    const q = dsFilter.toLowerCase();
    const textOk = !q || String(x.id).toLowerCase().includes(q) || String(x.name || "").toLowerCase().includes(q)
      || String(catOf(x)).toLowerCase().includes(q);
    const catOk = !dsCat || catOf(x) === dsCat;
    const covOk = !covMin || (Number(x.coverage) || 0) >= covMin;
    const valOk = !valMin || (Number(x.valueScore) || 0) >= valMin;
    return textOk && catOk && covOk && valOk;
  });
  const toggleDs = (id: string) => {
    const s = new Set(R.selDatasets);
    s.has(id) ? s.delete(id) : s.add(id);
    R.setSelDatasets([...s]);
  };
  const [dsN, setDsN] = usePersistentState("data:dataset-sample", 20);
  const selectAllDs = () => R.setSelDatasets([...new Set([...R.selDatasets, ...shownDatasets.map((d) => d.id)])]);
  const randomDs = () => {
    const pool = shownDatasets.map((d) => d.id).sort(() => Math.random() - 0.5).slice(0, Math.max(1, dsN));
    R.setSelDatasets(pool);
    toast(`Randomly selected ${pool.length} of ${shownDatasets.length} dataset(s).`);
  };

  // ── field faceting (prefix computed against the OTHER filters) ──────────────────────
  const fieldsBase = useMemo(() => R.fields.filter((f) => {
    if (fType !== "ALL" && String(f.type).toUpperCase() !== fType) return false;
    if (fVirgin && !f.is_virgin) return false;
    if (dateCovMin && (Number(f.dateCoverage) || 0) < dateCovMin) return false;
    const ac=Number(f.alphaCount)||0;
    if (alphaMin && ac < alphaMin) return false;
    if (alphaMax && ac > alphaMax) return false;
    const q = fQuery.toLowerCase();
    if (q && !(String(f.id).toLowerCase().includes(q) || String(f.description || "").toLowerCase().includes(q))) return false;
    return true;
  }), [R.fields, fType, fVirgin, fQuery, dateCovMin, alphaMin, alphaMax]);

  const prefixCounts = useMemo(() => {
    const m: Record<string, number> = {};
    fieldsBase.forEach((f) => { const p = (f.prefix || String(f.id).slice(0, 3)).toLowerCase(); if (p) m[p] = (m[p] || 0) + 1; });
    return m;
  }, [fieldsBase]);
  const prefixKeys = Object.keys(prefixCounts).sort((a, b) => prefixCounts[b] - prefixCounts[a] || a.localeCompare(b));

  const shownFields = useMemo(() =>
    fPrefix.length ? fieldsBase.filter((f) => fPrefix.includes((f.prefix || String(f.id).slice(0, 3)).toLowerCase())) : fieldsBase,
    [fieldsBase, fPrefix]);

  const toggleField = (id: string) => {
    const s = new Set(R.selFields);
    s.has(id) ? s.delete(id) : s.add(id);
    R.setSelFields([...s]);
  };
  const selectShown = () => R.setSelFields([...new Set([...R.selFields, ...shownFields.map((f) => f.id)])]);
  const sample = () => {
    const pool = shownFields.map((f) => f.id).sort(() => Math.random() - 0.5).slice(0, Math.max(1, sampleN));
    R.setSelFields(pool);
    toast(`Sampled ${pool.length} of ${shownFields.length} shown field(s).`);
  };
  // Sample an EQUAL number of fields FROM EACH dataset, so every selected dataset is represented
  // and you can test several datasets at once (not just whichever has the most fields).
  const samplePerDataset = () => {
    const byDs: Record<string, string[]> = {};
    shownFields.forEach((f) => { const d = (f as any).dataset_id || "?"; (byDs[d] = byDs[d] || []).push(f.id); });
    const picked: string[] = [];
    for (const ids of Object.values(byDs))
      picked.push(...ids.sort(() => Math.random() - 0.5).slice(0, Math.max(1, sampleN)));
    if (!picked.length) return toast("No shown fields to sample.", "warn");
    R.setSelFields([...new Set(picked)]);
    toast(`Sampled up to ${sampleN} field(s) from each of ${Object.keys(byDs).length} dataset(s) — ${picked.length} total.`, "ok");
  };
  const stratifiedSample = () => {
    const strata: Record<string, any[]> = {};
    shownFields.forEach((f:any)=>{const key=`${f.dataset_id||"?"}|${f.category||"?"}|${f.type||"?"}|${Number(f.dateCoverage||0)>=.8?"long":"short"}`;(strata[key]=strata[key]||[]).push(f)});
    const target=Math.max(1,sampleN), groups=Object.values(strata), picked:string[]=[];
    let i=0; while(picked.length<target&&groups.some(g=>g.length)){const g=groups[i++%groups.length];if(g.length)picked.push(g.splice(Math.floor(Math.random()*g.length),1)[0].id)}
    R.setSelFields([...new Set(picked)]); toast(`Stratified ${picked.length} fields across dataset, category, type, and history.`,`ok`);
  };
  async function saveCollection(){const selected=R.fields.filter(f=>R.selFields.includes(f.id));if(!selected.length)return toast("Select fields first.","warn");const d=await api.post<any>("/research/collections",{name:collectionName||`Field collection · ${R.ctx.region}`,theme:dsCat,description:`Reusable ${R.ctx.region} D${R.ctx.delay} research data nugget`,fields:selected,context:R.ctx});if(d.error)return toastErr(d.error);setCollectionName("");toast(`Saved ${d.name}.`,`ok`)}

  const nSelV = R.fields.filter((f) => R.selFields.includes(f.id) && String(f.type).toUpperCase() === "VECTOR").length;
  const nSelDs = new Set(R.fields.filter((f) => R.selFields.includes(f.id)).map((f) => (f as any).dataset_id).filter(Boolean)).size;

  return (
    <div className="dx data-page route-workspace">
      {R.flow.goal ? (
        <div className="panel" style={{ padding: "9px 14px", borderColor: "var(--acc)", background: "var(--acc-weak)" }}>
          <div className="dx-head" style={{ margin: 0 }}>
            <b style={{ color: "var(--acc)" }}>Guided · {R.flow.promptName || "Strategy"}</b>
            <span className="mut" style={{ fontSize: 12 }}>
              {R.flow.datasets.length ? <>Fetch {R.flow.datasets.map((d) => <code key={d}>{d}</code>).reduce((a, b) => <>{a}, {b}</>)}, tick them, select fields, then continue.</> : "Fetch data, select fields, then continue."}
            </span>
            <button className="btn sm" style={{ marginLeft: "auto" }} disabled={!R.selFields.length} onClick={continueToGeneration}>Continue To Generation →</button>
            <button className="btn ghost sm" onClick={() => R.clearFlow()}>Dismiss</button>
          </div>
        </div>
      ) : null}
      {/* context bar */}
      <div className="panel dx-ctx">
        <div className="dx-ctx-row">
          <label className="fld"><span>Instrument</span>
            <select value={R.ctx.instrument} onChange={(e) => setCtxValid({ instrument: e.target.value })}>
              {insts.map((i) => <option key={i}>{i}</option>)}
            </select></label>
          <label className="fld"><span>Region</span>
            <select value={R.ctx.region} onChange={(e) => setCtxValid({ region: e.target.value })}>
              {regions.map((r) => <option key={r}>{r}</option>)}
            </select></label>
          <label className="fld"><span>Delay</span>
            <select value={String(R.ctx.delay)} onChange={(e) => setCtxValid({ delay: intOr(e.target.value, 1) })}>
              {delays.map((d) => <option key={d} value={String(d)}>{d}</option>)}
            </select></label>
          <label className="fld"><span>Universe</span>
            <select value={R.ctx.universe} onChange={(e) => R.setCtx({ universe: e.target.value })}>
              {universes.map((u) => <option key={u}>{u}</option>)}
            </select></label>
          <div className={`catalog-source ${catalogScope?.datasets_ready?"ready":"missing"}`}><b>{busyDs?<><span className="spin"/> Loading catalog…</>:catalogScope?.datasets_ready?`${R.datasets.length} synchronized datasets`:"Catalog required for this market"}</b>{catalogScope?.errors?.length?<span className="badge bad">sync failed</span>:null}<button className="catalog-open-btn" onClick={()=>nav("/catalog")}>{catalogScope?.datasets_ready?"Manage catalog":"Synchronize market"}<span>→</span></button></div>
        </div>
      </div>

      {/* Post-selection guidance: once fields are picked, tell the user what to do next with
          buttons that route correctly. Hidden during a guided strategy flow (which has its own bar). */}
      {R.selFields.length && !R.flow.goal ? (
        <div className="panel dx-next">
          <div className="dx-next-lead">
            <b>{R.selFields.length} field{R.selFields.length === 1 ? "" : "s"} selected</b>
            <span className="mut">{nSelDs} dataset{nSelDs === 1 ? "" : "s"} · {nSelV}V / {R.selFields.length - nSelV}M · {R.ctx.region} D{R.ctx.delay} — what next?</span>
          </div>
          <div className="dx-next-actions">
            <button className="btn sm" onClick={() => nav("/generate")} title="Research hypotheses and generate alphas from these fields">✦ Alpha Lab →</button>
            <button className="btn ghost sm" onClick={() => nav("/templates")} title="Hand-build {field} templates and expand them over these fields">⛭ Template Studio →</button>
            <button className="btn ghost sm" onClick={() => nav("/strategies")} title="AI + semantic per-field hypotheses & operators for these fields">🧭 Strategy Lab →</button>
          </div>
        </div>
      ) : null}

      <div className="dx-split">
        {/* datasets */}
        <div className="panel dx-panel">
          <div className="dx-head">
            <b>Datasets</b>
            <span className="mut">{R.selDatasets.length} selected</span>
            <input placeholder="filter id / name / category" value={dsFilter}
              onChange={(e) => setDsFilter(e.target.value)} style={{ maxWidth: 180, marginLeft: "auto" }} />
            <span className="mut">{busyF?<><span className="spin"/> Mounting saved fields…</>:"Local catalog"}</span>
          </div>
          <>
            <div className="dx-filters wrap">
              <select value={dsCat} onChange={(e) => setDsCat(e.target.value)} style={{ maxWidth: 150 }} title="Filter by category">
                <option value="">all categories ({dsCategories.length})</option>
                {dsCategories.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <label className="inline-score"><span>Coverage ≥</span>
                <NumberInput min={0} fallback={0} step="0.05" value={covMin} onChange={setCovMin} /></label>
              <label className="inline-score"><span>Value score ≥</span>
                <NumberInput min={0} fallback={0} step="1" value={valMin} onChange={(v)=>setValMin(Math.round(v))} /></label>
              <span className="mut" style={{ fontSize: 11, alignSelf: "center" }}>{shownDatasets.length} shown</span>
            </div>
            <div className="dx-filters">
              <button className="btn ghost sm" onClick={selectAllDs}>Select all shown</button>
              <button className="btn ghost sm" onClick={() => R.setSelDatasets([])}>Clear</button>
              <button className="btn ghost sm" onClick={randomDs}>Random</button>
              <NumberInput min={1} fallback={20} value={dsN} onChange={setDsN} style={{ width: 64 }} />
            </div></>
          <div className="panel-scroll">
            {!R.datasets.length ? <div className="empty">This context has not been synchronized. Open Data Catalog to fetch it from BRAIN once.</div> :
              <table><thead><tr><th></th><th>dataset</th><th>value</th><th>alphas</th></tr></thead>
                <tbody>{shownDatasets.map((d) => (
                  <tr key={d.id} className={"tap-row" + (R.selDatasets.includes(d.id) ? " on" : "")} onClick={(e) => rowTap(e, () => toggleDs(d.id))}>
                    <td><input type="checkbox" checked={R.selDatasets.includes(d.id)} onChange={() => toggleDs(d.id)} /></td>
                    <td title={d.description || d.name || d.id}><code>{d.id}</code>
                      {catOf(d) ? <span className="cat-tag" title="category">{catOf(d)}</span> : null}
                      <div className="mut sub">{(d.name || d.description || "").slice(0, 46)}</div></td>
                    <td>{d.valueScore == null ? "—" : Math.round(Number(d.valueScore))}</td><td>{d.alphaCount ?? "—"}</td>
                  </tr>))}</tbody></table>}
          </div>
        </div>

        {/* fields */}
        <div className="panel dx-panel">
          <div className="dx-head">
            <b>Fields</b>
            <span className="mut">{R.selFields.length} selected · {nSelV}V</span>
          </div>
          <div className="dx-filters dx-field-filterbar">
            <input placeholder="filter id / description" value={fQuery} onChange={(e) => setFQuery(e.target.value)} style={{ flex: 1, minWidth: 90 }} />
            <select aria-label="Data type" title="Filter fields by data type" value={fType} onChange={(e)=>setFType(e.target.value as typeof fType)}>
              <option value="ALL">All data types</option><option value="VECTOR">VECTOR</option><option value="MATRIX">MATRIX</option><option value="GROUP">GROUP</option>
            </select>
            <select aria-label="Field prefix" title="Filter fields by identifier prefix" value={fPrefix[0]||""} onChange={(e)=>setFPrefix(e.target.value?[e.target.value]:[])}>
              <option value="">All prefixes ({prefixKeys.length})</option>{prefixKeys.map((p)=><option value={p} key={p}>{p} · {prefixCounts[p]} fields</option>)}
            </select>
            <select aria-label="Date coverage" title="Prefer fields populated across most of BRAIN's available history" value={dateCovMin} onChange={e=>setDateCovMin(+e.target.value)}>
              <option value="0">Any date coverage</option><option value="0.6">≥ 60% history</option><option value="0.8">≥ 80% history</option><option value="0.9">≥ 90% history</option>
            </select>
            <label className="inline-score" title="Minimum number of existing alphas using the field"><span>Alpha min</span><NumberInput min={0} fallback={0} value={alphaMin} onChange={v=>setAlphaMin(Math.max(0,Math.round(v)))}/></label>
            <label className="inline-score" title="Maximum existing alpha usage; zero means no ceiling"><span>Alpha max</span><NumberInput min={0} fallback={0} value={alphaMax} onChange={v=>setAlphaMax(Math.max(0,Math.round(v)))}/></label>
          </div>
          <div className="dx-filters">
            <button className={"btn ghost sm virgin-filter" + (fVirgin ? " active" : "")} onClick={() => setFVirgin((v) => !v)} title="Show only fields with no existing alpha usage">◇ Virgin only</button>
            <button className="btn ghost sm" onClick={selectShown}>Select shown</button>
            <button className="btn ghost sm" onClick={() => R.setSelFields([])}>Clear</button>
            <button className="btn ghost sm" onClick={sample}>Sample</button>
            <button className="btn ghost sm" onClick={samplePerDataset} title="Sample this many fields from EACH dataset (equal coverage)">Per dataset</button>
            <button className="btn ghost sm" onClick={stratifiedSample}>Stratified</button>
            <button className="btn ghost sm" disabled={R.selFields.length<2} onClick={()=>setCompare(v=>!v)}>Compare</button>
            <NumberInput min={1} fallback={50} value={sampleN} onChange={setSampleN} style={{ width: 64 }} />
          </div>
          <div className="panel-scroll">
            {!R.fields.length ? <div className="empty">Select synchronized datasets to mount their saved fields. If none appear, sync fields for this context in Data Catalog.</div> :
              !shownFields.length ? <div className="empty">No fields match the filter.</div> :
                <table><thead><tr><th></th><th>field</th><th>type / unit</th><th>history / quality</th><th>alphas</th></tr></thead>
                  <tbody>{shownFields.map((f) => {
                    const t = String(f.type).toUpperCase();
                    return <tr key={f.id} className={"tap-row" + (R.selFields.includes(f.id) ? " on" : "")} onClick={(e) => rowTap(e, () => toggleField(f.id))}>
                      <td><input type="checkbox" checked={R.selFields.includes(f.id)} onChange={() => toggleField(f.id)} /></td>
                      <td title={f.description || f.id}><code>{f.id}</code><div className="mut sub">{(f.description || "").slice(0, 50)}</div></td>
                      <td><span className={"badge " + (t === "VECTOR" ? "vec" : "mat")}>{t}</span>
                        <span className="badge" style={{ marginLeft: 4, opacity: 0.85 }} title="Data nature — pick operators that fit it">{fieldNature(f)}</span>
                        {f.is_virgin ? <span className="badge virgin">virgin</span> : null}<div className="mut sub">{(f as any).unit||"unit unknown"}</div></td>
                      <td title={(f as any).research_potential||"Fraction of platform history with observations"}>{(f as any).startDate?`${(f as any).startDate} → ${(f as any).endDate||"present"}`:f.dateCoverage==null?"dates unavailable":`${Math.round(Number(f.dateCoverage)*100)}% history`}<div className="mut sub">quality {(f as any).quality_score??Math.round(Number(f.dateCoverage||0)*100)} · {(f as any).reporting_lag||"lag unknown"}</div></td>
                      <td>{f.alphaCount ?? "—"}{(f as any).coverage != null ? <div className="mut sub">{Math.round((Number((f as any).coverage) || 0) * 100)}% cov</div> : null}</td>
                    </tr>;
                  })}</tbody></table>}
          </div>
          {compare?<div className="field-comparison"><b>Selected-field comparison</b>{R.fields.filter(f=>R.selFields.includes(f.id)).slice(0,12).map((f:any)=><div key={f.id}><code>{f.id}</code><span>{f.type} · {f.unit||fieldNature(f)} · {f.dateCoverage==null?"dates unavailable":`${Math.round(Number(f.dateCoverage)*100)}% history`} · {f.alphaCount||0} alphas</span><small>{f.research_potential||f.description}</small></div>)}</div>:null}
          {R.selFields.length?<div className="dx-filters"><input value={collectionName} onChange={e=>setCollectionName(e.target.value)} placeholder="Name this reusable field collection"/><button className="btn ghost sm" onClick={saveCollection}>Save data nugget</button><span className="mut">Estimated hypothesis capacity: {Math.min(999,R.selFields.length*(fieldModeEstimate(R.fields.filter(f=>R.selFields.includes(f.id)))))}</span></div>:null}
        </div>
      </div>
    </div>
  );
}
