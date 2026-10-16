import { useEffect, useRef, useState, type ReactNode } from "react";

export interface Series {
  key: string;
  label: string;
  color: string; // a --series-N token
  values: number[];
  /** Optional extra line for the tooltip, per bucket. */
  note?: (i: number) => string;
}

const H = 220;
const M = { top: 12, right: 64, bottom: 26, left: 36 };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Round the axis max up to a readable step and return the ticks. */
function niceTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = Math.max(1, [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw)!);
  const ticks = [];
  for (let t = 0; t <= max + step - 1e-9; t += step) ticks.push(Math.round(t * 100) / 100);
  return ticks;
}

export function Legend({ series }: { series: Pick<Series, "key" | "label" | "color">[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x + 12, 0), width - 190);
  return (
    <div role="status" className="pointer-events-none absolute top-2 z-10 w-44 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
         style={{ left }}>
      {children}
    </div>
  );
}

function XAxis({ labels, x, height }: { labels: string[]; x: (i: number) => number; height: number }) {
  const every = Math.max(1, Math.ceil(labels.length / 8));
  return (
    <g className="fill-muted text-[10px]">
      {labels.map((l, i) => (i % every === 0 || i === labels.length - 1) && (
        <text key={i} x={x(i)} y={height - 8} textAnchor="middle">{l}</text>
      ))}
    </g>
  );
}

function YGrid({ ticks, y, width }: { ticks: number[]; y: (v: number) => number; width: number }) {
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
          <text x={M.left - 6} y={y(t) + 3} textAnchor="end" className="fill-muted text-[10px]">{t}</text>
        </g>
      ))}
    </g>
  );
}

/** Multi-series line chart with a crosshair tooltip. One y-axis. */
export function LineChart({ labels, tooltipLabels, series, label }: {
  labels: string[]; tooltipLabels: string[]; series: Series[]; label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const ticks = niceTicks(Math.max(0, ...series.flatMap((s) => s.values)));
  const top = ticks[ticks.length - 1];
  const x = (i: number) => M.left + (n <= 1 ? 0 : (i / (n - 1)) * (width - M.left - M.right));
  const y = (v: number) => M.top + (1 - v / top) * (H - M.top - M.bottom);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - M.left) / (width - M.left - M.right)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  };

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={H} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <YGrid ticks={ticks} y={y} width={width} />
        <XAxis labels={labels} x={x} height={H} />
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} stroke="var(--muted)" strokeDasharray="3 3" />}
        {series.map((s) => (
          <g key={s.key}>
            <polyline fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                      points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
            {/* Direct label at the line's end (few series only). */}
            {series.length <= 4 && n > 0 && (
              <text x={x(n - 1) + 6} y={y(s.values[n - 1]) + 3} className="fill-text text-[11px] font-medium">{s.label}</text>
            )}
            {hover !== null && (
              <circle cx={x(hover)} cy={y(s.values[hover])} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            )}
          </g>
        ))}
      </svg>
      {hover !== null && (
        <Tooltip x={x(hover)} width={width}>
          <p className="mb-1 font-medium">{tooltipLabels[hover]}</p>
          {series.map((s) => (
            <p key={s.key} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-muted">
                <span className="size-2 rounded-sm" style={{ background: s.color }} aria-hidden />{s.label}
              </span>
              <span className="tabular font-medium">{s.values[hover]}</span>
              {s.note && <span className="sr-only">{s.note(hover)}</span>}
            </p>
          ))}
          {series.some((s) => s.note) && (
            <p className="mt-1 text-muted">{series.filter((s) => s.note).map((s) => s.note!(hover)).join(" · ")}</p>
          )}
        </Tooltip>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/** Stacked bars (one stack per bucket) with a per-bar tooltip. */
export function StackedBars({ labels, tooltipLabels, series, label }: {
  labels: string[]; tooltipLabels: string[]; series: Series[]; label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const totals = labels.map((_, i) => series.reduce((sum, s) => sum + s.values[i], 0));
  const ticks = niceTicks(Math.max(0, ...totals));
  const top = ticks[ticks.length - 1];
  const plotW = width - M.left - 16;
  const band = plotW / Math.max(1, n);
  const barW = Math.max(2, Math.min(28, band - 2));
  const x = (i: number) => M.left + band * i + band / 2;
  const y = (v: number) => M.top + (1 - v / top) * (H - M.top - M.bottom);
  const GAP = 2;

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={H} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
        <YGrid ticks={ticks} y={y} width={width + M.right - 16} />
        <XAxis labels={labels} x={x} height={H} />
        {labels.map((_, i) => {
          let base = 0;
          const segs = series.filter((s) => s.values[i] > 0);
          return (
            <g key={i} onPointerEnter={() => setHover(i)}>
              {/* Hit target taller and wider than the bar. */}
              <rect x={x(i) - band / 2} y={M.top} width={band} height={H - M.top - M.bottom} fill="transparent" />
              {segs.map((s, j) => {
                const y0 = y(base), y1 = y(base + s.values[i]);
                base += s.values[i];
                const h = Math.max(0, y0 - y1 - (j > 0 ? GAP : 0));
                const isTop = j === segs.length - 1;
                return isTop
                  ? <path key={s.key} d={roundedTop(x(i) - barW / 2, y1, barW, h, 4)} fill={s.color} />
                  : <rect key={s.key} x={x(i) - barW / 2} y={y1} width={barW} height={h} fill={s.color} />;
              })}
            </g>
          );
        })}
      </svg>
      {hover !== null && totals[hover] > 0 && (
        <Tooltip x={x(hover)} width={width}>
          <p className="mb-1 font-medium">{tooltipLabels[hover]} · {totals[hover]} total</p>
          {series.filter((s) => s.values[hover] > 0).map((s) => (
            <p key={s.key} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-muted">
                <span className="size-2 rounded-sm" style={{ background: s.color }} aria-hidden />{s.label}
              </span>
              <span className="tabular font-medium">{s.values[hover]}</span>
            </p>
          ))}
        </Tooltip>
      )}
    </div>
  );
}

/** Accessible table view of the same data. */
export function DataTable({ labels, series }: { labels: string[]; series: Series[] }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-xs text-accent">Show as table</summary>
      <div className="mt-2 max-h-64 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-surface text-muted">
            <tr><th className="py-1 pr-3 font-medium">Time</th>{series.map((s) => <th key={s.key} className="py-1 pr-3 font-medium">{s.label}</th>)}</tr>
          </thead>
          <tbody className="tabular">
            {labels.map((l, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-1 pr-3">{l}</td>
                {series.map((s) => <td key={s.key} className="py-1 pr-3">{s.values[i]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
