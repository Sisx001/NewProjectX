"""Knowledge sources: markdown/text chunking and PostgreSQL full-text retrieval (tsvector + ts_rank).

Retrieved chunks are always injected into prompts as *untrusted* context with citations.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ...core.errors import AppError
from ...db import utcnow
from .models import AiKnowledgeChunk, AiKnowledgeSource

MAX_SOURCE_CHARS = 1_000_000
CHUNK_CHARS = 1200
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"
_WORD = re.compile(r"[\w\u0980-\u09FF]{2,}", re.UNICODE)


def chunk_markdown(body: str, max_chars: int = CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split on headings, then paragraphs, keeping each chunk under max_chars. Returns (heading, content)."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in body.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            sections.append((m.group(1).strip()[:300], []))
        else:
            sections[-1][1].append(line)
    out: list[tuple[str, str]] = []
    for heading, lines in sections:
        buf = ""
        for para in re.split(r"\n\s*\n", "\n".join(lines)):
            para = para.strip()
            if not para:
                continue
            while len(para) > max_chars:
                out.append((heading, para[:max_chars]))
                para = para[max_chars:]
            if len(buf) + len(para) + 2 > max_chars and buf:
                out.append((heading, buf))
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            out.append((heading, buf))
    return out


def ingest(db: Session, src: AiKnowledgeSource, body: str) -> AiKnowledgeSource:
    if len(body) > MAX_SOURCE_CHARS:
        raise AppError("too_large", f"Knowledge sources are limited to {MAX_SOURCE_CHARS:,} characters")
    if not body.strip():
        raise AppError("empty", "The source has no text")
    db.add(src)
    db.flush()
    db.execute(delete(AiKnowledgeChunk).where(AiKnowledgeChunk.source_id == src.id))
    chunks = chunk_markdown(body)
    for i, (h, c) in enumerate(chunks):
        db.add(AiKnowledgeChunk(store_id=src.store_id, source_id=src.id, ord=i, heading=h, content=c))
    src.chunk_count, src.char_count = len(chunks), len(body)
    src.content_sha256 = hashlib.sha256(body.encode()).hexdigest()
    src.status, src.error, src.refreshed_at = "ready", None, utcnow()
    return src


def fetch_url(url: str) -> str:
    from ..webhooks.outbound import request

    r = request("GET", url, headers={"Accept": "text/markdown, text/plain, text/html;q=0.5",
                                     "User-Agent": "VOLTORA-Knowledge/1"}, timeout=15.0, max_bytes=MAX_SOURCE_CHARS)
    if not 200 <= r.status < 300:
        raise AppError("fetch_failed", f"The page returned HTTP {r.status}")
    body = r.text
    if "<html" in body[:2000].lower():
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
        body = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n\n", body)
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"[ \t]+", " ", body)
    return body


def ingest_docs(db: Session, store_id: uuid.UUID) -> list[AiKnowledgeSource]:
    """Index VOLTORA's own docs/*.md as help knowledge (idempotent per file)."""
    out = []
    for f in sorted(DOCS_DIR.glob("*.md")):
        name = f"VOLTORA docs: {f.name}"
        src = db.execute(select(AiKnowledgeSource).where(AiKnowledgeSource.store_id == store_id,
                                                         AiKnowledgeSource.kind == "docs",
                                                         AiKnowledgeSource.name == name)).scalar_one_or_none()
        if src is None:
            src = AiKnowledgeSource(store_id=store_id, name=name, kind="docs", url=f"docs/{f.name}")
            db.add(src)
        out.append(ingest(db, src, f.read_text(encoding="utf-8", errors="replace")[:MAX_SOURCE_CHARS]))
    return out


def _tsquery(q: str) -> str | None:
    words = [w.lower() for w in _WORD.findall(q)][:24]
    return " | ".join(dict.fromkeys(words)) if words else None


def search(db: Session, store_id: uuid.UUID, q: str, source_ids: list | None = None, limit: int = 5) -> list[dict]:
    tsq = _tsquery(q)
    if not tsq:
        return []
    query = func.to_tsquery("english", tsq)
    rank = func.ts_rank(AiKnowledgeChunk.tsv, query)
    stmt = (select(AiKnowledgeChunk, AiKnowledgeSource.name, rank.label("rank"))
            .join(AiKnowledgeSource, AiKnowledgeSource.id == AiKnowledgeChunk.source_id)
            .where(AiKnowledgeChunk.store_id == store_id, AiKnowledgeChunk.tsv.op("@@")(query)))
    if source_ids is not None:
        ids = [uuid.UUID(str(s)) for s in source_ids]
        if not ids:
            return []
        stmt = stmt.where(AiKnowledgeChunk.source_id.in_(ids))
    try:
        with db.begin_nested():
            rows = db.execute(stmt.order_by(text("rank DESC")).limit(limit)).all()
    except Exception:  # noqa: BLE001 - malformed tsquery from odd input; treat as no match
        return []
    return [{"source_id": str(c.source_id), "source": name, "heading": c.heading, "content": c.content,
             "rank": float(r), "citation": f"{name}{' › ' + c.heading if c.heading else ''} (#{c.ord + 1})"}
            for c, name, r in rows]
