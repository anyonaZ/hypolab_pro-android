import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { NumberInput } from "../../lib/NumberInput";
import { androidBridge, androidInfo, isAndroidApp, openExternal, type AndroidInfo } from "../../lib/android";
import { UI_SCALE_OPTIONS, readUiScalePref, setUiScalePref } from "../../lib/useAutoScale";

const PROVIDERS: Record<string, string> = {
  claude: "Claude (Anthropic)", openai: "OpenAI", gemini: "Gemini", deepseek: "DeepSeek",
  groq: "Groq (free)", huggingface: "Hugging Face", openrouter: "OpenRouter", mistral: "Mistral",
  together: "Together AI", xai: "xAI (Grok)",
};

// Settings: API keys, the active LLM provider + model with a live key test, BRAIN login,
// your simulation tags, and appearance.
export function Settings() {
  const { toast, toastErr } = useToast();
  const [status, setStatus] = useState<Record<string, { set: boolean; hint: string }>>({});
  const [prov, setProv] = useState<{ available: string[]; preferred: string; used: string[] }>({ available: [], preferred: "", used: [] });
  const [keyProv, setKeyProv] = useState("claude");
  const [keyVal, setKeyVal] = useState("");
  const [modelProv, setModelProv] = useState("gemini");
  const [models, setModels] = useState<{ models: string[]; current: string }>({ models: [], current: "" });
  const [testOut, setTestOut] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [tag, setTag] = useState(() => localStorage.getItem("ace2-tag") || "");
  const [winnerTag, setWinnerTag] = useState(() => localStorage.getItem("ace2-winnertag") || "");
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [savedBrainCredentials, setSavedBrainCredentials] = useState(false);
  const [changeBrainCredentials, setChangeBrainCredentials] = useState(false);
  const [loginMsg, setLoginMsg] = useState("");
  const [bioUrl, setBioUrl] = useState("");
  const [pruneSharpe, setPruneSharpe] = useState(0.5);
  const [pruneFit, setPruneFit] = useState(0.5);
  const [upd, setUpd] = useState<any>(null);
  const [updating, setUpdating] = useState(false);
  const [storage, setStorage] = useState<Record<string, number>>({});
  const [databaseBytes,setDatabaseBytes]=useState(0);
  const [uiScale, setUiScale] = useState(readUiScalePref);
  const [restoring, setRestoring] = useState(false);
  const restoreRef = useRef<HTMLInputElement>(null);
  const onAndroid = isAndroidApp();
  const [device, setDevice] = useState<AndroidInfo | null>(() => androidInfo());
  useEffect(() => {
    if (!onAndroid) return;
    // Re-read after returning from the system battery dialog.
    const refresh = () => setDevice(androidInfo());
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [onAndroid]);

  useEffect(() => { api.get<any>("/update/check").then((d) => { if (!d.error || d.current) setUpd(d); }); }, []);
  useEffect(() => { api.get<any>("/settings/login/status").then((d) => setSavedBrainCredentials(!!d.credentials_saved)); }, []);
  const loadStorage=()=>api.get<any>("/settings/storage").then(d=>{if(!d.error){setStorage(d.groups||{});setDatabaseBytes(d.database_bytes||0)}});
  async function maintain(){const d=await api.post<any>("/settings/storage/maintain");d.error?toastErr(d.error):(await loadStorage(),toast("Local database optimized.","ok"))}
  useEffect(()=>{loadStorage()},[]);
  async function clearStorage(group:string){
    if(!window.confirm(`Delete all local ${group.replace(/_/g," ")} data? Your session and API keys are kept.`))return;
    const d=await api.del<any>(`/settings/storage/${group}`);if(d.error)return toastErr(d.error);
    await loadStorage();toast(`Deleted ${d.deleted} ${group.replace(/_/g," ")} records.`,"ok");
  }
  async function applyUpdate() {
    const question = upd?.android
      ? `Download HypoLab ${upd?.latest}? Your browser opens the APK; install it to update. Your data is kept.`
      : `Update to ${upd?.latest}? The app will close and reopen automatically.`;
    if (!window.confirm(question)) return;
    setUpdating(true);
    const d = await api.post<any>("/update/apply");
    if (d.error) { setUpdating(false); return toastErr(d.error); }
    if (d.url) { openExternal(d.url); setUpdating(false); }
    toast(d.message || "Updating… the app will restart.", "ok");
  }
  async function restoreDatabase(file: File) {
    if (!window.confirm(`Replace this device's research database with “${file.name}” (${(file.size / 1048576).toFixed(1)} MB)? The current database is kept as backups/before-restore.db.`)) return;
    setRestoring(true);
    const fd = new FormData(); fd.append("file", file);
    const d = await api.upload<any>("/settings/storage/restore", fd);
    setRestoring(false);
    if (d.error) return toastErr(d.error);
    toast(`Database restored (${(Number(d.bytes || 0) / 1048576).toFixed(1)} MB). Reloading…`, "ok");
    await loadStorage();
    window.setTimeout(() => window.location.reload(), 1200);
  }
  function chooseUiScale(value: string) { setUiScale(value); setUiScalePref(value); }

  async function pruneResults() {
    if (!window.confirm(`Delete weak results while keeping positive candidates and strong negative inversion candidates?`)) return;
    const d = await api.post<any>("/analytics/results/prune", { min_sharpe: pruneSharpe, min_fitness: pruneFit });
    return d.error ? toastErr(d.error) : toast(`Deleted ${d.deleted} weak result(s).`, "ok");
  }
  async function resetAll() {
    if (!window.confirm("RESET EVERYTHING? This wipes results, prompts, usage, research sessions and catalogued datasets/fields. Your BRAIN session and API keys are kept. Cannot be undone.")) return;
    const d = await api.post<any>("/analytics/reset");
    return d.error ? toastErr(d.error) : toast("Everything reset.", "ok");
  }

  const loadKeys = () => api.get<any>("/settings/keys").then((d) => setStatus(d.status || {}));
  const loadProv = () => api.get<any>("/settings/providers").then((d) => {
    // Never trust a degraded/404 response to have the shape we want — default every field
    // so a backend still starting up can't white-screen this whole screen.
    const av: string[] = Array.isArray(d?.available) ? d.available : [];
    const pref: string = d?.preferred ?? "";
    setProv({ available: av, preferred: pref, used: Array.isArray(d?.used) ? d.used : [] });
    // Point the model panel at the PREFERRED provider so its models preload and stay selected
    // until the user changes provider. Falls back to the current pick, then the first available.
    if (av.length) setModelProv((m) => (pref && av.includes(pref)) ? pref : (av.includes(m) ? m : av[0]));
  });
  useEffect(() => { loadKeys(); loadProv(); }, []);
  useEffect(() => {
    if (modelProv) api.get<any>(`/settings/providers/${modelProv}/models`)
      .then((d) => setModels({ models: Array.isArray(d?.models) ? d.models : [], current: d?.current ?? "" }));
  }, [modelProv]);

  async function saveKey() {
    if (!keyVal.trim()) return;
    const d = await api.post("/settings/keys", { updates: { [keyProv]: keyVal.trim() } });
    if (d.error) return toastErr(d.error);
    setKeyVal(""); toast(`${PROVIDERS[keyProv]} key saved.`); loadKeys(); loadProv();
  }
  async function deleteKey() {
    if (!window.confirm(`Delete the ${PROVIDERS[keyProv]} API key?`)) return;
    const d = await api.post<any>(`/settings/keys/${keyProv}/delete`);
    if (d.error) return toastErr(d.error);
    setStatus(d.status || {}); loadProv(); toast(`${PROVIDERS[keyProv]} key deleted.`);
  }
  async function clearAllKeys() {
    if (!window.confirm("Delete ALL stored API keys? This cannot be undone.")) return;
    const d = await api.post<any>("/settings/keys/clear");
    if (d.error) return toastErr(d.error);
    setStatus(d.status || {}); loadProv(); toast("All API keys deleted.");
  }
  async function setPreferred(p: string) {
    await api.post("/settings/providers", { preferred: p }); loadProv();
  }
  async function setModel(m: string) {
    await api.post(`/settings/providers/${modelProv}/model`, { model: m });
    setModels((s) => ({ ...s, current: m })); toast(`${modelProv} → ${m}`);
  }
  async function testKey() {
    setTesting(true); setTestOut("testing…");
    const r = await api.post<any>(`/settings/providers/${modelProv}/test`);
    setTesting(false);
    setTestOut(r.ok ? `✓ works — ${r.model} replied` : `✗ ${r.error}${r.raw ? " · " + r.raw : ""}`);
  }
  function saveTags() {
    localStorage.setItem("ace2-tag", tag); localStorage.setItem("ace2-winnertag", winnerTag);
    toast("Tags saved — used when you simulate.");
  }
  async function beginLogin(force = false): Promise<void> {
    setLoginMsg("contacting BRAIN…"); setBioUrl("");
    const r = await api.post<any>("/settings/login/begin", { email: email || null, password: pass || null, force });
    // A task is running — re-login would cancel the BRAIN session and fail it. Confirm before forcing.
    if (r.job_running && !force) {
      setLoginMsg("");
      if (window.confirm(`${r.error}\n\nLog in anyway and lose the running task?`)) await beginLogin(true);
      return;
    }
    if (r.error) { setLoginMsg(""); toastErr(r.error); return; }
    const url = r.url || "";
    if (url) setBioUrl(url);   // always surface a clickable link as a fallback
    if (r.state === "biometrics" && url) {
      const w = window.open(url, "_blank", "noopener");   // try to open the biometrics page for them
      setLoginMsg(w
        ? (onAndroid ? "Biometrics opened in your browser — complete it there, come back to HypoLab, then tap Finish login."
                     : "Biometrics opened in a new tab — complete it there, then click Finish login.")
        : "Couldn't open the tab automatically (popup blocked). Click “Open biometrics page” below, complete it, then Finish login.");
    } else {
      setLoginMsg(r.message || (url
        ? "Click “Open biometrics page” below, complete it, then Finish login."
        : "Open the biometrics link in your browser, then click Finish login."));
    }
  }
  async function completeLogin() {
    const r = await api.post<any>("/settings/login/complete");
    if (r.error) return toastErr(r.error);
    setLoginMsg("Login complete."); setBioUrl(""); toast("Logged in.");
  }

  return (
    <div className="dx-split settings-page route-workspace" style={{ flex: 1, minHeight: 0 }}>
      <header className="terminal-title compact-title settings-title"><div><span className="eyebrow">LOCAL WORKSPACE CONTROL</span><h1>Settings</h1><p>Providers, credentials, BRAIN access, updates, privacy, appearance, and local research storage.</p></div><span className="terminal-count">{Object.values(status).filter(s=>s.set).length} AI keys</span></header>
      {/* keys + providers */}
      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>LLM providers</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>The selected provider is the only one used for generation.</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <label className="fld"><span>Active provider</span>
            <select value={prov.preferred} onChange={(e) => setPreferred(e.target.value)}>
              <option value="">auto (first available)</option>
              {prov.available.map((p) => <option key={p}>{p}</option>)}
            </select></label>
          <label className="fld"><span>Model for {modelProv}</span>
            <select value={models.current} onChange={(e) => setModel(e.target.value)}>
              {models.models.map((m) => <option key={m}>{m}</option>)}
            </select></label>
        </div>
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <select value={modelProv} onChange={(e) => setModelProv(e.target.value)} style={{ maxWidth: 150 }}>
            {Object.keys(PROVIDERS).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn ghost sm" onClick={testKey} disabled={testing}>{testing ? <><span className="spin" /> Testing…</> : "✓ Test Key"}</button>
        </div>
        {testOut ? <div className="mut" style={{ fontSize: 12, marginTop: 6, color: testOut.startsWith("✓") ? "var(--ok)" : testOut.startsWith("✗") ? "var(--bad)" : "var(--mut)" }}>{testOut}</div> : null}

        <div className="dx-head" style={{ marginTop: 14 }}><b>API keys</b>
          <span className="mut">{Object.values(status).filter((s) => s.set).length} of {Object.keys(PROVIDERS).length} set</span></div>
        <div className="dx-filters">
          <select value={keyProv} onChange={(e) => setKeyProv(e.target.value)} style={{ maxWidth: 150 }}>
            {Object.entries(PROVIDERS).map(([k, v]) => <option key={k} value={k}>{v}{status[k]?.set ? " ✓" : ""}</option>)}
          </select>
          <input type="password" placeholder="paste key…" value={keyVal} onChange={(e) => setKeyVal(e.target.value)} style={{ flex: 1 }} />
          <button className="btn sm" onClick={saveKey}>Save</button>
          <button className="btn ghost sm" onClick={deleteKey} disabled={!status[keyProv]?.set} title="Delete this provider's stored key" style={{ color: "var(--bad)" }}>Delete</button>
        </div>
        <div className="dx-filters" style={{ marginTop: 6 }}>
          <button className="btn ghost sm" onClick={clearAllKeys} style={{ color: "var(--bad)" }}>Delete all keys</button>
        </div>
        <div className="mut" style={{ fontSize: 11, marginTop: 6 }}>{onAndroid ? "Keys are stored in HypoLab's private app storage on this tablet and are only sent to the provider you test or use." : "Keys are stored locally in ~/secrets/ace_keys.json and never leave your machine."}</div>
      </div>

      {/* login + tags + appearance */}
      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>Updates</b>{upd?.current ? <span className="mut">v{upd.current}</span> : null}</div>
        {!upd ? <div className="mut" style={{ fontSize: 12 }}>Checking…</div>
          : upd.frozen === false ? <div className="mut" style={{ fontSize: 12 }}>Running from source — updates apply to the installed app.</div>
          : upd.error ? <div className="mut" style={{ fontSize: 12, color: "var(--mut)" }}>{upd.error}{upd.releases_url ? <> <button className="text-action" onClick={() => openExternal(upd.releases_url)}>Open Releases page ↗</button></> : null}</div>
          : upd.available ? (
            <div className="dx-filters" style={{ alignItems: "center", gap: 10 }}>
              <span className="badge ok">Update available: v{upd.latest}</span>
              <button className="btn sm" onClick={applyUpdate} disabled={updating}>
                {updating ? <><span className="spin" /> Updating…</> : upd.android ? "Download update" : "Update & restart"}</button>
            </div>
          ) : <div className="mut" style={{ fontSize: 12, color: "var(--ok)" }}>✓ You're on the latest version.</div>}

        <div className="dx-head" style={{ marginTop: 14 }}><b>BRAIN session</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>Login is started only when you request it. Complete biometrics in your browser when required.</div>
        {savedBrainCredentials && !changeBrainCredentials ? <div className="saved-login-row"><span><b>✓ Saved BRAIN credentials available</b><small>The existing local credentials will be reused.</small></span><button className="text-action" onClick={()=>setChangeBrainCredentials(true)}>Use different credentials</button></div> : <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <label className="fld"><span>Email (blank uses saved)</span><input value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="fld"><span>Password</span><input type="password" value={pass} onChange={(e) => setPass(e.target.value)} /></label>
        </div>}
        {savedBrainCredentials && changeBrainCredentials ? <button className="text-action" onClick={()=>{setChangeBrainCredentials(false);setEmail("");setPass("")}}>Use saved credentials instead</button> : null}
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <button className="btn sm" onClick={() => beginLogin()}>Begin login</button>
          <button className="btn ghost sm" onClick={completeLogin}>Finish login</button>
        </div>
        {loginMsg ? <div className="mut" style={{ fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>{loginMsg}</div> : null}
        {bioUrl ? <div style={{ marginTop: 8 }}>
          <a className="btn sm" href={bioUrl} target="_blank" rel="noopener noreferrer">Open biometrics page →</a>
        </div> : null}

        <div className="dx-head" style={{ marginTop: 14 }}><b>Your tags</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>Applied when you simulate — nothing is hardcoded.</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <label className="fld"><span>Tag every alpha</span><input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="my_news" /></label>
          <label className="fld"><span>Winner tag</span><input value={winnerTag} onChange={(e) => setWinnerTag(e.target.value)} placeholder="my_winner" /></label>
          <button className="btn sm" style={{ alignSelf: "end" }} onClick={saveTags}>Save</button>
        </div>

        <div className="dx-head" style={{ marginTop: 14 }}><b>Appearance</b></div>
        <div className="mut" style={{ fontSize: 12 }}>Use the appearance selector in the header to choose night themes, warm/cool/sage light modes, Pixel Blast, Particles, Threads, or Aurora. Animated backgrounds remain visible throughout the workspace, including Evolution Lab. Your choice is saved on this device.</div>
        <div className="dx-filters" style={{ marginTop: 8, alignItems: "end" }}>
          <label className="fld" style={{ minWidth: 190 }}><span>Interface scale</span>
            <select value={uiScale} onChange={(e) => chooseUiScale(e.target.value)}>
              {UI_SCALE_OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select></label>
          <span className="mut" style={{ fontSize: 11 }}>Auto fits the whole workspace on screen. A larger scale is easier to tap; panels then scroll inside.</span>
        </div>

        {onAndroid ? <>
          <div className="dx-head" style={{ marginTop: 14 }}><b>This tablet</b>{device ? <span className="mut">HypoLab {device.version} · Android {device.android}</span> : null}</div>
          <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>The research engine runs on this device. Keep background running allowed so long simulations and syncs continue with the screen off.</div>
          <div className="dx-filters wrap" style={{ alignItems: "center", gap: 10 }}>
            {device?.batteryUnrestricted
              ? <span className="badge ok">Background running allowed</span>
              : <button className="btn sm" onClick={() => androidBridge()?.requestUnrestrictedBattery()}>Allow background running</button>}
            <button className="btn ghost sm" onClick={() => { if (window.confirm("Restart HypoLab? Running background jobs will stop.")) androidBridge()?.restartApp(); }}>Restart engine</button>
          </div>
          {device ? <div className="mut" style={{ fontSize: 11, marginTop: 6 }}>{device.device} · {device.abi}</div> : null}
        </> : null}

        <div className="dx-head" style={{ marginTop: 16 }}><b>Local data management</b><span className="mut">{(databaseBytes/1048576).toFixed(1)} MB · every workspace store</span><a className="btn ghost sm" href="/api/settings/storage/backup">Backup database</a><button className="btn ghost sm" onClick={() => restoreRef.current?.click()} disabled={restoring}>{restoring ? <><span className="spin" /> Restoring…</> : "Restore database"}</button><button className="btn ghost sm" onClick={maintain}>Optimize</button></div>
        <input ref={restoreRef} type="file" style={{ display: "none" }}
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void restoreDatabase(f); }} />
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>Counts come from SQLite. Clearing a group never removes BRAIN credentials, the current session, AI keys, or update settings.{onAndroid ? " Backups are saved to Downloads/HypoLab. To bring your desktop research over, restore data/ace_studio_v2.db from ACE Studio." : ""}</div>
        <div className="storage-ledger">{Object.entries(storage).map(([group,count])=><div key={group}><span><b>{group.replace(/_/g," ")}</b><small>{count} local records</small></span><button className="btn ghost sm" onClick={()=>clearStorage(group)}>Clear</button></div>)}</div>
        <div className="dx-filters wrap" style={{marginTop:8}}>
          <button className="btn ghost sm" onClick={resetAll} style={{ color: "var(--bad)", borderColor: "var(--bad)" }}>Reset all workspace data</button>
        </div>
        <div className="dx-filters" style={{ marginTop: 8, alignItems: "end" }}>
          <label className="fld" style={{ width: 92 }}><span>Sharpe floor</span>
            <NumberInput min={0} fallback={0.5} step="0.1" value={pruneSharpe} onChange={setPruneSharpe} /></label>
          <label className="fld" style={{ width: 92 }}><span>Fitness floor</span>
            <NumberInput min={0} fallback={0.5} step="0.1" value={pruneFit} onChange={setPruneFit} /></label>
          <button className="btn ghost sm" onClick={pruneResults}>Delete weak results</button>
        </div>
        <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>Removes weak results while retaining positive candidates and strong negative results that may justify a separately simulated inversion.</div>
      </div>
    </div>
  );
}
