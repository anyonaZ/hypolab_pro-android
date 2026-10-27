import { useEffect, useState, type ComponentType } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";
import { Sidebar } from "./layout/Sidebar";
import { Header } from "./layout/Header";
import { useTheme } from "../lib/useTheme";
import { Placeholder } from "../features/common/Placeholder";
import { useAutoScale } from "../lib/useAutoScale";
import { ResearchProvider } from "../lib/store";
import { ToastProvider } from "../lib/toast";
import { SessionProvider, useSession } from "../lib/session";
import { LoginGate } from "./LoginGate";
import { DailyQuote } from "./DailyQuote";
import { api } from "../lib/api";
import { useButtonBusyFeedback } from "../lib/useButtonBusyFeedback";
import type { NavGroup } from "./types";
import "./shell.css";
import { ResearchBackground } from "../components/ResearchBackground";
import { CommandCenter } from "../features/command/CommandCenter";
import { DataExplorer } from "../features/data/DataExplorer";
import { Generation } from "../features/generate/Generation";
import { Simulation } from "../features/simulate/Simulation";
import { EvolutionLab } from "../features/evolve/EvolutionLab";
import { ResultsAnalytics } from "../features/results/ResultsAnalytics";
import { PromptLibrary } from "../features/prompts/PromptLibrary";
import { SuperAlpha } from "../features/super/SuperAlpha";
import { OperatorAtlas } from "../features/operators/OperatorAtlas";
import { Settings } from "../features/settings/Settings";
import { StrategyLab } from "../features/strategy/StrategyLab";
import { TemplateStudio } from "../features/templates/TemplateStudio";
import { CatalogSync } from "../features/catalog/CatalogSync";
import { ResearchFootprint } from "../features/account/ResearchFootprint";
import { RegionAtlas } from "../features/regions/RegionAtlas";

const SCREENS: Record<string, ComponentType> = {
  "/": CommandCenter,
  "/data": DataExplorer,
  "/catalog": CatalogSync,
  "/account-research": ResearchFootprint,
  "/regions": RegionAtlas,
  "/generate": Generation,
  "/simulate": Simulation,
  "/evolution": EvolutionLab,
  "/results": ResultsAnalytics,
  "/prompts": PromptLibrary,
  "/super": SuperAlpha,
  "/operators": OperatorAtlas,
  "/settings": Settings,
  "/strategies": StrategyLab,
  "/templates": TemplateStudio,
};

export function App() {
  useAutoScale();
  useButtonBusyFeedback();
  const { theme } = useTheme();
  const [navRaw, setNav] = useState<NavGroup[]>([]);
  const { pathname } = useLocation();
  useEffect(() => { api.get<{ nav: NavGroup[] }>("/meta/nav").then((d) => setNav(d.nav ?? [])); }, []);
  // Defensive: only ever show nav items whose route still exists in this build. If a stale backend
  // (e.g. an older running instance) serves a removed screen like /research or /knowledge, it is
  // dropped here rather than rendering a dead link.
  const nav = navRaw
    .map((g) => ({ ...g, items: g.items.filter((it) => it.path in SCREENS) }))
    .filter((g) => g.items.length);
  const items = nav.flatMap((g) => g.items);

  return (
    <ToastProvider>
      <ResearchProvider>
        <SessionProvider>
        <ResearchBackground mode={theme} />
        <div className="shell">
          <Sidebar nav={nav} />
          <div className="shell-main">
            <Header nav={nav} />
            <DailyQuote />
            <LoginGate />
            <main className="shell-content">
              <ErrorBoundary key={pathname}>
                <Routes>
                  {items.map((it) => {
                    const Screen = SCREENS[it.path];
                    return (
                      <Route key={it.id} path={it.path}
                        element={Screen ? <Screen /> : <Placeholder title={it.label} ready={it.ready} />} />
                    );
                  })}
                  <Route path="/" element={<CommandCenter />} />
                  {/* Settings remains off the sidebar and is reachable from the header gear. */}
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Placeholder title="Not found" ready={false} />} />
                </Routes>
              </ErrorBoundary>
            </main>
            <ShellFooter />
          </div>
        </div>
        </SessionProvider>
      </ResearchProvider>
    </ToastProvider>
  );
}

// Footer is hidden while the "no session" login banner is up, so the yellow prompt owns the
// bottom of the shell and nothing competes with it. It returns once a session exists.
function ShellFooter() {
  const { s, loading } = useSession();
  if (!loading && !s.ok) return null;
  return (
    <footer className="shell-foot">
      <span>HypoLab</span><span className="sep">·</span>
      <span>Alpha Research Lab</span>
      <span className="right">Happy Research ✦ · Let's Shine Together!</span>
    </footer>
  );
}
