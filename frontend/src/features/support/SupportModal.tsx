import { useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

// In-app issue reporter. Sends to the maintainer's collector via /api/support/report; if that isn't
// configured (or fails), it falls back to a copy-report button so nothing is lost.
export function SupportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { toast, toastErr } = useToast();
  const [cat, setCat] = useState<"bug" | "idea" | "question">("bug");
  const [msg, setMsg] = useState("");
  const [email, setEmail] = useState("");
  const [logs, setLogs] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fallback, setFallback] = useState<string>("");   // shown when the collector isn't set up

  if (!open) return null;

  const reportText = () =>
    `HypoLab issue report\nType: ${cat}\n${email ? `From: ${email}\n` : ""}\n${msg.trim()}`;

  async function send() {
    if (!msg.trim()) return toast("Write a short description first.", "warn");
    setBusy(true); setFallback("");
    const r = await api.post<any>("/support/report", { category: cat, message: msg, email, include_logs: logs });
    setBusy(false);
    if (r.ok) { toast("Thanks — your report was sent.", "ok"); setMsg(""); onClose(); return; }
    // Not configured or failed → offer the copy-report fallback rather than losing the message.
    setFallback(reportText());
    toastErr(r.error || "Could not send the report.");
  }

  return createPortal(
    <div className="modal-overlay support-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} role="presentation">
      <div className="modal support-modal" role="dialog" aria-modal="true" aria-labelledby="support-title">
        <div className="dx-head" style={{ marginBottom: 10 }}>
          <div><span className="eyebrow">SUPPORT CHANNEL</span><b id="support-title">Report an issue</b></div>
          <button className="icon-btn" style={{ marginLeft: "auto" }} onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="seg" style={{ marginBottom: 10 }}>
          {(["bug", "idea", "question"] as const).map((c) =>
            <button key={c} className={"seg-btn" + (cat === c ? " on" : "")} onClick={() => setCat(c)}>
              {c === "bug" ? "🐞 Bug" : c === "idea" ? "💡 Idea" : "❔ Question"}</button>)}
        </div>

        <label className="fld"><span>What happened / what you'd like</span>
          <textarea value={msg} onChange={(e) => setMsg(e.target.value)} style={{ minHeight: 110 }}
            placeholder="Describe the issue, steps to reproduce, or your idea…" /></label>
        <label className="fld" style={{ marginTop: 8 }}><span>Your email (optional — for a reply)</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" /></label>
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 12, color: "var(--mut)", cursor: "pointer" }}>
          <input type="checkbox" checked={logs} onChange={(e) => setLogs(e.target.checked)} />
          Attach recent app logs (helps diagnose bugs)
        </label>

        {fallback ? (
          <div style={{ marginTop: 10, border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", background: "var(--surface-2)" }}>
            <div className="mut" style={{ fontSize: 11, marginBottom: 6 }}>
              Couldn't send automatically. Copy your report and send it to the maintainer:</div>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 11.5, margin: 0, maxHeight: 120, overflow: "auto" }}>{fallback}</pre>
            <button className="btn ghost sm" style={{ marginTop: 6 }}
              onClick={() => { navigator.clipboard?.writeText(fallback); toast("Report copied."); }}>Copy report</button>
          </div>) : null}

        <div className="dx-filters" style={{ marginTop: 14, justifyContent: "flex-end" }}>
          <button className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button className="btn sm" onClick={send} disabled={busy || !msg.trim()}>
            {busy ? <><span className="spin" /> Sending…</> : "Send report"}</button>
        </div>
      </div>
    </div>, document.body
  );
}
