import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { BadgeCheck, Check, Clock, Info, MapPin, Package, Pause, Phone, Play, RefreshCw, ShieldCheck, Star, Store, Truck, Wallet, X } from "lucide-react";
import { nodeAttrs, str, type Renderer } from "./attrs";
import { useRender } from "./context";
import { sanitizeHtml, safeHref } from "./sanitize";
import { linkPublished } from "./css";

type Img = { url?: string; alt?: string; decorative?: boolean; width?: number; height?: number } | null | undefined;
type LinkItem = { label?: string; href?: string };

export const ICONS: Record<string, typeof Check> = {
  check: Check, truck: Truck, shield: ShieldCheck, refresh: RefreshCw, phone: Phone, store: Store, clock: Clock,
  "map-pin": MapPin, wallet: Wallet, package: Package, star: Star, info: Info, badge: BadgeCheck,
};

const RATIO: Record<string, string> = { "1:1": "1 / 1", "4:3": "4 / 3", "3:2": "3 / 2", "16:9": "16 / 9", "3:4": "3 / 4", "2:3": "2 / 3", "21:9": "21 / 9", "4:5": "4 / 5", "9:16": "9 / 16" };

/** Internal links use the router; external links open normally with rel=noopener. */
export function SmartLink({ href, children, className, ...rest }: { href: string; children: ReactNode; className?: string; "aria-label"?: string; onClick?: () => void }) {
  if (!safeHref(href)) return <span className={className}>{children}</span>;
  if (href.startsWith("/") && !href.startsWith("//")) return <Link to={href} className={className} {...rest}>{children}</Link>;
  const ext = /^https?:/i.test(href);
  return <a href={href} className={className} {...(ext ? { rel: "noopener noreferrer" } : {})} {...rest}>{children}</a>;
}

export function Picture({ img, ratio = "auto", fit = "cover", priority, sizes }: { img: Img; ratio?: string; fit?: string; priority?: boolean; sizes?: string }) {
  const { editing } = useRender();
  if (!img?.url) return editing ? <div className="vb-img-empty" style={{ aspectRatio: RATIO[ratio] ?? "16 / 9" }}>No image selected</div> : null;
  return (
    <img
      className="vb-img"
      src={img.url}
      alt={img.decorative ? "" : img.alt ?? ""}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      {...(priority ? { fetchPriority: "high" as const } : {})}
      width={img.width}
      height={img.height}
      sizes={sizes}
      style={{ aspectRatio: RATIO[ratio], objectFit: fit === "contain" ? "contain" : "cover" }}
    />
  );
}

const Heading: Renderer = ({ node, p }) => {
  const level = (["h1", "h2", "h3", "h4"].includes(String(p.level)) ? p.level : "h2") as "h1" | "h2" | "h3" | "h4";
  const H = level;
  const size = str(p.size, "auto") === "auto" ? { h1: "display", h2: "xl", h3: "lg", h4: "md" }[level] : str(p.size);
  const eyebrow = str(p.eyebrow);
  return (
    <div {...nodeAttrs(node, { className: "vb-heading" })} data-align={str(p.align, "start")}>
      {eyebrow ? <p className="vb-eyebrow">{eyebrow}</p> : null}
      <H className="vb-h" data-size={size} data-font={str(p.font, "display")}>{str(p.text)}</H>
    </div>
  );
};

const RichText: Renderer = ({ node, p }) => (
  <div
    {...nodeAttrs(node, { className: "vb-rich" })}
    data-size={str(p.size, "md")}
    data-measure={str(p.measure, "readable")}
    data-align={str(p.align, "start")}
    dangerouslySetInnerHTML={{ __html: sanitizeHtml(str(p.html)) }}
  />
);

