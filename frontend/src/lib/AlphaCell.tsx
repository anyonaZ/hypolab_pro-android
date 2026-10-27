import { useToast } from "./toast";

// A compact alpha-expression cell: shows a truncated form, reveals the WHOLE alpha on hover
// (native tooltip — never clipped by a scrolling panel), and copies the full expression on click
// of the copy button. Used in Results and Simulation so both behave the same.
export function AlphaCell({ expr, max = 52 }: { expr?: string | null; max?: number }) {
  const { toast } = useToast();
  const full = (expr || "").trim();
  if (!full) return <span className="mut">—</span>;
  const short = full.length > max ? full.slice(0, max) + "…" : full;
  return (
    <span className="alpha-cell" title={full}>
      <code>{short}</code>
      <button className="alpha-copy" title="Copy the full alpha" aria-label="Copy the full alpha"
        onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(full); toast("Alpha copied."); }}>
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
      </button>
    </span>
  );
}
