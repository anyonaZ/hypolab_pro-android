import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";

// The research context that flows into every screen: market coordinates plus the working
// set of datasets and fields. Persisted to localStorage so a reload never loses the setup.

export interface Ctx { instrument: string; region: string; delay: number; universe: string; }
export interface Dataset {
  id: string; name?: string; description?: string; coverage?: number;
  valueScore?: number; alphaCount?: number; category_id?: string; category_name?: string;
}
export interface Field {
  id: string; description?: string; type?: string; alphaCount?: number;
  is_virgin?: boolean; dataset_id?: string; prefix?: string; coverage?: number; dateCoverage?: number;
}
export interface OptRecord {
  instrument: string; region: string; delay: number; universes: string[]; neutralizations: string[];
}

// A cross-screen guided-workflow handoff (e.g. Strategy Atlas → Data Explorer → Generation).
export interface Flow { goal: string; datasets: string[]; promptName: string; promptBody: string; }
export interface TransferStudy {
  id:number; name:string; mode:"regions"|"delays"; instrument:string; status:string;
  current_stage:string; active_job_id:string; contexts:(Ctx&{neutralizations?:string[]})[]; datasets:Dataset[];
  selected_dataset_ids:string[]; fields:Field[]; selected_field_ids:string[]; settings?:Record<string,any>;
}

interface Research {
  ctx: Ctx;
  setCtx: (patch: Partial<Ctx>) => void;
  options: OptRecord[];
  datasets: Dataset[]; setDatasets: (d: Dataset[]) => void;
  fields: Field[]; setFields: (f: Field[]) => void;
  selDatasets: string[]; setSelDatasets: (v: string[]) => void;
  selFields: string[]; setSelFields: (v: string[]) => void;
  pending: string[]; setPending: (v: string[]) => void;   // expressions handed off to Simulation
  evolutionPending: string[]; setEvolutionPending: (v: string[]) => void;
  transferStudy: TransferStudy|null; setTransferStudy:(v:TransferStudy|null)=>void; exitTransferStudy:()=>Promise<void>;
  flow: Flow; setFlow: (f: Partial<Flow>) => void; clearFlow: () => void;
  // derived option helpers
  regions: (inst: string) => string[];
  delays: (inst: string, region: string) => number[];
  universes: (inst: string, region: string, delay: number) => string[];
  instruments: () => string[];
}

const DEFAULT_CTX: Ctx = { instrument: "EQUITY", region: "USA", delay: 1, universe: "TOP3000" };
const KEY = "ace2-research";
const NORMAL_KEY = "ace2-normal-research";

const ResearchCtx = createContext<Research>(null as unknown as Research);
export const useResearch = () => useContext(ResearchCtx);

function load(): any {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}

