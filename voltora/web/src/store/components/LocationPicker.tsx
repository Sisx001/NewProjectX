import { useMemo } from "react";
import { useLocations } from "../lib/data";

export type Loc = { division: string; district: string; upazila: string };
export const emptyLoc: Loc = { division: "", district: "", upazila: "" };

/** Division → district → upazila cascade with Bangla names. `compact` hides upazila (delivery estimator). */
export function LocationPicker({ value, onChange, compact, idPrefix = "loc", errors }: { value: Loc; onChange: (v: Loc) => void; compact?: boolean; idPrefix?: string; errors?: Partial<Record<keyof Loc, string>> }) {
  const q = useLocations();
  const divisions = q.data?.divisions ?? [];
  const div = useMemo(() => divisions.find((d) => d.name === value.division), [divisions, value.division]);
  const dist = useMemo(() => div?.districts.find((d) => d.name === value.district), [div, value.district]);
  const field = (k: keyof Loc, label: string, opts: { name: string; bn_name: string }[], disabled: boolean, set: (v: string) => void) => (
    <div className="s-field">
      <label htmlFor={`${idPrefix}-${k}`}>{label}</label>
      <select id={`${idPrefix}-${k}`} className="s-input" value={value[k]} disabled={disabled} onChange={(e) => set(e.target.value)} aria-invalid={!!errors?.[k]} autoComplete="off">
        <option value="">{q.isLoading ? "Loading…" : `Select ${label.toLowerCase()}`}</option>
        {opts.map((o) => <option key={o.name} value={o.name}>{o.name}{o.bn_name ? ` — ${o.bn_name}` : ""}</option>)}
      </select>
      {errors?.[k] ? <p className="s-error">{errors[k]}</p> : null}
    </div>
  );
  return (
    <div className="s-locgrid" data-compact={compact}>
      {q.isError ? <p className="s-error" role="alert">Couldn't load delivery areas. Please retry in a moment.</p> : null}
      {field("division", "Division", divisions, false, (v) => onChange({ division: v, district: "", upazila: "" }))}
      {field("district", "District", div?.districts ?? [], !div, (v) => onChange({ ...value, district: v, upazila: "" }))}
      {compact ? null : field("upazila", "Upazila / Thana", dist?.upazilas ?? [], !dist, (v) => onChange({ ...value, upazila: v }))}
    </div>
  );
}
