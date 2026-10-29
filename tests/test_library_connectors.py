"""Full connector create/update flows over isolated HTTP, including reader edits."""
import json
import base64
import os

import httpx
import pytest

from scholar_mcp import knowledge_base as kb, library_connectors as connectors
from scholar_mcp.library_connectors import _saved_credentials
from scholar_mcp import server, vault


@pytest.fixture
def collection(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "DEFAULT_KB_DIR", str(tmp_path))
    kb.add_papers([{"title": "Attention Is All You Need", "authors": [f"Author {i}" for i in range(35)],
                    "abstract": "Structured evidence.", "external_ids": {"DOI": "10.1234/example"}}],
                  notes="Grounded reading note. " * 150)
    return kb.get_store()


def transport(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_zotero_create_update_preserves_authors_and_separate_notes(collection, monkeypatch):
    writes = []
    def handle(request):
        data = json.loads(request.content)
        writes.append(data)
        if request.method == "POST":
            assert len(request.headers["Zotero-Write-Token"]) == 32
            key = "NOTE" if data[0]["itemType"] == "note" else "PAPER"
            return httpx.Response(200, json={"successful": {"0": {"key": key, "version": 1}}})
        assert request.headers["If-Unmodified-Since-Version"] == "1"
        return httpx.Response(204, headers={"Last-Modified-Version": "2"})
    transport(monkeypatch, handle)
    kwargs = dict(apply=True, api_key="fixture", library_id="fixture")
    assert connectors.sync_zotero(collection, "default", **kwargs)["notes_written"] == 1
    assert len(writes[0][0]["creators"]) == 35
    assert writes[1][0]["parentItem"] == "PAPER"
    assert "Grounded reading note." in writes[1][0]["note"]
    assert connectors.sync_zotero(collection, "default", **kwargs)["notes_written"] == 0
    kb.update_paper("Attention Is All You Need", notes="Revised evidence.")
    assert connectors.sync_zotero(collection, "default", **kwargs)["notes_written"] == 1
    assert "Revised evidence." in writes[-1]["note"]


def test_notion_title_only_database_is_readable_and_human_edits_survive(collection, monkeypatch):
    content, block_writes = {}, []
    def handle(request):
        if "/data_sources/" in request.url.path:
            return httpx.Response(200, json={"properties": {"Name": {"type": "title"}}})
        if request.url.path.endswith("/pages") or "/pages/" in request.url.path:
            return httpx.Response(200, json={"id": "PAGE", "last_edited_time": "fixture"})
        if request.method == "GET":
            if request.url.path.endswith("/children"):
                parent = request.url.path.split("/")[-2]
                return httpx.Response(200, json={"results": list(content[parent]["toggle"]["children"]), "has_more": False})
            return httpx.Response(200, json=content[request.url.path.rsplit("/", 1)[-1]])
        data = json.loads(request.content)
        block_writes.append(data)
        if "children" in data:
            key = "SUMMARY" if data["children"][0]["type"] == "paragraph" else "NOTES"
            content[key] = {"id": key, **data["children"][0]}
            if content[key]["type"] == "toggle":
                content[key]["toggle"]["children"][0]["id"] = "CHILD"
            return httpx.Response(200, json={"results": [content[key]]})
        key = request.url.path.rsplit("/", 1)[-1]
        if key == "CHILD":
            content["NOTES"]["toggle"]["children"][0].update(data)
            return httpx.Response(200, json={"id": key})
        content[key].update(data)
        return httpx.Response(200, json=content[key])
    transport(monkeypatch, handle)
    kwargs = dict(apply=True, api_key="fixture", data_source_id="fixture")
    assert connectors.publish_notion(collection, "default", **kwargs)["content"] == {"written": 1}
    text = connectors._notion_plain(content["SUMMARY"]["paragraph"]["rich_text"]) + connectors._notion_plain(content["NOTES"]["toggle"]["children"][0]["paragraph"]["rich_text"])
    assert "Author 34" in text and text.count("Grounded reading note.") == 150
    assert connectors.publish_notion(collection, "default", **kwargs)["content"] == {"unchanged": 1}
    content["NOTES"]["toggle"]["children"][0]["paragraph"]["rich_text"] = connectors._notion_text("Human clarification.")
    kb.update_paper("Attention Is All You Need", notes="New agent note.")
    result = connectors.publish_notion(collection, "default", **kwargs)
    assert result["imported_notes"] == 1 and result["content"] == {"unchanged": 1}
    assert len(block_writes) == 2
    assert kb.get_paper("Attention Is All You Need")["notes"] == "Human clarification."
    # An unchanged remote note cannot roll back a newer local agent draft.
    kb.update_paper("Attention Is All You Need", notes="Build on the human clarification.")
    assert connectors.publish_notion(collection, "default", **kwargs)["notes"] == {"written": 1}
    assert "Build on the human clarification." in connectors._notion_plain(content["NOTES"]["toggle"]["children"][0]["paragraph"]["rich_text"])


def test_notion_property_pagination_clear_and_mcp_readback(collection, monkeypatch):
    record_id, record = collection.list_records_with_ids("default")[0]
    notes = "Human correction. " * 300
    version = {"property_id": "notes%3Aid", "notes": connectors._note_hash(record["notes"])}
    collection.set_sync_state("notion_notes", record_id, external_id="PAGE",
                             external_version=json.dumps(version), record={"notes": record["notes"]})
    monkeypatch.setenv("NOTION_API_KEY", "fixture")
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path.endswith("/properties/notes:id")
        parts = connectors._notion_text(notes)
        cursor = request.url.params.get("start_cursor")
        return httpx.Response(200, json={"results": [{"rich_text": x} for x in (parts[1:] if cursor else parts[:1])],
            "has_more": bool(len(parts) > 1 and not cursor), "next_cursor": "next" if not cursor else None})
    transport(monkeypatch, handle)
    response = server.paper_library(action="get", paper_titles="Attention Is All You Need")
    assert response["paper"]["notes"] == notes and len(requests) == 2
    assert "Human correction." in kb.search_kb("correction")[0]["notes"]
    kb.update_paper("Attention Is All You Need", notes="An independent draft.")
    assert connectors.pull_notion_notes(collection, "default")["imported_notes"] == 0
    assert kb.get_paper("Attention Is All You Need")["notes"] == "An independent draft."
    notes = ""
    assert connectors.pull_notion_notes(collection, "default")["imported_notes"] == 1
    assert kb.get_paper("Attention Is All You Need")["notes"] == ""


def test_notion_legacy_summary_migrates_human_note_and_keeps_metadata(collection, monkeypatch):
    record_id, record = collection.list_records_with_ids("default")[0]
    original = "Scholar research\n\nStructured evidence.\n\nResearch notes\n" + record["notes"]
    collection.set_sync_state("notion_content", record_id, external_id="LEGACY",
                             external_version="2026-10-13T00:00:00Z", record={"text": original})
    transport(monkeypatch, lambda request: httpx.Response(200, json={"paragraph": {
        "rich_text": connectors._notion_text("Scholar research\n\nStructured evidence.\n\nResearch notes\nHuman corrected the comparison.")}}))
    assert connectors.pull_notion_notes(collection, "default", api_key="fixture")["imported_notes"] == 1
    paper = kb.get_paper("Attention Is All You Need")
    assert paper["notes"] == "Human corrected the comparison."
    assert paper["abstract"] == "Structured evidence." and len(paper["authors"]) == 35


def test_notion_unavailable_keeps_local_library_usable_and_blocks_publish(collection, monkeypatch):
    record_id, record = collection.list_records_with_ids("default")[0]
    collection.set_sync_state("notion_notes", record_id, external_id="BLOCK",
        external_version=json.dumps({"notes": connectors._note_hash(record["notes"])}), record={"notes": record["notes"]})
    monkeypatch.setenv("NOTION_API_KEY", "fixture")
    calls = []
    def handle(request):
        calls.append(request.method)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"code": "rate_limited"})
    transport(monkeypatch, handle)
    response = server.paper_library(action="get", paper_titles="Attention Is All You Need")
    assert response["paper"]["notes"] == record["notes"] and response["note_errors"]
    result = connectors.publish_notion(collection, "default", apply=True, api_key="fixture", data_source_id="fixture")
    assert result["successful"] == 0 and result["note_errors"] and calls == ["GET", "GET"]


