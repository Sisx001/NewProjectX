import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Bookmark, Trash2 } from "lucide-react";
import { Button, Checkbox, Empty, Input, Select, Skeleton, cx } from "./index";
import { usePref } from "@/lib/auth";

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  sortable?: boolean;
  align?: "left" | "right";
  width?: number | string;
  hideBelow?: number; // hide column under this viewport width (px)
};

export type BulkAction<T> = { label: string; onRun: (rows: T[]) => void; danger?: boolean };

type Props<T> = {
  rows: T[] | undefined;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: string | null;
  onRowClick?: (row: T) => void;
  bulkActions?: BulkAction<T>[];
  sort?: { key: string; dir: "asc" | "desc" };
  onSort?: (s: { key: string; dir: "asc" | "desc" }) => void;
  page?: number;
  pageSize?: number;
  total?: number;
  onPage?: (p: number) => void;
  empty?: ReactNode;
  label: string;
};

export function DataTable<T>({ rows, columns, rowKey, loading, error, onRowClick, bulkActions, sort, onSort, page = 1, pageSize = 50, total, onPage, empty, label }: Props<T>) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const keys = useMemo(() => (rows ?? []).map(rowKey), [rows, rowKey]);
  const allSel = keys.length > 0 && keys.every((k) => selected.has(k));
  const someSel = keys.some((k) => selected.has(k));
  const selRows = (rows ?? []).filter((r) => selected.has(rowKey(r)));
  const width = typeof window !== "undefined" ? window.innerWidth : 1440;
  const cols = columns.filter((c) => !c.hideBelow || width >= c.hideBelow);
  const pages = total !== undefined ? Math.max(1, Math.ceil(total / pageSize)) : 1;

  return (
    <div className="v-stack--sm" style={{ display: "grid" }}>
      {bulkActions && someSel ? (
        <div className="v-row" role="toolbar" aria-label="Bulk actions" style={{ padding: "8px 12px", background: "var(--v-ink)", color: "var(--v-bg)", borderRadius: "var(--v-radius)" }}>
          <strong className="v-num">{selRows.length} selected</strong>
          <span style={{ flex: 1 }} />
          {bulkActions.map((a) => (
            <Button key={a.label} size="sm" variant={a.danger ? "danger" : "default"} onClick={() => { a.onRun(selRows); setSelected(new Set()); }}>
              {a.label}
            </Button>
          ))}
          <Button size="sm" variant="ghost" style={{ color: "inherit" }} onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      ) : null}
      <div className="v-table-wrap">
        <table className="v-table" aria-label={label} aria-busy={loading || undefined}>
          <thead>
            <tr>
              {bulkActions ? (
                <th style={{ width: 40 }}>
                  <Checkbox label={<span className="v-sr">Select all</span>} checked={allSel} indeterminate={someSel && !allSel} onChange={(v) => setSelected(v ? new Set(keys) : new Set())} />
                </th>
              ) : null}
              {cols.map((c) => (
                <th key={c.key} style={{ width: c.width, textAlign: c.align === "right" ? "right" : undefined }} aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
                  {c.sortable && onSort ? (
                    <button className="v-sort" onClick={() => onSort({ key: c.key, dir: sort?.key === c.key && sort.dir === "desc" ? "asc" : "desc" })}>
                      {c.header}
                      {sort?.key === c.key ? sort.dir === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : null}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && !rows
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i}>
                    {bulkActions ? <td /> : null}
                    {cols.map((c) => (
                      <td key={c.key}>
                        <Skeleton h={12} w={`${50 + ((i * 17 + c.key.length * 7) % 40)}%`} />
                      </td>
                    ))}
                  </tr>
                ))
              : null}
            {(rows ?? []).map((r) => {
              const k = rowKey(r);
              return (
                <tr
                  key={k}
                  data-clickable={!!onRowClick}
                  aria-selected={selected.has(k) || undefined}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("input,button,a,label")) return;
                    onRowClick?.(r);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && onRowClick) onRowClick(r);
                  }}
                  tabIndex={onRowClick ? 0 : undefined}
                >
                  {bulkActions ? (
                    <td>
                      <Checkbox
                        label={<span className="v-sr">Select row</span>}
                        checked={selected.has(k)}
                        onChange={(v) => {
                          const n = new Set(selected);
                          if (v) n.add(k);
                          else n.delete(k);
                          setSelected(n);
                        }}
                      />
                    </td>
                  ) : null}
                  {cols.map((c) => (
                    <td key={c.key} className={cx(c.align === "right" && "v-right v-num")}>
                      {c.cell(r)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {error ? <div className="v-alert v-alert--danger" style={{ margin: 12 }}>{error}</div> : null}
        {!loading && !error && rows && rows.length === 0 ? empty ?? <Empty title="Nothing here yet" /> : null}
      </div>
      {total !== undefined && onPage && total > pageSize ? (
        <div className="v-row v-row--between">
          <span className="v-muted v-num">
            {(page - 1) * pageSize + 1}–{Math.min(total, page * pageSize)} of {total.toLocaleString()}
          </span>
          <div className="v-row">
            <Button size="sm" icon aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}>
              <ChevronLeft size={16} />
            </Button>
            <span className="v-num">{page} / {pages}</span>
            <Button size="sm" icon aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}>
              <ChevronRight size={16} />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Saved views: named filter sets per list, stored per browser. */
export function SavedViews<F extends Record<string, unknown>>({ id, current, onApply }: { id: string; current: F; onApply: (f: F) => void }) {
  const [views, setViews] = usePref<{ name: string; filters: F }[]>(`views:${id}`, []);
  const [name, setName] = useState("");
  return (
    <div className="v-row v-row--wrap">
      {views.length ? (
        <Select aria-label="Saved views" value="" onChange={(e) => { const v = views.find((x) => x.name === e.target.value); if (v) onApply(v.filters); }} style={{ width: "auto" }}>
          <option value="">Saved views…</option>
          {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
        </Select>
      ) : null}
      <Input aria-label="View name" placeholder="Name this view" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 160 }} />
      <Button size="sm" disabled={!name.trim()} onClick={() => { setViews([...views.filter((v) => v.name !== name.trim()), { name: name.trim(), filters: current }]); setName(""); }}>
        <Bookmark size={14} /> Save view
      </Button>
      {views.length ? (
        <Button size="sm" variant="ghost" onClick={() => setViews([])} aria-label="Delete saved views"><Trash2 size={14} /></Button>
      ) : null}
    </div>
  );
}
