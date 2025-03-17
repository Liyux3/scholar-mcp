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


def test_filter_by_fields_arxiv_categories():
    """arXiv category tags like cs.CL should map to Computer Science."""
    papers = [
        {"title": "Language Model Paper", "abstract": "NLP research", "fields_of_study": ["cs.CL", "cs.AI"]},
        {"title": "Pure Math Paper", "abstract": "Topology", "fields_of_study": ["math.AT"]},
        {"title": "Stats ML Paper", "abstract": "Statistical learning", "fields_of_study": ["stat.ML"]},
    ]
    cs_filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(cs_filtered) == 2
    titles = {p["title"] for p in cs_filtered}
    assert "Language Model Paper" in titles
    assert "Stats ML Paper" in titles

    math_filtered = relevance.filter_by_fields(papers, ["Mathematics"])
    assert len(math_filtered) == 1
    assert math_filtered[0]["title"] == "Pure Math Paper"


def test_filter_by_fields_none_returns_all():
    papers = [{"title": "A"}, {"title": "B"}]
    assert relevance.filter_by_fields(papers, None) == papers


def test_filter_by_fields_biology_keywords():
    papers = [
        {"title": "Gene Expression Analysis", "abstract": "Protein synthesis in cell cultures", "fields_of_study": []},
        {"title": "Neural Network Training", "abstract": "Deep learning optimization", "fields_of_study": []},
    ]
    filtered = relevance.filter_by_fields(papers, ["Biology"])
    assert len(filtered) == 1
    assert "gene" in filtered[0]["title"].lower()


def test_filter_by_fields_with_metadata_match():
    papers = [
        {"title": "Paper A", "abstract": "", "fields_of_study": ["Mathematics"]},
        {"title": "Paper B", "abstract": "", "fields_of_study": ["Computer Science"]},
    ]
    filtered = relevance.filter_by_fields(papers, ["Mathematics"])
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Paper A"


def test_score_title_match_boost():
    """Paper with query terms in title should score higher than abstract-only match."""
    papers = [
        {
            "title": "Attention Is All You Need",
            "abstract": "We propose transformers.",
            "citation_count": 100000, "venue": "NeurIPS", "year": 2017,
            "fields_of_study": ["Computer Science"],
        },
        {
            "title": "Tensor Product Networks for Images",
            "abstract": "We use attention mechanisms. All you need is tensors.",
            "citation_count": 50, "venue": "arXiv", "year": 2024,
            "fields_of_study": ["Computer Science"],
        },
    ]
    scored = relevance.score_results("attention is all you need", papers)
    assert scored[0]["title"] == "Attention Is All You Need"


def test_score_includes_relevance_key():
    papers = [
        {"title": "Attention Mechanism", "abstract": "Attention in transformers",
         "citation_count": 10, "venue": "ICML", "year": 2024, "fields_of_study": []},
    ]
    scored = relevance.score_results("attention transformer", papers)
    assert len(scored) == 1
    assert "_relevance_score" in scored[0]
    assert 0.0 <= scored[0]["_relevance_score"] <= 1.0


def test_consensus_score_multi_source():
    """Papers from multiple sources should score higher."""
    papers = [
        {
            "title": "Multi-Source Paper on Transformers",
            "abstract": "Attention mechanism in transformer models",
            "citation_count": 10, "venue": "", "year": 2025,
            "fields_of_study": [], "_source_count": 3, "source": "s2+arxiv+openalex",
        },
        {
            "title": "Single-Source Paper on Transformers",
            "abstract": "Attention mechanism in transformer architecture",
            "citation_count": 10, "venue": "", "year": 2025,
            "fields_of_study": [], "_source_count": 1, "source": "arxiv",
        },
    ]
    scored = relevance.score_results("transformer attention", papers)
    assert len(scored) >= 2
    assert scored[0]["title"] == "Multi-Source Paper on Transformers"
    assert scored[0]["_relevance_score"] > scored[1]["_relevance_score"]


def test_deduplicate_tracks_source_count():
    """Deduplication should track how many sources found each paper."""
    papers = [
        {"title": "Same Paper Title Here", "abstract": "Test", "source": "s2",
         "external_ids": {"DOI": "10.1234/test"}, "citation_count": 100},
        {"title": "Same Paper Title Here", "abstract": "Test abstract longer",
         "source": "openalex", "external_ids": {"DOI": "10.1234/test"}, "citation_count": 50},
        {"title": "Different Paper", "abstract": "Other", "source": "arxiv",
         "external_ids": {}},
    ]
    deduped = relevance.deduplicate(papers)
    merged = [p for p in deduped if "same" in p["title"].lower()]
    assert len(merged) == 1
    assert merged[0].get("_source_count", 1) == 2


