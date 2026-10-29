"""Reranker transport, response atomicity, and cross-pass model boundaries."""
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scholar_mcp import config, relevance, server, sources

ORIGINAL_FLASHRANK_SCORES = relevance._flashrank_scores


@pytest.mark.parametrize('cache_present', [True, False])
def test_gpu_loader_uses_local_assets_before_any_download(monkeypatch, cache_present):
    calls = []
    model = SimpleNamespace()
    model.to = lambda device: model
    model.eval = lambda: model
    def load_tokenizer(name, **kw):
        calls.append((name, kw['local_files_only']))
        if not cache_present and kw['local_files_only']:
            raise OSError('checkpoint not installed')
        return object()
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(float16='fp16'))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(
        try_to_load_from_cache=lambda *a, **k: '/cached/snapshot/config.json' if cache_present else None))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=load_tokenizer),
        AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=lambda *a, **kw: model)))
    monkeypatch.setattr(relevance, '_gpu_models', {})
    monkeypatch.setattr(relevance, 'cached_cpu_model_directory', lambda: None)
    assert relevance._load_gpu_model('mps', relevance.GPU_MODEL)[1] is model
    assert calls == ([(str(Path('/cached/snapshot')), True)] if cache_present else [(relevance.GPU_MODEL, False)])


@pytest.mark.integration
def test_cached_gpu_reranker_scores_with_network_connections_disabled(monkeypatch):
    import os
    from pathlib import Path
    import socket
    pytest.importorskip('torch')
    pytest.importorskip('transformers')
    monkeypatch.setattr(relevance, '_gpu_device', None)
    if not relevance._gpu():
        pytest.skip('requires a GPU with the optional torch runtime')
    data = Path(os.environ.get('SCHOLAR_DATA_DIR', '~/.scholar-mcp')).expanduser()
    if not (data / 'models' / ('models--' + relevance.GPU_MODEL.replace('/', '--'))).is_dir():
        pytest.skip('requires the already-installed checkpoint; this test never downloads it')
    monkeypatch.setattr(config, 'DATA_DIR', str(data))
    monkeypatch.setattr(relevance, '_gpu_models', {})
    attempts = []
    def denied(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('cached inference attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    scores = relevance._gpu_scores('How can an agent retain memories?', [
        {'title': 'Memory retrieval in autonomous language agents'},
        {'title': 'Numerical fluid dynamics'}], 128)
    assert len(scores) == 2 and all(0 <= item['relevance_score'] <= 1 for item in scores)
    assert not attempts


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "RERANK_URL", "")
    monkeypatch.setattr(config, "RERANK_MODEL", "")
    monkeypatch.setattr(config, "DASHSCOPE_HTTP_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")
    monkeypatch.setattr(config, "RERANK_BATCH_SIZE", 500)
    monkeypatch.setattr(relevance, "_reranker_state", dict(relevance._reranker_state))
    monkeypatch.setattr(relevance, "_dashscope_warning_shown", True)
    monkeypatch.setattr(relevance, "cpu_model_directory", lambda: Path('/models') / relevance.FLASHRANK_MODEL)


@pytest.mark.parametrize('quota, total, expected', [('200000 100000', 8, 2), ('max 100000', 8, 4), ('max 100000', 4, 0)])
def test_cpu_threads_respect_affinity_and_container_quota(monkeypatch, quota, total, expected):
    monkeypatch.setattr(relevance.sys, 'platform', 'linux')
    monkeypatch.setattr(relevance.os, 'cpu_count', lambda: total)
    monkeypatch.setattr(relevance.os, 'sched_getaffinity', lambda pid: {0, 1, 2, 3}, raising=False)
    monkeypatch.setattr(Path, 'read_text', lambda path, *a, **kw: quota)
    assert relevance._cpu_threads() == expected


