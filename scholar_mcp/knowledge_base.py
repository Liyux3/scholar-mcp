"""Persistent local paper collections backed by SQLite and FTS5."""

from __future__ import annotations

from datetime import datetime, timezone

from . import config, relevance
from .library_store import LibraryStore

DEFAULT_KB_DIR = config.KB_DIR


def get_store() -> LibraryStore:
    """Return the store rooted at the current configured KB directory."""
    return LibraryStore(DEFAULT_KB_DIR)


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
        "added_at": paper.get("added_at") or datetime.now(timezone.utc).isoformat(),
        "notes": notes or paper.get("notes", ""),
        "tags": list(dict.fromkeys(paper.get("tags") or [])),
    }


def add_papers(papers: list[dict], collection: str = "default", notes: str = "") -> dict:
    """Upsert papers into a collection using canonical cross-source identities."""
    records = [_record_from_paper(paper, notes=notes) for paper in papers]
    return get_store().add_records(records, collection)


def attach_pdf(title: str, pdf_path: str, collection: str = "downloads") -> bool:
    """Attach a stable local PDF path to an existing record."""
    return get_store().attach_pdf(collection, title, pdf_path)


def list_papers(collection: str = "default", limit: int = 50) -> list[dict]:
    """List papers in a collection, most recently added first."""
    return get_store().list_records(collection, limit)


def search_kb(query: str, collection: str = "default", limit: int = 20) -> list[dict]:
    """Search a collection through its persistent FTS5 index."""
    keywords = relevance.extract_keywords(query, max_keywords=12)
    if not keywords:
        return list_papers(collection, limit)
    expression = " OR ".join(
        f'"{word.replace(chr(34), chr(34) * 2)}"' for word in keywords
    )
    return get_store().search_records(collection, expression, keywords, limit)


def get_paper(identifier: str, collection: str = "default") -> dict | None:
    """Resolve one saved paper by title or any known identifier."""
    return get_store().get_record(collection, identifier)


def update_paper(
    identifier: str,
    collection: str = "default",
    *,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Update human-owned annotations and refresh the FTS index."""
    return get_store().update_annotations(
        collection,
        identifier,
        notes=notes,
        tags=tags,
    )


def remove_paper(identifier: str, collection: str = "default") -> bool:
    """Remove one paper while preserving the rest of the collection."""
    return get_store().remove_record(collection, identifier)


def list_collections() -> list[dict]:
    """List all collections with paper counts and JSONL snapshot paths."""
    return get_store().list_collections()


def remove_collection(collection: str) -> bool:
    """Remove one collection from SQLite and delete its JSONL snapshot."""
    return get_store().remove_collection(collection)