def test_unlinked_notion_needs_no_credentials_or_network(collection, monkeypatch):
    monkeypatch.setattr(connectors, "_saved_credentials", lambda target: pytest.fail("unlinked cloud credential lookup"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("unlinked cloud request"))
    assert connectors.pull_linked_notes(collection, "default")["imported_notes"] == 0


def test_notion_get_refreshes_only_the_requested_paper(collection, monkeypatch):
    kb.add_papers([{"title": "Another paper"}])
    for record_id, record in collection.list_records_with_ids("default"):
        collection.set_sync_state("notion_notes", record_id, external_id=record["title"],
            external_version=json.dumps({"notes": connectors._note_hash(record.get("notes", ""))}), record={"notes": record.get("notes", "")})
    monkeypatch.setenv("NOTION_API_KEY", "fixture")
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"paragraph": {"rich_text": connectors._notion_text("Research notes\nUpdated requested paper.")}})
    transport(monkeypatch, handle)
    result = server.paper_library(action="get", paper_titles="Attention Is All You Need")
    assert result["paper"]["notes"] == "Updated requested paper." and len(calls) == 1
    assert "Attention Is All You Need" in calls[0]


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("SCHOLAR_NOTION_E2E") != "1", reason="explicit isolated Notion acceptance")
def test_notion_live_notes_roundtrip(tmp_path, monkeypatch):
    """Real configured test data source, with only this test's new page mutated."""
    monkeypatch.setattr(kb, "DEFAULT_KB_DIR", str(tmp_path))
    monkeypatch.setattr(connectors, "_saved_credentials", _saved_credentials)
    kb.add_papers([{"title": "Scholar MCP connector acceptance", "authors": ["Scholar MCP Test"],
                    "abstract": "Isolated connector acceptance, not a research paper."}],
                  collection="connector-acceptance", notes="Agent draft.")
    store = kb.get_store()
    kwargs = dict(collection="connector-acceptance", apply=True)
    result = connectors.publish_notion(store, **kwargs)
    assert result["successful"] == 1 and result["failed"] == 0
    record_id, _ = store.list_records_with_ids("connector-acceptance")[0]
    page_id = store.get_sync_state("notion", record_id)["external_id"]
    note_id = store.get_sync_state("notion_notes", record_id)["external_id"]
    key = _saved_credentials("notion")["api_key"]
    headers = connectors._notion_headers(key)
    base = os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1").rstrip("/")
    with httpx.Client(timeout=30) as client:
        try:
            children = connectors._notion_note_children(client, base, headers, note_id)
            child_id = children[0]["id"]
            edited = client.patch(f"{base}/blocks/{child_id}", headers=headers,
                json={"paragraph": {"rich_text": connectors._notion_text("Human correction: compare evaluation methods.")}})
            edited.raise_for_status()
            result = server.paper_library(action="get", collection="connector-acceptance",
                                                         paper_titles="Scholar MCP connector acceptance")
            assert result["paper"]["notes"] == "Human correction: compare evaluation methods."
            found = server.paper_library(action="search", collection="connector-acceptance", query="correction")
            assert found["papers"] and not found.get("note_errors")
            kb.update_paper("Scholar MCP connector acceptance", collection="connector-acceptance", notes="Agent built on the human correction.")
            assert connectors.publish_notion(store, **kwargs)["notes"] == {"written": 1}
            live = client.get(f"{base}/blocks/{child_id}", headers=headers)
            live.raise_for_status()
            assert "Agent built on the human correction." in connectors._notion_plain(live.json()["paragraph"]["rich_text"])
            added = client.patch(f"{base}/blocks/{note_id}/children", headers=headers, json={"children": [{
                "object": "block", "type": "paragraph", "paragraph": {"rich_text": connectors._notion_text("Human added a second paragraph.")}}]})
            added.raise_for_status()
            assert connectors.pull_notion_notes(store, "connector-acceptance")["imported_notes"] == 1
            assert kb.get_paper("Scholar MCP connector acceptance", collection="connector-acceptance")["notes"].endswith("\n\nHuman added a second paragraph.")
            client.patch(f"{base}/blocks/{added.json()['results'][0]['id']}", headers=headers, json={"archived": True}).raise_for_status()
            cleared = client.patch(f"{base}/blocks/{child_id}", headers=headers,
                json={"paragraph": {"rich_text": []}})
            cleared.raise_for_status()
            assert connectors.pull_notion_notes(store, "connector-acceptance")["imported_notes"] == 1
            assert kb.get_paper("Scholar MCP connector acceptance", collection="connector-acceptance")["notes"] == ""
        finally:
            # Recoverable Notion trash, scoped to the page this test just created.
            client.patch(f"{base}/pages/{page_id}", headers=headers, json={"archived": True}).raise_for_status()