def test_ready_cpu_reader_does_not_retire_a_healthy_cuda_close_reader(monkeypatch):
    attempted = []
    def missing(*args, **kwargs):
        attempted.append(kwargs['local_files_only'])
        raise OSError('optional MiniLM checkpoint is not cached')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(float16=object()))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(try_to_load_from_cache=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=missing), AutoModelForSequenceClassification=object()))
    monkeypatch.setitem(sys.modules, 'flashrank', SimpleNamespace(
        RerankRequest=lambda query, passages: SimpleNamespace(query=query, passages=passages)))
    monkeypatch.setattr(relevance, '_gpu_device', 'cuda')
    monkeypatch.setattr(relevance, '_gpu_models', {})
    monkeypatch.setattr(relevance, '_flashrank_rankers', {})
    monkeypatch.setattr(relevance, '_close_error', None)
    monkeypatch.setattr(relevance, 'cached_cpu_model_directory', lambda: Path('/ready'))
    monkeypatch.setattr(relevance, '_new_cpu_ranker', lambda length: SimpleNamespace(
        rerank=lambda request: [{'id': p['id'], 'score': .4} for p in request.passages]))
    assert ORIGINAL_FLASHRANK_SCORES('q', [{'title': 'a'}], 128)[0]['relevance_score'] == .4
    assert relevance._gpu_device == 'cuda' and relevance._reader()[0] == relevance.CLOSE_MODEL
    assert relevance.reranker_status()['device'] == 'cpu' and attempted == []


def test_local_model_runs_on_the_gpu_and_falls_back_to_the_cpu_for_good(monkeypatch):
    monkeypatch.setattr(relevance, "_reranker_state", dict(relevance._reranker_state))
    monkeypatch.setattr(relevance, "_gpu_device", "mps")
    monkeypatch.setattr(relevance, "_gpu_scores", lambda query, papers, tokens: [
        {"index": i, "relevance_score": .5} for i in range(len(papers))])
    papers = [{"title": "A"}, {"title": "B"}]
    assert len(ORIGINAL_FLASHRANK_SCORES("q", papers, 128)) == 2 and relevance._reranker_state["device"] == "mps"

    batches, sessions = [], []

    class Ranker:
        def __init__(self, **kwargs):
            pytest.fail('the stock constructor would create a second thread pool')

        def _get_tokenizer(self, max_length):
            return object()

        def rerank(self, request):
            batches.append(len(request.passages))
            return [{"id": p["id"], "score": .4} for p in request.passages]
    monkeypatch.setitem(sys.modules, "flashrank", SimpleNamespace(
        Ranker=Ranker, RerankRequest=lambda query, passages: SimpleNamespace(query=query, passages=passages)))
    monkeypatch.setitem(sys.modules, "flashrank.Config", SimpleNamespace(model_file_map={relevance.FLASHRANK_MODEL: "model.onnx"}))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(enable_cpu_mem_arena=True),
        InferenceSession=lambda path, sess_options, providers: sessions.append((path, sess_options.enable_cpu_mem_arena,
                                                                              sess_options.intra_op_num_threads, providers))))
    monkeypatch.setattr(relevance, '_cpu_threads', lambda: 2)
    monkeypatch.setattr(relevance, "_flashrank_rankers", {})
    monkeypatch.setattr(relevance, "_gpu_scores", lambda *args: 1 / 0)
    assert [item["relevance_score"] for item in ORIGINAL_FLASHRANK_SCORES("q", papers, 128)] == [.4, .4]
    assert relevance._gpu_device == "" and relevance._reranker_state["device"] == "cpu"
    assert relevance._reranker_state["gpu_error"].startswith("ZeroDivisionError")
    # The CPU session runs without ONNX Runtime's arena, in batches sized in tokens.
    assert sessions == [(str(Path('/models') / relevance.FLASHRANK_MODEL / 'model.onnx'), False, 2, ["CPUExecutionProvider"])]
    monkeypatch.setattr(relevance, "FLASHRANK_BATCH_TOKENS", 256)
    batches.clear()
    ORIGINAL_FLASHRANK_SCORES("q", [{"title": str(i)} for i in range(5)], 128)
    assert batches == [2, 2, 1]


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
    assert seen["timeout"].read == config.RERANK_TIMEOUT
    assert seen["trust_env"] is False
    assert relevance.reranker_status()["model"] == "local-model"