export function ResearchProvider({ children }: { children: ReactNode }) {
  const saved = load();
  const [ctx, setCtxState] = useState<Ctx>({ ...DEFAULT_CTX, ...(saved.ctx || {}) });
  const [options, setOptions] = useState<OptRecord[]>(saved.options || []);
  // Drop a stale cached dataset list that predates category tags (none of the rows carry a
  // category) so it can't hide the category badges — it'll be re-fetched fresh on demand.
  const _rawDs: Dataset[] = saved.datasets || [];
  const _freshDs = _rawDs.length && !_rawDs.some((d: any) => d.category_id || d.category_name) ? [] : _rawDs;
  const [datasets, setDatasets] = useState<Dataset[]>(_freshDs);
  const [fields, setFields] = useState<Field[]>(saved.fields || []);
  const [selDatasets, setSelDatasets] = useState<string[]>(saved.selDatasets || []);
  const [selFields, setSelFields] = useState<string[]>(saved.selFields || []);
  const [pending, setPending] = useState<string[]>(saved.pending || []);
  const [evolutionPending, setEvolutionPending] = useState<string[]>([]);
  const [transferStudy, setTransferStudyState] = useState<TransferStudy|null>(null);
  const EMPTY_FLOW: Flow = { goal: "", datasets: [], promptName: "", promptBody: "" };
  const [flow, setFlowState] = useState<Flow>(saved.flow || EMPTY_FLOW);

  useEffect(() => {
    api.get<{ records: OptRecord[] }>("/data/options").then((d) => { if(d.records?.length)setOptions(d.records); });
    api.get<any>("/regions/studies/active").then((d)=>{
      const study=d.study as TransferStudy|null; if(!study)return;
      setTransferStudyState(study);
      if(study.contexts?.[0])setCtxState(c=>({...c,...study.contexts[0],instrument:study.instrument||c.instrument||"EQUITY"}));
      if(study.datasets?.length)setDatasets(study.datasets);
      if(study.fields?.length)setFields(study.fields);
      setSelDatasets(study.selected_dataset_ids||[]);setSelFields(study.selected_field_ids||[]);
    });
  }, []);

  // persist (skip the big datasets/fields arrays if they blow the quota)
  useEffect(() => {
    const snap = { ctx, options, selDatasets, selFields, datasets, fields, pending, flow };
    if(transferStudy)return;
    try { localStorage.setItem(KEY, JSON.stringify(snap)); localStorage.setItem(NORMAL_KEY,JSON.stringify(snap)); }
    catch { try { localStorage.setItem(KEY, JSON.stringify({ ctx, selDatasets, selFields, pending, flow })); } catch {} }
  }, [ctx, options, selDatasets, selFields, datasets, fields, pending, flow,transferStudy]);

  const setCtx = (patch: Partial<Ctx>) => setCtxState((c) => ({ ...c, ...patch }));
  const setFlow = (patch: Partial<Flow>) => setFlowState((f) => ({ ...f, ...patch }));
  const clearFlow = () => setFlowState(EMPTY_FLOW);
  const setTransferStudy=(study:TransferStudy|null)=>{
    if(study&&!transferStudy){try{localStorage.setItem(NORMAL_KEY,JSON.stringify({ctx,options,selDatasets,selFields,datasets,fields,pending,flow}))}catch{}}
    setTransferStudyState(study); if(!study)return;
    if(study.contexts?.[0])setCtxState(c=>({...c,...study.contexts[0],instrument:study.instrument||c.instrument||"EQUITY"}));
    setDatasets(study.datasets||[]);setFields(study.fields||[]);
    setSelDatasets(study.selected_dataset_ids||[]);setSelFields(study.selected_field_ids||[]);
  };
  const exitTransferStudy=async()=>{const closing=transferStudy;if(closing)await api.post(`/regions/studies/${closing.id}/deactivate`);let normal:any={};try{normal=JSON.parse(localStorage.getItem(NORMAL_KEY)||localStorage.getItem(KEY)||"{}")}catch{}setTransferStudyState(null);if(normal.ctx)setCtxState(normal.ctx);setDatasets(normal.datasets||[]);setFields(normal.fields||[]);setSelDatasets(normal.selDatasets||[]);setSelFields(normal.selFields||[]);setPending(normal.pending||[]);if(normal.flow)setFlowState(normal.flow);if(closing)localStorage.removeItem(`ace2-transfer-sim:${closing.id}`)};
  useEffect(()=>{if(transferStudy&&pending.length)api.post(`/regions/studies/${transferStudy.id}/candidates`,{expressions:pending,origin:"research-handoff"})},[pending,transferStudy?.id]);
  useEffect(()=>{if(transferStudy&&evolutionPending.length)api.post(`/regions/studies/${transferStudy.id}/candidates`,{expressions:evolutionPending,origin:"evolution-handoff"})},[evolutionPending,transferStudy?.id]);
  // Keep a transfer comparison alive and synchronized even while the user works on another route.
  // A backend restart loses only the thread; the persisted stage checkpoints are resumed here.
  useEffect(()=>{
    if(!transferStudy?.active_job_id)return;
    let stopped=false;
    const refresh=async()=>{
      const j=await api.get<any>(`/regions/jobs/${transferStudy.active_job_id}`);
      if(stopped)return;
      if(j.error&&j.status===undefined){
        const resumed=await api.post<any>(`/regions/studies/${transferStudy.id}/compare/${transferStudy.current_stage}`);
        if(resumed.job_id)setTransferStudyState(s=>s?{...s,active_job_id:resumed.job_id}:s);
        return;
      }
      if(j.status!=="running"){
        const fresh=await api.get<any>(`/regions/studies/${transferStudy.id}`);
        if(!fresh.error)setTransferStudy(fresh);
      }
    };
    refresh();const timer=window.setInterval(refresh,2000);
    return()=>{stopped=true;window.clearInterval(timer)};
  },[transferStudy?.active_job_id]);

  const uniq = <T,>(a: T[]) => [...new Set(a)];
  const value = useMemo<Research>(() => ({
    ctx, setCtx, options, datasets, setDatasets, fields, setFields,
    selDatasets, setSelDatasets, selFields, setSelFields, pending, setPending, evolutionPending, setEvolutionPending,
    transferStudy,setTransferStudy,exitTransferStudy,
    flow, setFlow, clearFlow,
    instruments: () => uniq(options.map((r) => r.instrument)),
    regions: (inst) => uniq(options.filter((r) => !inst || r.instrument === inst).map((r) => r.region)),
    delays: (inst, region) =>
      uniq(options.filter((r) => r.instrument === inst && r.region === region).map((r) => r.delay)).sort((a, b) => a - b),
    universes: (inst, region, delay) => {
      let rr = options.filter((r) => r.instrument === inst && r.region === region && r.delay === delay);
      if (!rr.length) rr = options.filter((r) => r.instrument === inst && r.region === region);
      return uniq(rr.flatMap((r) => r.universes));
    },
  }), [ctx, options, datasets, fields, selDatasets, selFields, pending, evolutionPending, flow,transferStudy]);

  return <ResearchCtx.Provider value={value}>{children}</ResearchCtx.Provider>;
}
