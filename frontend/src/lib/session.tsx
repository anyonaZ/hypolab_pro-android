import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

// Live BRAIN-session status, shared app-wide. Polls the backend so any screen can react to
// login state, and drives the global login prompt. Invariant: we NEVER trigger a login when
// a session already exists — this only observes status and opens the dialog on request.
export interface SessionState {
  ok: boolean;            // true while a session is present (valid or a transient "unknown")
  state: string;          // "valid" | "unknown" | "expired" | "none" | "loading"
  remaining: number;
  remaining_human: string;
  status: string;
  user_id?: string;
  display_name?: string;
}

interface SessionCtx {
  s: SessionState;
  loading: boolean;       // true only until the FIRST status resolves (avoids a flash of the prompt)
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  refresh: (immediateRemaining?: number) => Promise<void>;
}

const EMPTY: SessionState = { ok: false, state: "loading", remaining: 0, remaining_human: "", status: "" };
const Ctx = createContext<SessionCtx | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState<SessionState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const timer = useRef<number | null>(null);
  const expiryWarned = useRef(false);
  const profileLoaded = useRef(false);

  const refresh = async (immediateRemaining?: number) => {
    // Login responses already contain BRAIN's authoritative lifetime. Paint it immediately while
    // the status request fills in the remaining metadata, instead of leaving the expired timer up.
    if (typeof immediateRemaining === "number" && immediateRemaining > 0) {
      setS(v => ({ ...v, ok: true, state: "valid", remaining: immediateRemaining,
        remaining_human: "", status: "Session refreshed" }));
      setLoading(false);
      expiryWarned.current = false;
    }
    const d = await api.get<any>("/session/status");
    // A 404/degraded backend returns { error } — treat that as "not logged in" rather than crashing.
    if (d?.error || typeof d?.ok !== "boolean") {
      setS({ ok: false, state: "none", remaining: 0, remaining_human: "", status: d?.error || "" });
    } else {
      // Functional update is essential: interval callbacks otherwise capture the
      // initial empty state and repeatedly erase the asynchronously loaded profile.
      setS(v => ({ ok: !!d.ok, state: d.state || "none", remaining: d.remaining || 0,
             remaining_human: d.remaining_human || "", status: d.status || "",
             user_id: d.ok ? (d.user_id || v.user_id) : undefined, display_name: d.ok ? (d.display_name || v.display_name) : undefined }));
      if (!d.ok) profileLoaded.current = false;
      // UNKNOWN means a session file exists but BRAIN has not confirmed it. Do not launch account
      // traffic from that optimistic state; wait for the quick status poll to report VALID.
      if (d.ok && d.state === "valid" && !profileLoaded.current) {
        profileLoaded.current = true;
        api.get<any>("/session/profile").then(p => {
          if (!p?.error) {
            setS(v => ({...v, user_id:p.user_id||"", display_name:p.display_name||""}));
            const d=new Date(), quarter=`${d.getFullYear()}-Q${Math.floor(d.getMonth()/3)+1}`;
            const key=`ace2:account-sync:${p.user_id||"self"}:${d.toISOString().slice(0,10)}`;
            // Daily background sync is metrics-only. It never inventories alpha expressions.
            if(!localStorage.getItem(key)){localStorage.setItem(key,"started");api.post("/account-research/sync",{quarter,include_submitted_regular:false}).then(r=>{if(r?.error)localStorage.removeItem(key)});}
          }
          else profileLoaded.current = false;
        });
      }
      if (d.renew_required && !expiryWarned.current) {
        expiryWarned.current = true;
        setLoginOpen(true);
      } else if ((d.remaining || 0) >= 600) expiryWarned.current = false;
    }
    setLoading(false);
  };

  useEffect(() => {
    refresh();
    // Re-check periodically so an expiry (or a login done elsewhere) is reflected without a reload.
    timer.current = window.setInterval(refresh, 30000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, []);

  // A persisted session is admitted immediately while BRAIN validation runs in the backend.
  // Poll briefly in that transitional state so the verified expiry appears as soon as the
  // background request finishes instead of waiting for the normal 30-second interval.
  useEffect(() => {
    if (s.state !== "unknown") return;
    const quick = window.setInterval(refresh, 1000);
    return () => window.clearInterval(quick);
  }, [s.state]);

  const value: SessionCtx = {
    s, loading, loginOpen,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
    refresh,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession must be used within a SessionProvider");
  return c;
}