@pytest.mark.parametrize("base", [
    "https://workspace.cn-beijing.maas.aliyuncs.com",
    "https://workspace.ap-southeast-1.maas.aliyuncs.com/api/v1/",
    "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-api/v1",
    "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://gateway.example/prefix/api/v1",
])
def test_workspace_base_preserves_host_prefix_key_and_qwen_instruction(monkeypatch, base):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setattr(config, "DASHSCOPE_HTTP_BASE_URL", base)
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": .8}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    result = relevance.rerank("query", [{"title": "A"}], 1, "dataset")
    url, request = requests[0]
    assert httpx.URL(url).host == httpx.URL(base).host
    assert url.endswith("/compatible-api/v1/reranks")
    if "prefix" in base:
        assert "/prefix/compatible-api/" in url
    assert request["headers"]["Authorization"] == "Bearer fixture-key"
    assert request["json"]["model"] == "qwen3-rerank"
    assert request["timeout"].read == config.RERANK_TIMEOUT
    assert request["json"]["instruct"] == relevance.INTENT_INSTRUCTS["dataset"]
    assert result[0]["_reranker_provider"] == "dashscope"


@pytest.mark.parametrize("model", ["qwen3.7-text-rerank", "gte-rerank-v2", "qwen3-vl-rerank"])
def test_native_dashscope_wire_format_and_size_splitting(monkeypatch, model):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "fixture-key")
    monkeypatch.setattr(config, "DASHSCOPE_HTTP_BASE_URL", "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1")
    monkeypatch.setattr(config, "RERANK_MODEL", model)
    seen = []
    def post(url, **kwargs):
        body = kwargs["json"]
        assert url.endswith(relevance._DASHSCOPE_NATIVE_RERANK_PATH)
        assert body["model"] == model
        assert set(body) == {"model", "input", "parameters"}
        assert "query" in body["input"]
        assert ("instruct" in body["parameters"]) == (model != "gte-rerank-v2")
        documents = body["input"]["documents"]
        seen.append(len(documents))
        if len(documents) > 1:
            return httpx.Response(413, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"output": {"results": [{"index": 0, "relevance_score": .7}]}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    result = relevance.rerank("query", [{"title": "A"}, {"title": "B"}], 2, "method")
    assert seen == [2, 1, 1]
    assert {p["_reranker_model"] for p in result} == {model}
    assert relevance.reranker_status()["provider"] == "dashscope"


def test_explicit_native_url_uses_only_custom_key(monkeypatch):
    url = "https://gateway.example" + relevance._DASHSCOPE_NATIVE_RERANK_PATH
    monkeypatch.setattr(config, "RERANK_URL", url)
    monkeypatch.setattr(config, "RERANK_MODEL", "qwen3.7-text-rerank")
    monkeypatch.setattr(config, "RERANK_API_KEY", "fixture-custom-key")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "must-not-leak")
    def post(target, **kwargs):
        assert target == url
        assert kwargs["headers"]["Authorization"] == "Bearer fixture-custom-key"
        assert "input" in kwargs["json"]
        return httpx.Response(200, json={"output": {"results": [{"index": 0, "relevance_score": .8}]}},
                              request=httpx.Request("POST", target))
    monkeypatch.setattr(httpx, "post", post)
    assert relevance.rerank("query", [{"title": "A"}], 1)[0]["_reranker_provider"] == "custom"


def test_explicit_endpoint_and_legacy_default_are_preserved(monkeypatch):
    assert relevance._dashscope_rerank_url("qwen3-rerank") == "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
    url = "https://gateway.example/private/reranks"
    monkeypatch.setattr(config, "DASHSCOPE_HTTP_BASE_URL", url)
    assert relevance._dashscope_rerank_url("model") == url


