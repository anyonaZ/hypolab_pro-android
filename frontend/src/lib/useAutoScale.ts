import { useEffect } from "react";
import { androidViewportDp } from "./android";

// Fits the workspace to the screen.
//
// Desktop: the UI shrinks (CSS zoom) when the window is smaller than the 1240×720 design
// baseline, e.g. at 125%+ OS zoom. It never scales up past 1.
//
// Tablet (html.touch-ui): the workspace gets a layout canvas about 1100px wide and the page is
// scaled to the screen through the viewport. Unlike CSS zoom, this keeps the width-based layout
// rules consistent (two-pane screens stay side by side), and anything taller than the screen
// scrolls instead of being cut off. Inside the Android app the canvas comes from the WebView's
// real size in dp; in a tablet browser CSS zoom is the fallback.
//
// Settings → Appearance → Interface scale overrides the automatic value on either.
const BASE_W = 1240;
const BASE_H = 720;
const MIN = 0.55;
const TOUCH_CANVAS_W = 1100;
export const UI_SCALE_KEY = "ace2-ui-scale";
export const UI_SCALE_EVENT = "ace-ui-scale-change";
export const UI_SCALE_OPTIONS = [
  { id: "auto", label: "Auto (fit screen)" },
  { id: "0.7", label: "70%" },
  { id: "0.8", label: "80%" },
  { id: "0.9", label: "90%" },
  { id: "1", label: "100%" },
  { id: "1.1", label: "110%" },
  { id: "1.2", label: "120%" },
];

export function readUiScalePref(): string {
  try { return localStorage.getItem(UI_SCALE_KEY) || "auto"; } catch { return "auto"; }
}

export function setUiScalePref(value: string) {
  try { localStorage.setItem(UI_SCALE_KEY, value); } catch {}
  window.dispatchEvent(new CustomEvent(UI_SCALE_EVENT));
}

/** Tablet default: a ~1100px canvas, never smaller than 80% or larger than 100%. */
export function touchAutoScale(widthDp: number): number {
  return Math.min(1, Math.max(0.8, Math.floor((widthDp / TOUCH_CANVAS_W) * 100) / 100));
}

function setViewport(width: number, scale: number) {
  let meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "viewport";
    document.head.appendChild(meta);
  }
  const content = `width=${width}, initial-scale=${scale}, minimum-scale=${scale}, maximum-scale=${scale}, user-scalable=no, viewport-fit=cover`;
  if (meta.content !== content) meta.content = content;
}

/** Applies the current scale (also called once before the first render). */
export function applyUiScale() {
  const root = document.documentElement;
  const touch = root.classList.contains("touch-ui");
  const pref = Number(readUiScalePref());
  const manual = Number.isFinite(pref) && pref > 0;
  const dp = touch ? androidViewportDp() : null;
  if (dp) {
    const s = manual ? pref : touchAutoScale(dp.w);
    setViewport(Math.round(dp.w / s), s);
    root.style.setProperty("--ui-scale", "1");
    root.dataset.scale = String(s);
    return;
  }
  let scale: number;
  if (manual) scale = pref;
  else if (touch) scale = touchAutoScale(window.innerWidth);
  else {
    const s = Math.min(1, window.innerWidth / BASE_W, window.innerHeight / BASE_H);
    scale = Math.max(MIN, Math.floor(s * 100) / 100);
  }
  root.style.setProperty("--ui-scale", String(scale));
  root.dataset.scale = String(scale);
}

export function useAutoScale() {
  useEffect(() => {
    const apply = applyUiScale;
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
