"""Local knowledge base for persistent paper collections.

Stores papers as JSON files on disk. Supports adding papers from search results,
listing saved papers, and searching within the collection by keywords.
Cross-session persistent: papers survive MCP server restarts.
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config, relevance

DEFAULT_KB_DIR = config.KB_DIR


def _kb_path(collection: str = "default") -> Path:
    """Get the path to a collection's JSON file."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", collection)
    p = Path(DEFAULT_KB_DIR) / f"{safe_name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_collection(path: Path) -> list[dict]:
    if not path.exists():
        return []
    papers = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            papers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return papers


def _write_collection(path: Path, papers: list[dict]) -> None:
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(paper, ensure_ascii=False, default=str) + "\n" for paper in papers),
        encoding="utf-8",
    )
    temporary.replace(path)


def _identity_keys(paper: dict) -> set[str]:
    candidate = dict(paper)
    external = dict(candidate.get("external_ids") or {})
    if candidate.get("doi"):
        external.setdefault("DOI", candidate["doi"])
    candidate["external_ids"] = external
    keys = relevance.paper_identity_keys(candidate)
    title = _norm(candidate.get("title", ""))
    if title:
        keys.add(f"library_title:{title}")
    return keys


def _record_from_paper(paper: dict, notes: str = "") -> dict:
    external = dict(paper.get("external_ids") or {})
    doi = external.get("DOI", "") or paper.get("doi", "")
    return {
        "title": paper.get("title", ""),
        "authors": (paper.get("authors") or [])[:20],
        "year": paper.get("year"),
        "publication_date": paper.get("publication_date"),
        "citation_count": paper.get("citation_count", 0),
        "paper_id": paper.get("paper_id", ""),
        "canonical_id": relevance.best_paper_id(paper),
        "external_ids": external,
        "doi": doi,
        "venue": paper.get("venue", ""),
        "abstract": (paper.get("abstract") or "")[:4000],
        "tldr": paper.get("tldr", ""),
        "url": paper.get("url", ""),
        "open_access_url": paper.get("open_access_url", ""),
        "pdf_path": paper.get("pdf_path", ""),
        "source": paper.get("source", ""),
        "added_at": paper.get("added_at") or datetime.now().isoformat(),
        "notes": notes or paper.get("notes", ""),
        "tags": list(dict.fromkeys(paper.get("tags") or [])),
    }


def _merge_record(existing: dict, incoming: dict) -> tuple[dict, bool]:
    merged = dict(existing)
    changed = False

    for field in (
        "year", "publication_date", "paper_id", "canonical_id", "doi",
        "venue", "tldr", "url", "open_access_url", "pdf_path", "source",
    ):
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming[field]
            changed = True
    for field in ("abstract", "notes"):
        if len(str(incoming.get(field) or "")) > len(str(merged.get(field) or "")):
            merged[field] = incoming[field]
            changed = True
    if len(incoming.get("authors") or []) > len(merged.get("authors") or []):
        merged["authors"] = incoming["authors"]
        changed = True
    if (incoming.get("citation_count") or 0) > (merged.get("citation_count") or 0):
        merged["citation_count"] = incoming["citation_count"]
        changed = True

    identifiers = {**(incoming.get("external_ids") or {}), **(merged.get("external_ids") or {})}
    if identifiers != (merged.get("external_ids") or {}):
        merged["external_ids"] = identifiers
        changed = True
    tags = list(dict.fromkeys((merged.get("tags") or []) + (incoming.get("tags") or [])))
    if tags != (merged.get("tags") or []):
        merged["tags"] = tags
        changed = True
    if changed:
        merged["updated_at"] = datetime.now().isoformat()
    return merged, changed


def add_papers(papers: list[dict], collection: str = "default", notes: str = "") -> dict:
    """Upsert papers into a collection using cross-source identities."""
    path = _kb_path(collection)
    records = _load_collection(path)
    key_to_index = {
        key: index
        for index, record in enumerate(records)
        for key in _identity_keys(record)
    }
    added = updated = 0
    for paper in papers:
        incoming = _record_from_paper(paper, notes=notes)
        keys = _identity_keys(incoming)
        if not keys:
            continue
        matches = {key_to_index[key] for key in keys if key in key_to_index}
        if matches:
            index = min(matches)
            records[index], changed = _merge_record(records[index], incoming)
            updated += int(changed)
        else:
            index = len(records)
            records.append(incoming)
            added += 1
        for key in _identity_keys(records[index]):
            key_to_index[key] = index

    if added or updated:
        _write_collection(path, records)
    return {
        "added": added,
        "updated": updated,
        "total": len(records),
        "collection": collection,
    }


