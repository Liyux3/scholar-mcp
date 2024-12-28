"""Tests for relevance scoring, deduplication, query optimization, and field filtering."""

from scholar_mcp import relevance


def test_extract_keywords_removes_stopwords():
    kws = relevance.extract_keywords("how does the attention mechanism work in transformers")
    assert "how" not in kws
    assert "the" not in kws
    assert "attention" in kws
    assert "mechanism" in kws
    assert "transformers" in kws


def test_extract_keywords_limits_count():
    long_query = "hybrid sliding window linear attention sparse transformer mamba state space model architecture long context retrieval benchmark efficiency"
    kws = relevance.extract_keywords(long_query, max_keywords=6)
    assert len(kws) == 6


def test_optimize_query_shortens_long():
    long_q = "how does the hybrid attention mechanism with sliding window and linear attention affect long context retrieval performance in frontier language models"
    optimized = relevance.optimize_query(long_q)
    words = optimized.split()
    assert len(words) <= 8


def test_optimize_query_preserves_short():
    short_q = "Mamba transformer hybrid"
    assert relevance.optimize_query(short_q) == short_q


def test_deduplicate_by_doi():
    papers = [
        {"title": "Paper A", "external_ids": {"DOI": "10.1234/abc"}, "source": "s2"},
        {"title": "Paper A copy", "external_ids": {"DOI": "10.1234/abc"}, "source": "arxiv"},
        {"title": "Paper B", "external_ids": {"DOI": "10.1234/def"}, "source": "s2"},
    ]
    result = relevance.deduplicate(papers)
    assert len(result) == 2
    assert result[0]["title"] == "Paper A"
    assert result[1]["title"] == "Paper B"


def test_deduplicate_by_title():
    papers = [
        {"title": "Attention Is All You Need", "external_ids": {}},
        {"title": "Attention is all you need", "external_ids": {}},
        {"title": "Different Paper", "external_ids": {}},
    ]
    result = relevance.deduplicate(papers)
    assert len(result) == 2


def test_score_results_ranks_relevant_higher():
    papers = [
        {
            "title": "Cyber Law and Espionage",
            "abstract": "Legal framework for espionage operations.",
            "citation_count": 5, "venue": "", "year": 2018,
            "fields_of_study": [],
        },
        {
            "title": "Hybrid Mamba-Transformer for Long Context",
            "abstract": "We combine sliding window attention with Mamba for efficient long context retrieval.",
            "citation_count": 50, "venue": "NeurIPS", "year": 2025,
            "fields_of_study": ["Computer Science"],
        },
    ]
    scored = relevance.score_results("hybrid attention transformer long context retrieval", papers)
    assert len(scored) >= 1
    assert "mamba" in scored[0]["title"].lower() or "hybrid" in scored[0]["title"].lower()
    assert scored[0]["_relevance_score"] > 0.3


def test_score_results_filters_irrelevant():
    papers = [
        {
            "title": "Tinnitus and Hearing Loss",
            "abstract": "Study of hearing disorders in elderly populations.",
            "citation_count": 0, "venue": "", "year": 2019,
            "fields_of_study": ["Medicine"],
        },
    ]
    scored = relevance.score_results("hybrid attention transformer", papers, min_score=0.1)
    assert len(scored) == 0


def test_filter_by_fields_keeps_matching():
    papers = [
        {"title": "Neural Networks", "abstract": "Deep learning model", "fields_of_study": ["Computer Science"]},
        {"title": "Bird Migration", "abstract": "Seasonal patterns", "fields_of_study": ["Biology"]},
    ]
    filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Neural Networks"


def test_filter_by_fields_uses_keywords_when_no_field():
    papers = [
        {"title": "Efficient Transformer Architecture", "abstract": "Neural network optimization with attention layers", "fields_of_study": []},
        {"title": "Kampung House Spatial Transformation", "abstract": "Urban dynamics of house modifications", "fields_of_study": []},
    ]
    filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(filtered) == 1
    assert "transformer" in filtered[0]["title"].lower()


def test_filter_by_fields_none_returns_all():
    papers = [{"title": "A"}, {"title": "B"}]
    assert relevance.filter_by_fields(papers, None) == papers


def test_score_includes_relevance_key():
    papers = [
        {"title": "Attention Mechanism", "abstract": "Attention in transformers",
         "citation_count": 10, "venue": "ICML", "year": 2024, "fields_of_study": []},
    ]
    scored = relevance.score_results("attention transformer", papers)
    assert len(scored) == 1
    assert "_relevance_score" in scored[0]
    assert 0.0 <= scored[0]["_relevance_score"] <= 1.0
