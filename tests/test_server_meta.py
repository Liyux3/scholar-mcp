"""Tests for diagnostic-only source metadata and concise result warnings."""

import asyncio
import logging

import yaml

from scholar_mcp import __version__, server


def test_server_suppresses_credential_bearing_request_logs(monkeypatch, caplog):
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: None)
    with caplog.at_level(logging.INFO):
        server.main()
        logging.getLogger("httpx").info("GET https://example.test/?api_key=fixture-secret")
    assert "fixture-secret" not in caplog.text


def _report(source, status="ok", count=100, latency_ms=1000, error=None):
    return {"source": source, "status": status, "count": count,
            "latency_ms": latency_ms, "error": error}


def test_paper_summary_removes_catalog_jats_before_truncation():
    paper = {"title": "A paper", "abstract": "<jats:p>Language <jats:italic>models</jats:italic> "
             "&amp; retrieval.</jats:p><jats:p>Second paragraph.</jats:p>"}
    result = server._format_paper(paper)
    assert result["abstract"] == "Language models & retrieval. Second paragraph."
    assert paper["abstract"].startswith("<jats:p>")
    plain = "The bound is x < y and y > 0."
    assert server._format_paper({"abstract": plain})["abstract"] == plain


class TestMetaBlock:
    def test_expansion_status_is_separate_from_search_source_coverage(self):
        reports = [_report('openalex'), {**_report('expansion', 'partial'),
                   'stage': True, 'error': 'references'}]
        meta = server._meta_block(reports, debug=True)
        assert meta['source_coverage'] == '1/1'
        assert meta['expansion_incomplete'] == ['references']
        assert 'Sources unavailable' not in server._meta_block(reports)['warning']

    def test_transport_outage_does_not_blame_the_query(self, monkeypatch):
        monkeypatch.setattr(server.relevance, 'optimize_query', lambda query: query)
        monkeypatch.setattr(server, '_pipeline', lambda *a, **kw: ([], [
            _report('openalex', 'error', count=0), _report('crossref', 'timeout', count=0)]))
        result = server.search_papers('valid research question')
        assert result['retryable'] is True
        monkeypatch.setattr(server, '_pipeline', lambda *a, **kw: ([], [
            _report('openalex', 'empty', count=0)]))
        assert 'retryable' not in server.search_papers('no matching records')

    def test_tool_keeps_diagnostics_behind_debug(self, monkeypatch):
        paper = {"title": "Example paper", "year": 2026, "authors": ["A"], "venue": "ICLR"}
        reports = [_report("openalex", count=1),
                   _report("dblp", status="blocked", count=0, error="PermissionError: verification")]
        monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: ([paper], reports))
        monkeypatch.setattr(server.relevance, "optimize_query", lambda q: q)
        monkeypatch.setattr(server.s2, "is_healthy", lambda: False)
        monkeypatch.setattr(server.relevance, "reranker_status", lambda *args: {"provider": "dashscope"})
        normal = server.search_papers("example")
        debug = server.search_papers("example", debug=True)
        assert set(normal) == {"results", "warning"}
        assert normal["results"] == debug["results"]
        assert debug["_meta"]["source_coverage"] == "1/2"
        assert debug["_meta"]["reranker"]["provider"] == "dashscope"

    def test_paper_relations_omit_empty_metadata(self, monkeypatch):
        monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: ([], [_report("openalex", "empty", count=0)]))
        result = server.paper_info("10.1/example", include="references")
        assert result == {"references": []}

    def test_healthy_sources_are_quiet_by_default(self):
        meta = server._meta_block([_report("openalex", count=100),
                                   _report("arxiv", count=42)])
        assert meta == {}

    def test_zero_matches_count_as_a_successful_source_response(self):
        reports = [_report("openalex"), _report("pubmed", "empty", count=0),
                   _report("dblp", "blocked", count=0)]
        assert server._meta_block(reports, debug=True)["source_coverage"] == "2/3"
        warning = server._meta_block(reports)["warning"]
        assert "dblp" in warning and "pubmed" not in warning

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
        ], debug=True)
        assert meta["source_coverage"] == "1/2"
        assert len(meta["sources_unavailable"]) == 1
        assert meta["sources_unavailable"][0]["source"] == "dblp"
        assert "503" in meta["sources_unavailable"][0]["error"]

    def test_empty_status_is_quiet_outside_debug(self):
        """An empty corpus match is ordinary; diagnostics can still inspect it."""
        meta = server._meta_block([_report("doaj", status="empty", count=0)])
        assert meta == {}
        debug = server._meta_block(
            [_report("doaj", status="empty", count=0)], debug=True
        )
        assert debug["source_reports"][0]["status"] == "empty"

    def test_extra_fields_pass_through(self):
        assert server._meta_block([_report("a")], debug=True, total=7)["total"] == 7
        assert server._meta_block([_report("a")], total=7, reranker={"provider": "dashscope"}) == {}

    def test_cloud_failure_names_local_fallback_without_raw_details(self):
        meta = server._meta_block([], reranker={"provider": "flashrank", "fallback_reason": "ReadTimeout"})
        assert "local fallback" in meta["warning"]
        assert "ReadTimeout" not in meta["warning"]
        assert server._meta_block([], reranker={"provider": "flashrank", "fallback_reason": "not configured"}) == {}

    def test_default_error_is_readable_and_debug_preserves_exception(self):
        reports = [_report("dblp", status="blocked", count=0,
                           error="PermissionError: DBLP requires browser verification")]
        normal = server._meta_block(reports)
        debug = server._meta_block(reports, debug=True)
        assert set(normal) == {"warning"}
        assert "incomplete" in normal["warning"]
        assert "dblp" in normal["warning"]
        assert debug["source_reports"][0]["error"].startswith("PermissionError:")


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
            error="Error for url 'https://api.openalex.org/works?api_key=LEAKME'")], debug=True)
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
        assert out["other_collections"] == ["downloads (70)"]

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

    def test_unknown_bibliographic_metadata_is_omitted(self):
        formatted = server._format_paper({
            "title": "Sparse Record",
            "authors": [],
            "year": None,
            "venue": "",
            "citation_count": 0,
            "abstract": "",
        })

        assert formatted == {
            "title": "Sparse Record",
            "authors": None,
            "year": None,
            "venue": None,
            "citations": None,
            "abstract": None,
        }

    def test_known_bibliographic_metadata_is_preserved(self):
        formatted = server._format_paper({
            "title": "Complete Record",
            "authors": ["Ada Author"],
            "year": 2026,
            "venue": "ICML",
            "citation_count": 7,
            "_citation_count_known": True,
            "abstract": "Evidence.",
            "external_ids": {"DOI": "10.1/example"},
            "is_open_access": True,
        })

        assert formatted == {
            "title": "Complete Record",
            "authors": ["Ada Author"],
            "year": 2026,
            "venue": "ICML",
            "citations": 7,
            "abstract": "Evidence.",
            "doi": "10.1/example",
            "id": "10.1/example",
            "open_access": True,
        }

    def test_known_zero_citations_is_distinct_from_unknown(self):
        formatted = server._format_paper({
            "title": "New Paper",
            "citation_count": 0,
            "_citation_count_known": True,
        })

        assert formatted["citations"] == 0