def test_endpoint_failure_is_sanitized_and_uses_existing_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "private-cloud-key")
    monkeypatch.setattr(config, "DASHSCOPE_HTTP_BASE_URL", "https://private-workspace.example/api/v1")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        404, text="private-cloud-key private-workspace.example", request=httpx.Request("POST", url)))
    monkeypatch.setattr(relevance, "_rerank_flashrank", lambda q, p, n: p[:n])
    assert len(relevance.rerank("query", [{"title": "A"}], 1)) == 1
    reason = relevance.reranker_status()["fallback_reason"]
    assert "base URL" in reason
    assert "private" not in reason


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
    def broken_ranker(max_length):
        raise OSError("cannot load model")

    monkeypatch.setitem(sys.modules, "flashrank", SimpleNamespace(Ranker=broken_ranker, RerankRequest=object))
    monkeypatch.setattr(relevance, '_new_cpu_ranker', broken_ranker)
    monkeypatch.setattr(relevance, "_flashrank_scores", ORIGINAL_FLASHRANK_SCORES)
    monkeypatch.setattr(relevance, "_flashrank_rankers", {})
    paper = {"title": "A", "_reranker_model": "cloud", "_rerank_rank": 1}
    result = relevance._rerank_flashrank("q", [paper], 1)
    assert result == [paper]
    assert paper["_reranker_model"] is None
    assert paper["_reranker_provider"] == "unavailable"
    assert "_rerank_rank" not in paper
    assert "OSError" in relevance.reranker_status()["fallback_reason"]