def test_notion_does_not_overwrite_personal_notes_or_silently_truncate():
    text = "x" * 6000
    payload = connectors._notion_properties({"title": "Title", "notes": text},
        {"Name": {"type": "title"}, "Notes": {"type": "rich_text"}, "Research notes": {"type": "rich_text"}})
    assert "Notes" not in payload
    assert "".join(x["text"]["content"] for x in payload["Research notes"]["rich_text"]) == text
    with pytest.raises(ValueError):
        connectors._notion_text("x" * 200001)


def test_notion_text_notes_keep_lists_equations_and_table_cells():
    assert connectors._notion_block_text({"type": "to_do", "to_do": {"checked": True,
        "rich_text": connectors._notion_text("Compare baselines")}}) == "- [x] Compare baselines"
    assert connectors._notion_block_text({"type": "equation", "equation": {"expression": "x^2"}}) == "$$x^2$$"
    assert connectors._notion_block_text({"type": "table_row", "table_row": {
        "cells": [connectors._notion_text("Model"), connectors._notion_text("Quality")]}}) == "| Model | Quality |"


def test_connector_credentials_reuse_scoped_system_store(monkeypatch):
    from scholar_mcp import credentials
    def stored(account, *, service):
        assert account == "zotero" and service == "scholar-mcp-connectors"
        return base64.b64encode(json.dumps({"api_key": "fixture", "library_id": "123"}).encode()).decode()
    monkeypatch.setattr(credentials, "credential", stored)
    assert _saved_credentials("zotero") == {"api_key": "fixture", "library_id": "123"}