const ImageC: Renderer = ({ node, p }) => {
  const img = p.image as Img;
  const caption = str(p.caption);
  const pic = <Picture img={img} ratio={str(p.ratio, "auto")} fit={str(p.fit, "cover")} priority={!!p.priority} />;
  const href = str(p.link);
  return (
    <figure {...nodeAttrs(node, { className: "vb-figure" })}>
      {href ? <SmartLink href={href}>{pic}</SmartLink> : pic}
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
};

export function ButtonLink({ label, href, style = "primary", size = "md", action }: { label: string; href: string; style?: string; size?: string; action?: string }) {
  const cls = `vb-btn vb-btn--${style} vb-btn--${size}`;
  if (action === "open-cart") {
    return <button type="button" className={cls} onClick={() => window.dispatchEvent(new Event("voltora:open-cart"))}>{label}</button>;
  }
  return <SmartLink href={href} className={cls}>{label}</SmartLink>;
}

const Button: Renderer = ({ node, p }) => (
  <div {...nodeAttrs(node, { className: "vb-btnwrap" })}>
    <ButtonLink label={str(p.label, "Button")} href={str(p.href, "/")} style={str(p.style, "primary")} size={str(p.size, "md")} action={str(p.action, "navigate")} />
  </div>
);

const IconList: Renderer = ({ node, p }) => {
  const items = (p.items as { icon?: string; title?: string; text?: string; href?: string }[]) ?? [];
  return (
    <ul {...nodeAttrs(node, { className: "vb-iconlist" })} data-layout={str(p.layout, "list")}>
      {items.map((it, i) => {
        const Icon = ICONS[it.icon ?? "check"] ?? Check;
        const body = (
          <>
            <Icon size={20} strokeWidth={1.6} aria-hidden />
            <span>
              {it.title ? <strong>{it.title}</strong> : null}
              {it.text ? <span className="vb-muted">{it.text}</span> : null}
            </span>
          </>
        );
        return <li key={i} data-stagger-item>{it.href ? <SmartLink href={it.href}>{body}</SmartLink> : body}</li>;
      })}
    </ul>
  );
};

const PolicyStrip: Renderer = ({ node, p }) => {
  const env = useRender();
  const items = ((p.items as { icon?: string; title?: string; text?: string; href?: string; requires_setting?: string }[]) ?? []).filter((it) => {
    if (it.requires_setting && !String(env.data.settings[it.requires_setting] ?? "").trim()) return env.editing;
    return !it.href || linkPublished(it.href, env.published) || env.editing;
  });
  if (!items.length && !env.editing) return null;
  return (
    <ul {...nodeAttrs(node, { className: "vb-policy" })}>
      {items.map((it, i) => {
        const Icon = ICONS[it.icon ?? "info"] ?? Info;
        const inner = (
          <>
            <Icon size={22} strokeWidth={1.5} aria-hidden />
            <span><strong>{it.title}</strong>{it.text ? <span>{it.text}</span> : null}</span>
          </>
        );
        return <li key={i} data-stagger-item>{it.href ? <SmartLink href={it.href}>{inner}</SmartLink> : inner}</li>;
      })}
    </ul>
  );
};

const Quote: Renderer = ({ node, p }) => (
  <figure {...nodeAttrs(node, { className: "vb-quote" })}>
    <blockquote><p>{str(p.text)}</p></blockquote>
    {str(p.cite) ? <figcaption>— {str(p.cite)}</figcaption> : null}
  </figure>
);

function embedUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\.|^m\./, "");
    if (u.protocol !== "https:") return null;
    if (host === "youtu.be") return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(u.pathname.slice(1))}?autoplay=1&rel=0`;
    if (host === "youtube.com" || host === "youtube-nocookie.com") {
      const id = u.searchParams.get("v") ?? u.pathname.split("/").filter(Boolean).pop();
      return id ? `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0` : null;
    }
    if (host === "vimeo.com" || host === "player.vimeo.com") {
      const id = u.pathname.split("/").filter(Boolean).pop();
      return id && /^\d+$/.test(id) ? `https://player.vimeo.com/video/${id}?autoplay=1&dnt=1` : null;
    }
  } catch {
    return null;
  }
  return null;
}

