import { ReactNode, useState } from "react";

export const BAND_LABEL: Record<string, string> = {
  observe: "Observe", notify_owner: "Notify owner", notify_security: "Notify security", slow: "Slow or hold", block: "Block",
};

export function Chip({ kind = "n", children }: { kind?: "ok" | "warn" | "crit" | "info" | "n"; children: ReactNode }) {
  return <span className={`chip ${kind}`}>{children}</span>;
}

/** One chip that says what Varuna did with an action. */
export function Verdict({ verdict, band }: { verdict?: string; band?: string }) {
  if (verdict === "block") return <Chip kind="crit">Blocked</Chip>;
  if (verdict === "hold") return <Chip kind="warn">Held for approval</Chip>;
  if (verdict === "delay") return <Chip kind="warn">Slowed</Chip>;
  if (band === "notify_security" || band === "slow") return <Chip kind="warn">Alerted security</Chip>;
  if (band === "notify_owner") return <Chip kind="info">Notified owner</Chip>;
  return <Chip kind="ok">Allowed</Chip>;
}

export function Scores({ dev, risk }: { dev?: number; risk?: number }) {
  const s = Math.max(dev ?? 0, risk ?? 0);
  const kind = s >= 90 ? "crit" : s >= 60 ? "warn" : s >= 40 ? "info" : "ok";
  return <Chip kind={kind}>{Math.round(dev ?? 0)} / {Math.round(risk ?? 0)}</Chip>;
}

export function Panel({ title, note, children, right }: { title?: ReactNode; note?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="panel">
      {(title || right) && <h5 className="ph"><span style={{ color: "var(--ink)", fontWeight: 600, fontSize: 13 }}>{title}</span>{right ?? (note && <span>{note}</span>)}</h5>}
      {children}
    </section>
  );
}

export function KPI({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "up" | "dn" }) {
  return (
    <section className="panel kpi">
      <h5 className="muted" style={{ fontWeight: 500 }}>{label}</h5>
      <div className="v">{value}</div>
      <div className={`d ${tone || ""}`}>{sub}</div>
    </section>
  );
}

export function Dots({ n }: { n: number }) {
  const cls = n >= 4 ? "f" : "m";
  return <span className="dots" title={`Severity ${n} of 5`}>{[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= n ? cls : ""} />)}</span>;
}

export function Spark({ data, color = "var(--accent)" }: { data: number[]; color?: string }) {
  const w = 64, h = 18, max = Math.max(1, ...data);
  const pts = data.map((v, i) => `${(i * w) / Math.max(1, data.length - 1)},${h - 2 - (v / max) * (h - 4)}`).join(" ");
  return <svg width={w} height={h} aria-hidden="true"><polyline fill="none" stroke={color} strokeWidth="1.6" points={pts} /></svg>;
}

export function Bars({ items, max }: { items: { label: string; value: number }[]; max?: number }) {
  const m = max ?? Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="bars">
      {items.map((i) => {
        const pct = (i.value / m) * 100;
        return (
          <div className="b" key={i.label}>
            <span>{i.label}</span>
            <div className="track"><div className={`fill ${pct > 70 ? "hi" : pct > 40 ? "mid" : ""}`} style={{ width: `${pct}%` }} /></div>
            <span className="num">{i.value}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Highest risk score per hour, drawn against one scale, with the slow and block thresholds. */
export function RiskChart({ series, slow = 75, block = 90 }: { series: { t: string; max_risk: number }[]; slow?: number; block?: number }) {
  const W = 640, H = 210, L = 34, R = 12, T = 14, B = 26, pw = W - L - R, ph = H - T - B;
  const n = series.length;
  const X = (i: number) => L + (n <= 1 ? 0 : (i * pw) / (n - 1));
  const Y = (v: number) => T + ph - (Math.min(100, v) / 100) * ph;
  const pts = series.map((s, i) => `${X(i)},${Y(s.max_risk)}`).join(" ");
  let pk = 0;
  series.forEach((s, i) => { if (s.max_risk > series[pk].max_risk) pk = i; });
  const label = (i: number) => new Date(series[i].t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const ticks = n > 1 ? [0, Math.round((n - 1) / 4), Math.round((n - 1) / 2), Math.round((3 * (n - 1)) / 4), n - 1] : [];
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
         aria-label={`Highest risk score per hour. Peak ${Math.round(series[pk]?.max_risk ?? 0)}.`}>
      {[0, 25, 50, 75, 100].map((g) => (
        <g key={g}><line x1={L} x2={W - R} y1={Y(g)} y2={Y(g)} stroke="var(--line)" /><text x={L - 6} y={Y(g) + 3} textAnchor="end">{g}</text></g>
      ))}
      {[[slow, "Slow", "var(--warn)"], [block, "Block", "var(--crit)"]].map(([v, name, c]) => (
        <g key={name as string}>
          <line x1={L} x2={W - R} y1={Y(v as number)} y2={Y(v as number)} stroke={c as string} strokeWidth="1.2" strokeDasharray="4 3" />
          <text x={L + 4} y={Y(v as number) - 4} style={{ fill: c as string }}>{name as string} {v as number}</text>
        </g>
      ))}
      {n > 1 && <>
        <polygon points={`${X(0)},${Y(0)} ${pts} ${X(n - 1)},${Y(0)}`} fill="var(--accent)" opacity=".14" />
        <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
        {series[pk].max_risk > 0 && <>
          <circle cx={X(pk)} cy={Y(series[pk].max_risk)} r="4.5" fill={series[pk].max_risk >= block ? "var(--crit)" : "var(--accent)"} />
          <text x={X(pk) + (pk > n * 0.8 ? -8 : 8)} y={Y(series[pk].max_risk) - 8} textAnchor={pk > n * 0.8 ? "end" : "start"}>{Math.round(series[pk].max_risk)}</text>
        </>}
        {ticks.map((i, k) => <text key={k} x={X(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>{label(i)}</text>)}
      </>}
    </svg>
  );
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }

export function CopyBtn({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn s" onClick={async () => {
      try { await navigator.clipboard.writeText(text); setDone(true); window.setTimeout(() => setDone(false), 1500); } catch { /* clipboard blocked */ }
    }}>{done ? "Copied" : label}</button>
  );
}

export function Tags({ items, onRemove, kind, hl, onHover }: {
  items: string[]; onRemove?: (i: number) => void; kind?: "av" | "ok"; hl?: number | null; onHover?: (i: number | null) => void;
}) {
  return (
    <div className="tagrow">
      {items.map((t, i) => (
        <span key={`${t}-${i}`} className={`tag ${kind || ""} ${hl === i ? "hl" : ""}`} onMouseEnter={() => onHover?.(i)} onMouseLeave={() => onHover?.(null)}>
          {t}{onRemove && <button aria-label={`Remove ${t}`} onClick={() => onRemove(i)}>×</button>}
        </span>
      ))}
    </div>
  );
}

export function AddTag({ placeholder, onAdd }: { placeholder: string; onAdd: (v: string) => void }) {
  const [v, setV] = useState("");
  const commit = () => { const s = v.trim(); if (s) { onAdd(s); setV(""); } };
  return (
    <input className="input" style={{ width: 230 }} value={v} placeholder={placeholder} aria-label={placeholder}
           onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} onBlur={commit} />
  );
}
