"""External projections and sync adapters for the Research Library."""

from __future__ import annotations

import hashlib
import base64
from html import escape
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


def _saved_credentials(target: str) -> dict:
    from .credentials import credential
    from subprocess import SubprocessError
    try:
        encoded = credential(target, service="scholar-mcp-connectors")
        return json.loads(base64.b64decode(encoded)) if encoded else {}
    except (OSError, ValueError, RuntimeError, ImportError, SubprocessError):
        return {}


def connector_status(store: LibraryStore) -> dict:
    """Report connector readiness without exposing credentials."""
    zotero, notion = _saved_credentials("zotero"), _saved_credentials("notion")
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
            "configured": bool((os.environ.get("ZOTERO_API_KEY") or zotero.get("api_key")) and (os.environ.get("ZOTERO_LIBRARY_ID") or zotero.get("library_id"))),
            "api_base": os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org"),
            "library_type": os.environ.get("ZOTERO_LIBRARY_TYPE", "users"),
        },
        "notion": {
            "configured": bool((os.environ.get("NOTION_API_KEY") or notion.get("api_key"))
            and (os.environ.get("NOTION_DATA_SOURCE_ID") or notion.get("data_source_id"))),
            "api_base": os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1"),
        },
    }


def _note_hash(text: str) -> str:
    return hashlib.sha256(text.strip("\r\n").encode()).hexdigest()


def linked_obsidian_root(store: LibraryStore, collection: str) -> Path | None:
    with store.connection() as connection:
        rows = connection.execute("SELECT s.external_id FROM sync_state s JOIN records r ON r.id=s.record_id WHERE s.target='obsidian' AND r.collection=?", (collection,)).fetchall()
    roots = {Path(row["external_id"]).parent.parent for row in rows if row["external_id"]}
    if len(roots) > 1:
        raise ValueError("This collection is linked to multiple vaults; export to one explicit path first")
    return next(iter(roots), None)


def pull_obsidian_notes(store: LibraryStore, collection: str) -> dict:
    """Read linked Markdown edits, with human edits taking precedence over drafts."""
    imported, conflicts = 0, []
    with store.connection() as connection:
        rows = connection.execute("SELECT r.id, r.data_json, s.external_id, s.external_version FROM records r JOIN sync_state s ON s.record_id=r.id WHERE s.target='obsidian' AND r.collection=?", (collection,)).fetchall()
    for state in rows:
        record_id, record = state["id"], json.loads(state["data_json"])
        state = dict(state)
        if not state.get("external_id"):
            continue
        path = Path(state["external_id"])
        if path.suffix != ".md" or path.is_symlink() or not path.is_file():
            continue
        identifier = record.get("canonical_id") or record.get("title", "")
        try:
            sections = vault.read_note_sections(path)
            previous = json.loads(state.get("external_version") or "{}")
            if not isinstance(previous, dict) or not sections:
                raise ValueError("Editable note headings are missing")
        except (OSError, ValueError):
            conflicts.append({"id": identifier, "path": str(path), "reason": "Note structure requires review"})
            continue
        local = record.get("notes", "")
        incoming = sections.get("notes", "" if previous.get("research_present") else local)
        baseline = previous.get("notes", _note_hash(local))
        changed_file = _note_hash(incoming) != baseline
        # Linked Markdown is the human editing surface. A changed file wins even
        # when an agent independently updated its draft; unchanged files do not
        # roll back newer library notes. This also adopts edits on legacy bindings.
        desired = incoming if changed_file else local
        personal = sections.get("personal_notes", "")
        if store.update_annotations(collection, identifier, notes=desired, personal_notes=personal):
            imported += 1
            record = {**record, "notes": desired, "personal_notes": personal}
        # When only the library changed, keep the old file baseline until export succeeds.
        version = json.dumps({"notes": _note_hash(incoming), "research_present": "notes" in sections})
        if state.get("external_version") != version:
            store.set_sync_state("obsidian", record_id, external_id=str(path), external_version=version, record=record)
    return {"imported_notes": imported, "note_conflicts": conflicts}


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
        or os.path.expanduser(os.environ.get("SCHOLAR_OBSIDIAN_VAULT", ""))
        or linked_obsidian_root(store, collection)
        or vault.DEFAULT_VAULT_DIR
    )
    readback = pull_obsidian_notes(store, collection)
    if readback["note_conflicts"]:
        return {"target": "obsidian", "collection": collection, "notes_written": 0, **readback}
    records = store.list_records_with_ids(collection)
    for record_id, record in records:
        candidate = vault.vault_dir(collection, target) / (vault.note_name(record.get("title", "")) + ".md")
        binding = store.get_sync_state("obsidian", record_id)
        if candidate.is_symlink():
            return {"target": "obsidian", "collection": collection, "notes_written": 0,
                    "note_conflicts": [{"path": str(candidate), "reason": "Projection is a symbolic link; no file was overwritten"}]}
        linked_here = binding and binding.get("external_id") and Path(binding["external_id"]).resolve() == candidate.resolve()
        if candidate.exists() and not linked_here:
            existing = vault.read_note_sections(candidate)
            if existing.get("notes") and existing["notes"] != record.get("notes", ""):
                return {"target": "obsidian", "collection": collection, "notes_written": 0,
                        "note_conflicts": [{"path": str(candidate), "reason": "Unlinked existing research notes require review"}]}
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
            external_id=str(note_path.resolve()),
            external_version=json.dumps({"notes": _note_hash(record.get("notes", "")), "research_present": bool(record.get("notes"))}),
            record=record,
        )
    result["target"] = "obsidian"
    result["imported_notes"] = readback["imported_notes"]
    return result


