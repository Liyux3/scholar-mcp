"""Tests for the per-source metadata block attached to search results.

This block is returned on every search, so its size is a recurring cost to
the caller's context, and its contents are a recurring disclosure risk.
"""

import asyncio

import yaml

from scholar_mcp import __version__, server


def _report(source, status="ok", count=100, latency_ms=1000, error=None):
    return {"source": source, "status": status, "count": count,
            "latency_ms": latency_ms, "error": error}


class TestMetaBlock:
    def test_healthy_sources_collapse_to_coverage(self):
        meta = server._meta_block([_report("openalex", count=100),
                                   _report("arxiv", count=42)])
        assert meta["source_coverage"] == "2/2"
        assert "sources_unavailable" not in meta
        assert "source_reports" not in meta

    def test_debug_includes_stable_per_source_reports(self):
        meta = server._meta_block(
            [_report("b", count=90), _report("a", count=5)], debug=True
        )
        assert [report["source"] for report in meta["source_reports"]] == ["a", "b"]
        assert meta["source_reports"][0]["count"] == 5

    def test_failures_are_expanded(self):
        meta = server._meta_block([
            _report("openalex"),
            _report("dblp", status="error", count=0, error="HTTPStatusError: 503"),
        ])
        assert meta["source_coverage"] == "1/2"
        assert len(meta["sources_unavailable"]) == 1
        assert meta["sources_unavailable"][0]["source"] == "dblp"
        assert "503" in meta["sources_unavailable"][0]["error"]

    def test_empty_status_is_quiet_outside_debug(self):
        """An empty corpus match is ordinary; diagnostics can still inspect it."""
        meta = server._meta_block([_report("doaj", status="empty", count=0)])
        assert meta["source_coverage"] == "0/1"
        assert "sources_unavailable" not in meta
        debug = server._meta_block(
            [_report("doaj", status="empty", count=0)], debug=True
        )
        assert debug["source_reports"][0]["status"] == "empty"

    def test_extra_fields_pass_through(self):
        assert server._meta_block([_report("a")], total=7)["total"] == 7


class TestErrorCleaning:
    def test_strips_urls_carrying_api_keys(self):
        """httpx puts the full request URL in its message, and several sources
        pass their key as a query parameter. That must not reach the caller.
        """
        raw = ("HTTPStatusError: Client error '429 Too Many Requests' for url "
               "'https://api.openalex.org/works?api_key=SECRETKEY123&search=x'")
        cleaned = server._clean_error(raw)
        assert "SECRETKEY123" not in cleaned
        assert "api_key" not in cleaned
        assert "429" in cleaned, "the useful part of the message was lost"

    def test_collapses_whitespace(self):
        assert server._clean_error("a\n\n  b   c") == "a b c"

    def test_truncates_long_messages(self):
        cleaned = server._clean_error("x" * 500)
        assert len(cleaned) <= server.ERROR_MAX_CHARS + 3
        assert cleaned.endswith("...")

    def test_handles_empty(self):
        assert server._clean_error("") == ""
        assert server._clean_error(None) is None

    def test_meta_block_cleans_errors(self):
        meta = server._meta_block([_report(
            "openalex", status="error", count=0,
            error="Error for url 'https://api.openalex.org/works?api_key=LEAKME'")])
        assert "LEAKME" not in str(meta)