class TestPaperInfoInput:
    def test_rejects_unknown_sections(self):
        out = server.paper_info("paper", include="detail,unknown")
        assert out["error"] == "Invalid include selection."
        assert out["unknown"] == ["unknown"]


class TestResearchTools:
    def test_graph_requires_resolved_seeds(self, monkeypatch):
        monkeypatch.setattr(server, "_resolve_graph_seed", lambda value: None)
        out = server.build_paper_graph("missing-id")
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
        out = server.build_paper_graph("10.1234/seed")
        assert out["seeds"] == [{"id": "10.1234/seed", "title": "Seed Paper"}]
        assert "analytics" in out

    def test_paper_library_update_keeps_notes_when_only_tags_change(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb

        captured = {}

        def update(identifier, collection, notes=None, tags=None):
            captured.update(identifier=identifier, collection=collection, notes=notes, tags=tags)
            return True

        monkeypatch.setattr(kb, "update_paper", update)
        out = server.paper_library(
            action="update",
            paper_ids="10.1234/paper",
            collection="reading",
            tags="rag, method",
        )
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
        paper = {
            "title": "Projected Paper",
            "paper_id": "projected",
            "authors": [],
        }
        monkeypatch.setenv("SCHOLAR_OBSIDIAN_VAULT", str(tmp_path))
        monkeypatch.setattr(server, "_resolve_graph_seed", lambda value: paper)

        out = server.paper_library(action="save", paper_ids="projected", collection="reading")
        assert out["obsidian"]["notes_written"] == 1
        assert (tmp_path / "reading" / "Projected Paper.md").exists()


class TestDownloadIndexing:
    def test_saved_pdf_survives_a_library_index_failure(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb
        monkeypatch.setattr(server, '_find_paper', lambda *a, **kw: {'title': 'Saved'})
        monkeypatch.setattr(server.pdf_utils, 'download_paper', lambda *a, **kw: {
            'success': True, 'file_path': '/tmp/retained.pdf', 'source': 'test'})
        def unavailable(*args, **kwargs):
            raise OSError('index unavailable')
        monkeypatch.setattr(kb, 'get_paper', unavailable)
        result = server.download_paper('Saved')
        assert result['success'] and not result['indexed'] and result['file_path'] == '/tmp/retained.pdf'

    def test_successful_download_enters_download_collection(self, monkeypatch):
        from scholar_mcp import knowledge_base as kb

        paper = {"title": "Saved", "external_ids": {"DOI": "10.1/saved"}}
        captured = {}
        monkeypatch.setattr(server, "_find_paper", lambda paper_id, **_: paper)
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

        result = server.download_paper("10.1/saved")

        assert result["indexed"] is True
        assert result["newly_indexed"] is True
        assert captured["collection"] == "downloads"
        assert captured["paper"]["pdf_path"] == "/tmp/saved.pdf"


class TestReadPaper:
    def _patch_read(self, monkeypatch, text):
        monkeypatch.setattr(server, "_find_paper", lambda paper_id, **_: {"title": paper_id})
        monkeypatch.setattr(
            server.pdf_utils,
            "download_paper",
            lambda *args, **kwargs: {
                "success": True,
                "file_path": "/tmp/paper.pdf",
                "source": "test",
            },
        )
        monkeypatch.setattr(
            server.paper_reader,
            "read",
            lambda *args, **kwargs: {
                "content": text,
                "pages": server.paper_reader.DEFAULT_READ_PAGES,
                "total_pages": 24,
                "next_pages": "11-20",
            },
        )

    def test_default_read_returns_main_text(self, monkeypatch):
        self._patch_read(monkeypatch, "a" * 12_500)

        result = server.read_paper("paper")

        assert result["content"] == "a" * 12_500
        assert result["pages"] == "1-10"
        assert result["next_pages"] == "11-20"
        assert result["content_notice"] == server.READ_CONTENT_NOTICE
        assert result["copy"] == {"source": "test"}, "the reader learns which copy it read"

    def test_page_range_is_forwarded(self, monkeypatch):
        self._patch_read(monkeypatch, "unused")
        captured = {}

        def extract(file_path, pages=server.paper_reader.DEFAULT_READ_PAGES, visual="", budget=None):
            captured.update(file_path=file_path, pages=pages, visual=visual, budget=budget)
            return {"content": "appendix", "pages": pages, "total_pages": 18}

        monkeypatch.setattr(server.paper_reader, "read", extract)
        result = server.read_paper("paper", pages="11-18")

        assert result["content"] == "appendix"
        assert captured == {
            "file_path": "/tmp/paper.pdf", "pages": "11-18", "visual": "", "budget": None,
        }

    def test_visual_returns_text_and_image_content(self, monkeypatch):
        self._patch_read(monkeypatch, "unused")
        monkeypatch.setattr(
            server.paper_reader,
            "read",
            lambda *args, **kwargs: {
                "content": "figure context",
                "pages": "3",
                "total_pages": 12,
                "visual": {"selector": "Figure 1", "page": 3},
                "_image_bytes": b"png-bytes",
            },
        )

        data = server.read_paper("paper", visual="Figure 1")
        assert data["image_png"] == b"png-bytes"
        result = server._tool_result(data)

        assert result.content[0].type == "text"
        assert "image_png" not in result.content[0].text
        assert result.content[1].type == "image"
        assert result.content[1].mimeType == "image/png"
        assert result.structured_content["visual"]["selector"] == "Figure 1"


class TestPublishedToolMetadata:
    def test_server_version_matches_package(self):
        assert server.mcp.version == __version__

    def test_search_modes_are_schema_enums(self):
        tools = {
            tool.name: tool
            for tool in asyncio.run(server.mcp.list_tools())
        }
        schema = tools["search_papers"].parameters["properties"]

        assert schema["sort"]["enum"] == ["relevance", "citations", "date"]
        assert schema["sort"]["default"] == "relevance"
        assert schema["intent"]["enum"] == [
            "balanced", "foundational", "recent", "survey", "method", "dataset",
        ]
        assert schema["intent"]["default"] == "balanced"

    def test_every_tool_loads_upfront_in_claude_code(self):
        tools = asyncio.run(server.mcp.list_tools())
        assert all(tool.meta["anthropic/alwaysLoad"] is True for tool in tools)
        read = next(tool for tool in tools if tool.name == "read_paper")
        assert read.meta["anthropic/maxResultSizeChars"] == server.READ_RESULT_CHARS

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

    def test_mcp_boundary_renders_yaml_text_and_structured_data(self):
        data = {"results": [{"title": "Example", "abstract": "line one\nline two"}]}
        result = server._tool_result(data)
        text = result.content[0].text
        assert text.startswith("results:")
        assert "abstract: |-\n" in text
        assert yaml.safe_load(text) == data
        assert result.structured_content == data


def test_paced_source_reports_reduced_coverage():
    reports = [_report("openalex"), _report("openaire", "paced", 0, error="hourly quota used")]
    assert "openaire" in server._meta_block(reports)["warning"]
    debug = server._meta_block(reports, debug=True)
    assert debug["source_coverage"] == "1/2"
    assert debug["sources_unavailable"][0]["status"] == "paced"


def test_concurrent_searches_report_their_own_reranker(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    relevance = server.relevance
    waiting, release = threading.Event(), threading.Event()
    monkeypatch.setattr(relevance, "_reranker_state", dict(relevance._reranker_state))
    monkeypatch.setattr(relevance, "optimize_query", lambda query: query)
    monkeypatch.setattr(server.s2, "is_healthy", lambda: False)

    def remote(query, papers, top_n, intent):
        if query == "local-query":
            relevance._note_reranker(provider=None, fallback_reason="simulated remote timeout")
            return None
        relevance._note_reranker(provider="dashscope", model="cloud-model", fallback_reason=None)
        return papers

    def local(query, papers, top_n):
        relevance._note_reranker(provider="flashrank", model="local-model")
        return papers

    def pipeline(_dispatch, query, *args, **kwargs):
        papers = [{"paper_id": query, "title": query, "abstract": "Known test content", "year": 2026}]
        return relevance.rerank(query, papers, 1), []

    def hydrate(papers, fields=None):
        if fields == {"abstract"} and papers[0]["title"] == "local-query":
            waiting.set()
            assert release.wait(5)
        return papers

    monkeypatch.setattr(relevance, "_remote_batch", remote)
    monkeypatch.setattr(relevance, "_local_batches", local)
    monkeypatch.setattr(server, "_pipeline", pipeline)
    monkeypatch.setattr(server.metadata, "hydrate", hydrate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(server.search_papers, "local-query", debug=True)
        try:
            assert waiting.wait(5)
            second = pool.submit(server.search_papers, "cloud-query", debug=True).result(timeout=5)
        finally:
            release.set()
        first = first.result(timeout=5)
    assert first["_meta"]["reranker"] == {
        "provider": "flashrank", "model": "local-model", "fallback_reason": "simulated remote timeout"}
    assert second["_meta"]["reranker"] == {"provider": "dashscope", "model": "cloud-model"}


def test_paced_s2_traffic_skips_optional_metadata_enrichment(monkeypatch):
    paper = {"title": "Example", "abstract": "Known", "year": 2026, "authors": ["A"]}
    monkeypatch.setattr(server, "_pipeline", lambda *args, **kwargs: ([paper], [_report("s2_snippet", "paced", 0)]))
    monkeypatch.setattr(server.relevance, "optimize_query", lambda query: query)
    monkeypatch.setattr(server.s2, "is_healthy", lambda: True)

    def forbidden(*args):
        raise AssertionError("optional metadata must not compete with paced core traffic")

    monkeypatch.setattr(server.s2_snippet, "enrich_metadata", forbidden)
    assert server.search_papers("example")["results"][0]["title"] == "Example"


def test_paper_detail_preserves_available_authors_and_the_full_abstract():
    paper = {"title": "A paper", "authors": [f"Author {i}" for i in range(8)], "abstract": "A long abstract. " * 100}
    summary = server._format_paper(paper)
    detail = server._format_paper(paper, detailed=True)
    assert len(summary["authors"]) == 5 and summary["abstract"].endswith("...")
    assert detail["authors"] == paper["authors"] and detail["abstract"] == paper["abstract"]
