import { useEffect } from "react";

// Adds spinner feedback to async buttons that become disabled after a click. Existing explicit
// spinners win; this fills the gaps without showing activity on controls disabled before a click.
export function useButtonBusyFeedback() {
  useEffect(() => {
    let clicked: HTMLButtonElement | null = null;
    const active = new Map<string,HTMLButtonElement>();
    const onClick = (event: MouseEvent) => {
      clicked = (event.target as HTMLElement | null)?.closest("button") as HTMLButtonElement | null;
      queueMicrotask(() => { clicked = null; });
    };
    const start = (event:Event) => { if (clicked) { const id=(event as CustomEvent<string>).detail;clicked.setAttribute("data-click-busy", "true");active.set(id,clicked); } };
    const end = (event:Event) => { const id=(event as CustomEvent<string>).detail,button=active.get(id);if(button){active.delete(id);if(![...active.values()].includes(button))button.removeAttribute("data-click-busy");} };
    document.addEventListener("click", onClick, true);
    window.addEventListener("ace-api-start", start); window.addEventListener("ace-api-end", end);
    return () => { document.removeEventListener("click", onClick, true);window.removeEventListener("ace-api-start",start);window.removeEventListener("ace-api-end",end); };
  }, []);
}