def test_obsidian_notes_roundtrip_through_real_mcp_entry_points(collection, tmp_path):
    kb.update_paper("Attention Is All You Need", notes="Agent draft.")
    connectors.export_obsidian(collection, "default", base_dir=tmp_path / "notes")
    path = tmp_path / "notes/default/Attention Is All You Need.md"
    path.write_text(path.read_text().replace("Agent draft.", "Human corrected the argument.") + "Personal starlight reminder.\n")
    result = server.paper_library(action="get", paper_titles="Attention Is All You Need")
    assert result["paper"]["notes"] == "Human corrected the argument."
    assert result["paper"]["personal_notes"] == "Personal starlight reminder."
    found = server.paper_library(action="search", query="starlight")
    assert found["papers"][0]["personal_notes"] == "Personal starlight reminder."
    # The remembered vault works without setting a global environment variable.
    updated = server.paper_library(action="update", paper_titles="Attention Is All You Need", notes="Revised with the human correction.")
    assert updated["obsidian"]["notes_written"] == 1
    assert "Revised with the human correction." in path.read_text()
    assert "Personal starlight reminder." in path.read_text()
    cleared = server.paper_library(action="update", paper_titles="Attention Is All You Need", notes="")
    assert cleared["updated"] and kb.get_paper("Attention Is All You Need")["notes"] == ""


@pytest.mark.parametrize("legacy", [False, True])
def test_obsidian_human_edits_win_over_concurrent_agent_drafts(collection, tmp_path, legacy):
    kb.update_paper("Attention Is All You Need", notes="Original note.")
    connectors.export_obsidian(collection, "default", base_dir=tmp_path / "notes")
    path = tmp_path / "notes/default/Attention Is All You Need.md"
    path.write_text(path.read_text().replace("Original note.", "Human changed this."))
    kb.update_paper("Attention Is All You Need", notes="Agent changed this independently.")
    if legacy:
        with collection.connection() as connection:
            connection.execute("UPDATE sync_state SET external_version=NULL WHERE target='obsidian'")
    result = connectors.export_obsidian(collection, "default")
    assert result["notes_written"] == 1 and result["imported_notes"] == 1
    assert not result.get("note_conflicts")
    assert "Human changed this." in path.read_text()
    assert "Agent changed this independently." not in path.read_text()
    assert kb.get_paper("Attention Is All You Need")["notes"] == "Human changed this."
    response = server.paper_library(action="get", paper_titles="Attention Is All You Need")
    assert not response.get("note_conflicts")
    assert response["paper"]["notes"] == "Human changed this."


def test_obsidian_explicit_human_clear_and_legacy_note_protection(collection, tmp_path):
    kb.update_paper("Attention Is All You Need", notes="Original note.")
    connectors.export_obsidian(collection, "default", base_dir=tmp_path / "notes")
    path = tmp_path / "notes/default/Attention Is All You Need.md"
    path.write_text(path.read_text().replace("Original note.", ""))
    assert connectors.pull_obsidian_notes(collection, "default")["imported_notes"] == 1
    assert kb.get_paper("Attention Is All You Need")["notes"] == ""
    # A legacy file with no binding must not be overwritten when it differs.
    other = tmp_path / "other/default/Attention Is All You Need.md"
    other.parent.mkdir(parents=True)
    other.write_text(vault.render_note({"title": "Attention Is All You Need", "notes": "Unlinked human work."}))
    with collection.connection() as connection:
        connection.execute("DELETE FROM sync_state WHERE target='obsidian'")
    result = connectors.export_obsidian(collection, "default", base_dir=tmp_path / "other")
    assert result["note_conflicts"] and "Unlinked human work." in other.read_text()


def test_note_parser_ignores_code_fence_heading_examples(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("## Research notes\n\n```md\n## Notes\nexample\n```\n\nEvidence.\n\n## Notes\n\nHuman text.\n")
    sections = vault.read_note_sections(path)
    assert sections["notes"].endswith("Evidence.")
    assert "## Notes\nexample" in sections["notes"]
    assert sections["personal_notes"] == "Human text."
