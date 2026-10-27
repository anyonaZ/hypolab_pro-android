import { useEffect, useState } from "react";

export const THEMES = [
  { id: "dark", label: "Black" },
  { id: "light", label: "Warm paper" },
  { id: "light-cool", label: "Cool daylight" },
  { id: "light-soft", label: "Soft sage" },
  { id: "pixel", label: "Pixel Blast" },
  { id: "particles", label: "Particles" },
  { id: "threads", label: "Threads" },
  { id: "aurora", label: "Aurora night" },
  { id: "aurora-light", label: "Aurora day" },
];
const VALID_THEMES = new Set(THEMES.map((item) => item.id));

export function useTheme() {
  const [theme, setThemeState] = useState<string>(() => {
    const saved = localStorage.getItem("ace2-theme") || "dark";
    return VALID_THEMES.has(saved) ? saved : "dark";
  });
  useEffect(() => {
    const sync = (event: Event) => {
      const next = (event as CustomEvent<string>).detail;
      if (VALID_THEMES.has(next)) setThemeState(next);
    };
    window.addEventListener("ace-theme-change", sync);
    return () => window.removeEventListener("ace-theme-change", sync);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ace2-theme", theme);
  }, [theme]);
  const setTheme = (next: string) => {
    const safe = VALID_THEMES.has(next) ? next : "dark";
    setThemeState(safe);
    window.dispatchEvent(new CustomEvent("ace-theme-change", { detail: safe }));
  };
  return { theme, setTheme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") };
}
