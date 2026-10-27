import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/toast";
import "./login.css";

// Global login prompt. A banner appears on every screen whenever no BRAIN session exists, and
// opens a dialog that runs the two-step biometrics login from anywhere in the app. It NEVER
// prompts or logs in while a session is present — that's the studio's core invariant.
export function LoginGate() {
  const { s, loading, loginOpen, closeLogin, refresh } = useSession();
  // Show the prompt only once the first status has resolved and it's genuinely absent.
  const needsLogin = !loading && !s.ok;

  return (
    <>
      {(needsLogin || loginOpen) ? <LoginModal blocking={needsLogin} renewing={!!s.ok&&s.remaining>0&&s.remaining<600} onClose={closeLogin} onDone={refresh} /> : null}
    </>
  );
}

function LoginModal({ blocking, renewing, onClose, onDone }: { blocking: boolean; renewing:boolean; onClose: () => void; onDone: (remaining?:number) => Promise<void> }) {
  const { toast, toastErr } = useToast();
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [changeCredentials, setChangeCredentials] = useState(false);
  useEffect(() => { api.get<any>("/settings/login/status").then(r => setSaved(!!r.credentials_saved)); }, []);

  async function begin(force=false) {
    setBusy(true); setMsg("Contacting BRAIN… (this can take 10–30s)"); setUrl("");
    const r = await api.post<any>("/settings/login/begin", { email: email || null, password: pass || null, force });
    setBusy(false);
    if(r.job_running&&!force){if(window.confirm(`${r.error}\n\nThe session guard has requested cancellation. Continue with re-login?`))return begin(true)}
    if (r.error) { setMsg(""); return toastErr(r.error); }
    if (r.state === "done") { setMsg("Logged in — session saved."); toast("Logged in."); await onDone(Number(r.remaining)||undefined); return void onClose(); }
    if (r.state === "biometrics" && r.url) {
      setUrl(r.url);
      setMsg("Biometrics required. Open the link below, complete it in your browser, then click Finish login.");
      window.open(r.url, "_blank", "noopener");     // pop the biometrics page for them
      return;
    }
    setMsg(r.message || "Unexpected response. Try again.");
  }

  async function finish() {
    setBusy(true); setMsg("Confirming…");
    const r = await api.post<any>("/settings/login/complete");
    setBusy(false);
    if (r.error) { setMsg(""); return toastErr(r.error); }
    if (r.state === "done") { toast("Logged in."); await onDone(Number(r.remaining)||undefined); return void onClose(); }
    setMsg(r.message || "Biometrics not finished yet — complete it in the browser, then click Finish again.");
  }

  return (
    <div className="login-overlay" onClick={blocking ? undefined : onClose} role="dialog" aria-modal="true">
      <div className="login-box" onClick={(e) => e.stopPropagation()}>
        {!blocking ? <span className="close-x" onClick={onClose}>×</span> : null}
        <h3>{renewing?"Renew your BRAIN session":"Sign in to WorldQuant BRAIN"}</h3>
        <div className="sub">
          {renewing?"Less than 10 minutes remain. Running simulations were stopped safely; renew before continuing.":"Your workspace is locked until a BRAIN session is active."} Enter your BRAIN email and
          password, or leave them blank to reuse credentials saved on this machine. Complete
          biometrics in your browser when requested, then finish the login here.
        </div>
        {saved && !changeCredentials ? <div className="saved-credentials"><b>Saved credentials ready</b><span>Your locally stored BRAIN login will be reused.</span><button className="text-action" onClick={()=>setChangeCredentials(true)}>Use different credentials</button></div> : <>
          <label className="fld"><span>Email</span><input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
          <label className="fld"><span>Password</span><input type="password" value={pass} onChange={(e) => setPass(e.target.value)} /></label>
          {saved ? <button className="text-action credential-back" onClick={()=>{setChangeCredentials(false);setEmail("");setPass("")}}>Use saved credentials instead</button> : null}
        </>}

        <div className="steps">
          <button className="btn" onClick={()=>begin()} disabled={busy}>
            {busy ? <span className="spin" /> : null} Begin login
          </button>
          <button className="btn ghost" onClick={finish} disabled={busy}>Finish login</button>
        </div>

        {msg ? (
          <div className="msg">
            {msg}
            {url ? <div style={{ marginTop: 8 }}>
              <a href={url} target="_blank" rel="noopener">Open biometrics link ↗</a>
            </div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
