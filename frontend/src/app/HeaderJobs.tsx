import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import "./headerjobs.css";

interface Job { id: string; kind: string; status: string; message: string; total: number; done: number; }

const LABEL: Record<string, string> = {
  generate: "Generation", simulate: "Simulation", research: "Research", strategy: "Strategy Atlas",
  super: "SuperAlpha", super_suggest: "Super suggest", super_sim: "Super sim", selection: "Selection preview",
  rewrite: "Master prompt", "push-prompt": "Save prompt", "knowledge-judge": "AI verify",
  "knowledge-xregion": "Cross-region", "cross-region-sweep": "Cross-region sweep", "batch-queue": "Batch queue",
  "walk-forward": "Walk-forward", "prod-corr": "Prod-corr", correlation: "Correlation",
  "account-research-sync": "Account Research Sync", "catalog-sync": "Data Catalog Sync",
  evolve: "Evolution Study", evolution: "Evolution Study",
};
const WORDS: Record<string,string> = { ai:"AI", api:"API", llm:"LLM", pnl:"PnL", prod:"Production", corr:"Correlation", d0:"D0", d1:"D1" };
const label = (k: string) => LABEL[k] || String(k || "background-job")
  .trim().split(/[\s_-]+/).filter(Boolean)
  .map((word) => WORDS[word.toLowerCase()] || `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`)
  .join(" ");

// Background-job indicator living in the HEADER. Shows a spinner + running count when work is in
// flight; click to see per-job progress. Toasts when a job finishes. Hidden when nothing runs.
export function HeaderJobs() {
  const { toast, toastErr } = useToast();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const prev = useRef<Record<string, string>>({});
  const seeded = useRef(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const userClosed = useRef(false);   // once the user closes it, stay closed until jobs go idle

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const d = await api.get<{ jobs: Job[] }>("/meta/jobs");
      if (!alive || d.error) return;
      const list = d.jobs || [];
      const directIds=[(()=>{try{return JSON.parse(localStorage.getItem("ace2:sim:job")||'""')}catch{return ""}})()].filter(Boolean) as string[];
      const recovered=await Promise.all(directIds.filter(id=>!list.some(j=>j.id===id)).map(id=>api.get<Job>(`/simulate/jobs/${id}`)));
      for(const job of recovered)if(job?.id&&!job.error)list.push(job);
      if (seeded.current) {
        for (const j of list) {
          const was = prev.current[j.id];
          if (was === "running" && j.status !== "running") {
            if (j.status === "done") toast(`✓ ${label(j.kind)} finished.`, "ok");
            else if (j.status === "error") toastErr(`${label(j.kind)} failed.`);
          }
        }
      }
      prev.current = Object.fromEntries(list.map((j) => [j.id, j.status]));
      seeded.current = true;
      setJobs(list);
    };
    const wake=()=>tick();
    window.addEventListener("ace-job-started",wake);
    tick();
    const t = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(t); window.removeEventListener("ace-job-started",wake); };
  }, []);

  const runningCount = jobs.filter((j) => j.status === "running").length;

  // Auto-open the dropdown whenever jobs are active — unless the user has closed it. When work
  // finishes (idle), reset so the next batch opens it again.
  useEffect(() => {
    if (runningCount > 0) { if (!userClosed.current) setOpen(true); }
    else { userClosed.current = false; setOpen(false); }
  }, [runningCount]);

  // NOTE: the dropdown intentionally does NOT close on an outside click. Once open it stays
  // open until the user explicitly clicks the ✕ close button (or jobs go idle) — so clicking
  // around the app while jobs run never dismisses the progress panel.
  const close = () => { userClosed.current = true; setOpen(false); };

  const running = jobs.filter((j) => j.status === "running");
  const cancel=async(id:string)=>{const d=await api.post<any>(`/meta/jobs/${id}/cancel`);if(d.error)toastErr(d.error);else if(d.cancelled)toast("Stopping job…","warn");};
  if (!running.length) return null;

  return (
    <div className="hjobs" ref={wrap}>
      <button className="hjobs-btn" onClick={() => setOpen((v) => { const nv = !v; userClosed.current = !nv; return nv; })} title={`${running.length} job(s) running`}>
        <span className="spin" /> <b>{running.length}</b>
      </button>
      {open ? (
        <div className="hjobs-pop">
          <div className="hjobs-pophead">
            <span>{running.length} running</span>
            <button className="hjobs-close" onClick={close} title="Close" aria-label="Close">✕</button>
          </div>
          {running.map((j) => (
            <div key={j.id} className="hjobs-item">
              <div className="hjobs-itemhead"><div className="hjobs-kind">{label(j.kind)}</div><button className="hjobs-stop" onClick={()=>cancel(j.id)}>Stop</button></div>
              <div className="hjobs-msg">{j.message || "working…"}{j.total ? ` · ${j.done}/${j.total}` : ""}</div>
              {j.total ? <div className="hjobs-bar"><div style={{ width: `${Math.round(100 * j.done / Math.max(1, j.total))}%` }} /></div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
