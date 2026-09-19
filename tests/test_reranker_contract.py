"""Reranker transport, response atomicity, and cross-pass model boundaries."""
import copy
import sys
from types import SimpleNamespace

import httpx
import pytest

from scholar_mcp import config, relevance, server, sources


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "")
    monkeypatch.setattr(config, "RERANK_BATCH_SIZE", 500)
    monkeypatch.setattr(relevance, "_reranker_state", dict(relevance._reranker_state))
    monkeypatch.setattr(relevance, "_dashscope_warning_shown", True)


def test_custom_capacity_changes_batches_not_candidate_coverage(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "http://localhost/rerank")
    monkeypatch.setattr(config, "RERANK_BATCH_SIZE", 2)
    seen = []

    def remote(query, papers, top_n, intent):
        seen.append(len(papers))
        return relevance._apply_rerank_results([
            {"index": index, "relevance_score": paper["value"] / 10}
            for index, paper in enumerate(papers)
        ], papers, top_n, "custom", "model")

    monkeypatch.setattr(relevance, "_remote_batch", remote)
    result = relevance.rerank("q", [{"value": i} for i in range(5)], 5)
    assert sorted(seen) == [1, 2, 2]
    assert [paper["value"] for paper in result] == [4, 3, 2, 1, 0]


def test_custom_capacity_does_not_change_builtin_qwen_limit(monkeypatch):
    monkeypatch.setattr(config, "RERANK_BATCH_SIZE", 1)
    seen = []
    def remote(query, papers, top_n, intent):
        seen.append(len(papers))
        return papers
    monkeypatch.setattr(relevance, "_remote_batch", remote)
    relevance.rerank("q", [{"title": "A"}, {"title": "B"}], 2)
    assert seen == [2]


def test_token_budget_splits_long_documents_without_dropping_any(monkeypatch):
    monkeypatch.setattr(relevance, "DASHSCOPE_TOKEN_BUDGET", 2000)
    seen = []
    def remote(query, batch, top_n, intent):
        seen.append(len(batch))
        for paper in batch:
            paper["_rerank_score"] = .5
        return batch
    monkeypatch.setattr(relevance, "_remote_batch", remote)
    papers = [{"title": str(i), "abstract": "长文本" * 500} for i in range(4)]
    assert len(relevance.rerank("query", papers, 4)) == 4
    assert seen == [1, 1, 1, 1]


def test_provider_size_rejection_splits_on_same_model(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "http://localhost/rerank")
    seen = []
    def post(url, **kwargs):
        body = kwargs["json"]
        seen.append(len(body["documents"]))
        if len(body["documents"]) > 2:
            return httpx.Response(400, text="maximum input token limit exceeded", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"results": [
            {"index": i, "relevance_score": .5} for i in range(len(body["documents"]))
        ]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    result = relevance.rerank("q", [{"title": str(i)} for i in range(5)], 5)
    assert len(result) == 5 and {p["_reranker_provider"] for p in result} == {"custom"}
    assert seen == [5, 2, 3, 1, 2]


def test_custom_endpoint_does_not_inherit_cloud_credentials(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "http://127.0.0.1:9010/rerank")
    monkeypatch.setattr(config, "RERANK_MODEL", "local-model")
    monkeypatch.setattr(config, "RERANK_API_KEY", None)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "private-cloud-key")
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.2},
            {"index": 1, "relevance_score": 0.8},
        ]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    result = relevance.rerank("Which paper?", [{"title": "A"}, {"title": "B"}], 2, "method")
    assert [p["title"] for p in result] == ["B", "A"]
    assert "Authorization" not in seen["headers"]
    assert seen["json"]["model"] == "local-model"
    assert "instruct" not in seen["json"]
    assert "Which paper?" in seen["json"]["query"]
    assert seen["timeout"] == config.RERANK_TIMEOUT
    assert seen["trust_env"] is False
    assert relevance.reranker_status()["model"] == "local-model"


