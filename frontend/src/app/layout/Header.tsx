import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { useSession } from "../../lib/session";
import { THEMES, useTheme } from "../../lib/useTheme";
import { HeaderJobs } from "../HeaderJobs";
import { SupportModal } from "../../features/support/SupportModal";
import type { NavGroup } from "../types";

export function Header({ nav }: { nav: NavGroup[] }) {
  const { pathname } = useLocation(), navigate = useNavigate();
  const { ctx, transferStudy, exitTransferStudy } = useResearch();
  const { s: session, openLogin } = useSession();
  const { theme, setTheme } = useTheme();
  const [supportOpen,setSupportOpen]=useState(false),[profileOpen,setProfileOpen]=useState(false);
  const [upd,setUpd]=useState<{available?:boolean;latest?:string}>({});
  const current=nav.flatMap(g=>g.items).find(it=>it.path===pathname)?.label??"HypoLab";
  useEffect(()=>{const check=()=>api.get<any>("/update/check").then(d=>{if(d?.available)setUpd({available:true,latest:d.latest})}).catch(()=>{});check();const t=window.setInterval(check,3600000);return()=>window.clearInterval(t)},[]);
  return <header className="header">
    <span className="title">{current}</span><div className="spacer"/>
    {!transferStudy ? <span className="ctx-chip">{ctx.region} · {ctx.universe} · <b>D{ctx.delay}</b> · {ctx.instrument}</span> :
      <span className="ctx-chip transfer-chip" title="This shared-field context follows your research and simulations">
        <button onClick={()=>navigate("/regions")}>Transfer #{transferStudy.id} · {transferStudy.contexts.map(x=>`${x.region} D${x.delay}`).join(" ↔ ")} · {transferStudy.selected_field_ids.length}F</button>
        <button title="Close transfer mode and restore the normal workspace" onClick={exitTransferStudy}>×</button>
      </span>}
    {upd.available?<button className="ctx-chip" onClick={()=>navigate("/settings")} style={{cursor:"pointer",borderColor:"var(--acc)",color:"var(--acc)"}}>⬆ Update {upd.latest}</button>:null}
    <HeaderJobs/>
    <label className="eye-theme" title="Choose a calm research background"><span>◉</span><select value={theme} onChange={e=>setTheme(e.target.value)}>{THEMES.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
    <button className="icon-btn" onClick={()=>setSupportOpen(true)} title="Report an issue" aria-label="Report an issue">?</button>
    <SupportModal open={supportOpen} onClose={()=>setSupportOpen(false)}/>
    <div className="profile-menu">
      <button className={"profile-trigger"+(profileOpen?" active":"")} onClick={()=>setProfileOpen(v=>!v)} title={session.user_id||"Profile"}><span className={"dot "+(session.ok?"ok":"")}/><b>{session.user_id||"No Session"}</b><i>⌄</i></button>
      {profileOpen?<div className="profile-popover"><header><span>BRAIN ACCOUNT</span><b>{session.display_name||session.user_id||"No active session"}</b>{session.user_id?<code>{session.user_id}</code>:null}</header><button onClick={()=>{setProfileOpen(false);navigate("/account-research")}}>My Research Footprint</button><button onClick={()=>{setProfileOpen(false);navigate("/settings")}}>Settings</button>{!session.ok?<button onClick={()=>{setProfileOpen(false);openLogin()}}>Log in</button>:null}</div>:null}
    </div>
  </header>;
}
