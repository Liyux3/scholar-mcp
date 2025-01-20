"""
Benchmark evaluation for search quality.
Tests real API calls, measures precision and known-item retrieval.
Run with: pytest tests/test_eval_benchmark.py -v -s --timeout=300

Metrics:
- known_item_hit@5: did the target paper appear in top 5?
- known_item_rank: what position was it? (1 = best)
- precision@5: fraction of top 5 results that are relevant (judged by keyword overlap)
"""

import json
import time
import pytest
from scholar_mcp.server import search_papers
from scholar_mcp.relevance import extract_keywords


KNOWN_ITEM_QUERIES = [
    {
        "query": "Attention Is All You Need",
        "expect_title_contains": "attention is all you need",
        "category": "exact_title",
    },
    {
        "query": "BERT pre-training deep bidirectional transformers",
        "expect_title_contains": "bert",
        "category": "partial_title",
    },
    {
        "query": "Mamba linear time sequence modeling selective state spaces",
        "expect_title_contains": "mamba",
        "category": "partial_title",
    },
    {
        "query": "scaling laws neural language models Kaplan",
        "expect_title_contains": "scaling laws",
        "category": "title_plus_author",
    },
    {
        "query": "chain of thought prompting elicits reasoning",
        "expect_title_contains": "chain of thought",
        "category": "partial_title",
    },
    {
        "query": "AlphaFold protein structure prediction",
        "expect_title_contains": "alphafold",
        "category": "partial_title",
    },
    {
        "query": "RLHF training language models follow instructions human feedback",
        "expect_title_contains": "human feedback",
        "category": "partial_title",
    },
    {
        "query": "GPT-4 technical report",
        "expect_title_contains": "gpt-4",
        "category": "exact_title",
    },
    {
        "query": "LoRA low rank adaptation large language models",
        "expect_title_contains": "lora",
        "category": "partial_title",
    },
    {
        "query": "diffusion models beat GANs image synthesis",
        "expect_title_contains": "diffusion",
        "category": "partial_title",
    },
]


TOPIC_QUERIES = [
    {
        "query": "test time compute scaling inference reasoning",
        "kwargs": {"year": "2024-2026"},
        "must_contain_any": ["test-time", "inference", "compute", "scaling", "reasoning"],
        "category": "emerging_CS",
    },
    {
        "query": "vision language action model robot",
        "kwargs": {"year": "2024-2026"},
        "must_contain_any": ["vision", "language", "action", "robot", "vla"],
        "category": "frontier_CS",
    },
    {
        "query": "process reward model verification step",
        "kwargs": {"year": "2024-2026"},
        "must_contain_any": ["process", "reward", "verification", "step"],
        "category": "niche_CS",
    },
    {
        "query": "CRISPR gene editing cancer therapy",
        "kwargs": {"fields_of_study": "Biology"},
        "must_contain_any": ["crispr", "gene", "editing", "cancer"],
        "category": "biology",
    },
    {
        "query": "protein structure prediction deep learning",
        "kwargs": {},
        "must_contain_any": ["protein", "structure", "prediction", "alphafold"],
        "category": "cross_domain",
    },
    {
        "query": "KV cache eviction attention sink efficient inference",
        "kwargs": {},
        "must_contain_any": ["kv", "cache", "attention", "eviction", "sink", "inference"],
        "category": "niche_technique",
    },
    {
        "query": "constitutional AI alignment safety",
        "kwargs": {},
        "must_contain_any": ["constitutional", "alignment", "safety", "rlhf"],
        "category": "safety",
    },
    {
        "query": "mixture of experts sparse model efficient",
        "kwargs": {},
        "must_contain_any": ["mixture", "expert", "moe", "sparse"],
        "category": "architecture",
    },
]


EDGE_CASES = [
    {
        "query": "xyzzyplugh42 nonexistent topic",
        "expect_empty": True,
        "category": "nonsense",
    },
    {
        "query": "transformer",
        "expect_empty": False,
        "min_results": 3,
        "category": "single_word",
    },
    {
        "query": "how do large language models work and why are they so effective at generating human-like text in various domains including code generation and creative writing",
        "expect_empty": False,
        "min_results": 1,
        "category": "very_long_query",
    },
]


