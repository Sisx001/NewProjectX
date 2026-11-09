import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Clock, Search, X } from "lucide-react";
import { get, qs } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useDebounced } from "../lib/data";
import { useRecentSearches } from "../lib/local";

type Suggest = { products: { slug: string; title: string; image?: { url: string; alt: string } | string | null; price_minor: number }[]; categories: { slug: string; name: string }[] };
type Opt = { key: string; label: string; sub?: string; img?: string; go: string; kind: "recent" | "product" | "category" | "query" };

const imgUrl = (i: Suggest["products"][number]["image"]) => (typeof i === "string" ? i : i?.url);

/** ARIA combobox: debounced suggestions, arrow/enter/escape, recent searches when empty. */
export default function SearchBox({ initial = "", autoFocus, onDone, currency = "BDT" }: { initial?: string; autoFocus?: boolean; onDone?: () => void; currency?: string }) {
  const nav = useNavigate();
  const id = useId();
  const [text, setText] = useState(initial);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const wrap = useRef<HTMLDivElement>(null);
  const recent = useRecentSearches();
  const dq = useDebounced(text.trim(), 220);
  const q = useQuery({ queryKey: ["suggest", dq], enabled: dq.length >= 2, queryFn: ({ signal }) => get<Suggest>(`/api/store/search/suggest${qs({ q: dq })}`, signal), staleTime: 30_000 });

  useEffect(() => setText(initial), [initial]);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const typed = text.trim();
  const opts: Opt[] = [];
  if (typed.length < 2) recent.items.forEach((r) => opts.push({ key: "r" + r, label: r, go: `/search?q=${encodeURIComponent(r)}`, kind: "recent" }));
  else if (q.data && dq === typed) {
    opts.push({ key: "q", label: `Search for “${typed}”`, go: `/search?q=${encodeURIComponent(typed)}`, kind: "query" });
    q.data.categories.forEach((c) => opts.push({ key: "c" + c.slug, label: c.name, sub: "Category", go: `/c/${c.slug}`, kind: "category" }));
    q.data.products.forEach((p) => opts.push({ key: "p" + p.slug, label: p.title, sub: formatMoney(p.price_minor, currency), img: imgUrl(p.image), go: `/p/${p.slug}`, kind: "product" }));
  }
  function choose(o: Opt | null) {
    setOpen(false);
    if (o) {
      if (o.kind === "query" || o.kind === "recent") recent.push(o.kind === "recent" ? o.label : typed);
      nav(o.go);
    } else if (typed) {
      recent.push(typed);
      nav(`/search?q=${encodeURIComponent(typed)}`);
    }
    onDone?.();
  }
  const lb = `${id}-lb`;
  return (
    <div className="s-search" ref={wrap}>
      <form role="search" onSubmit={(e) => { e.preventDefault(); choose(active >= 0 ? opts[active] ?? null : null); }}>
        <Search size={18} aria-hidden className="s-search__ico" />
        <input
          role="combobox" aria-expanded={open && opts.length > 0} aria-controls={lb} aria-autocomplete="list" aria-activedescendant={active >= 0 ? `${id}-o${active}` : undefined}
          aria-label="Search products" className="s-input s-search__in" type="search" enterKeyHint="search" autoComplete="off" autoFocus={autoFocus}
          placeholder="Search products, brands…" value={text} data-testid="search-input"
          onChange={(e) => { setText(e.target.value); setOpen(true); setActive(-1); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => (a + 1) % Math.max(1, opts.length)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a <= 0 ? opts.length - 1 : a - 1)); }
            else if (e.key === "Escape") { if (open) { e.stopPropagation(); setOpen(false); } else setText(""); }
          }}
        />
        {text ? <button type="button" className="s-search__clear" aria-label="Clear search" onClick={() => { setText(""); setActive(-1); }}><X size={16} /></button> : null}
      </form>
      {open && opts.length > 0 ? (
        <ul id={lb} role="listbox" className="s-search__list" aria-label={typed.length < 2 ? "Recent searches" : "Suggestions"}>
          {opts.map((o, i) => (
            <li key={o.key} id={`${id}-o${i}`} role="option" aria-selected={i === active} data-active={i === active} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); choose(o); }}>
              {o.img ? <img src={o.img} alt="" width={40} height={40} /> : <span className="s-search__sym">{o.kind === "recent" ? <Clock size={16} /> : <Search size={16} />}</span>}
              <span className="s-search__txt">{o.label}{o.sub ? <small>{o.sub}</small> : null}</span>
            </li>
          ))}
          {typed.length < 2 && recent.items.length ? <li role="presentation" className="s-search__foot"><button type="button" onMouseDown={(e) => { e.preventDefault(); recent.clear(); }}>Clear recent searches</button></li> : null}
        </ul>
      ) : null}
    </div>
  );
}