class TestKnowledgeBaseDiscoverability:
    """An empty listing of the default collection reads as "nothing saved",
    even when other collections are full. Downloads and research workflows
    write to named collections, so that is the normal state.
    """

    def test_empty_result_points_at_other_collections(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb
        monkeypatch.setattr(kb, "list_papers", lambda **kw: [])
        monkeypatch.setattr(kb, "list_collections", lambda: [
            {"name": "default", "papers": 0},
            {"name": "downloads", "papers": 70},
        ])
        out = server.knowledge_base(action="list")
        assert "downloads (70)" in out

    def test_no_hint_when_nothing_is_saved_anywhere(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb
        monkeypatch.setattr(kb, "list_papers", lambda **kw: [])
        monkeypatch.setattr(kb, "list_collections", lambda: [
            {"name": "default", "papers": 0},
        ])
        assert "other_collections" not in server.knowledge_base(action="list")

    def test_no_hint_when_results_exist(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb
        monkeypatch.setattr(kb, "list_papers", lambda **kw: [{"title": "A Paper"}])
        monkeypatch.setattr(kb, "list_collections", lambda: [
            {"name": "other", "papers": 5},
        ])
        assert "other_collections" not in server.knowledge_base(action="list")


class TestFollowUpIdentifiers:
    def test_search_result_exposes_downloadable_id(self):
        formatted = server._format_paper({
            "title": "An arXiv Paper",
            "external_ids": {"ArXiv": "2401.01234"},
        })
        assert formatted["id"] == "ARXIV:2401.01234"

    def test_default_result_hides_internal_ranking_metadata(self):
        paper = {
            "title": "A Paper",
            "source": "openalex+arxiv",
            "_final_score": 0.93,
            "_source_ranks": {"openalex": 1, "arxiv": 2},
        }
        assert "score" not in server._format_paper(paper)
        assert "sources" not in server._format_paper(paper)
        debug = server._format_paper(paper, debug=True)
        assert debug["score"] == 0.93
        assert debug["sources"] == ["arxiv", "openalex"]


class TestPaperInfoInput:
    def test_rejects_unknown_sections(self):
        out = yaml.safe_load(server.paper_info("paper", include="detail,unknown"))
        assert out["error"] == "Invalid include selection."
        assert out["unknown"] == ["unknown"]


class TestResearchTools:
    def test_graph_requires_resolved_seeds(self, monkeypatch):
        monkeypatch.setattr(server, "_resolve_graph_seed", lambda value: None)
        out = yaml.safe_load(server.build_paper_graph("missing-id"))
        assert out["error"] == "No graph seeds could be resolved."
        assert out["unresolved"] == ["missing-id"]

    def test_graph_returns_seed_resolution_and_analytics(self, monkeypatch):
        seed = {
            "title": "Seed Paper",
            "paper_id": "seed",
            "external_ids": {"DOI": "10.1234/seed"},
        }
        monkeypatch.setattr(server, "_resolve_graph_seed", lambda value: seed)
        monkeypatch.setattr(server.graph, "build_graph", lambda *args, **kwargs: {
            "summary": "Graph summary",
            "mermaid": "graph TD",
            "nodes": [],
            "edges": [],
            "stats": {"total_nodes": 1},
            "analytics": {"pagerank": {}},
        })
        out = yaml.safe_load(server.build_paper_graph("10.1234/seed"))
        assert out["seeds"] == [{"id": "10.1234/seed", "title": "Seed Paper"}]
        assert "analytics" in out

    def test_paper_library_update_keeps_notes_when_only_tags_change(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb

        captured = {}

        def update(identifier, collection, notes=None, tags=None):
            captured.update(identifier=identifier, collection=collection, notes=notes, tags=tags)
            return True

        monkeypatch.setattr(kb, "update_paper", update)
        out = yaml.safe_load(server.paper_library(
            action="update",
            paper_ids="10.1234/paper",
            collection="reading",
            tags="rag, method",
        ))
        assert out["updated"] is True
        assert captured == {
            "identifier": "10.1234/paper",
            "collection": "reading",
            "notes": None,
            "tags": ["rag", "method"],
        }

    def test_paper_library_save_refreshes_configured_obsidian_projection(
        self, monkeypatch, tmp_path
    ):
        from scholar_mcp import knowledge_base as kb

        paper = {
            "title": "Projected Paper",
            "paper_id": "projected",
            "authors": [],
        }
        monkeypatch.setenv("SCHOLAR_OBSIDIAN_VAULT", str(tmp_path))
        monkeypatch.setattr(server, "_resolve_graph_seed", lambda value: paper)
        monkeypatch.setattr(
            kb,
            "add_papers",
            lambda papers, collection, notes="": {
                "added": 1,
                "updated": 0,
                "total": 1,
                "collection": collection,
            },
        )
        monkeypatch.setattr(kb, "list_papers", lambda collection, limit: [paper])

        out = yaml.safe_load(
            server.paper_library(
                action="save",
                paper_ids="projected",
                collection="reading",
            )
        )
        assert out["obsidian"]["notes_written"] == 1
        assert (tmp_path / "reading" / "Projected Paper.md").exists()


class TestDownloadIndexing:
    def test_successful_download_enters_download_collection(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb

        paper = {"title": "Saved", "external_ids": {"DOI": "10.1/saved"}}
        captured = {}
        monkeypatch.setattr(server, "_find_paper", lambda paper_id: paper)
        monkeypatch.setattr(
            server.pdf_utils,
            "download_paper",
            lambda *args: {"success": True, "file_path": "/tmp/saved.pdf", "source": "cache"},
        )

        def add_papers(papers, collection):
            captured["paper"] = papers[0]
            captured["collection"] = collection
            return {"added": 1}

        monkeypatch.setattr(kb, "add_papers", add_papers)

        result = yaml.safe_load(server.download_paper("10.1/saved"))

        assert result["indexed"] is True
        assert result["newly_indexed"] is True
        assert captured["collection"] == "downloads"
        assert captured["paper"]["pdf_path"] == "/tmp/saved.pdf"


class TestReadPaper:
    def _patch_read(self, monkeypatch, text):
        monkeypatch.setattr(server, "_find_paper", lambda paper_id: {"title": paper_id})
        monkeypatch.setattr(
            server.pdf_utils,
            "download_paper",
            lambda *args: {
                "success": True,
                "file_path": "/tmp/paper.pdf",
                "source": "test",
            },
        )
        monkeypatch.setattr(
            server.pdf_utils,
            "extract_text",
            lambda *args, **kwargs: {
                "content": text,
                "pages": server.pdf_utils.DEFAULT_READ_PAGES,
                "total_pages": 24,
                "next_pages": "11-20",
            },
        )

    def test_default_read_returns_main_text(self, monkeypatch):
        self._patch_read(monkeypatch, "a" * 12_500)

        result = yaml.safe_load(server.read_paper("paper"))

        assert result["content"] == "a" * 12_500
        assert result["pages"] == "1-10"
        assert result["next_pages"] == "11-20"
        assert result["content_notice"] == server.READ_CONTENT_NOTICE

    def test_page_range_is_forwarded(self, monkeypatch):
        self._patch_read(monkeypatch, "unused")
        captured = {}

        def extract(file_path, pages=server.pdf_utils.DEFAULT_READ_PAGES, visual=""):
            captured.update(file_path=file_path, pages=pages, visual=visual)
            return {"content": "appendix", "pages": pages, "total_pages": 18}

        monkeypatch.setattr(server.pdf_utils, "extract_text", extract)
        result = yaml.safe_load(server.read_paper("paper", pages="11-18"))

        assert result["content"] == "appendix"
        assert captured == {
            "file_path": "/tmp/paper.pdf", "pages": "11-18", "visual": "",
        }

    def test_visual_returns_text_and_image_content(self, monkeypatch):
        self._patch_read(monkeypatch, "unused")
        monkeypatch.setattr(
            server.pdf_utils,
            "extract_text",
            lambda *args, **kwargs: {
                "content": "figure context",
                "pages": "3",
                "total_pages": 12,
                "visual": {"selector": "Figure 1", "page": 3},
                "_image_bytes": b"png-bytes",
            },
        )

        result = server.read_paper("paper", visual="Figure 1")

        assert isinstance(result, server.ToolResult)
        assert result.content[0].type == "text"
        assert result.content[1].type == "image"
        assert result.content[1].mimeType == "image/png"
        assert result.structured_content["visual"]["selector"] == "Figure 1"

class TestPublishedToolMetadata:
    def test_server_version_matches_package(self):
        assert __version__ == "0.8.2"
        assert server.mcp.version == __version__

    def test_every_tool_declares_behavior_annotations(self):
        tools = {
            tool.name: tool
            for tool in asyncio.run(server.mcp.list_tools())
        }
        assert set(tools) == {
            "search_papers",
            "paper_info",
            "recommend_papers",
            "search_authors",
            "download_paper",
            "read_paper",
        }
        assert all(tool.annotations is not None for tool in tools.values())
        assert tools["search_papers"].annotations.readOnlyHint is True
        assert tools["download_paper"].annotations.readOnlyHint is False
        assert tools["read_paper"].annotations.readOnlyHint is True
        assert all(tool.annotations.destructiveHint is False for tool in tools.values())
        assert tools["search_papers"].output_schema == {
            "type": "object",
            "additionalProperties": True,
        }

    def test_status_is_a_resource(self):
        resources = asyncio.run(server.mcp.list_resources())
        assert [str(resource.uri) for resource in resources] == ["scholar://status"]

    def test_yaml_adapter_preserves_text_and_structured_data(self):
        result = server._yaml_tool_result("results:\n- title: Example\n")
        assert result.content[0].text.startswith("results:")
        assert result.structured_content == {"results": [{"title": "Example"}]}