def test_tag_source_ranks():
    """tag_source_ranks should annotate each paper with its position."""
    papers = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    relevance.tag_source_ranks(papers, "s2")
    assert papers[0]["_source_ranks"] == {"s2": 0}
    assert papers[1]["_source_ranks"] == {"s2": 1}
    assert papers[2]["_source_ranks"] == {"s2": 2}
    assert papers[0]["source"] == "s2"


def test_rrf_score_single_source():
    """RRF with single source should give 1/(k+rank)."""
    paper = {"_source_ranks": {"s2": 0}, "_source_count": 1}
    score = relevance.rrf_score(paper, k=60)
    assert abs(score - 1.0 / 60) < 1e-9

    paper2 = {"_source_ranks": {"s2": 5}, "_source_count": 1}
    score2 = relevance.rrf_score(paper2, k=60)
    assert abs(score2 - 1.0 / 65) < 1e-9


def test_rrf_score_multi_source():
    """RRF with multiple sources should sum contributions."""
    paper = {"_source_ranks": {"s2": 0, "arxiv": 2, "openalex": 1}, "_source_count": 3}
    score = relevance.rrf_score(paper, k=60)
    expected = 1.0/60 + 1.0/62 + 1.0/61
    assert abs(score - expected) < 1e-9


def test_rrf_score_empty_ranks():
    """Paper with no rank info should score 0."""
    assert relevance.rrf_score({}) == 0.0
    assert relevance.rrf_score({"_source_ranks": {}}) == 0.0


def test_consensus_rrf_score():
    """Consensus RRF should multiply by vote count."""
    paper = {"_source_ranks": {"s2": 0, "arxiv": 0}, "_source_count": 2}
    rrf = relevance.rrf_score(paper, k=60)
    consensus = relevance.consensus_rrf_score(paper, k=60)
    assert abs(consensus - 2 * rrf) < 1e-9


def test_rrf_fuse_ordering():
    """rrf_fuse should rank multi-source papers above single-source."""
    papers = [
        {"title": "Single Source", "_source_ranks": {"arxiv": 0}, "_source_count": 1,
         "external_ids": {}},
        {"title": "Multi Source", "_source_ranks": {"s2": 1, "arxiv": 0, "openalex": 2},
         "_source_count": 3, "external_ids": {}},
        {"title": "Two Sources", "_source_ranks": {"s2": 0, "openalex": 0},
         "_source_count": 2, "external_ids": {}},
    ]
    fused = relevance.rrf_fuse(papers, method="consensus")
    assert fused[0]["title"] == "Two Sources" or fused[0]["title"] == "Multi Source"
    assert all("_rrf_score" in p for p in fused)
    assert fused[0]["_rrf_score"] >= fused[1]["_rrf_score"] >= fused[2]["_rrf_score"]


def test_rrf_fuse_standard_vs_consensus():
    """Standard RRF should not multiply by vote count."""
    papers = [
        {"title": "A", "_source_ranks": {"s2": 0}, "_source_count": 1},
        {"title": "B", "_source_ranks": {"s2": 5, "arxiv": 5}, "_source_count": 2},
    ]
    std = relevance.rrf_fuse([dict(p) for p in papers], method="rrf")
    con = relevance.rrf_fuse([dict(p) for p in papers], method="consensus")
    assert std[0]["_rrf_score"] != con[0]["_rrf_score"] or std[1]["_rrf_score"] != con[1]["_rrf_score"]


def test_merge_preserves_source_ranks():
    """Deduplication should merge _source_ranks from both copies."""
    papers = [
        {"title": "Same Paper Title Here", "abstract": "Test", "source": "s2",
         "external_ids": {"DOI": "10.1234/test"}, "citation_count": 100,
         "_source_ranks": {"s2": 0}, "_source_count": 1},
        {"title": "Same Paper Title Here", "abstract": "Test longer",
         "source": "openalex", "external_ids": {"DOI": "10.1234/test"},
         "citation_count": 50, "_source_ranks": {"openalex": 3}, "_source_count": 1},
    ]
    deduped = relevance.deduplicate(papers)
    assert len(deduped) == 1
    ranks = deduped[0].get("_source_ranks", {})
    assert "s2" in ranks and "openalex" in ranks
    assert ranks["s2"] == 0 and ranks["openalex"] == 3
