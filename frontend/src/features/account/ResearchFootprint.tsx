import {useEffect,useMemo,useState} from "react";
import {useNavigate} from "react-router-dom";
import {api} from "../../lib/api";
import {useResearch} from "../../lib/store";
import {useToast} from "../../lib/toast";

export function ResearchFootprint(){
 const R=useResearch(),nav=useNavigate(),{toast,toastErr}=useToast();
 const [scope,setScope]=useState(currentQuarter),[data,setData]=useState<any>({}),[query,setQuery]=useState(""),[region,setRegion]=useState("all"),[focus,setFocus]=useState("pyramids"),[busy,setBusy]=useState(false);
 const load=async()=>{const d=await api.get<any>(`/account-research/report?quarter=${scope}`);d.error?toastErr(d.error):setData(d)};
 useEffect(()=>{load()},[scope]);
 const sync=async()=>{setBusy(true);const d=await api.post<any>("/account-research/sync",{quarter:scope,include_submitted_regular:true});if(d.error){setBusy(false);return toastErr(d.error)}for(;;){const j=(await api.get<any>("/meta/jobs")).jobs?.find((x:any)=>x.id===d.job_id);if(j&&j.status!=="running"){setBusy(false);j.status==="done"?(await load(),toast(`Synchronized ${j.result?.fetched||0} submitted REGULAR alphas and account metrics.`,"ok")):toastErr(j.error||j.status);break}await new Promise(r=>setTimeout(r,1200))}};
 const regions=useMemo(()=>[...new Set((data.recent||[]).map((x:any)=>x.region).filter(Boolean))] as string[],[data]);
 const rows=useMemo(()=>(data.recent||[]).filter((x:any)=>(region==="all"||x.region===region)&&`${x.id} ${x.expression} ${x.region} ${x.status}`.toLowerCase().includes(query.toLowerCase())),[data,query,region]);
 const usage=data.authoritative_usage||{},activities=data.activities||{};
 const pyramids=Array.isArray(activities.pyramid_alphas)?activities.pyramid_alphas:[];
 const multipliers=Array.isArray(activities.pyramid_multipliers)?activities.pyramid_multipliers:[];
 const pyramidKey=(x:any)=>`${x.region}|${x.delay}|${x.category?.id||x.category?.name||""}`;
 const multiplierByKey=new Map(multipliers.map((x:any)=>[pyramidKey(x),Number(x.multiplier)||1]));
 // BRAIN activates a pyramid after three qualifying alphas. A nonzero count can
 // therefore still be progress toward activation rather than an activated slot.
 const activatedPyramids=pyramids.filter((x:any)=>Number(x.alphaCount)>=3);
 const pyramidSubmissions=activatedPyramids.reduce((n:number,x:any)=>n+Number(x.alphaCount||0),0);
 const pyramidRows=activatedPyramids.map((x:any)=>({key:`${x.region} D${x.delay} · ${x.category?.name||x.category?.id||"Pyramid"}`,count:Number(x.alphaCount)||0,multiplier:multiplierByKey.get(pyramidKey(x))})).sort((a:any,b:any)=>b.count-a.count);
 const tabs:any[]=[["pyramids","Pyramids"],["markets","Markets"]];if(usage.operators?.available)tabs.push(["operators","Operators"]);if(usage.fields?.available)tabs.push(["fields","Fields"]);
 const ranked=focus==="pyramids"?pyramidRows:focus==="markets"?(data.markets||[]):(usage[focus]?.items||[]);
 const useAlpha=(x:any,destination:"evolution"|"simulate")=>{R.setCtx({region:x.region||R.ctx.region,delay:Number(x.delay??R.ctx.delay),universe:x.universe||R.ctx.universe});if(destination==="evolution"){R.setEvolutionPending([x.expression]);nav("/evolution")}else{R.setPending([x.expression]);nav("/simulate")}};
 return <div className="footprint-terminal">
  <header className="terminal-title compact-title"><div><span className="eyebrow">ACCOUNT RESEARCH / CURRENT QUARTER</span><h1>Research Footprint</h1><p>Review submitted REGULAR research and verified account activity. Operator and field counts appear only when BRAIN exposes an authoritative metric.</p></div><div className="footprint-controls"><select value={scope} onChange={e=>setScope(e.target.value)}>{recentQuarters().map(q=><option key={q} value={q}>{q}{q===currentQuarter()?" · current":""}</option>)}<option value="ALL">All time</option></select><button className="btn" disabled={busy} onClick={sync}>{busy?<><span className="spin"/> Synchronizing…</>:"Sync account metrics"}</button></div></header>
  <div className="footprint-strip"><Metric n={data.alphas||0} l={scope==="ALL"?"submitted regular · all time":"submitted regular · quarter"}/><Metric n={sum(activities.simulations)} l="simulations"/><Metric n={sum(activities.submissions)} l="submissions"/><Metric n={usage.operators?.available?usage.operators.count:"—"} l="operators · API"/><Metric n={usage.fields?.available?usage.fields.count:"—"} l="fields · API"/></div>
  <section className="footprint-api-evidence"><div><span>Alpha summary</span><b>{summaryLabel(activities.alpha_summary)}</b></div><div><span>Activated pyramids</span><b>{pyramids.length?activatedPyramids.length:unavailableLabel(activities.pyramid_alphas)}</b></div><div><span>Pyramid submissions</span><b>{pyramids.length?pyramidSubmissions:unavailableLabel(activities.pyramid_alphas)}</b></div></section>
  <div className="footprint-body"><section className="footprint-ledger"><div className="dx-head"><b>Submitted REGULAR alphas</b><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Find alpha, expression or market"/><select value={region} onChange={e=>setRegion(e.target.value)}><option value="all">All regions</option>{regions.map(x=><option key={x}>{x}</option>)}</select></div><div className="footprint-table"><table><thead><tr><th>alpha / expression</th><th>market</th><th>status</th><th>Sharpe</th><th>Fitness</th><th>actions</th></tr></thead><tbody>{rows.map((x:any)=><tr key={x.id}><td><b>{x.id}</b><code title={x.expression}>{x.expression||"expression unavailable"}</code></td><td>{x.region} D{x.delay}<small>{x.universe}</small></td><td><span className="badge">{x.status||"submitted"}</span></td><td>{x.metrics?.sharpe??"-"}</td><td>{x.metrics?.fitness??"-"}</td><td><div className="footprint-row-actions"><button disabled={!x.expression} onClick={()=>useAlpha(x,"evolution")}>Evolve</button><button disabled={!x.expression} onClick={()=>useAlpha(x,"simulate")}>Simulate</button></div></td></tr>)}</tbody></table></div></section>
  <aside className="footprint-insight"><div className="footprint-tabs">{tabs.map(([k,l])=><button key={k} className={focus===k?"on":""} onClick={()=>setFocus(k)}>{l}</button>)}</div><p className="mut">{focus==="pyramids"?`Activated pyramids in ${scope}. Activation requires at least 3 qualifying alphas; each row shows its alpha count.`:focus==="markets"?"Submitted REGULAR research grouped by market.":`Authoritative ${focus} usage supplied by the account API.`}</p><div className="rank-list">{ranked.length?ranked.slice(0,100).map((x:any,i:number)=><div key={x.key}><i>{i+1}</i><code title={x.key}>{x.key}{x.multiplier>1?` · ${x.multiplier}×`:""}</code><b>{x.count}</b><span style={{width:`${100*x.count/Math.max(1,ranked[0]?.count||1)}%`}}/></div>):<div className="empty">No verified {focus} evidence for this period.</div>}</div></aside></div>
 </div>
}
function Metric({n,l}:{n:any,l:string}){return <div><b>{n}</b><span>{l}</span></div>}
function sum(x:any){return Array.isArray(x)?x.reduce((n,r)=>n+Number(r?.[1]||0),0):"-"}
function unavailableLabel(x:any){return x?.unavailable?"Unavailable":"No activity"}
function summaryLabel(x:any){if(!x)return "Not synchronized";if(x.unavailable)return "Unavailable";if(x.is!=null||x.os!=null||x.prod!=null)return `IS ${x.is??0} · OS ${x.os??0} · PROD ${x.prod??0}`;const n=x.count??x.total??x.alphaCount??x.submitted;return n!=null?`${n} alphas`:"Available"}
function currentQuarter(){const d=new Date();return `${d.getFullYear()}-Q${Math.floor(d.getMonth()/3)+1}`}
function recentQuarters(){const d=new Date(),out:string[]=[];let y=d.getFullYear(),q=Math.floor(d.getMonth()/3)+1;for(let i=0;i<4;i++){out.push(`${y}-Q${q}`);if(--q<1){q=4;y--}}return out}
