"""Local knowledge base for persistent paper collections.

Stores papers as JSON files on disk. Supports adding papers from search results,
listing saved papers, and searching within the collection by keywords.
Cross-session persistent: papers survive MCP server restarts.
"""

import json
import re
from pathlib import Path
from datetime import datetime

from . import config


DEFAULT_KB_DIR = config.KB_DIR


def _kb_path(collection: str = "default") -> Path:
    """Get the path to a collection's JSON file."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", collection)
    p = Path(DEFAULT_KB_DIR) / f"{safe_name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def add_papers(papers: list[dict], collection: str = "default", notes: str = "") -> dict:
    """Add papers to a collection. Deduplicates by title."""
    path = _kb_path(collection)

    existing = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    p = json.loads(line)
                    existing.add(_norm(p.get("title", "")))
                except json.JSONDecodeError:
                    pass

    added = 0
    with open(path, "a") as f:
        for paper in papers:
            nt = _norm(paper.get("title", ""))
            if not nt or nt in existing:
                continue
            existing.add(nt)
            ext = paper.get("external_ids") or {}
            doi = ext.get("DOI", "") if isinstance(ext, dict) else paper.get("doi", "")
            entry = {
                "title": paper.get("title", ""),
                "authors": (paper.get("authors") or [])[:5],
                "year": paper.get("year"),
                "citation_count": paper.get("citation_count", 0),
                "paper_id": paper.get("paper_id", ""),
                "doi": doi,
                "venue": paper.get("venue", ""),
                # Retain enough evidence for useful local search, later FTS,
                # and vault export. The old 300-character cut often ended
                # before a paper's actual method or finding appeared.
                "abstract": (paper.get("abstract") or "")[:4000],
                "url": paper.get("url", ""),
                "pdf_path": paper.get("pdf_path", ""),
                "added_at": datetime.now().isoformat(),
                "notes": notes,
            }
            f.write(json.dumps(entry, default=str) + "\n")
            added += 1

    return {"added": added, "total": len(existing), "collection": collection}


def attach_pdf(title: str, pdf_path: str, collection: str = "downloads") -> bool:
    """Attach a stable local PDF path to an existing paper record."""
    path = _kb_path(collection)
    target = _norm(title)
    if not target or not path.exists():
        return False

    updated = False
    output = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            paper = json.loads(line)
        except json.JSONDecodeError:
            output.append(line)
            continue
        if _norm(paper.get("title", "")) == target and paper.get("pdf_path") != pdf_path:
            paper["pdf_path"] = pdf_path
            updated = True
        output.append(json.dumps(paper, default=str))

    if updated:
        temporary = path.with_suffix(".jsonl.tmp")
        temporary.write_text("\n".join(output) + "\n")
        temporary.replace(path)
    return updated


def list_papers(collection: str = "default", limit: int = 50) -> list[dict]:
    """List papers in a collection, most recently added first."""
    path = _kb_path(collection)
    if not path.exists():
        return []

    papers = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                papers.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    papers.reverse()
    return papers[:limit]


def search_kb(query: str, collection: str = "default", limit: int = 20) -> list[dict]:
    """Search within a collection by keyword matching on title + abstract."""
    keywords = [w.lower() for w in query.split() if len(w) > 2]
    if not keywords:
        return list_papers(collection, limit)

    papers = list_papers(collection, limit=1000)
    scored = []
    for p in papers:
        text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            scored.append((hits, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def list_collections() -> list[dict]:
    """List all collections with paper counts."""
    kb_dir = Path(DEFAULT_KB_DIR)
    if not kb_dir.exists():
        return []

    collections = []
    for f in sorted(kb_dir.glob("*.jsonl")):
        count = sum(1 for line in f.read_text().splitlines() if line.strip())
        collections.append({
            "name": f.stem,
            "papers": count,
            "path": str(f),
        })
    return collections


def remove_collection(collection: str) -> bool:
    """Delete a collection."""
    path = _kb_path(collection)
    if path.exists():
        path.unlink()
        return True
    return False


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())