@pytest.mark.parametrize("items", [
    [], [{"index": 0, "relevance_score": 0.5}],
    [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.7}],
    [{"index": 0, "relevance_score": 0.5}, {"index": -1, "relevance_score": 0.7}],
    [{"index": 0, "relevance_score": 0.5}, {"index": True, "relevance_score": 0.7}],
    [{"index": 0, "relevance_score": 0.5}, {"index": 1, "relevance_score": float("nan")}],
    [{"index": 0, "relevance_score": 0.5}, {"index": 1, "relevance_score": -4.2}],
    [{"index": 0, "relevance_score": 0.5}, {"index": 1, "relevance_score": 4.2}],
])
def test_invalid_response_is_atomic(items):
    papers = [{"title": "A", "_rerank_score": 0.9}, {"title": "B"}]
    before = copy.deepcopy(papers)
    with pytest.raises(ValueError):
        relevance._apply_rerank_results(items, papers, 2, "custom", "model")
    assert papers == before


def test_custom_failure_uses_local_fallback_not_dashscope(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "http://127.0.0.1:9010/rerank")
    monkeypatch.setattr(relevance, "_rerank_remote", lambda *a, **k: None)
    monkeypatch.setattr(relevance, "_rerank_dashscope", lambda *a, **k: pytest.fail("cloud fallback"))
    monkeypatch.setattr(relevance, "_rerank_flashrank", lambda q, p, n: p[:n])
    assert relevance.rerank("q", [{"title": "A"}], 1)[0]["title"] == "A"


def test_document_keeps_publication_date_without_year():
    text = relevance.rerank_document({"title": "A", "publication_date": "2026-09-14", "abstract": "Body"})
    assert "2026-09-14" in text and "Abstract: Body" in text


def test_local_model_failure_does_not_erase_papers_or_keep_old_provenance(monkeypatch):
    def broken_ranker(**kwargs):
        from pathlib import Path
        assert kwargs["log_level"] == "WARNING"
        assert Path(kwargs["cache_dir"]) == Path(config.DATA_DIR) / "models"
        raise OSError("cannot load model")

    monkeypatch.setitem(sys.modules, "flashrank", SimpleNamespace(Ranker=broken_ranker, RerankRequest=object))
    monkeypatch.setattr(relevance, "_flashrank_ranker", None)
    paper = {"title": "A", "_reranker_model": "cloud", "_rerank_rank": 1}
    result = relevance._rerank_flashrank("q", [paper], 1)
    assert result == [paper]
    assert paper["_reranker_model"] is None
    assert paper["_reranker_provider"] == "unavailable"
    assert "_rerank_rank" not in paper
    assert "OSError" in relevance.reranker_status()["fallback_reason"]


def test_expansion_uses_final_scores_without_cross_model_blending(monkeypatch):
    monkeypatch.setattr(sources, "parallel_search", lambda *a, **kw: [
        sources.SourceResult("openalex", "ok", [{"title": "Seed"}], 1),
    ])
    monkeypatch.setattr(relevance, "optimize_query_short", lambda q: q)
    monkeypatch.setattr(relevance, "rank_final", lambda papers: papers)
    monkeypatch.setattr(server.expansion, "expand", lambda *a, **kw: {"references": [{"title": "New"}]})
    passes = []

    def rerank(query, papers, top_n, intent):
        passes.append(len(papers))
        for p in papers:
            p["_rerank_score"] = 0.99 if len(passes) == 1 else 0.1
            p["_reranker_provider"] = "dashscope" if len(passes) == 1 else "flashrank"
        return papers

    monkeypatch.setattr(relevance, "rerank", rerank)
    papers, _ = server._pipeline("search", "q", 10, rerank_query="q", expand_min_pool=1)
    assert passes == [1, 2]
    assert {p["_rerank_score"] for p in papers} == {0.1}
    assert {p["_reranker_provider"] for p in papers} == {"flashrank"}