const Video: Renderer = ({ node, p }) => {
  const [play, setPlay] = useState(false);
  const { editing } = useRender();
  const src = embedUrl(str(p.url));
  const title = str(p.title, "Video");
  const ratio = RATIO[str(p.ratio, "16:9")] ?? "16 / 9";
  if (!src) return editing ? <div {...nodeAttrs(node, { className: "vb-img-empty" })} style={{ aspectRatio: ratio }}>Add a YouTube or Vimeo link</div> : null;
  return (
    <div {...nodeAttrs(node, { className: "vb-video" })} style={{ aspectRatio: ratio }}>
      {play && !editing ? (
        <iframe src={src} title={title} allow="autoplay; fullscreen; picture-in-picture; encrypted-media" allowFullScreen loading="lazy" referrerPolicy="strict-origin-when-cross-origin" sandbox="allow-scripts allow-same-origin allow-presentation allow-popups" />
      ) : (
        <button type="button" className="vb-video__poster" onClick={() => setPlay(true)} aria-label={`Play video: ${title}`}>
          {(p.poster as Img)?.url ? <img src={(p.poster as Img)!.url} alt="" loading="lazy" /> : null}
          <span className="vb-video__play"><Play size={22} fill="currentColor" aria-hidden /></span>
          <span className="vb-video__title">{title}</span>
        </button>
      )}
    </div>
  );
};

const Hero: Renderer = ({ node, p }) => {
  const variant = str(p.variant, "split");
  const img = p.image as Img;
  const buttons = ((p.primary as LinkItem[]) ?? []).filter((b) => b.label && b.href);
  const hasImg = !!img?.url;
  const title = str(p.title);
  const sub = str(p.subtitle);
  const eyebrow = str(p.eyebrow);
  const H = variant === "banner" ? "h2" : "h1";
  return (
    <section {...nodeAttrs(node, { className: "vb-hero" })} data-variant={variant} data-has-img={hasImg || undefined} data-align={str(p.align, "start")} data-height={str(p.height, "auto")}>
      {variant === "full-bleed" && hasImg ? <div className="vb-hero__bg"><Picture img={img} priority /></div> : null}
      <div className="vb-wrap vb-hero__in" data-width="wide">
        <div className="vb-hero__text">
          {eyebrow ? <p className="vb-eyebrow">{eyebrow}</p> : null}
          <H className="vb-hero__title">{title}</H>
          {sub ? <p className="vb-hero__sub">{sub}</p> : null}
          {buttons.length ? (
            <div className="vb-hero__actions">
              {buttons.map((b, i) => <ButtonLink key={i} label={b.label!} href={b.href!} style={i === 0 ? "primary" : "secondary"} size={variant === "banner" ? "md" : "lg"} />)}
            </div>
          ) : null}
        </div>
        {variant !== "full-bleed" && variant !== "banner" && hasImg ? <div className="vb-hero__media"><Picture img={img} priority ratio={variant === "editorial" ? "21:9" : "4:5"} /></div> : null}
      </div>
    </section>
  );
};

const LogoCloud: Renderer = ({ node, p }) => {
  const logos = ((p.logos as { image?: Img; href?: string }[]) ?? []).filter((l) => l.image?.url);
  const { editing } = useRender();
  if (!logos.length && !editing) return null;
  return (
    <div {...nodeAttrs(node, { className: "vb-logos" })} data-mono={p.grayscale ? "" : undefined}>
      {str(p.title) ? <p className="vb-eyebrow">{str(p.title)}</p> : null}
      <ul>
        {logos.map((l, i) => {
          const im = <img src={l.image!.url} alt={l.image!.alt ?? ""} loading="lazy" />;
          return <li key={i} data-stagger-item>{l.href ? <SmartLink href={l.href}>{im}</SmartLink> : im}</li>;
        })}
        {!logos.length ? <li className="vb-muted">Add logos in the inspector</li> : null}
      </ul>
    </div>
  );
};

const Faq: Renderer = ({ node, p }) => {
  const items = ((p.items as { q?: string; a?: string }[]) ?? []).filter((i) => i.q);
  return (
    <div {...nodeAttrs(node, { className: "vb-faq" })}>
      {str(p.title) ? <h2 className="vb-h" data-size="lg">{str(p.title)}</h2> : null}
      {items.map((it, i) => (
        <details key={i} className="vb-acc-item" data-stagger-item>
          <summary><span>{it.q}</span><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="M3 5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg></summary>
          <div className="vb-acc-item__body vb-rich" dangerouslySetInnerHTML={{ __html: sanitizeHtml(it.a ?? "") }} />
        </details>
      ))}
    </div>
  );
};

