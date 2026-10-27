import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { icon } from "../../lib/icons";
import { useSession } from "../../lib/session";
import { usePersistentState } from "../../lib/persist";
import { Wordmark } from "../../lib/Logo";
import type { NavGroup } from "../types";

// Touch screens narrower than this (in layout px) get the navigation as a drawer: an icon rail
// that opens a labelled overlay and closes again after you choose a screen.
const DRAWER_BELOW = 1320;

function useDrawerMode() {
  const check = () => document.documentElement.classList.contains("touch-ui")
    && window.innerWidth / (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale")) || 1) < DRAWER_BELOW;
  const [drawer, setDrawer] = useState(check);
  useEffect(() => {
    const update = () => setDrawer(check());
    window.addEventListener("resize", update);
    window.addEventListener("ace-ui-scale-change", update);
    return () => { window.removeEventListener("resize", update); window.removeEventListener("ace-ui-scale-change", update); };
  }, []);
  return drawer;
}

// The sidebar collapses to an icon-only rail. The toggle sits at the TOP of the rail when
// collapsed, and at the TOP-RIGHT of the full sidebar when open — never in the header.
export function Sidebar({ nav }: { nav: NavGroup[] }) {
  const { s: sess, openLogin } = useSession();
  const [collapsedPref, setCollapsedPref] = usePersistentState("ui:sidebar-collapsed", false);
  const drawer = useDrawerMode();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const collapsed = drawer ? !drawerOpen : collapsedPref;
  const setCollapsed = (fn: (v: boolean) => boolean) =>
    drawer ? setDrawerOpen((open) => !fn(!open)) : setCollapsedPref(fn);
  const closeDrawer = () => { if (drawer) setDrawerOpen(false); };
  // Session expiring within the hour: offer a one-click re-login (user-initiated; never automatic).
  const expiring = sess.ok && (sess.remaining ?? 0) > 0 && (sess.remaining ?? 0) < 3600;

  const Toggle = () => (
    <button className="sidebar-toggle" onClick={() => setCollapsed((v) => !v)}
      title={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-label="Toggle sidebar">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {collapsed ? <path d="M9 6l6 6-6 6" /> : <path d="M15 6l-6 6 6 6" />}
      </svg>
    </button>
  );

  return (
    <>
    {drawer && drawerOpen ? <div className="sidebar-scrim" onClick={closeDrawer} aria-hidden="true" /> : null}
    <aside className={"sidebar" + (collapsed ? " collapsed" : "") + (drawer ? " drawer" : "") + (drawer && drawerOpen ? " open" : "")}>
      <div className="sidebar-top">
        {!collapsed ? <div className="sidebar-brand"><Wordmark /></div> : null}
        <Toggle />
      </div>

      <nav className="sidebar-nav">
        {nav.map((g) => (
          <div key={g.group}>
            {!collapsed ? <div className="nav-group-label">{g.group}</div> : null}
            {g.items.map((it) => (
              <NavLink key={it.id} to={it.path} end={it.path === "/"} title={collapsed ? it.label : undefined} onClick={closeDrawer}
                className={({ isActive }) =>
                  "nav-link" + (isActive ? " active" : "") + (it.ready ? "" : " soon")}>
                {icon(it.icon)}
                {!collapsed ? <span>{it.label}</span> : null}
                {!collapsed && !it.ready ? <span className="soon-dot">soon</span> : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      {!collapsed ? (
        <div className="sidebar-foot">
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="lbl"><span className={"dot" + (sess.ok ? " ok" : "")} /> Session</span>
            <NavLink to="/settings" onClick={closeDrawer} className="sidebar-settings" title="Settings" aria-label="Settings">{icon("settings")}</NavLink>
            {expiring ? (
              <button onClick={openLogin} title="Session expires within the hour — log in again"
                style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 700, color: "var(--warn)",
                         background: "var(--warn-weak, #4a3a12)", border: "1px solid var(--warn)",
                         borderRadius: 6, padding: "1px 7px", cursor: "pointer" }}>Re-login</button>
            ) : null}
          </div>
          <b>{sess.ok ? (sess.remaining_human || "active") : "No Session"}</b>
          {sess.ok && sess.user_id ? <small className="mut" title="Authenticated BRAIN user">User · {sess.user_id}</small> : null}
          {!sess.ok ? <button className="btn sm" onClick={openLogin} style={{ marginTop: 8, width: "100%" }}>Re-login</button> : null}
        </div>
      ) : (
        <div className="sidebar-foot-mini" onClick={(expiring || !sess.ok) ? openLogin : undefined}
          title={expiring ? "Session expiring — click to re-login" : (sess.ok ? "Session active" : "No Session — click to re-login")}
          style={(expiring || !sess.ok) ? { cursor: "pointer" } : undefined}>
          <span className={"dot" + (sess.ok ? " ok" : "")} style={expiring ? { background: "var(--warn)" } : undefined} />
        </div>
      )}
    </aside>
    </>
  );
}