def attach_pdf(title: str, pdf_path: str, collection: str = "downloads") -> bool:
    """Attach a stable local PDF path to an existing paper record."""
    path = _kb_path(collection)
    target = _norm(title)
    if not target or not path.exists():
        return False

    updated = False
    papers = _load_collection(path)
    for paper in papers:
        if _norm(paper.get("title", "")) == target and paper.get("pdf_path") != pdf_path:
            paper["pdf_path"] = pdf_path
            paper["updated_at"] = datetime.now().isoformat()
            updated = True

    if updated:
        _write_collection(path, papers)
    return updated


def list_papers(collection: str = "default", limit: int = 50) -> list[dict]:
    """List papers in a collection, most recently added first."""
    path = _kb_path(collection)
    if not path.exists():
        return []

    papers = _load_collection(path)
    papers.reverse()
    return papers[:limit]


def search_kb(query: str, collection: str = "default", limit: int = 20) -> list[dict]:
    """Search a collection with FTS5 BM25 and multi-term coverage."""
    keywords = relevance.extract_keywords(query, max_keywords=12)
    if not keywords:
        return list_papers(collection, limit)

    papers = list_papers(collection, limit=100_000)
    if not papers:
        return []
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE VIRTUAL TABLE docs USING fts5(title, abstract, authors, notes, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        connection.executemany(
            "INSERT INTO docs(rowid, title, abstract, authors, notes) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    index + 1,
                    paper.get("title", ""),
                    paper.get("abstract", ""),
                    " ".join(paper.get("authors") or []),
                    paper.get("notes", ""),
                )
                for index, paper in enumerate(papers)
            ],
        )
        expression = " OR ".join(f'"{word.replace(chr(34), chr(34) * 2)}"' for word in keywords)
        rows = connection.execute(
            "SELECT rowid, bm25(docs, 8.0, 3.0, 1.0, 2.0) "
            "FROM docs WHERE docs MATCH ? ORDER BY 2 LIMIT ?",
            (expression, max(limit * 5, 50)),
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        rows = []

    ranked = []
    for rowid, bm25_score in rows:
        paper = papers[rowid - 1]
        text = " ".join(
            [paper.get("title", ""), paper.get("abstract", ""), paper.get("notes", "")]
        ).casefold()
        coverage = sum(1 for keyword in keywords if keyword.casefold() in text)
        ranked.append((coverage, bm25_score, paper))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [paper for _, _, paper in ranked[:limit]]


def _matches_identifier(paper: dict, identifier: str) -> bool:
    target = identifier.strip().casefold()
    if not target:
        return False
    if target in {
        str(paper.get("canonical_id") or "").casefold(),
        str(paper.get("paper_id") or "").casefold(),
        str(paper.get("doi") or "").casefold(),
    }:
        return True
    if _norm(identifier) == _norm(paper.get("title", "")):
        return True
    normalized_targets = {
        target,
        target.removeprefix("doi:"),
        target.removeprefix("arxiv:"),
    }
    return any(
        key.split(":", 1)[-1].casefold() in normalized_targets
        for key in _identity_keys(paper)
    )


def get_paper(identifier: str, collection: str = "default") -> dict | None:
    """Resolve one saved paper by title or any known identifier."""
    return next(
        (paper for paper in list_papers(collection, limit=100_000)
         if _matches_identifier(paper, identifier)),
        None,
    )


def update_paper(
    identifier: str,
    collection: str = "default",
    *,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Update human-owned annotations without replacing paper metadata."""
    path = _kb_path(collection)
    papers = _load_collection(path)
    updated = False
    for paper in papers:
        if not _matches_identifier(paper, identifier):
            continue
        if notes is not None and paper.get("notes", "") != notes:
            paper["notes"] = notes
            updated = True
        if tags is not None:
            normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
            if paper.get("tags", []) != normalized:
                paper["tags"] = normalized
                updated = True
        if updated:
            paper["updated_at"] = datetime.now().isoformat()
        break
    if updated:
        _write_collection(path, papers)
    return updated


def remove_paper(identifier: str, collection: str = "default") -> bool:
    """Remove one saved paper while preserving the rest of the collection."""
    path = _kb_path(collection)
    papers = _load_collection(path)
    kept = [paper for paper in papers if not _matches_identifier(paper, identifier)]
    if len(kept) == len(papers):
        return False
    _write_collection(path, kept)
    return True


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