function parts(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
}

const Countdown: Renderer = ({ node, p }) => {
  const end = new Date(str(p.ends_at)).getTime();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!Number.isFinite(end) || end <= Date.now()) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [end]);
  const { editing } = useRender();
  if (!Number.isFinite(end)) return editing ? <div {...nodeAttrs(node, { className: "vb-img-empty" })}>Set an end date</div> : null;
  if (end <= now) {
    const after = str(p.ended_text);
    return after ? <p {...nodeAttrs(node, { className: "vb-countdown vb-countdown--ended" })}>{after}</p> : null;
  }
  const t = parts(end - now);
  const cell = (v: number, l: string) => <span><b className="vb-num">{String(v).padStart(2, "0")}</b><small>{l}</small></span>;
  return (
    <div {...nodeAttrs(node, { className: "vb-countdown" })}>
      {str(p.title) ? <p className="vb-eyebrow">{str(p.title)}</p> : null}
      <p role="timer" aria-live="off" aria-label={`${t.d} days ${t.h} hours ${t.m} minutes remaining`}>
        {cell(t.d, "days")}{cell(t.h, "hrs")}{cell(t.m, "min")}{cell(t.s, "sec")}
      </p>
    </div>
  );
};

const Marquee: Renderer = ({ node, p }) => {
  const items = ((p.items as { text?: string }[]) ?? []).map((i) => i.text).filter(Boolean) as string[];
  const [paused, setPaused] = useState(false);
  if (!items.length) return null;
  const row = items.map((t, i) => <span key={i}>{t}</span>);
  return (
    <div {...nodeAttrs(node, { className: "vb-marquee" })} data-speed={str(p.speed, "slow")} data-hoverpause={p.pause_on_hover ? "" : undefined} data-paused={paused || undefined}>
      <p className="vb-sr">{items.join(" · ")}</p>
      <div className="vb-marquee__track" aria-hidden>
        <div>{row}</div>
        <div>{row}</div>
      </div>
      <div className="vb-marquee__static" aria-hidden>{row}</div>
      <button type="button" className="vb-marquee__toggle" onClick={() => setPaused((v) => !v)} aria-label={paused ? "Play scrolling text" : "Pause scrolling text"}>
        {paused ? <Play size={12} /> : <Pause size={12} />}
      </button>
    </div>
  );
};

const Announcement: Renderer = ({ node, p }) => {
  const text = str(p.text).trim();
  const key = `vt-ann:${text}`;
  const [closed, setClosed] = useState(() => {
    try {
      return sessionStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  const { editing } = useRender();
  if (!text) return editing ? <div {...nodeAttrs(node, { className: "vb-announce vb-announce--empty" })}>Announcement is empty — set “Announcement bar text” in Settings or type text here.</div> : null;
  if (closed && !editing) return null;
  const href = str(p.href);
  return (
    <div {...nodeAttrs(node, { className: "vb-announce" })} role="region" aria-label="Announcement">
      <p>{href ? <SmartLink href={href}>{text}</SmartLink> : text}</p>
      {p.dismissible ? (
        <button type="button" aria-label="Dismiss announcement" onClick={() => {
          setClosed(true);
          try { sessionStorage.setItem(key, "1"); } catch { /* storage unavailable */ }
        }}><X size={14} /></button>
      ) : null}
    </div>
  );
};

export const CONTENT: Record<string, Renderer> = {
  heading: Heading,
  text: RichText,
  image: ImageC,
  button: Button,
  "icon-list": IconList,
  "policy-strip": PolicyStrip,
  quote: Quote,
  video: Video,
  hero: Hero,
  "logo-cloud": LogoCloud,
  faq: Faq,
  countdown: Countdown,
  marquee: Marquee,
  announcement: Announcement,
};
