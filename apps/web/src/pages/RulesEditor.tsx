import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Link, useParams } from "react-router";

import { api, isLine, type Point, type Rule, type RuleInput, type RuleType } from "../api";
import { useAuth } from "../auth";
import { Button, Card, Empty, ErrorNote, inputCls, selectCls } from "../components/ui";

const TYPES: { type: RuleType; label: string; hint: string; color: string }[] = [
  { type: "zone_entry", label: "Zone", hint: "Alert when someone enters", color: "var(--bad)" },
  { type: "line_crossing", label: "Tripwire", hint: "Alert when someone crosses a line", color: "var(--warn)" },
  { type: "loitering", label: "Loitering", hint: "Alert when someone stays too long", color: "var(--accent)" },
  { type: "occupancy", label: "Occupancy", hint: "Alert when too many people are inside", color: "#9085e9" },
  { type: "counting_line", label: "Counter", hint: "Count people and vehicles going in and out (no alerts)", color: "var(--ok)" },
];
const typeInfo = (t: RuleType) => TYPES.find((x) => x.type === t)!;
const CLASSES = ["person", "bicycle", "car", "motorcycle", "bus", "truck"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const clamp = (v: number) => Math.min(1, Math.max(0, v));
const round = (p: Point): Point => [Math.round(p[0] * 10000) / 10000, Math.round(p[1] * 10000) / 10000];

function Shape({ type, points, color, selected, draft }: {
  type: RuleType; points: Point[]; color: string; selected?: boolean; draft?: boolean;
}) {
  const d = points.map((p) => p.join(",")).join(" ");
  const common = { stroke: color, strokeWidth: selected ? 3 : 2, vectorEffect: "non-scaling-stroke" as const,
                   strokeDasharray: draft ? "6 4" : undefined };
  if (isLine(type) || (draft && points.length < 3)) {
    return <polyline points={d} fill="none" {...common} />;
  }
  return <polygon points={d} fill={color} fillOpacity={selected ? 0.28 : 0.16} {...common} />;
}

/** Arrow across a line pointing the way that counts as "in" (left to right, facing from point 1
 * to point 2). Angles are computed in pixels, since the surface is stretched to the camera's shape. */
function InArrow({ points, aspect, color }: { points: Point[]; aspect: number; color: string }) {
  const [[x1, y1], [x2, y2]] = points;
  const angle = (Math.atan2(-(x2 - x1), (y2 - y1) * (1 / aspect)) * 180) / Math.PI;
  return (
    <span aria-hidden className="pointer-events-none absolute flex items-center text-xs font-semibold text-white"
          style={{ left: `${((x1 + x2) / 2) * 100}%`, top: `${((y1 + y2) / 2) * 100}%`,
                   transform: `translate(-50%, -50%) rotate(${angle}deg) translateX(18px)` }}>
      <svg width="22" height="12" viewBox="0 0 22 12" style={{ filter: "drop-shadow(0 0 2px #000)" }}>
        <path d="M0 6h16M11 1l6 5-6 5" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span style={{ transform: `rotate(${-angle}deg)`, textShadow: "0 0 3px #000" }}>in</span>
    </span>
  );
}

function RuleForm({ rule, canEdit, onDeleted }: { rule: Rule; canEdit: boolean; onDeleted: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Rule>(rule);
  useEffect(() => setForm(rule), [rule]);
  const set = <K extends keyof Rule>(k: K, v: Rule[K]) => setForm((f) => ({ ...f, [k]: v }));
  const invalidate = () => qc.invalidateQueries({ queryKey: ["rules", rule.camera_id] });

  const save = useMutation({
    mutationFn: () => {
      const { id: _id, camera_id: _cam, type: _type, points: _points, ...body } = form;
      return api.updateRule(rule.id, body);
    },
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: () => api.deleteRule(rule.id), onSuccess: () => { invalidate(); onDeleted(); } });
  const dirty = JSON.stringify(form) !== JSON.stringify(rule);

  return (
    <form className="flex flex-col gap-3 p-4 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <fieldset disabled={!canEdit} className="contents">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Name</span>
          <input className={inputCls} value={form.name} onChange={(e) => set("name", e.target.value)} required maxLength={120} />
        </label>
        {form.type === "counting_line" && (
          <p className="text-xs text-muted">
            Counts crossings without raising alerts, even while the system is disarmed. Crossing the way the arrow points
            counts as <strong className="text-text">in</strong>. Totals are in Analytics → Entries and exits.
          </p>
        )}
        <div className="grid grid-cols-2 gap-3">
          {form.type !== "counting_line" && (<>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Severity</span>
            <select className={selectCls} value={form.severity} onChange={(e) => set("severity", e.target.value as Rule["severity"])}>
              <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Repeat alerts after (s)</span>
            <input className={inputCls} type="number" min={0} max={86400} value={form.cooldown_seconds}
                   onChange={(e) => set("cooldown_seconds", Number(e.target.value))} />
          </label>
          </>)}
          {form.type === "loitering" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Alert after staying (s)</span>
              <input className={inputCls} type="number" min={1} max={86400} value={form.dwell_seconds}
                     onChange={(e) => set("dwell_seconds", Number(e.target.value))} />
            </label>
          )}
          {form.type === "occupancy" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Alert above (people)</span>
              <input className={inputCls} type="number" min={0} max={1000} value={form.max_count}
                     onChange={(e) => set("max_count", Number(e.target.value))} />
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Minimum confidence (%)</span>
            <input className={inputCls} type="number" min={0} max={100} step={5}
                   value={Math.round(form.min_confidence * 100)} title="0 = use the worker's default. Raise it to cut false alarms on this rule."
                   onChange={(e) => set("min_confidence", Math.min(1, Math.max(0, Number(e.target.value) / 100)))} />
          </label>
          {form.type === "line_crossing" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Direction (arrow = in)</span>
              <select className={selectCls} value={form.direction} onChange={(e) => set("direction", e.target.value as Rule["direction"])}>
                <option value="both">Both ways</option><option value="in">In only</option><option value="out">Out only</option>
              </select>
            </label>
          )}
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-xs font-medium text-muted">Detect</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {CLASSES.map((c) => (
              <label key={c} className="flex items-center gap-1.5 capitalize">
                <input type="checkbox" checked={form.classes.includes(c)}
                       onChange={(e) => set("classes", e.target.checked ? [...form.classes, c] : form.classes.filter((x) => x !== c))} />
                {c}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="flex flex-col gap-2">
          <legend className="text-xs font-medium text-muted">Active</legend>
          <select className={selectCls} aria-label="When the rule is active" value={form.schedule ? "scheduled" : "always"}
                  onChange={(e) => set("schedule", e.target.value === "always" ? null
                    : { days: [0, 1, 2, 3, 4, 5, 6], start: "22:00", end: "06:00" })}>
            <option value="always">Always</option>
            <option value="scheduled">Only at certain times</option>
          </select>
          {form.schedule && (
            <>
              <div className="flex flex-wrap gap-1" role="group" aria-label="Days">
                {DAYS.map((d, i) => {
                  const on = form.schedule!.days.includes(i);
                  return (
                    <button key={d} type="button" aria-pressed={on}
                            onClick={() => {
                              const days = on ? form.schedule!.days.filter((x) => x !== i) : [...form.schedule!.days, i].sort();
                              if (days.length) set("schedule", { ...form.schedule!, days });
                            }}
                            className={`rounded-md border px-2 py-1 text-xs font-medium ${on ? "border-accent bg-accent/15 text-accent" : "border-line text-muted"}`}>
                      {d}
                    </button>
                  );
                })}
              </div>
              <div className="flex items-center gap-2">
                <input className={inputCls} type="time" aria-label="From" value={form.schedule.start}
                       onChange={(e) => set("schedule", { ...form.schedule!, start: e.target.value })} required />
                <span className="text-muted">to</span>
                <input className={inputCls} type="time" aria-label="Until" value={form.schedule.end}
                       onChange={(e) => set("schedule", { ...form.schedule!, end: e.target.value })} required />
              </div>
              <p className="text-xs text-muted">
                {form.schedule.start === form.schedule.end ? "All day on the selected days."
                  : form.schedule.start > form.schedule.end ? "Overnight: runs into the next morning."
                  : "Same-day window."} Times are the edge computer's local time.
              </p>
            </>
          )}
        </fieldset>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Enabled
        </label>
      </fieldset>
      {(save.error || remove.error) && <ErrorNote error={save.error || remove.error} />}
      {canEdit && (
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!dirty || save.isPending || form.classes.length === 0}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button variant="danger" className="ml-auto" disabled={remove.isPending}
                  onClick={() => confirm(`Delete rule "${rule.name}"?`) && remove.mutate()}>
            Delete
          </Button>
        </div>
      )}
    </form>
  );
}

