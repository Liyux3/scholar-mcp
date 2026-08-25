"""External projections and sync adapters for the Research Library."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import httpx

from . import vault
from .library_store import LibraryStore

NOTION_VERSION = "2025-09-03"


def _digest(record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _configured(name: str) -> bool:
    return bool(os.environ.get(name))


def connector_status(store: LibraryStore) -> dict:
    """Report connector readiness without exposing credentials."""
    return {
        "library": store.status(),
        "obsidian": {
            "configured": True,
            "vault_dir": os.path.expanduser(
                os.environ.get("SCHOLAR_OBSIDIAN_VAULT", str(vault.DEFAULT_VAULT_DIR))
            ),
            "auth": "not required",
        },
        "zotero": {
            "configured": _configured("ZOTERO_API_KEY") and _configured("ZOTERO_LIBRARY_ID"),
            "api_base": os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org"),
            "library_type": os.environ.get("ZOTERO_LIBRARY_TYPE", "users"),
        },
        "notion": {
            "configured": _configured("NOTION_API_KEY")
            and _configured("NOTION_DATA_SOURCE_ID"),
            "api_base": os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1"),
        },
    }


def export_obsidian(
    store: LibraryStore,
    collection: str,
    *,
    base_dir: str | Path | None = None,
    link_citations: bool = False,
) -> dict:
    """Project a collection into an Obsidian-compatible Markdown vault."""
    target = Path(
        base_dir
        or os.path.expanduser(
            os.environ.get("SCHOLAR_OBSIDIAN_VAULT", str(vault.DEFAULT_VAULT_DIR))
        )
    )
    records = store.list_records_with_ids(collection)
    result = vault.export_collection(
        [record for _, record in records],
        collection,
        link_citations=link_citations,
        base_dir=target,
    )
    for record_id, record in records:
        note_path = vault.vault_dir(collection, target) / (
            vault.note_name(record.get("title", "")) + ".md"
        )
        store.set_sync_state(
            "obsidian",
            record_id,
            external_id=str(note_path),
            external_version=None,
            record=record,
        )
    result["target"] = "obsidian"
    return result


def _zotero_payload(record: dict, collection_key: str = "") -> dict:
    creators = [
        {"creatorType": "author", "name": str(author)}
        for author in (record.get("authors") or [])[:20]
    ]
    external = record.get("external_ids") or {}
    arxiv = external.get("ArXiv", "")
    extra = f"arXiv: {arxiv}" if arxiv else ""
    payload = {
        "itemType": "journalArticle",
        "title": record.get("title", ""),
        "creators": creators,
        "abstractNote": record.get("abstract", ""),
        "publicationTitle": record.get("venue", ""),
        "date": str(record.get("publication_date") or record.get("year") or ""),
        "DOI": record.get("doi", "") or external.get("DOI", ""),
        "url": record.get("url", "") or record.get("open_access_url", ""),
        "extra": extra,
        "tags": [{"tag": tag} for tag in (record.get("tags") or [])],
        "collections": [collection_key] if collection_key else [],
    }
    return payload


def sync_zotero(
    store: LibraryStore,
    collection: str,
    *,
    apply: bool = False,
    collection_key: str = "",
    api_key: str | None = None,
    library_id: str | None = None,
    library_type: str | None = None,
    api_base: str | None = None,
) -> dict:
    """Create or update Zotero items; dry-run unless apply=True."""
    api_key = api_key or os.environ.get("ZOTERO_API_KEY")
    library_id = library_id or os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = library_type or os.environ.get("ZOTERO_LIBRARY_TYPE", "users")
    api_base = (api_base or os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org")).rstrip("/")
    records = store.list_records_with_ids(collection)
    plans = []
    for record_id, record in records:
        state = store.get_sync_state("zotero", record_id)
        digest = _digest(record)
        if state and state.get("content_hash") == digest:
            plans.append(("unchanged", record_id, record, state, None))
            continue
        action = "update" if state and state.get("external_id") else "create"
        plans.append((action, record_id, record, state, _zotero_payload(record, collection_key)))

    summary = {
        "target": "zotero",
        "collection": collection,
        "apply": apply,
        "create": sum(action == "create" for action, *_ in plans),
        "update": sum(action == "update" for action, *_ in plans),
        "unchanged": sum(action == "unchanged" for action, *_ in plans),
        "sample": [
            {
                "action": action,
                "title": record.get("title", ""),
                **({"payload": payload} if payload else {}),
            }
            for action, _, record, _, payload in plans[:3]
        ],
    }
    if not apply:
        return summary
    if not api_key or not library_id:
        raise ValueError("ZOTERO_API_KEY and ZOTERO_LIBRARY_ID are required with --apply")

    prefix = f"{api_base}/{library_type}/{library_id}"
    headers = {"Zotero-API-Key": api_key, "Content-Type": "application/json"}
    successful = failed = 0
    with httpx.Client(timeout=30) as client:
        for action, record_id, record, state, payload in plans:
            if action == "unchanged":
                continue
            if action == "create":
                response = client.post(
                    f"{prefix}/items",
                    headers={**headers, "Zotero-Write-Token": str(uuid.uuid4())},
                    json=[payload],
                )
                response.raise_for_status()
                result = response.json()
                created = (result.get("successful") or {}).get("0")
                if not created:
                    failed += 1
                    continue
                external_id = created.get("key")
                version = str(created.get("version") or "")
            else:
                external_id = state["external_id"]
                request_headers = dict(headers)
                if state.get("external_version"):
                    request_headers["If-Unmodified-Since-Version"] = str(
                        state["external_version"]
                    )
                response = client.patch(
                    f"{prefix}/items/{external_id}",
                    headers=request_headers,
                    json=payload,
                )
                response.raise_for_status()
                version = response.headers.get("Last-Modified-Version", "")
            store.set_sync_state(
                "zotero",
                record_id,
                external_id=external_id,
                external_version=version,
                record=record,
            )
            successful += 1
    summary.update(successful=successful, failed=failed)
    return summary


def _notion_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _notion_properties(record: dict, schema: dict | None = None) -> dict:
    schema = schema or {"Name": {"type": "title"}}

    def property_name(candidates: list[str], expected_type: str) -> str | None:
        lowered = {name.casefold(): name for name in schema}
        for candidate in candidates:
            name = lowered.get(candidate.casefold())
            if name and schema[name].get("type") == expected_type:
                return name
        return next(
            (name for name, value in schema.items() if value.get("type") == expected_type),
            None,
        ) if expected_type == "title" else None

    properties = {}
    title_name = property_name(["Name", "Title", "Paper"], "title") or "Name"
    properties[title_name] = {
        "title": [{"text": {"content": str(record.get("title", ""))[:2000]}}]
    }
    optional = [
        (["Scholar ID", "Canonical ID"], "rich_text", str(record.get("canonical_id", ""))),
        (["Year"], "number", record.get("year")),
        (["Venue"], "rich_text", str(record.get("venue", ""))),
        (["Citations"], "number", record.get("citation_count", 0)),
        (["URL"], "url", record.get("url") or record.get("open_access_url")),
        (["Notes"], "rich_text", str(record.get("notes", ""))),
    ]
    for candidates, expected_type, value in optional:
        name = property_name(candidates, expected_type)
        if not name or value in (None, ""):
            continue
        if expected_type == "rich_text":
            properties[name] = {
                "rich_text": [{"text": {"content": str(value)[:2000]}}]
            }
        else:
            properties[name] = {expected_type: value}
    tags_name = property_name(["Tags"], "multi_select")
    if tags_name:
        properties[tags_name] = {
            "multi_select": [{"name": str(tag)[:100]} for tag in record.get("tags") or []]
        }
    return properties


def publish_notion(
    store: LibraryStore,
    collection: str,
    *,
    apply: bool = False,
    api_key: str | None = None,
    data_source_id: str | None = None,
    api_base: str | None = None,
) -> dict:
    """Create or update Notion pages; dry-run unless apply=True."""
    api_key = api_key or os.environ.get("NOTION_API_KEY")
    data_source_id = data_source_id or os.environ.get("NOTION_DATA_SOURCE_ID")
    api_base = (api_base or os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1")).rstrip("/")
    records = store.list_records_with_ids(collection)
    plans = []
    for record_id, record in records:
        state = store.get_sync_state("notion", record_id)
        digest = _digest(record)
        if state and state.get("content_hash") == digest:
            plans.append(("unchanged", record_id, record, state))
        else:
            plans.append(("update" if state and state.get("external_id") else "create", record_id, record, state))
    summary = {
        "target": "notion",
        "collection": collection,
        "apply": apply,
        "create": sum(action == "create" for action, *_ in plans),
        "update": sum(action == "update" for action, *_ in plans),
        "unchanged": sum(action == "unchanged" for action, *_ in plans),
        "sample": [
            {
                "action": action,
                "title": record.get("title", ""),
                "properties": _notion_properties(record),
            }
            for action, _, record, _ in plans[:3]
        ],
    }
    if not apply:
        return summary
    if not api_key or not data_source_id:
        raise ValueError("NOTION_API_KEY and NOTION_DATA_SOURCE_ID are required with --apply")

    headers = _notion_headers(api_key)
    successful = failed = 0
    with httpx.Client(timeout=30) as client:
        schema_response = client.get(
            f"{api_base}/data_sources/{data_source_id}", headers=headers
        )
        schema_response.raise_for_status()
        schema = schema_response.json().get("properties") or {}
        for action, record_id, record, state in plans:
            if action == "unchanged":
                continue
            properties = _notion_properties(record, schema)
            if action == "create":
                response = client.post(
                    f"{api_base}/pages",
                    headers=headers,
                    json={
                        "parent": {
                            "type": "data_source_id",
                            "data_source_id": data_source_id,
                        },
                        "properties": properties,
                    },
                )
            else:
                response = client.patch(
                    f"{api_base}/pages/{state['external_id']}",
                    headers=headers,
                    json={"properties": properties},
                )
            if response.is_error:
                failed += 1
                continue
            page = response.json()
            store.set_sync_state(
                "notion",
                record_id,
                external_id=page.get("id"),
                external_version=page.get("last_edited_time"),
                record=record,
            )
            successful += 1
    summary.update(successful=successful, failed=failed)
    return summary
