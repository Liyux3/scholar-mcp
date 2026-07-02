"""Tests for the per-source metadata block attached to search results.

This block is returned on every search, so its size is a recurring cost to
the caller's context, and its contents are a recurring disclosure risk.
"""

from scholar_mcp import server


def _report(source, status="ok", count=100, latency_ms=1000, error=None):
    return {"source": source, "status": status, "count": count,
            "latency_ms": latency_ms, "error": error}


class TestMetaBlock:
    def test_healthy_sources_are_summarised(self):
        meta = server._meta_block([_report("openalex", count=100),
                                   _report("arxiv", count=42)])
        assert meta["sources_used"] == ["openalex (100)", "arxiv (42)"]
        assert "sources_unavailable" not in meta

    def test_sorted_by_yield(self):
        meta = server._meta_block([_report("a", count=5), _report("b", count=90)])
        assert meta["sources_used"] == ["b (90)", "a (5)"]

    def test_failures_are_expanded(self):
        meta = server._meta_block([
            _report("openalex"),
            _report("dblp", status="error", count=0, error="HTTPStatusError: 503"),
        ])
        assert meta["sources_used"] == ["openalex (100)"]
        assert len(meta["sources_unavailable"]) == 1
        assert meta["sources_unavailable"][0]["source"] == "dblp"
        assert "503" in meta["sources_unavailable"][0]["error"]

    def test_empty_status_counts_as_unavailable(self):
        """A source returning nothing is worth surfacing; it may be a corpus
        mismatch, but it may also be a defect, which is how a broken Scopus
        client went unnoticed for two months.
        """
        meta = server._meta_block([_report("doaj", status="empty", count=0)])
        assert meta["sources_used"] == []
        assert meta["sources_unavailable"][0]["source"] == "doaj"

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
    even when other collections are full. discover_field and download both
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