def test_expansion_uses_final_scores_without_cross_model_blending(monkeypatch):
    monkeypatch.setattr(sources, "search_sources", lambda: [
        sources.Source("openalex", search=lambda q, limit, **kw: [{"title": "Seed"}]),
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


def test_unreachable_reranker_pauses_instead_of_waiting_every_pass(monkeypatch):
    import httpx

    from scholar_mcp import relevance

    monkeypatch.setattr(relevance, "_remote_paused", {})
    calls = []

    def hang(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise httpx.ConnectTimeout("upstream unreachable")

    monkeypatch.setattr(httpx, "post", hang)
    papers = [{"title": "A"}, {"title": "B"}]
    options = dict(url="https://rerank.example/v1/rerank", model="m", api_key="k", provider="custom", timeout=120)
    assert relevance._rerank_remote("q", papers, 2, "", **options) is None
    assert relevance._rerank_remote("q", papers, 2, "", **options) is None
    assert len(calls) == 1
    assert calls[0].connect == relevance.REMOTE_CONNECT_TIMEOUT_S
    assert "retrying the endpoint" in relevance.reranker_status()["fallback_reason"]


def test_account_arrearage_on_http_400_pauses_the_endpoint(monkeypatch):
    monkeypatch.setattr(relevance, '_remote_paused', {})
    calls = []
    def denied(url, **kw):
        calls.append(True)
        return httpx.Response(400, json={'code': 'Arrearage'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', denied)
    options = dict(url='https://rank.test/rerank', model='m', api_key='k', provider='dashscope', timeout=10)
    for _ in range(3):
        assert relevance._rerank_remote('q', [{'title': 'A'}], 1, '', **options) is None
    assert len(calls) == 1
    relevance._remote_paused.clear()  # the next harness starts a fresh server
    assert relevance._rerank_remote('q', [{'title': 'A'}], 1, '', **options) is None
    assert len(calls) == 1
    assert relevance._rerank_remote('q', [{'title': 'A'}], 1, '', **{**options, 'api_key': 'replacement'}) is None
    assert len(calls) == 2


def test_local_passes_reuse_exact_inputs_and_preserve_scores(monkeypatch):
    seen = []
    def score(query, papers, max_length):
        seen.append((query, [p['title'] for p in papers], max_length))
        return [{'index': i, 'relevance_score': len(p['title']) / 100} for i, p in enumerate(papers)]
    monkeypatch.setattr(relevance, '_flashrank_scores', score)
    papers = [{'title': 'A'}, {'title': 'Longer'}]
    first = relevance._cached_local_scores('q', papers, 128)
    second = relevance._cached_local_scores('q', [papers[1], papers[0], {'title': 'New'}], 128)
    assert [r['relevance_score'] for r in second] == [first[1]['relevance_score'], first[0]['relevance_score'], .03]
    assert seen[1][1] == ['New']
    relevance._cached_local_scores('q', papers, 384)
    relevance._cached_local_scores('another question', papers, 128)
    relevance._cached_local_scores('q', [{'title': 'A', 'abstract': 'changed'}], 128)
    assert len(seen) == 5


def test_a_source_late_for_the_seeds_still_joins_the_final_ranking(monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(config, "SOURCE_BUDGET_S", 0.2)
    monkeypatch.setattr(sources, "search_sources", lambda: [
        sources.Source("fast", search=lambda q, limit, **kw: [{"title": f"Fast {i}"} for i in range(12)]),
        sources.Source("late", search=lambda q, limit, **kw: release.wait(5) and [{"title": "Late but relevant"}]),
    ])
    monkeypatch.setattr(relevance, "rerank", lambda query, papers, top_n, intent: papers)
    monkeypatch.setattr(relevance, "rank_final", lambda papers: papers)

    def expand(seeds, **kw):
        release.set()
        import time
        time.sleep(0.2)
        return {}
    monkeypatch.setattr(server.expansion, "expand", expand)

    papers, reports = server._pipeline("search", "q", 50, rerank_query="q")
    assert "Late but relevant" in {p["title"] for p in papers}
    assert {r["source"]: r["status"] for r in reports} == {"fast": "ok", "late": "ok"}


def test_a_late_source_joins_even_when_the_pool_is_too_small_to_expand(monkeypatch):
    import threading
    import time
    release = threading.Event()
    monkeypatch.setattr(config, "SOURCE_BUDGET_S", 0.2)
    monkeypatch.setattr(sources, "search_sources", lambda: [
        sources.Source("fast", search=lambda q, limit, **kw: [{"title": "Fast"}]),
        sources.Source("late", search=lambda q, limit, **kw: release.wait(5) and [{"title": "Late but relevant"}]),
    ])

    def rerank(query, papers, top_n, intent):
        release.set()  # the late source answers while the seed pool is ranked
        time.sleep(0.2)
        return papers
    monkeypatch.setattr(relevance, "rerank", rerank)
    monkeypatch.setattr(relevance, "rank_final", lambda papers: papers)
    monkeypatch.setattr(server.expansion, "expand", lambda *a, **kw: pytest.fail("pool below expand_min_pool"))

    papers, reports = server._pipeline("search", "q", 50, rerank_query="q")
    assert {p["title"] for p in papers} == {"Fast", "Late but relevant"}
    assert {r["source"]: r["status"] for r in reports} == {"fast": "ok", "late": "ok"}


def test_local_ranking_reads_the_leaders_again_with_whole_abstracts(monkeypatch):
    """The 128-token pass picks the leaders; a 384-token pass orders them, and
    nobody below a leader can overtake it."""
    lengths = []

    class Ranker:
        def __init__(self, max_length, **kwargs):
            self.max_length = max_length
            lengths.append(max_length)

        def rerank(self, request):
            # The short pass prefers early ids; the long pass reverses the leaders.
            n = len(request.passages)
            return [{"id": p["id"], "score": (0.9 - p["id"] / 1000) if self.max_length == 128
                     else (0.5 + p["id"] / 1000)} for p in request.passages][:n]

    monkeypatch.setitem(sys.modules, "flashrank", SimpleNamespace(
        Ranker=Ranker, RerankRequest=lambda query, passages: SimpleNamespace(query=query, passages=passages)))
    monkeypatch.setattr(relevance, '_new_cpu_ranker', lambda max_length: Ranker(max_length=max_length))
    monkeypatch.setattr(relevance, "_flashrank_rankers", {})
    monkeypatch.setattr(relevance, "_flashrank_scores", ORIGINAL_FLASHRANK_SCORES)
    monkeypatch.setattr(relevance, "FLASHRANK_CLOSE_TOP", 3)
    papers = [{"title": f"P{i}", "abstract": "x"} for i in range(6)]
    ranked = relevance._local_batches("q", papers, top_n=6)
    assert [p["title"] for p in ranked] == ["P2", "P1", "P0", "P3", "P4", "P5"]
    assert sorted(set(lengths)) == [128, relevance.FLASHRANK_CLOSE_TOKENS]
    assert all(p["_rerank_score"] <= ranked[2]["_rerank_score"] for p in ranked[3:])
    assert [p["_rerank_rank"] for p in ranked] == [1, 2, 3, 4, 5, 6]


def test_failed_batch_keeps_its_reason_after_another_batch_succeeds(monkeypatch):
    import threading
    failed = threading.Event()
    monkeypatch.setattr(config, "RERANK_URL", "http://localhost/rerank")
    monkeypatch.setattr(config, "RERANK_BATCH_SIZE", 1)

    def remote(query, papers, top_n, intent):
        if papers[0]["title"] == "A":
            relevance._note_reranker(provider=None, fallback_reason="request failed")
            failed.set()
            return None
        assert failed.wait(5)
        relevance._note_reranker(provider="custom", model="remote", fallback_reason=None)
        return papers

    def local(query, papers, top_n):
        relevance._note_reranker(provider="flashrank", model="local")
        return papers

    monkeypatch.setattr(relevance, "_remote_batch", remote)
    monkeypatch.setattr(relevance, "_local_batches", local)
    ranked = relevance.rerank("q", [{"title": "A"}, {"title": "B"}], 2)
    status = relevance.reranker_status(ranked)
    assert status["provider"] == "flashrank" and status["fallback_reason"] == "request failed"


def test_provenance_reports_the_whole_pass_and_empty_input_preserves_it(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(relevance.time, "monotonic", lambda: clock[0])

    def rank(query, papers, top_n, intent):
        relevance._note_reranker(provider="custom", model="test-model", latency_ms=15)
        clock[0] += 2.5
        return papers

    monkeypatch.setattr(relevance, "_rank_candidates", rank)
    papers = relevance.rerank("q", [{"title": "A"}], 1)
    assert relevance.reranker_status(papers)["latency_ms"] == 2500
    assert relevance.reranker_status()["latency_ms"] == 2500
    assert relevance.rerank("q", []) == []
    assert relevance.reranker_status()["provider"] == "custom"


def test_on_a_gpu_gte_reads_the_top_300_and_brings_its_own_weights(monkeypatch):
    monkeypatch.setattr(relevance, "_gpu_device", "cuda")
    monkeypatch.setattr(relevance, "_close_error", None)
    monkeypatch.setattr(relevance, "_local_score_cache", {})
    read = []

    def gpu(query, papers, tokens, checkpoint=""):
        read.append((checkpoint or relevance.GPU_MODEL, tokens, len(papers)))
        return [{"index": i, "relevance_score": .9 - i / 1000} for i in range(len(papers))]
    monkeypatch.setattr(relevance, "_gpu_scores", gpu)
    monkeypatch.setattr(relevance, "_flashrank_scores", lambda query, papers, tokens: gpu(query, papers, tokens))
    ranked = relevance._local_batches("q", [{"title": f"P{i}", "abstract": "x"} for i in range(400)], top_n=400)
    assert (relevance.CLOSE_MODEL, relevance.FLASHRANK_CLOSE_TOKENS, 300) in read
    assert {p["_reranker_model"] for p in ranked[:300]} == {relevance.CLOSE_MODEL}
    assert relevance.reranker_status()['model'] == relevance.CLOSE_MODEL
    assert relevance._load_rank_params(relevance.CLOSE_MODEL)["alpha"] == .02
    assert relevance._load_rank_params()["alpha"] == relevance.DEFAULT_RANK_PARAMS["alpha"]


@pytest.mark.parametrize('device', ['mps', ''])
def test_interactive_devices_keep_the_light_close_reader(monkeypatch, device):
    monkeypatch.setattr(relevance, '_gpu_device', device)
    monkeypatch.setattr(relevance, '_close_error', None)
    assert relevance._reader() == (relevance.FLASHRANK_MODEL, relevance.FLASHRANK_CLOSE_TOP)


def test_a_gpu_that_cannot_load_gte_reads_with_minilm_from_then_on(monkeypatch):
    monkeypatch.setattr(relevance, "_gpu_device", "cuda")
    monkeypatch.setattr(relevance, "_close_error", None)
    monkeypatch.setattr(relevance, "_local_score_cache", {})
    calls = []

    def gpu(query, papers, tokens, checkpoint=""):
        calls.append(checkpoint)
        if checkpoint == relevance.CLOSE_MODEL:
            raise OSError("ModernBERT needs a newer transformers")
        return [{"index": i, "relevance_score": .5} for i in range(len(papers))]
    monkeypatch.setattr(relevance, "_gpu_scores", gpu)
    monkeypatch.setattr(relevance, "_flashrank_scores", lambda query, papers, tokens: gpu(query, papers, tokens))
    papers = [{"title": f"P{i}", "abstract": "x"} for i in range(5)]
    first = relevance._read_leaders_closely("q", [dict(p, _rerank_score=.5) for p in papers])
    assert relevance._close_error.startswith("OSError") and relevance._reader()[0] == relevance.FLASHRANK_MODEL
    assert {p["_reranker_model"] for p in first} == {relevance.FLASHRANK_MODEL}
    relevance._read_leaders_closely("again", [dict(p, _rerank_score=.5) for p in papers])
    assert calls.count(relevance.CLOSE_MODEL) == 1


def test_the_close_reader_is_what_the_result_reports(monkeypatch):
    monkeypatch.setattr(relevance, "_gpu_device", "cuda")
    monkeypatch.setattr(relevance, "_close_error", None)
    monkeypatch.setattr(relevance, "_local_score_cache", {})
    monkeypatch.setattr(relevance, "_reranker_state", dict(relevance._reranker_state))
    def score(query, papers, tokens, checkpoint=""):
        return [{"index": i, "relevance_score": .5} for i in range(len(papers))]
    monkeypatch.setattr(relevance, "_gpu_scores", score)
    monkeypatch.setattr(relevance, "_flashrank_scores", score)
    relevance._local_batches("q", [{"title": f"P{i}"} for i in range(4)], top_n=4)
    assert relevance._reranker_state["model"] == relevance.CLOSE_MODEL


def test_a_search_that_chose_gte_before_its_failure_does_not_load_it_again(monkeypatch):
    monkeypatch.setattr(relevance, "_gpu_device", "cuda")
    monkeypatch.setattr(relevance, "_close_error", None)
    monkeypatch.setattr(relevance, "_local_score_cache", {})
    monkeypatch.setattr(relevance, "_gpu_models", {("cuda", relevance.CLOSE_MODEL): "loaded", ("cuda", "minilm"): "kept"})
    loads = []

    def gpu(query, papers, tokens, checkpoint=""):
        loads.append(checkpoint)
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(relevance, "_gpu_scores", gpu)
    for query in ("first", "second"):  # both chose GTE before either failed
        with pytest.raises(RuntimeError):
            relevance._cached_local_scores(query, [{"title": "A"}], 384, relevance.CLOSE_MODEL)
    assert loads == [relevance.CLOSE_MODEL]
    assert relevance._gpu_models == {("cuda", "minilm"): "kept"}
    assert relevance._close_error.startswith("RuntimeError: CUDA out of memory")


def test_minilm_scores_are_cooled_so_a_named_new_paper_beats_cited_neighbours(monkeypatch):
    import math
    from datetime import datetime
    monkeypatch.setattr(relevance, "_rank_params", {})
    sigmoid = lambda z: 1 / (1 + math.exp(-z))  # noqa: E731

    def pool(model):
        return [{"title": "Named new paper", "_rerank_score": sigmoid(9), "citation_count": 0,
                 "year": datetime.now().year, "_reranker_model": model},
                {"title": "Cited neighbour", "_rerank_score": sigmoid(2), "citation_count": 3000,
                 "year": 2015, "_reranker_model": model}]
    assert relevance.rank_final(pool(relevance.FLASHRANK_MODEL))[0]["title"] == "Named new paper"
    assert relevance.rank_final(pool("some-cloud-model"))[0]["title"] == "Cited neighbour"
    assert relevance._load_rank_params(relevance.FLASHRANK_MODEL)["temperature"] == 8.0
    assert relevance._load_rank_params(relevance.CLOSE_MODEL)["temperature"] == 1.0