export default function RulesEditor() {
  const cameraId = Number(useParams().id);
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const rules = useQuery({ queryKey: ["rules", cameraId], queryFn: () => api.rules(cameraId) });
  const camera = cameras.data?.find((c) => c.id === cameraId);

  const [frameSrc, setFrameSrc] = useState(() => api.frameUrl(cameraId));
  const [frameState, setFrameState] = useState<"loading" | "ok" | "error">("loading");
  const [aspect, setAspect] = useState("16 / 9");
  const [tool, setTool] = useState<RuleType | null>(null);
  const [draft, setDraft] = useState<Point[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [dragging, setDragging] = useState<{ ruleId: number; index: number; points: Point[] } | null>(null);
  const surface = useRef<HTMLDivElement>(null);

  const create = useMutation({
    mutationFn: (body: Partial<RuleInput>) => api.createRule(cameraId, body),
    onSuccess: (rule) => {
      qc.invalidateQueries({ queryKey: ["rules", cameraId] });
      setSelected(rule.id);
    },
  });
  const move = useMutation({
    mutationFn: ({ ruleId, points }: { ruleId: number; points: Point[] }) => api.updateRule(ruleId, { points }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["rules", cameraId] }),
  });

  const toPoint = (e: { clientX: number; clientY: number }): Point => {
    const r = surface.current!.getBoundingClientRect();
    return round([clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)]);
  };

  const finish = (points = draft) => {
    if (!tool) return;
    const n = (rules.data?.filter((r) => r.type === tool).length ?? 0) + 1;
    create.mutate({ type: tool, name: `${typeInfo(tool).label} ${n}`, points,
                    severity: tool === "zone_entry" ? "high" : "medium" });
    setTool(null);
    setDraft([]);
  };

  const onSurfaceClick = (e: React.MouseEvent) => {
    if (!tool) return;
    const pts = [...draft, toPoint(e)];
    if (isLine(tool) && pts.length === 2) finish(pts);
    else setDraft(pts);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!tool) return;
      if (e.key === "Escape") { setTool(null); setDraft([]); }
      if (e.key === "Enter" && !isLine(tool) && draft.length >= 3) finish();
      if (e.key === "Backspace") setDraft((d) => d.slice(0, -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // The ref is the source of truth (fast drags can deliver move/up before React re-renders);
  // the state copy drives rendering.
  const dragRef = useRef<typeof dragging>(null);
  const setDrag = (d: typeof dragging) => { dragRef.current = d; setDragging(d); };

  const startDrag = (e: ReactPointerEvent, rule: Rule, index: number) => {
    if (!isAdmin) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ ruleId: rule.id, index, points: rule.points.map((p) => [...p] as Point) });
  };
  const onDrag = (e: ReactPointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const points = [...d.points];
    points[d.index] = toPoint(e);
    setDrag({ ...d, points });
  };
  const endDrag = (e: ReactPointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const points = [...d.points];
    points[d.index] = toPoint(e);
    move.mutate({ ruleId: d.ruleId, points });
    // Show the dragged shape until the refetch lands.
    qc.setQueryData<Rule[]>(["rules", cameraId], (old) => old?.map((r) => (r.id === d.ruleId ? { ...r, points } : r)));
    setDrag(null);
  };

  const [aw, ah] = aspect.split("/").map(Number);
  const aspectRatio = aw && ah ? aw / ah : 16 / 9;
  const shown = (rules.data ?? []).map((r) => (dragging?.ruleId === r.id ? { ...r, points: dragging.points } : r));
  const selectedRule = rules.data?.find((r) => r.id === selected);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link to="/cameras" className="text-sm text-accent">← Cameras</Link>
          <h1 className="text-xl font-semibold">Rules · {camera?.name ?? `Camera ${cameraId}`}</h1>
        </div>
        <Button onClick={() => { setFrameState("loading"); setFrameSrc(api.frameUrl(cameraId)); }}>Refresh image</Button>
      </div>
      {rules.error && <ErrorNote error={rules.error} />}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex flex-col gap-3">
          {isAdmin && (
            <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Draw a rule">
              {TYPES.map((t) => (
                <Button key={t.type} variant={tool === t.type ? "primary" : "secondary"} title={t.hint}
                        onClick={() => { setTool(tool === t.type ? null : t.type); setDraft([]); setSelected(null); }}>
                  <span className="size-2.5 rounded-sm" style={{ background: t.color }} aria-hidden />
                  {t.label}
                </Button>
              ))}
              {tool && !isLine(tool) && (
                <Button variant="primary" disabled={draft.length < 3} onClick={() => finish()}>Finish ({draft.length} points)</Button>
              )}
            </div>
          )}
          <p className="text-xs text-muted" aria-live="polite">
            {tool
              ? isLine(tool)
                ? `Click two points to draw the ${tool === "counting_line" ? "counting line" : "tripwire"}. Facing from the first point to the second, crossing left to right counts as "in".`
                : "Click the corners of the area on the floor. Enter or Finish to save, Backspace to undo, Esc to cancel."
              : isAdmin
                ? "Pick a rule type to draw, or click a rule to edit it. Drag a selected rule's corners to adjust."
                : "Rules are managed by admins."}
            {" "}People are located by their feet, so draw zones on the floor.
          </p>

          <div
            ref={surface}
            onClick={onSurfaceClick}
            onPointerMove={onDrag}
            onPointerUp={endDrag}
            className={`relative overflow-hidden rounded-xl border border-line bg-black select-none ${tool ? "cursor-crosshair" : ""}`}
            style={{ aspectRatio: aspect, touchAction: dragging ? "none" : undefined }}
          >
            {/* Rules are normalized to the frame, so the surface takes the camera's own shape. */}
            <img src={frameSrc} alt={`Current view of ${camera?.name ?? "camera"}`} draggable={false}
                 onLoad={(e) => {
                   const img = e.currentTarget;
                   setAspect(`${img.naturalWidth} / ${img.naturalHeight}`);
                   setFrameState("ok");
                 }}
                 onError={() => setFrameState("error")}
                 className={`absolute inset-0 size-full object-fill ${frameState === "ok" ? "" : "invisible"}`} />
            {frameState !== "ok" && (
              <p className="absolute inset-0 grid place-items-center p-6 text-center text-sm text-white/70">
                {frameState === "loading" ? "Grabbing a frame from the camera…"
                  : "Couldn't get an image from this camera. Is it streaming? You can still draw rules."}
              </p>
            )}
            <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 size-full">
              {shown.map((r) => (
                <g key={r.id} opacity={r.enabled ? 1 : 0.4}>
                  <Shape type={r.type} points={r.points} color={typeInfo(r.type).color} selected={r.id === selected} />
                </g>
              ))}
              {tool && draft.length > 0 && <Shape type={tool} points={draft} color={typeInfo(tool).color} draft />}
            </svg>
            {/* Labels and handles are HTML so they don't stretch with the non-uniform SVG scale. */}
            {shown.map((r) => (
              <button key={r.id} type="button"
                      onClick={(e) => { if (!tool) { e.stopPropagation(); setSelected(r.id); } }}
                      className="absolute -translate-y-full rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-white"
                      style={{ left: `${Math.min(...r.points.map((p) => p[0])) * 100}%`,
                               top: `${Math.min(...r.points.map((p) => p[1])) * 100}%`,
                               background: typeInfo(r.type).color, pointerEvents: tool ? "none" : "auto" }}>
                {r.name}
              </button>
            ))}
            {shown.filter((r) => isLine(r.type) && (r.type === "counting_line" || r.direction !== "both" || r.id === selected)).map((r) => (
              <InArrow key={`arrow-${r.id}`} points={r.points} aspect={aspectRatio} color={typeInfo(r.type).color} />
            ))}
            {selectedRule && !tool && shown.find((r) => r.id === selected)!.points.map((p, i) => (
              <span key={i} role="presentation" onPointerDown={(e) => startDrag(e, selectedRule, i)}
                    className={`absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow ${isAdmin ? "cursor-grab" : ""}`}
                    style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%`, background: typeInfo(selectedRule.type).color }} />
            ))}
            {tool && draft.map((p, i) => (
              <span key={i} className="pointer-events-none absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white"
                    style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%`, background: typeInfo(tool).color }} />
            ))}
          </div>
          {create.error && <ErrorNote error={create.error} />}
        </div>

        <div className="flex flex-col gap-4">
          <Card title={`Rules (${rules.data?.length ?? 0})`}>
            {rules.data?.length === 0 && <Empty>No rules yet.{isAdmin && " Pick a type above and draw one."}</Empty>}
            <ul className="divide-y divide-line">
              {rules.data?.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => { setTool(null); setSelected(r.id); }}
                          className={`flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-surface-2 ${r.id === selected ? "bg-surface-2" : ""}`}>
                    <span className="size-2.5 shrink-0 rounded-sm" style={{ background: typeInfo(r.type).color }} aria-hidden />
                    <span className="min-w-0 flex-1 truncate font-medium">{r.name}</span>
                    <span className="text-xs text-muted">{typeInfo(r.type).label}{!r.enabled && " · off"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {selectedRule && (
            <Card title={`Edit ${selectedRule.name}`}>
              <RuleForm key={selectedRule.id} rule={selectedRule} canEdit={isAdmin} onDeleted={() => setSelected(null)} />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
