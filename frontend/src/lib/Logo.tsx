// HypoLab mark — the two legs of an "H" joined by a rising research signal.
// Uses the theme accent so it follows every appearance option.
export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-label="HypoLab">
      <rect width="32" height="32" rx="8" fill="var(--bg-2, #06120c)" stroke="var(--acc)" strokeOpacity="0.45" />
      <path d="M9.5 8.5v15M22.5 8.5v15" fill="none" stroke="var(--acc)" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M9.5 19.2 L13.6 15.6 L16.9 17.4 L22.5 12.3" fill="none" stroke="var(--fg, #e3f2ea)"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="22.5" cy="12.3" r="1.9" fill="var(--fg, #e3f2ea)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
      <LogoMark size={28} />
      <span style={{
        fontFamily: "'Bahnschrift', 'Century Gothic', 'Trebuchet MS', 'Segoe UI Semibold', system-ui, sans-serif",
        fontWeight: 800, fontSize: 21, letterSpacing: "0.6px", lineHeight: "28px",
      }}>Hypo<span style={{ color: "var(--acc)" }}>Lab</span></span>
    </span>
  );
}
