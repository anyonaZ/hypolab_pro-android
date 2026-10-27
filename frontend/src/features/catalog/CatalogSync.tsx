import {useEffect,useMemo,useState} from "react";
import {api} from "../../lib/api";
import {usePersistentState} from "../../lib/persist";
import {useToast} from "../../lib/toast";

const toggle=(a:string[],x:string)=>a.includes(x)?a.filter(v=>v!==x):[...a,x];

export function CatalogSync(){
 const {toast,toastErr}=useToast();
 const [matrix,setMatrix]=useState<any>({regions:[]}),[runs,setRuns]=useState<any[]>([]),[active,setActive]=usePersistentState("catalog:region","USA"),[selected,setSelected]=usePersistentState<string[]>("catalog:contexts",[]),[job,setJob]=usePersistentState("catalog:job",""),[busy,setBusy]=useState(false),[progress,setProgress]=useState<any>(null);
 const load=async()=>{const [m,r]=await Promise.all([api.get<any>("/catalog/matrix"),api.get<any>("/catalog/runs")]);if(!m.error)setMatrix(m);if(!r.error)setRuns(r.runs||[])};
 useEffect(()=>{load();if(job){setBusy(true);poll(job)}},[]);
 const region=useMemo(()=>(matrix.regions||[]).find((x:any)=>x.region===active)||(matrix.regions||[])[0],[matrix,active]);
 useEffect(()=>{if(region&&region.region!==active)setActive(region.region)},[region?.region]);
 const chooseRegion=(name:string)=>{setActive(name);setSelected([])};
 async function poll(id:string){let s:any={};for(;;){s=await api.get<any>(`/catalog/jobs/${id}`);setProgress(s);if(s.status!=="running")break;await new Promise(r=>setTimeout(r,1200))}setBusy(false);setJob("");await load();s.status==="done"?toast(`Catalog sync complete. Datasets and datafields are ready locally.`,"ok"):s.status==="cancelled"?toast("Catalog sync paused.","warn"):toastErr(s.error||s.status)}
 async function sync(){
  if(!selected.length)return toast("Select at least one universe and delay.","warn");
  setBusy(true);
  const plan=await api.post<any>("/catalog/plan",{contexts:selected,categories:[],include_fields:true,coverage_min:0,value_min:0,request_budget:0,refresh_mode:"missing",stale_days:30});
  if(plan.error){setBusy(false);return toastErr(plan.error)}
  const d=await api.post<any>(`/catalog/runs/${plan.run_id}/start`);
  if(d.error){setBusy(false);return toastErr(d.error)}
  setJob(d.job_id);poll(d.job_id);
 }
 async function resume(id:number){setBusy(true);const d=await api.post<any>(`/catalog/runs/${id}/resume`);if(d.error){setBusy(false);return toastErr(d.error)}setJob(d.job_id);poll(d.job_id)}
 async function reset(){if(!confirm("Clear the complete local catalog and its sync history?"))return;const d=await api.del<any>("/catalog/reset");d.error?toastErr(d.error):(setSelected([]),await load(),toast("Local catalog reset.","ok"))}
 const all=(region?.contexts||[]).map((c:any)=>c.key);
 return <div className="catalog-terminal">
  <header className="terminal-title compact-title"><div><span className="eyebrow">LOCAL RESEARCH DATA</span><h1>Data Catalog</h1><p>Select a region, then choose the universe and delay combinations you use. One sync stores every available dataset and its datafields locally.</p></div><div className="catalog-actions"><button className="btn ghost sm" onClick={()=>window.open("/api/catalog/export","_blank")}>Export</button><button className="btn ghost sm danger" onClick={reset}>Reset catalog</button></div></header>
  <div className="catalog-workspace"><aside className="catalog-regions"><div className="section">1 · Select a region</div><p className="mut">Work one region at a time for clearer progress. Previously completed scopes are skipped automatically.</p>{(matrix.regions||[]).map((r:any)=><button key={r.region} className={active===r.region?"active":""} onClick={()=>chooseRegion(r.region)}><b>{r.region}</b><span>{r.ready?"available locally":"choose scopes"}</span><i/></button>)}</aside>
   <main className="catalog-scope"><div className="catalog-scope-head"><div><span className="eyebrow">2 · SELECT UNIVERSES AND DELAYS</span><h2>{region?.region||"Choose a region"}</h2></div></div>
    <div className="catalog-universes"><div className="dx-head"><b>Available account scopes</b><span className="mut">Each selected scope includes both datasets and datafields.</span><button className="text-action" onClick={()=>setSelected(all)}>Select all in region</button><button className="text-action" onClick={()=>setSelected([])}>Clear</button></div>{region?.contexts?.map((c:any)=><button key={c.key} onClick={()=>setSelected(toggle(selected,c.key))} className={`${selected.includes(c.key)?"selected ":""}${c.fields_ready?"ready":c.errors?.length?"failed":""}`}><span>D{c.delay}</span><b>{c.universe}</b><small>{c.fields_ready?"datasets + fields ready":c.datasets_ready?"finishing fields":c.errors?.length?"retry available":"not synchronized"}</small><i>{selected.includes(c.key)?"selected":""}</i></button>)}</div>
    <footer className="catalog-runbar"><span>{selected.length} universe/delay scope{selected.length===1?"":"s"} selected in {region?.region}</span><button className="btn catalog-sync-primary" onClick={sync} disabled={busy||!selected.length}>{busy?<><span className="spin"/> {progress?.message||"Synchronizing datasets and fields…"}</>:"Sync selected data"}</button>{busy?<button className="btn ghost" onClick={()=>api.post(`/catalog/jobs/${job}/cancel`)}>Pause</button>:null}</footer>
    <div className="catalog-plan">Synchronization continues until every selected scope is complete. You may leave this screen; progress remains in the job menu and incomplete work can be resumed.</div>
    {runs.some(x=>["paused","error","planned"].includes(x.status))?<details className="catalog-history"><summary>Resume an interrupted sync</summary>{runs.filter(x=>["paused","error","planned"].includes(x.status)).map(x=><div key={x.id}><b>#{x.id} {x.status}</b><span>{x.requests_used} requests completed</span><button className="text-action" disabled={busy} onClick={()=>resume(x.id)}>Resume</button></div>)}</details>:null}
   </main></div>
 </div>
}