def _search(query, **kwargs):
    """Call search_papers and parse result."""
    raw = search_papers(query, limit=5, **kwargs)
    return json.loads(raw)


def _has_keyword_overlap(title_abstract: str, keywords: list[str], threshold: int = 1) -> bool:
    """Check if text contains at least threshold keywords."""
    text = title_abstract.lower()
    hits = sum(1 for kw in keywords if kw in text)
    return hits >= threshold


class TestKnownItemRetrieval:
    """Can we find specific well-known papers?"""

    @pytest.mark.parametrize("case", KNOWN_ITEM_QUERIES,
                             ids=[c["query"][:40] for c in KNOWN_ITEM_QUERIES])
    def test_known_item(self, case):
        result = _search(case["query"])
        time.sleep(2)

        if "error" in result:
            pytest.skip(f"Search failed: {result.get('_meta', {}).get('sources_failed', [])}")

        papers = result.get("results", [])
        assert len(papers) > 0, f"No results for: {case['query']}"

        target = case["expect_title_contains"].lower()
        titles = [p["title"].lower() for p in papers]

        hit_idx = None
        for i, t in enumerate(titles):
            if target in t:
                hit_idx = i
                break

        if hit_idx is not None:
            print(f"  FOUND at rank {hit_idx + 1}: {papers[hit_idx]['title'][:60]}")
        else:
            top_titles = [t[:60] for t in titles[:3]]
            print(f"  MISSED. Top results: {top_titles}")

        assert hit_idx is not None, f"'{target}' not found in top 5 results"
        assert hit_idx < 3, f"'{target}' found but at rank {hit_idx + 1}, expected top 3"


class TestTopicSearch:
    """Do topic searches return relevant papers?"""

    @pytest.mark.parametrize("case", TOPIC_QUERIES,
                             ids=[c["category"] for c in TOPIC_QUERIES])
    def test_topic_precision(self, case):
        result = _search(case["query"], **case.get("kwargs", {}))
        time.sleep(2)

        if "error" in result:
            pytest.skip(f"Search failed: {result.get('_meta', {}).get('sources_failed', [])}")

        papers = result.get("results", [])
        assert len(papers) >= 3, f"Too few results ({len(papers)}) for: {case['query']}"

        relevant_count = 0
        for p in papers[:5]:
            text = (p.get("title", "") + " " + p.get("abstract", "")).lower()
            if _has_keyword_overlap(text, case["must_contain_any"], threshold=2):
                relevant_count += 1

        precision = relevant_count / min(len(papers), 5)
        print(f"  Precision@5: {precision:.1%} ({relevant_count}/{min(len(papers), 5)})")

        assert precision >= 0.6, f"Precision@5 too low: {precision:.1%} for {case['category']}"


class TestEdgeCases:
    """Handle edge cases gracefully."""

    @pytest.mark.parametrize("case", EDGE_CASES,
                             ids=[c["category"] for c in EDGE_CASES])
    def test_edge_case(self, case):
        result = _search(case["query"])
        time.sleep(2)

        papers = result.get("results", [])

        if case.get("expect_empty"):
            assert len(papers) == 0 or all(
                p.get("_relevance_score", 0) < 0.2 for p in papers
            ), f"Nonsense query returned confident results: {[p['title'][:40] for p in papers[:3]]}"
            print(f"  Correctly returned {'empty' if not papers else 'low-confidence'} results")
        else:
            min_r = case.get("min_results", 1)
            assert len(papers) >= min_r, f"Expected >= {min_r} results, got {len(papers)}"
            print(f"  Returned {len(papers)} results")


class TestSearchMeta:
    """Verify metadata and transparency."""

    def test_meta_present(self):
        result = _search("transformer architecture")
        time.sleep(1)
        assert "_meta" in result
        meta = result["_meta"]
        assert "sources_used" in meta
        assert "sources_failed" in meta

    def test_multi_source(self):
        result = _search("deep learning optimization")
        time.sleep(1)
        if "error" not in result:
            meta = result["_meta"]
            print(f"  Sources used: {meta['sources_used']}")
            assert len(meta["sources_used"]) >= 1

    def test_relevance_scores_present(self):
        result = _search("neural network training")
        time.sleep(1)
        if "results" in result:
            for p in result["results"]:
                assert "_relevance_score" in p
                assert 0 <= p["_relevance_score"] <= 1.0
