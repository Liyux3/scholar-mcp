"""Tests for persistent knowledge base."""

import tempfile

from scholar_mcp import knowledge_base as kb


def _with_tmp_dir(fn):
    """Run test with a temporary KB directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original = kb.DEFAULT_KB_DIR
        kb.DEFAULT_KB_DIR = tmpdir
        try:
            fn()
        finally:
            kb.DEFAULT_KB_DIR = original


def test_add_and_list():
    def _test():
        papers = [
            {"title": "Paper A", "year": 2024, "citation_count": 100},
            {"title": "Paper B", "year": 2023, "citation_count": 200},
        ]
        result = kb.add_papers(papers, collection="test")
        assert result["added"] == 2
        assert result["total"] == 2

        listed = kb.list_papers("test")
        assert len(listed) == 2
        assert listed[0]["title"] == "Paper B"

    _with_tmp_dir(_test)


def test_dedup():
    def _test():
        papers = [{"title": "Same Paper", "year": 2024}]
        kb.add_papers(papers, collection="test")
        kb.add_papers(papers, collection="test")
        listed = kb.list_papers("test")
        assert len(listed) == 1

    _with_tmp_dir(_test)


def test_search():
    def _test():
        papers = [
            {"title": "Transformer Attention", "abstract": "Self-attention mechanism"},
            {"title": "Graph Neural Network", "abstract": "Message passing on graphs"},
        ]
        kb.add_papers(papers, collection="test")
        results = kb.search_kb("attention", "test")
        assert len(results) == 1
        assert "Transformer" in results[0]["title"]

    _with_tmp_dir(_test)


def test_search_prefers_multi_term_title_matches():
    def _test():
        kb.add_papers([
            {
                "title": "Retrieval Augmented Generation for Knowledge Tasks",
                "abstract": "A method that combines retrieval with language generation.",
            },
            {
                "title": "Points of Interest for Leisure Walks",
                "abstract": "Natural language generation for walking descriptions.",
            },
        ], collection="test")
        results = kb.search_kb("retrieval generation", "test")
        assert results[0]["title"].startswith("Retrieval Augmented")

    _with_tmp_dir(_test)


def test_add_upserts_missing_metadata_without_duplication():
    def _test():
        kb.add_papers([{
            "title": "A Paper",
            "external_ids": {"DOI": "10.1234/paper"},
            "year": None,
        }], collection="test")
        result = kb.add_papers([{
            "title": "A Paper",
            "external_ids": {"DOI": "https://doi.org/10.1234/PAPER"},
            "year": 2025,
            "venue": "ICLR",
            "pdf_path": "/tmp/paper.pdf",
        }], collection="test")
        assert result["added"] == 0
        assert result["updated"] == 1
        papers = kb.list_papers("test")
        assert len(papers) == 1
        assert papers[0]["year"] == 2025
        assert papers[0]["venue"] == "ICLR"
        assert papers[0]["pdf_path"] == "/tmp/paper.pdf"

    _with_tmp_dir(_test)


def test_get_update_and_remove_by_doi():
    def _test():
        kb.add_papers([{
            "title": "Persistent Paper",
            "external_ids": {"DOI": "10.1234/persistent"},
        }], collection="test")
        assert kb.get_paper("DOI:10.1234/persistent", "test")["title"] == "Persistent Paper"
        assert kb.update_paper(
            "10.1234/persistent", "test", notes="Read carefully", tags=["rag", "method"]
        )
        updated = kb.get_paper("Persistent Paper", "test")
        assert updated["notes"] == "Read carefully"
        assert updated["tags"] == ["rag", "method"]
        assert kb.remove_paper("10.1234/persistent", "test")
        assert kb.get_paper("Persistent Paper", "test") is None

    _with_tmp_dir(_test)


def test_collections():
    def _test():
        kb.add_papers([{"title": "Paper 1"}], collection="a")
        kb.add_papers([{"title": "Paper 2"}, {"title": "Paper 3"}], collection="b")
        colls = kb.list_collections()
        assert len(colls) == 2
        names = {c["name"] for c in colls}
        assert "a" in names and "b" in names

    _with_tmp_dir(_test)


def test_remove_collection():
    def _test():
        kb.add_papers([{"title": "Temp"}], collection="temp")
        assert kb.remove_collection("temp")
        assert kb.list_papers("temp") == []

    _with_tmp_dir(_test)


def test_notes():
    def _test():
        kb.add_papers([{"title": "Noted Paper"}], collection="test", notes="important")
        listed = kb.list_papers("test")
        assert listed[0]["notes"] == "important"

    _with_tmp_dir(_test)


def test_pdf_path():
    def _test():
        papers = [{"title": "With PDF", "pdf_path": "/tmp/paper.pdf"}]
        kb.add_papers(papers, collection="test")
        listed = kb.list_papers("test")
        assert listed[0]["pdf_path"] == "/tmp/paper.pdf"

    _with_tmp_dir(_test)


def test_keeps_abstract_evidence_beyond_preview_length():
    def _test():
        abstract = "method evidence " * 100
        kb.add_papers([{"title": "Detailed", "abstract": abstract}], collection="test")
        stored = kb.list_papers("test")[0]["abstract"]
        assert len(stored) > 300
        assert stored == abstract[:4000]

    _with_tmp_dir(_test)


def test_attach_pdf_updates_an_existing_record():
    def _test():
        kb.add_papers(
            [{"title": "Downloaded", "pdf_path": "./downloads/old.pdf"}],
            collection="downloads",
        )
        assert kb.attach_pdf(
            "Downloaded", "/tmp/library/new.pdf", collection="downloads"
        )
        assert kb.list_papers("downloads")[0]["pdf_path"] == "/tmp/library/new.pdf"

    _with_tmp_dir(_test)
