"""Persistent SQLite authority for the local research library."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import relevance

SCHEMA_VERSION = 1
_SAFE_COLLECTION = re.compile(r"[^a-zA-Z0-9_-]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").casefold())


def _identity_keys(record: dict) -> set[str]:
    candidate = dict(record)
    external = dict(candidate.get("external_ids") or {})
    if candidate.get("doi"):
        external.setdefault("DOI", candidate["doi"])
    candidate["external_ids"] = external
    keys = relevance.paper_identity_keys(candidate)
    title = _title_key(candidate.get("title", ""))
    if title:
        keys.add(f"library_title:{title}")
    return keys


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
        merged["updated_at"] = _now()
    return merged, changed


class LibraryStore:
    """One SQLite file with FTS5, JSONL migration, snapshots, and sync state."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "library.sqlite3"
        with self.connection() as connection:
            self._initialize(connection)
            self._migrate_jsonl(connection)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                canonical_id TEXT,
                title_key TEXT NOT NULL,
                data_json TEXT NOT NULL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_collection_added
                ON records(collection, added_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS identifiers (
                collection TEXT NOT NULL,
                identity_key TEXT NOT NULL,
                record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                PRIMARY KEY(collection, identity_key)
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                target TEXT NOT NULL,
                record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                external_id TEXT,
                external_version TEXT,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(target, record_id)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                title, abstract, authors, notes,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        connection.execute(
            "INSERT INTO library_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _migrate_jsonl(self, connection: sqlite3.Connection) -> None:
        migrated = connection.execute(
            "SELECT value FROM library_meta WHERE key='legacy_jsonl_migrated'"
        ).fetchone()
        if migrated:
            return
        for path in sorted(self.root.glob("*.jsonl")):
            collection = path.stem
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._upsert(connection, record, collection)
        connection.execute(
            "INSERT INTO library_meta(key, value) VALUES('legacy_jsonl_migrated', ?)",
            (_now(),),
        )

    def _load_record(self, row: sqlite3.Row) -> dict:
        return json.loads(row["data_json"])

    def _matching_ids(
        self, connection: sqlite3.Connection, collection: str, keys: set[str]
    ) -> list[int]:
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        rows = connection.execute(
            f"SELECT DISTINCT record_id FROM identifiers "
            f"WHERE collection=? AND identity_key IN ({placeholders})",
            (collection, *sorted(keys)),
        ).fetchall()
        return sorted(int(row["record_id"]) for row in rows)

    def _replace_fts(self, connection: sqlite3.Connection, record_id: int, record: dict) -> None:
        connection.execute("DELETE FROM records_fts WHERE rowid=?", (record_id,))
        connection.execute(
            "INSERT INTO records_fts(rowid, title, abstract, authors, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                record_id,
                record.get("title", ""),
                record.get("abstract", ""),
                " ".join(record.get("authors") or []),
                record.get("notes", ""),
            ),
        )

    def _upsert(
        self, connection: sqlite3.Connection, record: dict, collection: str
    ) -> tuple[bool, bool, int]:
        keys = _identity_keys(record)
        if not keys:
            return False, False, 0
        matches = self._matching_ids(connection, collection, keys)
        added = updated = False
        now = _now()
        if matches:
            record_id = matches[0]
            row = connection.execute(
                "SELECT * FROM records WHERE id=?", (record_id,)
            ).fetchone()
            merged, updated = _merge_record(self._load_record(row), record)
            record = merged
            if updated:
                connection.execute(
                    "UPDATE records SET canonical_id=?, title_key=?, data_json=?, updated_at=? "
                    "WHERE id=?",
                    (
                        record.get("canonical_id", ""),
                        _title_key(record.get("title", "")),
                        json.dumps(record, ensure_ascii=False, default=str),
                        now,
                        record_id,
                    ),
                )
        else:
            added = True
            cursor = connection.execute(
                "INSERT INTO records(collection, canonical_id, title_key, data_json, added_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    collection,
                    record.get("canonical_id", ""),
                    _title_key(record.get("title", "")),
                    json.dumps(record, ensure_ascii=False, default=str),
                    record.get("added_at") or now,
                    now,
                ),
            )
            record_id = int(cursor.lastrowid)
        connection.execute("DELETE FROM identifiers WHERE record_id=?", (record_id,))
        connection.executemany(
            "INSERT OR REPLACE INTO identifiers(collection, identity_key, record_id) "
            "VALUES (?, ?, ?)",
            [(collection, key, record_id) for key in sorted(_identity_keys(record))],
        )
        self._replace_fts(connection, record_id, record)
        return added, updated, record_id

    def _snapshot(self, connection: sqlite3.Connection, collection: str) -> Path:
        safe = _SAFE_COLLECTION.sub("_", collection)
        path = self.root / f"{safe}.jsonl"
        records = connection.execute(
            "SELECT data_json FROM records WHERE collection=? ORDER BY id",
            (collection,),
        ).fetchall()
        temporary = path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "".join(row["data_json"] + "\n" for row in records),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def add_records(self, records: list[dict], collection: str) -> dict:
        added = updated = 0
        with self.connection() as connection:
            for record in records:
                was_added, was_updated, _ = self._upsert(connection, record, collection)
                added += int(was_added)
                updated += int(was_updated)
            self._snapshot(connection, collection)
            total = connection.execute(
                "SELECT COUNT(*) FROM records WHERE collection=?", (collection,)
            ).fetchone()[0]
        return {"added": added, "updated": updated, "total": total, "collection": collection}

    def list_records(self, collection: str, limit: int = 50) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data_json FROM records WHERE collection=? ORDER BY id DESC LIMIT ?",
                (collection, limit),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def list_records_with_ids(self, collection: str, limit: int = 10_000) -> list[tuple[int, dict]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, data_json FROM records WHERE collection=? ORDER BY id DESC LIMIT ?",
                (collection, limit),
            ).fetchall()
        return [(int(row["id"]), json.loads(row["data_json"])) for row in rows]

    def search_records(
        self, collection: str, expression: str, keywords: list[str], limit: int
    ) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT r.data_json, bm25(records_fts, 8.0, 3.0, 1.0, 2.0) AS score "
                "FROM records_fts JOIN records r ON r.id=records_fts.rowid "
                "WHERE r.collection=? AND records_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                (collection, expression, max(limit * 5, 50)),
            ).fetchall()
        ranked = []
        for row in rows:
            record = json.loads(row["data_json"])
            text = " ".join(
                [record.get("title", ""), record.get("abstract", ""), record.get("notes", "")]
            ).casefold()
            coverage = sum(1 for keyword in keywords if keyword.casefold() in text)
            ranked.append((coverage, float(row["score"]), record))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [record for _, _, record in ranked[:limit]]

    def get_record(self, collection: str, identifier: str) -> dict | None:
        target = identifier.strip().casefold()
        bare_doi = target.removeprefix("doi:")
        bare_arxiv = target.removeprefix("arxiv:")
        keys = {
            target,
            bare_doi,
            bare_arxiv,
            f"doi:{bare_doi}",
            f"arxiv:{bare_arxiv}",
            f"library_title:{_title_key(identifier)}",
        }
        with self.connection() as connection:
            matches = self._matching_ids(connection, collection, keys)
            if not matches:
                return None
            row = connection.execute(
                "SELECT data_json FROM records WHERE id=?", (matches[0],)
            ).fetchone()
        return json.loads(row["data_json"])

    def update_annotations(
        self, collection: str, identifier: str, *, notes: str | None, tags: list[str] | None
    ) -> bool:
        record = self.get_record(collection, identifier)
        if record is None:
            return False
        changed = False
        if notes is not None and record.get("notes", "") != notes:
            record["notes"] = notes
            changed = True
        if tags is not None:
            normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
            if record.get("tags", []) != normalized:
                record["tags"] = normalized
                changed = True
        if not changed:
            return False
        record["updated_at"] = _now()
        with self.connection() as connection:
            matches = self._matching_ids(connection, collection, _identity_keys(record))
            if not matches:
                return False
            record_id = matches[0]
            connection.execute(
                "UPDATE records SET data_json=?, updated_at=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False, default=str), record["updated_at"], record_id),
            )
            self._replace_fts(connection, record_id, record)
            self._snapshot(connection, collection)
        return True

    def attach_pdf(self, collection: str, title: str, pdf_path: str) -> bool:
        record = self.get_record(collection, title)
        if record is None or record.get("pdf_path") == pdf_path:
            return False
        record["pdf_path"] = pdf_path
        record["updated_at"] = _now()
        with self.connection() as connection:
            matches = self._matching_ids(connection, collection, _identity_keys(record))
            record_id = matches[0]
            connection.execute(
                "UPDATE records SET data_json=?, updated_at=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False, default=str), record["updated_at"], record_id),
            )
            self._replace_fts(connection, record_id, record)
            self._snapshot(connection, collection)
        return True

    def remove_record(self, collection: str, identifier: str) -> bool:
        record = self.get_record(collection, identifier)
        if record is None:
            return False
        with self.connection() as connection:
            matches = self._matching_ids(connection, collection, _identity_keys(record))
            if not matches:
                return False
            connection.execute("DELETE FROM records_fts WHERE rowid=?", (matches[0],))
            connection.execute("DELETE FROM records WHERE id=?", (matches[0],))
            self._snapshot(connection, collection)
        return True

    def list_collections(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT collection, COUNT(*) AS papers FROM records "
                "GROUP BY collection ORDER BY collection"
            ).fetchall()
        return [
            {
                "name": row["collection"],
                "papers": int(row["papers"]),
                "path": str(self.root / f"{_SAFE_COLLECTION.sub('_', row['collection'])}.jsonl"),
            }
            for row in rows
        ]

    def remove_collection(self, collection: str) -> bool:
        with self.connection() as connection:
            record_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM records WHERE collection=?", (collection,)
                ).fetchall()
            ]
            if not record_ids:
                return False
            connection.executemany(
                "DELETE FROM records_fts WHERE rowid=?", [(record_id,) for record_id in record_ids]
            )
            connection.execute("DELETE FROM records WHERE collection=?", (collection,))
        snapshot = self.root / f"{_SAFE_COLLECTION.sub('_', collection)}.jsonl"
        if snapshot.exists():
            snapshot.unlink()
        return True

    def set_sync_state(
        self, target: str, record_id: int, *, external_id: str | None,
        external_version: str | None, record: dict
    ) -> None:
        payload = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO sync_state(target, record_id, external_id, external_version, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target, record_id) DO UPDATE SET "
                "external_id=excluded.external_id, external_version=excluded.external_version, "
                "content_hash=excluded.content_hash, updated_at=excluded.updated_at",
                (target, record_id, external_id, external_version, digest, _now()),
            )

    def get_sync_state(self, target: str, record_id: int) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sync_state WHERE target=? AND record_id=?",
                (target, record_id),
            ).fetchone()
        return dict(row) if row else None

    def status(self) -> dict:
        with self.connection() as connection:
            records = int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])
            collections = int(
                connection.execute("SELECT COUNT(DISTINCT collection) FROM records").fetchone()[0]
            )
        return {
            "database": str(self.path),
            "schema_version": SCHEMA_VERSION,
            "records": records,
            "collections": collections,
            "journal_mode": "WAL",
        }