def _zotero_payload(record: dict, collection_key: str = "") -> dict:
    creators = [
        {"creatorType": "author", "name": str(author)}
        for author in (record.get("authors") or [])
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


def _zotero_notes(client, prefix, headers, store, records):
    """A managed child note, separate from notes the reader creates in Zotero."""
    count = 0
    for record_id, record in records:
        text = str(record.get("notes") or "")
        parent = store.get_sync_state("zotero", record_id)
        state = store.get_sync_state("zotero_notes", record_id)
        if not parent or not text or state and state["content_hash"] == _digest({"text": text}):
            continue
        payload = {"itemType": "note", "parentItem": parent["external_id"],
                   "note": "<h2>Scholar research notes</h2><p>" + escape(text).replace("\n", "<br>") + "</p>"}
        if state:
            response = client.patch(f"{prefix}/items/{state['external_id']}",
                headers={**headers, "If-Unmodified-Since-Version": state["external_version"]}, json=payload)
            response.raise_for_status()
            key, version = state["external_id"], response.headers.get("Last-Modified-Version", "")
        else:
            response = client.post(f"{prefix}/items", headers={**headers, "Zotero-Write-Token": uuid.uuid4().hex}, json=[payload])
            response.raise_for_status()
            created = response.json().get("successful", {}).get("0")
            if not created:
                raise RuntimeError("Zotero did not accept the research note")
            key, version = created["key"], str(created["version"])
        store.set_sync_state("zotero_notes", record_id, external_id=key, external_version=version, record={"text": text})
        count += 1
    return count


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
    saved = _saved_credentials("zotero") if not api_key or not library_id else {}
    api_key = api_key or os.environ.get("ZOTERO_API_KEY") or saved.get("api_key")
    library_id = library_id or os.environ.get("ZOTERO_LIBRARY_ID") or saved.get("library_id")
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
                    headers={**headers, "Zotero-Write-Token": uuid.uuid4().hex},
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
        summary["notes_written"] = _zotero_notes(client, prefix, headers, store, records)
    summary.update(successful=successful, failed=failed)
    return summary


def _notion_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _notion_text(text: str) -> list:
    if len(text) > 200_000:
        raise ValueError("Notion text exceeds its 100 x 2000 character block limit")
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)]


def _notion_plain(parts: list) -> str:
    return "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in parts)


def _notion_note_property(schema: dict) -> str | None:
    return next((name for name, value in schema.items()
                 if name.casefold() in {"research notes", "scholar notes"}
                 and value.get("type") == "rich_text"), None)


