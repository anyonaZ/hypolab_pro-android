import { useEffect } from "react";

// Keeps the whole UI scroll-free by shrinking it when the viewport is smaller than the
// design baseline (e.g. at 125%+ OS zoom, or on a tablet). We scale DOWN but never UP past 1,
// so the layout stays compact on big screens too.
//
// The user can override the automatic fit (Settings → Appearance → Interface scale). On a
// touch tablet a slightly larger scale is often easier to use; panels then scroll internally.
const BASE_W = 1240;
const BASE_H = 720;
const MIN = 0.55;
export const UI_SCALE_KEY = "ace2-ui-scale";
export const UI_SCALE_EVENT = "ace-ui-scale-change";
export const UI_SCALE_OPTIONS = [
  { id: "auto", label: "Auto (fit screen)" },
  { id: "0.7", label: "70%" },
  { id: "0.8", label: "80%" },
  { id: "0.9", label: "90%" },
  { id: "1", label: "100%" },
  { id: "1.1", label: "110%" },
];

export function readUiScalePref(): string {
  try { return localStorage.getItem(UI_SCALE_KEY) || "auto"; } catch { return "auto"; }
}

export function setUiScalePref(value: string) {
  try { localStorage.setItem(UI_SCALE_KEY, value); } catch {}
  window.dispatchEvent(new CustomEvent(UI_SCALE_EVENT));
}

export function useAutoScale() {
  useEffect(() => {
    const apply = () => {
      const pref = Number(readUiScalePref());
      let scale: number;
      if (Number.isFinite(pref) && pref > 0) {
        scale = pref;
      } else {
        const s = Math.min(1, window.innerWidth / BASE_W, window.innerHeight / BASE_H);
        scale = Math.max(MIN, Math.floor(s * 100) / 100);
      }
      document.documentElement.style.setProperty("--ui-scale", String(scale));
    };
    apply();
    window.addEventListener("resize", apply);
    window.addEventListener(UI_SCALE_EVENT, apply);
    // devicePixelRatio changes when the OS display scale changes at runtime.
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    mq.addEventListener?.("change", apply);
    return () => {
      window.removeEventListener("resize", apply);
      window.removeEventListener(UI_SCALE_EVENT, apply);
      mq.removeEventListener?.("change", apply);
    };
  }, []);
}
