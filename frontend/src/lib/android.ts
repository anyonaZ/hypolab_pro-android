// Android integration. Inside the HypoLab Android app the page runs in a WebView that exposes
// `window.HypoLabAndroid`. These shims make standard browser behaviour work there, so screens
// don't need Android-specific code:
//   • window.open / target=_blank to other sites → Chrome Custom Tab (BRAIN biometrics, alpha pages)
//   • links to /api/... downloads (database backup, catalog export) → saved to Downloads/HypoLab
//   • <a download href="blob:…"> exports (CSV evidence, prompt library JSON) → Downloads/HypoLab
//   • navigator.clipboard.writeText → native clipboard
// In a normal browser nothing here runs.

export interface HypoLabAndroidBridge {
  appInfo(): string;
  openExternal(url: string): void;
  download(url: string, filename: string): void;
  saveBase64(filename: string, mime: string, base64: string): void;
  copyText(text: string): void;
  requestUnrestrictedBattery(): void;
  restartApp(): void;
}

declare global {
  interface Window { HypoLabAndroid?: HypoLabAndroidBridge }
}

export interface AndroidInfo {
  version: string; versionCode: number; android: string; sdk: number;
  device: string; abi: string; batteryUnrestricted: boolean;
}

export function androidBridge(): HypoLabAndroidBridge | undefined {
  return typeof window !== "undefined" ? window.HypoLabAndroid : undefined;
}

export const isAndroidApp = (): boolean => !!androidBridge();

export function androidInfo(): AndroidInfo | null {
  const b = androidBridge();
  if (!b) return null;
  try { return JSON.parse(b.appInfo()) as AndroidInfo; } catch { return null; }
}

/** Open a URL outside the app (browser) when running on Android; otherwise a new tab. */
export function openExternal(url: string) {
  const b = androidBridge();
  if (b) b.openExternal(url);
  else window.open(url, "_blank", "noopener");
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });
}

async function saveBlobUrl(b: HypoLabAndroidBridge, href: string, filename: string) {
  const blob = await (await fetch(href)).blob();
  b.saveBase64(filename || "hypolab-export", blob.type || "application/octet-stream", await blobToBase64(blob));
}

/** Handle an anchor natively when it would download or leave the app. Returns true if handled. */
function handleAnchor(b: HypoLabAndroidBridge, a: HTMLAnchorElement): boolean {
  const href = a.getAttribute("href");
  if (!href || href.startsWith("#") || href.startsWith("javascript:")) return false;
  let url: URL;
  try { url = new URL(href, location.href); } catch { return false; }
  if (url.protocol === "blob:" || url.protocol === "data:") {
    if (!a.hasAttribute("download")) return false;
    void saveBlobUrl(b, url.href, a.getAttribute("download") || "");
    return true;
  }
  if (url.origin !== location.origin) {
    if (url.protocol === "http:" || url.protocol === "https:") { b.openExternal(url.href); return true; }
    return false;
  }
  if (url.pathname.startsWith("/api/")) {   // e.g. "Backup database"
    b.download(url.href, a.getAttribute("download") || "");
    return true;
  }
  return false;
}

export function installAndroidShims() {
  const b = androidBridge();
  if (!b) return;
  document.documentElement.classList.add("android-app");

  // A truthy stand-in for the window object window.open would return, so callers that check
  // "did the popup open?" (BRAIN biometrics) report success.
  const external = { closed: false, close() {}, focus() {}, blur() {}, postMessage() {} } as unknown as Window;
  const nativeOpen = window.open.bind(window);
  window.open = ((url?: string | URL, target?: string, features?: string) => {
    if (url) {
      try {
        const u = new URL(String(url), location.href);
        if (u.origin !== location.origin) { b.openExternal(u.href); return external; }
        if (u.pathname.startsWith("/api/")) { b.download(u.href, ""); return external; }
      } catch { /* fall through */ }
    }
    return nativeOpen(url as string, target, features);
  }) as typeof window.open;

  // Clicks on links in the document…
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented) return;
    const a = (e.target as Element | null)?.closest?.("a");
    if (a instanceof HTMLAnchorElement && handleAnchor(b, a)) e.preventDefault();
  }, true);

  // …and programmatic clicks on detached <a download> elements (export helpers do this).
  const nativeClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
    if (!this.isConnected && handleAnchor(b, this)) return;
    return nativeClick.call(this);
  };

  const clip = navigator.clipboard;
  const writeText = (text: string) => { b.copyText(String(text)); return Promise.resolve(); };
  try {
    if (clip) Object.defineProperty(clip, "writeText", { value: writeText, configurable: true });
    else Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  } catch { /* keep the browser implementation */ }
}