def _notion_note_children(client, base, headers, block_id):
    """Read the editable note container, including paragraphs added in Notion."""
    blocks, cursor = [], None
    while True:
        response = client.get(f"{base}/blocks/{block_id}/children", headers=headers,
                              params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        response.raise_for_status()
        page = response.json()
        blocks.extend(page["results"])
        if not page.get("has_more"):
            return blocks
        cursor = page["next_cursor"]


def _notion_block_text(block: dict) -> str:
    kind = block.get("type", "paragraph")
    body = block.get(kind, {})
    if kind == "table_row":
        return "| " + " | ".join(_notion_plain(cell) for cell in body.get("cells", [])) + " |"
    if kind == "equation":
        return "$$" + body.get("expression", "") + "$$"
    value = _notion_plain(body.get("rich_text", []))
    if kind == "to_do":
        return ("- [x] " if body.get("checked") else "- [ ] ") + value
    if not value and body.get("caption"):
        value = _notion_plain(body["caption"])
    if kind == "code":
        return f"```{body.get('language', '')}\n{value}\n```"
    prefix = {"bulleted_list_item": "- ", "numbered_list_item": "1. ",
              "quote": "> ", "heading_1": "# ", "heading_2": "## ", "heading_3": "### "}.get(kind, "")
    return prefix + value


def _notion_container_text(client, base, headers, block_id):
    sections = []
    for block in _notion_note_children(client, base, headers, block_id):
        text = _notion_block_text(block)
        if block.get("has_children"):
            text += "\n\n" + _notion_container_text(client, base, headers, block["id"])
        sections.append(text)
    return "\n\n".join(sections)


def pull_notion_notes(store: LibraryStore, collection: str, *, api_key: str | None = None,
                      api_base: str | None = None, identifier: str | None = None) -> dict:
    """Adopt human edits on linked research-note fields before library access.

    A changed remote note wins; an unchanged remote note never rolls back an
    agent draft. Only Scholar-bound pages/blocks are read, including legacy
    summary paragraphs. Personal properties and bibliographic data stay local.
    """
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT r.id, r.data_json, s.* FROM records r JOIN sync_state s ON s.record_id=r.id "
            "WHERE r.collection=? AND (s.target='notion_notes' OR (s.target='notion_content' "
            "AND NOT EXISTS (SELECT 1 FROM sync_state n WHERE n.record_id=r.id AND n.target='notion_notes')))",
            (collection,),
        ).fetchall()
    if identifier:
        wanted = store.get_record(collection, identifier)
        rows = [row for row in rows if wanted and _digest(json.loads(row["data_json"])) == _digest(wanted)]
    result = {"imported_notes": 0, "note_conflicts": []}
    if not rows:
        return result
    api_key = api_key or os.environ.get("NOTION_API_KEY") or _saved_credentials("notion").get("api_key")
    if not api_key:
        result["note_errors"] = [{"target": "notion", "reason": "Linked notes need the Notion connection"}]
        return result
    base = (api_base or os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1")).rstrip("/")
    headers = _notion_headers(api_key)
    with httpx.Client(timeout=8) as client:
        for row in rows:
            state, record = dict(row), json.loads(row["data_json"])
            identifier = record.get("canonical_id") or record.get("title", "")
            legacy = state["target"] == "notion_content"
            version = json.loads(state["external_version"] or "{}") if not legacy else {}
            try:
                if version.get("property_id"):
                    parts, cursor = [], None
                    while True:
                        response = client.get(f"{base}/pages/{state['external_id']}/properties/{version['property_id']}",
                                              headers=headers, params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
                        response.raise_for_status()
                        page = response.json()
                        parts.extend(item["rich_text"] for item in page["results"])
                        if not page.get("has_more"):
                            break
                        cursor = page["next_cursor"]
                    incoming = _notion_plain(parts)
                elif version.get("container"):
                    incoming = _notion_container_text(client, base, headers, state["external_id"])
                else:
                    response = client.get(f"{base}/blocks/{state['external_id']}", headers=headers)
                    response.raise_for_status()
                    block = response.json()
                    if block.get("in_trash") or block.get("archived"):
                        raise ValueError("The linked note block was removed")
                    current = _notion_plain(block.get("paragraph", {}).get("rich_text", []))
                    incoming = current.partition("Research notes\n")[2] if "Research notes\n" in current else current
                    if legacy and _digest({"text": current}) == state["content_hash"]:
                        # Unchanged legacy summaries carry metadata, not a new human note.
                        continue
                    if legacy and "Research notes\n" not in current:
                        raise ValueError("Research notes heading is missing from the legacy summary")
                baseline = version.get("notes", _note_hash(record.get("notes", "")))
                if _note_hash(incoming) != baseline:
                    if store.update_annotations(collection, identifier, notes=incoming):
                        result["imported_notes"] += 1
                version["notes"] = _note_hash(incoming)
                if legacy:
                    version["legacy"] = True
                store.set_sync_state("notion_notes", row["id"], external_id=state["external_id"],
                                     external_version=json.dumps(version), record={"notes": incoming})
            except (httpx.HTTPError, ValueError) as error:
                reason = f"Notion HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else str(error) if isinstance(error, ValueError) else type(error).__name__
                issue = {"target": "notion", "id": identifier, "reason": reason}
                if isinstance(error, httpx.HTTPStatusError) and error.response.headers.get("Retry-After"):
                    issue["retry_after"] = error.response.headers["Retry-After"]
                result.setdefault("note_errors", []).append(issue)
                # Stop the batch when the service or connection needs recovery.
                if not isinstance(error, ValueError) and (not isinstance(error, httpx.HTTPStatusError)
                    or error.response.status_code in {401, 403, 429, 529} or error.response.status_code >= 500):
                    break
    return result


def pull_linked_notes(store: LibraryStore, collection: str, *, identifier: str | None = None) -> dict:
    """Refresh the optional human editing surfaces through one library path."""
    markdown = pull_obsidian_notes(store, collection)
    notion = pull_notion_notes(store, collection, identifier=identifier)
    return {"imported_notes": markdown["imported_notes"] + notion["imported_notes"],
            "note_conflicts": markdown["note_conflicts"] + notion["note_conflicts"],
            **({"note_errors": notion["note_errors"]} if notion.get("note_errors") else {})}


def _notion_notes(client, base, headers, store, record_id, page_id, record, schema):
    """Publish one editable research-note surface per paper, separate from its summary."""
    notes = record.get("notes", "")
    state = store.get_sync_state("notion_notes", record_id)
    version = json.loads(state["external_version"] or "{}") if state else {}
    property_name = _notion_note_property(schema)
    if property_name:
        # The page write already publishes this property, including explicit clears.
        external_id = page_id
        version = {"property_id": schema[property_name].get("id") or property_name}
    else:
        if state and not version.get("legacy") and not version.get("property_id") and _note_hash(notes) == version.get("notes"):
            return "unchanged"
        paragraph = {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _notion_text(notes)}}
        if state and version.get("container"):
            external_id = state["external_id"]
            children = _notion_note_children(client, base, headers, external_id)
            if len(children) == 1 and children[0].get("type") == "paragraph":
                response = client.patch(f"{base}/blocks/{children[0]['id']}", headers=headers,
                                        json={"paragraph": paragraph["paragraph"]})
                response.raise_for_status()
            else:
                # Append first; an unsuccessful replacement cannot erase the old note.
                response = client.patch(f"{base}/blocks/{external_id}/children", headers=headers,
                                        json={"children": [paragraph]})
                response.raise_for_status()
                for child in children:
                    client.patch(f"{base}/blocks/{child['id']}", headers=headers, json={"archived": True}).raise_for_status()
        elif state and not version.get("legacy") and not version.get("property_id"):
            external_id = state["external_id"]
            response = client.patch(f"{base}/blocks/{external_id}", headers=headers,
                                    json={"paragraph": {"rich_text": _notion_text("Research notes\n" + notes)}})
            response.raise_for_status()
        else:
            response = client.patch(f"{base}/blocks/{page_id}/children", headers=headers,
                json={"children": [{"object": "block", "type": "toggle", "toggle": {
                    "rich_text": _notion_text("Research notes"), "children": [paragraph]}}]})
            response.raise_for_status()
            external_id = response.json()["results"][0]["id"]
            version = {"container": True}
    version["notes"] = _note_hash(notes)
    store.set_sync_state("notion_notes", record_id, external_id=external_id,
                         external_version=json.dumps(version), record={"notes": notes})
    return "written"


def _notion_content(client, base, headers, store, record_id, page_id, record):
    """Publish a readable summary even into a title-only database. Preserve human edits."""
    text = "\n\n".join(value for value in (
        "Scholar research", ", ".join(record.get("authors") or []),
        str(record.get("venue") or ""), str(record.get("abstract") or ""),
    ) if value)
    state = store.get_sync_state("notion_content", record_id)
    if state and (state.get("external_version") or "").startswith('{"human_summary":'):
        return "human_edit_preserved"
    if state and state["content_hash"] == _digest({"text": text}):
        return "unchanged"
    payload = {"paragraph": {"rich_text": _notion_text(text)}}
    human_summary = False
    if state:
        response = client.get(f"{base}/blocks/{state['external_id']}", headers=headers)
        response.raise_for_status()
        current = _notion_plain(response.json().get("paragraph", {}).get("rich_text", []))
        note_state = store.get_sync_state("notion_notes", record_id)
        migrated = "Research notes\n" in current and note_state and not json.loads(note_state["external_version"] or "{}").get("legacy")
        if migrated:
            # Notes already have a separate durable binding. Remove only their
            # old section, preserving any human edits to the summary prefix.
            prefix = current.partition("Research notes\n")[0].rstrip("\n")
            human_summary = prefix != text
            text = prefix
            payload["paragraph"]["rich_text"] = _notion_text(text)
        elif _digest({"text": current}) != state["content_hash"]:
            return "human_edit_preserved"
        response = client.patch(f"{base}/blocks/{state['external_id']}", headers=headers, json=payload)
        response.raise_for_status()
        block = response.json()
    else:
        response = client.patch(f"{base}/blocks/{page_id}/children", headers=headers,
                                json={"children": [{"object": "block", "type": "paragraph", **payload}]})
        response.raise_for_status()
        block = response.json()["results"][0]
    store.set_sync_state("notion_content", record_id, external_id=block["id"],
                         external_version=json.dumps({"human_summary": True}) if human_summary else block.get("last_edited_time"), record={"text": text})
    return "written"


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
    ]
    for candidates, expected_type, value in optional:
        name = property_name(candidates, expected_type)
        if not name or value in (None, ""):
            continue
        if expected_type == "rich_text":
            properties[name] = {
                "rich_text": _notion_text(str(value))
            }
        else:
            properties[name] = {expected_type: value}
    tags_name = property_name(["Tags"], "multi_select")
    if tags_name:
        properties[tags_name] = {
            "multi_select": [{"name": str(tag)[:100]} for tag in record.get("tags") or []]
        }
    notes_name = _notion_note_property(schema)
    if notes_name:
        properties[notes_name] = {"rich_text": _notion_text(str(record.get("notes", "")))}
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
    saved = _saved_credentials("notion") if not api_key or not data_source_id else {}
    api_key = api_key or os.environ.get("NOTION_API_KEY") or saved.get("api_key")
    data_source_id = data_source_id or os.environ.get("NOTION_DATA_SOURCE_ID") or saved.get("data_source_id")
    api_base = (api_base or os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1")).rstrip("/")
    readback = pull_notion_notes(store, collection, api_key=api_key, api_base=api_base) if apply else {}
    if readback.get("note_errors"):
        return {"target": "notion", "collection": collection, "apply": apply,
                "successful": 0, "failed": len(readback["note_errors"]), **readback}
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
        **readback,
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
        summary["content"] = {}
        summary["notes"] = {}
        for record_id, record in records:
            state = store.get_sync_state("notion", record_id)
            if state:
                result = _notion_notes(client, api_base, headers, store, record_id, state["external_id"], record, schema)
                summary["notes"][result] = summary["notes"].get(result, 0) + 1
                result = _notion_content(client, api_base, headers, store, record_id, state["external_id"], record)
                summary["content"][result] = summary["content"].get(result, 0) + 1
    summary.update(successful=successful, failed=failed)
    return summary
