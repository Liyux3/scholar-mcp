"""Query preprocessing, semantic reranking and result ordering."""

import hashlib
import math
import os
import re
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import config
from . import records as record_ops
from .cache import DEFAULT_TTL, _disk, cached
from .model_assets import CPU_MODEL, cached_cpu_model_directory, cpu_model_directory

STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "that", "which", "who", "whom", "this", "these", "those", "it", "its",
    "not", "no", "nor", "as", "if", "then", "than", "too", "very", "so",
    "just", "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "over", "out", "up", "down",
    "how", "what", "when", "where", "why", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "also", "using", "based", "via", "etc",
    "us", "we", "our", "me", "my", "you", "your", "they", "their", "them",
    "let", "like", "new", "make", "get", "use", "way",
    "show", "know", "take", "come", "see", "look", "find", "give",
    "tell", "think", "say", "try", "ask", "seem", "help", "keep",
    "really", "actually", "still", "even", "much", "well",
})


# Words carrying little information on their own, stripped before KeyBERT so
# it does not spend its phrase slots on scaffolding.
#
# Comparatives are deliberately absent. They look like filler but often carry
# the entire claim: "smaller dataset ... better models than bigger datasets"
# compressed to "models bigger datasets pre training result", inverting the
# meaning and retrieving the largest dataset papers in existence.
WEAK_WORDS = frozenset({
    "wondering", "simply", "question", "whether", "think", "believe",
    "approach", "problem", "paper", "work", "method", "proposed", "propose",
    "study", "studies", "explore", "any", "research", "results", "recent", "existing", "current",
    "different", "various", "several", "multiple", "many", "possible",
    "important", "significant", "main", "key", "novel", "particular",
    "general", "specific", "common", "typical", "standard", "basic",
    "first", "second", "third", "one", "two", "three", "four", "five",
    "requires", "require", "required", "need", "needs", "needed",
    "able", "unable", "enable", "consider", "considered", "considering",
    "always", "never", "often", "sometimes", "usually", "increasingly",
    "however", "therefore", "although", "despite", "beyond", "within",
    "across", "along", "among", "towards", "toward", "without",
    "achieve", "achieves", "address", "addresses", "aim", "aims",
    "high", "highest", "low", "lowest", "large", "small",
    "fully", "enough", "already", "particularly", "especially", "primarily",
    "point", "making", "fundamentally", "inherent", "inherently",
    "ceiling", "incremental", "ever", "baked",
    "there", "papers", "uses", "used", "using", "based", "like",
    "find", "found", "show", "shown", "given", "take", "know",
    "called", "related", "available", "also", "well", "still",
    "recommend", "suggest", "list", "refer", "direct",
})


def extract_keywords(query: str, max_keywords: int = 8) -> list[str]:
    """Extract informative keywords from a query. Helps S2/arXiv search.
    For long queries, prioritizes technical terms over generic language.
    """
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query.lower())
    all_kw = [w for w in words if w not in STOPWORDS and len(w) > 1]
    if len(all_kw) <= max_keywords:
        return all_kw

    strong = [w for w in all_kw if w not in WEAK_WORDS and len(w) > 3]
    freq: dict[str, int] = {}
    for w in strong:
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(set(strong), key=lambda w: (-freq[w], strong.index(w)))
    if len(ranked) >= max_keywords:
        return ranked[:max_keywords]
    remaining = [w for w in all_kw if w not in set(ranked)]
    return (ranked + remaining)[:max_keywords]


def extract_lexical_anchors(query: str, max_anchors: int = 3) -> list[str]:
    """Keep exact names that semantic compression is most likely to erase.

    Dataset names, shared-task names, acronyms, camel-case identifiers, and
    short proper-noun phrases are often the only bridge from an indirect user
    description to an older paper title. They are cheap lexical signals, so
    preserve a few without turning the whole natural-language question into a
    brittle exact query.
    """
    candidates = []
    candidates.extend(re.findall(r'["“]([^"”]{3,60})["”]', query))
    candidates.extend(re.findall(
        r"\b(?:[A-Z]{2,}[A-Za-z0-9-]*|[A-Za-z]*[a-z][A-Z][A-Za-z0-9-]*|"
        r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+)\b",
        query,
    ))
    candidates.extend(re.findall(
        r"\b[A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+){1,3}\b",
        query,
    ))

    anchors = []
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.;:()[]")
        normalized = candidate.casefold()
        words = re.findall(r"[A-Za-z0-9][\w-]*", normalized)
        if not words or all(word in STOPWORDS | WEAK_WORDS for word in words):
            continue
        if normalized not in seen:
            seen.add(normalized)
            anchors.append(candidate)
        if len(anchors) >= max_anchors:
            break
    return anchors


def keyword_query(query: str, max_words: int) -> str:
    """Build a bounded keyword query with exact lexical anchors first."""
    words = []
    seen = set()
    values = extract_lexical_anchors(query)
    values.extend(
        word for word in extract_keywords(query, max_keywords=max_words * 2)
        if word not in STOPWORDS and word not in WEAK_WORDS
    )
    for value in values:
        for word in re.findall(r"[A-Za-z0-9][\w-]*", value):
            normalized = word.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            words.append(word)
            if len(words) >= max_words:
                return " ".join(words)
    return " ".join(words)


_keybert_model = None
_keybert_lock = threading.Lock()
_keybert_retry_at = 0.0

# Queries at or below this length already read like keyword queries; compressing
# them further only discards signal.
COMPRESS_THRESHOLD_WORDS = 12

# Two-stage compression parameters, tuned by the 2026-05-11 sensitivity sweep.
EK_PRE_MAX = 15      # keyword budget for the noise-stripping stage
KB_TOP_N = 2         # keyphrases KeyBERT selects from the cleaned text
KB_DIVERSITY = 0.5   # MMR diversity; higher values drift off-topic
KB_NGRAM_RANGE = (1, 3)

# Used only when KeyBERT is unavailable. Wider than the two-stage output
# because plain keyword extraction has no salience ranking to lean on.
FALLBACK_MAX_KEYWORDS = 10


def _word_count(text: str) -> int:
    return len(re.findall(r"[a-zA-Z0-9][\w\-]*", text))


@cached(ttl=3600)
def optimize_query(query: str) -> str:
    """Compress a long natural-language query into a keyword query.

    Keyword-based APIs (OpenAlex, arXiv, Crossref) lose recall sharply as the
    query grows: an unshortened 25-word query scores near zero on all of them.
    Semantic sources bypass this via the raw-query route in sources._pick_query.
    """
    if _word_count(query) <= COMPRESS_THRESHOLD_WORDS:
        return query

    compressed = _keybert_extract(query)
    if compressed:
        anchor_words = " ".join(extract_lexical_anchors(query)).split()
        present = set(re.findall(r"[A-Za-z0-9][\w-]*", compressed.casefold()))
        missing = [word for word in anchor_words if word.casefold() not in present]
        return " ".join([compressed, *missing]).strip()

    return keyword_query(query, max_words=FALLBACK_MAX_KEYWORDS)


def optimize_query_short(query: str, max_words: int = 8) -> str:
    """Ultra-short keyword query for APIs with tight length limits (e.g. S2 ~10 words)."""
    if _word_count(query) <= max_words:
        return query
    return keyword_query(query, max_words=max_words)


def _load_keybert():
    """Load the optional keyword model once, using installed assets first.

    Missing dependencies or unavailable assets use plain keyword extraction,
    with another load attempt after the ordinary cache interval.
    """
    global _keybert_model, _keybert_retry_at
    if _keybert_model is None and time.monotonic() >= _keybert_retry_at:
        with _keybert_lock:
            if _keybert_model is None and time.monotonic() >= _keybert_retry_at:
                try:
                    from keybert import KeyBERT
                    from model2vec import StaticModel
                    _keybert_model = KeyBERT(StaticModel.from_pretrained(
                        'minishlab/potion-base-8M', force_download=False))
                except Exception:
                    _keybert_retry_at = time.monotonic() + DEFAULT_TTL
    return _keybert_model


def _keybert_extract(query: str, top_n: int = KB_TOP_N) -> str | None:
    """Strip noise words, then rank the survivors by embedding salience.

    Stripping first matters: it keeps KeyBERT from spending its few phrase
    slots on scaffolding like "are there any papers that".
    """
    model = _load_keybert()
    if model is None:
        return None

    cleaned = " ".join(extract_keywords(query, max_keywords=EK_PRE_MAX))
    if not cleaned:
        return None

    keyphrases = model.extract_keywords(
        cleaned, keyphrase_ngram_range=KB_NGRAM_RANGE, stop_words="english",
        top_n=top_n, use_mmr=True, diversity=KB_DIVERSITY,
    )
    if not keyphrases:
        # Cleaned text is still a valid keyword query, just unranked.
        return cleaned

    compressed = " ".join(kw for kw, _ in keyphrases)
    return _restore_contrast(query, compressed)


# Comparatives that carry a claim rather than describe magnitude. KeyBERT
# ranks by embedding salience, and these score low on their own, so a query
# built around a contrast can lose the half that makes it a contrast.
CONTRAST_WORDS = frozenset({
    "smaller", "larger", "bigger", "fewer", "less", "lower", "shorter",
    "better", "worse", "faster", "slower", "cheaper", "weaker", "stronger",
})


def _restore_contrast(original: str, compressed: str) -> str:
    """Re-insert contrast words that compression dropped one side of.

    "smaller dataset ... better than bigger datasets" compressed to
    "models bigger datasets pre training result": KeyBERT kept "bigger" and
    dropped "smaller", inverting the claim, so the search returned the largest
    datasets in existence. Only restores a word when its counterpart survived,
    since that asymmetry is what signals a broken comparison.
    """
    present = set(re.findall(r"[a-zA-Z0-9][\w\-]*", compressed.lower()))
    missing = [
        word for word in re.findall(r"[a-zA-Z0-9][\w\-]*", original.lower())
        if word in CONTRAST_WORDS and word not in present
    ]
    if not missing or not (present & CONTRAST_WORDS):
        return compressed
    # Preserve first-appearance order, without duplicates.
    ordered = list(dict.fromkeys(missing))
    return " ".join([compressed] + ordered)


_flashrank_rankers: dict = {}  # one loaded model per token limit
_gpu_models: dict = {}  # (device, checkpoint): (tokenizer, model), or None for an uncached optional reader
_gpu_device: str | None = None  # "cuda", "mps", or "" once known to be absent or broken
_rank_params = None
_close_error: str | None = None  # why the GPU close reader failed, once it has


# Ranking weights for rank_final. A fitted rank_params.json overrides any
# subset of these; unspecified keys keep the default. The temperature rescales the
# model's logit before fusion (1 leaves its score as it is).
DEFAULT_RANK_PARAMS = {"gamma": 1.0, "alpha": 0.05, "beta": 0.02, "delta": 0.10, "temperature": 1.0}


def _load_rank_params(model: str | None = None) -> dict:
    """The ranking weights for scores from `model`: its own weights over the defaults,
    with a fitted rank_params.json over both."""
    global _rank_params
    if _rank_params is None:
        import json
        try:
            with open(config.RANK_PARAMS_PATH) as f:
                _rank_params = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _rank_params = {}
    return {**DEFAULT_RANK_PARAMS, **MODEL_RANK_PARAMS.get(model or "", {}), **_rank_params}


INTENT_INSTRUCTS = {
    "foundational": "Given a scientific literature search query, retrieve the most relevant research papers. Among equally relevant papers, prefer seminal and highly-cited works that established this field or method.",
    "recent": "Given a scientific literature search query, retrieve the most relevant research papers. Among equally relevant papers, prefer more recent publications with novel contributions.",
    "survey": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer survey, review, and tutorial papers that provide comprehensive overviews.",
    "method": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer papers that propose specific methods or techniques directly addressing the query.",
    "dataset": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer papers that introduce, document, or evaluate the requested dataset or benchmark, with exact task and resource identity over general popularity.",
    "": "Given a scientific literature search query, retrieve the most relevant research papers whose methods, findings, or contributions directly address the query topic.",
}


_dashscope_warning_shown = False
_reranker_state = {
    "provider": None,
    "model": None,
    "dashscope_configured": bool(config.DASHSCOPE_API_KEY),
    "fallback_reason": None,
    "latency_ms": None,
    "device": None,  # where the local model ran: "cuda", "mps" or "cpu"
}


_active_reranker: ContextVar[dict | None] = ContextVar("scholar_reranker", default=None)


def _note_reranker(**fields) -> None:
    """Keep a process summary and an isolated record for the ranking in progress."""
    active = _active_reranker.get()
    if active is not None:
        active.update(fields)
    else:
        _reranker_state.update(fields)


def reranker_status(papers: list[dict] | None = None) -> dict:
    """Return the result's own provenance, or the active/process status."""
    if papers:
        for paper in papers:
            if isinstance(paper.get("_reranker_meta"), dict):
                return dict(paper["_reranker_meta"])
    active = _active_reranker.get()
    return dict(active if active is not None else _reranker_state)


def _warn_dashscope_down(reason: str) -> None:
    """Print once per process when the primary reranker stops working.

    A silent fallback to FlashRank costs roughly 3x latency and measurably
    worse ranking, with no other symptom. Account arrearage in particular is
    permanent until someone acts, so it needs to be visible somewhere. stderr
    is safe: the MCP protocol uses stdout.
    """
    global _dashscope_warning_shown
    if not _dashscope_warning_shown:
        _dashscope_warning_shown = True
        print(f"scholar-mcp: Primary reranker unavailable ({reason}), "
              f"trying the local FlashRank fallback",
              file=sys.stderr)


def rerank_document(paper: dict) -> str:
    """Stable model input shared by hosted rerankers and offline evaluation."""
    parts = [f"Title: {paper.get('title') or ''}"]
    venue = paper.get("venue") or ""
    date = paper.get("publication_date") or str(paper.get("year") or "")
    if venue or date:
        parts.append(f"Venue: {venue}, Published: {date}".strip(", "))
    if paper.get("abstract"):
        parts.append(f"Abstract: {paper['abstract']}")
    return "\n".join(parts)[:2000]


def _apply_rerank_results(items: list[dict], papers: list[dict], top_n: int,
                          provider: str, model: str) -> list[dict]:
    """Validate a complete response before mutating any candidate scores.

    The fusion policy expects finite 0-1 relevance scores, not raw logits.
    Normalization belongs to the serving model, where its score semantics
    are known. A partial or malformed response must not erase candidates.
    """
    expected = min(top_n, len(papers))
    if not isinstance(items, list) or len(items) < expected:
        raise ValueError("Incomplete reranker response")
    checked = []
    seen = set()
    for item in items:
        index = item["index"]
        raw = item["relevance_score"]
        if type(index) is not int or not 0 <= index < len(papers) or index in seen:
            raise ValueError("Invalid or repeated reranker index")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("Invalid reranker score")
        score = float(raw)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Reranker must return finite scores in [0, 1]")
        seen.add(index)
        checked.append((index, score))
    checked.sort(key=lambda item: -item[1])
    result = []
    for rank, (index, score) in enumerate(checked[:expected], 1):
        paper = papers[index]
        paper.update(_rerank_score=score, _rerank_rank=rank,
                     _reranker_provider=provider, _reranker_model=model)
        result.append(paper)
    return result


_DASHSCOPE_NATIVE_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


def _dashscope_rerank_url(model: str) -> str:
    """Resolve a console/SDK base URL while retaining an explicit endpoint."""
    base = config.DASHSCOPE_HTTP_BASE_URL.rstrip("/")
    if base.endswith(("/reranks", _DASHSCOPE_NATIVE_RERANK_PATH)):
        return base
    for suffix in ("/api/v1", "/compatible-api/v1", "/compatible-mode/v1"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    path = "/compatible-api/v1/reranks" if model == "qwen3-rerank" else _DASHSCOPE_NATIVE_RERANK_PATH
    return base + path


# An unreachable endpoint otherwise costs its full timeout on every rerank
# pass of every search (two or more per search). After a transport failure,
# rank locally for a while and probe the endpoint again afterwards.
REMOTE_PAUSE_S = 300
REMOTE_CONNECT_TIMEOUT_S = 10
_remote_paused: dict[str, tuple[float, str]] = {}


def _rerank_remote(query: str, papers: list[dict], top_n: int, intent: str,
                   *, url: str, model: str, api_key: str | None,
                   provider: str, timeout: float) -> list[dict] | None:
    """Shared transport and scoring, with two small wire-format adapters."""
    started = time.monotonic()
    # Each harness has its own server. An account rejection remains the same
    # after a restart, while another endpoint or credential is a distinct route.
    route = "reranker.pause:" + hashlib.sha256((url + "\0" + model + "\0" + (api_key or "")).encode()).hexdigest()
    if route not in _remote_paused and (saved := _disk(route)) is not None:
        _remote_paused[route] = (started + max(0, saved[0] - time.time()), saved[1])
    paused_until, paused_reason = _remote_paused.get(route, (0.0, ""))
    if started < paused_until:
        _note_reranker(provider=None, model=None, dashscope_configured=bool(config.DASHSCOPE_API_KEY),
                               fallback_reason=f"{paused_reason}; retrying the endpoint in "
                                               f"{int(paused_until - started)}s", latency_ms=0)
        return None
    try:
        import httpx
        native = urlsplit(url).path.rstrip("/").endswith(_DASHSCOPE_NATIVE_RERANK_PATH)
        body = {
            "query": query[:500],
            "documents": [rerank_document(paper) for paper in papers],
            "top_n": min(top_n, len(papers)),
        }
        if model:
            body["model"] = model
        if (provider == "dashscope" or native) and model != "gte-rerank-v2":
            body["instruct"] = INTENT_INSTRUCTS.get(intent, INTENT_INSTRUCTS[""])
        elif intent:
            # Standard rerank APIs have no shared `instruct` field.
            body["query"] = INTENT_INSTRUCTS.get(intent, "") + "\n" + query[:500]
        if native:
            parameters = {"top_n": body["top_n"]}
            if "instruct" in body:
                parameters["instruct"] = body["instruct"]
            body = {"model": model,
                    "input": {"query": body["query"], "documents": body["documents"]},
                    "parameters": parameters}
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = httpx.post(
            url, headers=headers, json=body,
            timeout=httpx.Timeout(timeout, connect=min(timeout, REMOTE_CONNECT_TIMEOUT_S)),
            trust_env=urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"},
        )
        # A provider can enforce a smaller token/payload budget than its
        # advertised document count. Split only size failures, on the same
        # endpoint/model, rather than silently degrading the whole pass.
        size_error = resp.status_code == 413 or (
            resp.status_code == 400 and any(marker in resp.text.casefold() for marker in (
                "too many", "too large", "too long", "maximum", "max_tokens", "token limit", "length limit",
            ))
        )
        if size_error and len(papers) > 1:
            middle = len(papers) // 2
            ranked = []
            for half in (papers[:middle], papers[middle:]):
                result = _rerank_remote(query, half, len(half), intent, url=url, model=model,
                                        api_key=api_key, provider=provider, timeout=timeout)
                if result is None:
                    return None
                ranked.extend(result)
            ranked.sort(key=lambda paper: -paper["_rerank_score"])
            return ranked[:top_n]
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output", {}) if native else data
        reranked = _apply_rerank_results(output.get("results"), papers, top_n, provider, model)
        _note_reranker(
            provider=provider,
            model=model or None,
            dashscope_configured=bool(config.DASHSCOPE_API_KEY),
            fallback_reason=None,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return reranked
    except Exception as e:
        reason = _dashscope_reason(e)
        import httpx
        status = getattr(getattr(e, "response", None), "status_code", None)
        if (isinstance(e, httpx.TransportError) or status in {401, 402, 403, 404}
                or reason.startswith("account in arrearage")):
            _remote_paused[route] = (time.monotonic() + REMOTE_PAUSE_S, reason)
            if status in {401, 402, 403, 404} or reason.startswith("account in arrearage"):
                _disk(route, (time.time() + REMOTE_PAUSE_S, reason))
        _note_reranker(
            provider=None,
            model=None,
            dashscope_configured=bool(config.DASHSCOPE_API_KEY),
            fallback_reason=reason,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        _warn_dashscope_down(reason)
        return None


def _rerank_dashscope(query: str, papers: list[dict], top_n: int, intent: str = "") -> list[dict] | None:
    """Existing default, using the same adapter as a self-hosted model."""
    if not config.DASHSCOPE_API_KEY:
        _note_reranker(provider=None, model=None, dashscope_configured=False,
                               fallback_reason="not configured", latency_ms=None)
        return None
    return _rerank_remote(
        query, papers, top_n, intent,
        url=_dashscope_rerank_url(config.RERANK_MODEL or "qwen3-rerank"),
        model=config.RERANK_MODEL or "qwen3-rerank", api_key=config.DASHSCOPE_API_KEY,
        provider="dashscope", timeout=config.RERANK_TIMEOUT,
    )


def _dashscope_reason(exc: Exception) -> str:
    """Name the failure, separating states that need action from transient ones."""
    body = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    if "Arrearage" in body or "overdue" in body.lower():
        return "account in arrearage, top up at model-studio"
    if "InvalidApiKey" in body or "401" in body:
        return "invalid API key or endpoint region/workspace mismatch"
    if getattr(getattr(exc, "response", None), "status_code", None) == 404:
        return "reranker endpoint or model not found; check base URL and model"
    return f"{type(exc).__name__}"


def _gpu() -> str:
    """The device torch can rank on ("cuda" or "mps"), or "" without torch or a GPU."""
    global _gpu_device
    if _gpu_device is None:
        try:
            import torch
            _gpu_device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else ""
        except ImportError:
            _gpu_device = ""
    return _gpu_device


def _load_gpu_model(device: str, checkpoint: str):
    """Prepare a checkpoint once, preferring ready local assets."""
    if (device, checkpoint) in _gpu_models and _gpu_models[device, checkpoint] is None:
        return None
    if (device, checkpoint) not in _gpu_models:
        from huggingface_hub import try_to_load_from_cache

        cache = os.path.join(config.DATA_DIR, "models")
        cached_config = try_to_load_from_cache(checkpoint, 'config.json', cache_dir=cache)
        snapshot = str(Path(cached_config).parent) if isinstance(cached_config, str) else None
        if snapshot is None and checkpoint == GPU_MODEL and cached_cpu_model_directory() is not None:
            _gpu_models[device, checkpoint] = None
            return None
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        # Once installed, inference must also work on a disconnected machine.
        # A real local path also avoids Hub telemetry/metadata requests that can
        # occur for a repo ID even with local_files_only=True.
        for local_only in (True, False):
            if local_only and snapshot is None:
                continue
            location = snapshot if local_only else checkpoint
            try:
                tokenizer = AutoTokenizer.from_pretrained(location, cache_dir=cache, local_files_only=local_only)
                model = AutoModelForSequenceClassification.from_pretrained(
                    location, cache_dir=cache, dtype=torch.float16, local_files_only=local_only)
                break
            except OSError:
                if local_only and checkpoint == GPU_MODEL and cached_cpu_model_directory() is not None:
                    _gpu_models[device, checkpoint] = None
                    return None
                if not local_only:
                    raise
        _gpu_models[device, checkpoint] = (tokenizer, model.to(device).eval())
    return _gpu_models[device, checkpoint]


def _gpu_scores(query: str, papers: list[dict], max_length: int, checkpoint: str = "") -> list[dict] | None:
    """A half-precision cross-encoder with FlashRank-compatible sigmoid scores."""
    if not papers:
        return []
    import torch
    device, checkpoint = _gpu(), checkpoint or GPU_MODEL
    loaded = _load_gpu_model(device, checkpoint)
    if loaded is None:
        return None
    tokenizer, model = loaded
    texts = [((p.get("title") or "") + ". " + (p.get("abstract") or ""))[:2000] for p in papers]
    scores: list[float] = []
    with torch.inference_mode():
        for start in range(0, len(texts), 50):
            chunk = texts[start:start + 50]
            batch = tokenizer([query] * len(chunk), chunk, truncation=True, max_length=max_length,
                              padding=True, return_tensors="pt").to(device)
            scores += torch.sigmoid(model(**batch).logits.float().reshape(-1)).tolist()
    return [{"index": i, "relevance_score": score} for i, score in enumerate(scores)]


def _cpu_threads() -> int:
    """Keep ORT's native default unless affinity or a cgroup constrains the CPU."""
    total = os.cpu_count() or 1
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = total
    constrained = available < total
    if not sys.platform.startswith('linux'):
        return max(1, available) if constrained else 0
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            available = min(available, math.ceil(int(quota) / int(period)))
            constrained = True
    except (OSError, ValueError, ZeroDivisionError):
        for name in ('cpu', 'cpu,cpuacct'):
            try:
                base = Path('/sys/fs/cgroup') / name
                quota = int((base / 'cpu.cfs_quota_us').read_text())
                period = int((base / 'cpu.cfs_period_us').read_text())
                if quota > 0:
                    available = min(available, math.ceil(quota / period))
                    constrained = True
                break
            except (OSError, ValueError, ZeroDivisionError):
                continue
    return max(1, available) if constrained else 0


def _new_cpu_ranker(max_length: int):
    """Keep FlashRank's tokenizer/scoring, constructing one configured ORT session.

    FlashRank 0.2.10 has no session-options seam. Its pinned tokenizer helper is
    reused here; the stock constructor would create an unwanted thread pool.
    """
    import logging
    import onnxruntime as ort
    from flashrank import Ranker
    from flashrank.Config import model_file_map

    class CpuRanker(Ranker):
        def __init__(self):
            self.model_dir = cpu_model_directory()
            self.cache_dir = self.model_dir.parent
            self.logger = logging.getLogger('scholar_mcp.local_reader')
            self.llm_model = None
            self._thread_count = _cpu_threads()
            self.session = next((reader.session for reader in _flashrank_rankers.values()
                                 if getattr(reader, 'model_dir', None) == self.model_dir
                                 and getattr(reader, '_thread_count', None) == self._thread_count), None)
            if self.session is None:
                options = ort.SessionOptions()
                options.enable_cpu_mem_arena = False
                options.intra_op_num_threads = self._thread_count
                self.session = ort.InferenceSession(str(self.model_dir / model_file_map[FLASHRANK_MODEL]),
                                                    sess_options=options, providers=['CPUExecutionProvider'])
            self.tokenizer = self._get_tokenizer(max_length)
            _note_reranker(cpu_threads=self._thread_count or 'auto')
    return CpuRanker()


def _flashrank_scores(query: str, papers: list[dict], max_length: int) -> list[dict]:
    """MiniLM scores as reranker items: on the GPU when torch reaches one, about ten
    times faster on an Apple laptop, else ONNX on the CPU. Raises when neither runs."""
    global _gpu_device
    if _gpu():
        try:
            items = _gpu_scores(query, papers, max_length)
            if items is not None:
                _note_reranker(device=_gpu_device)
                return items
        except Exception as error:  # a broken GPU stack falls back for the rest of the process
            _note_reranker(gpu_error=f"{type(error).__name__}: {error}"[:200])
            _gpu_device = ""
    _note_reranker(device="cpu")
    from flashrank import RerankRequest
    ranker = _flashrank_rankers.get(max_length)
    if ranker is None:
        ranker = _new_cpu_ranker(max_length)
        _flashrank_rankers[max_length] = ranker
    passages = [{"id": i, "text": ((p.get("title") or "") + ". " + (p.get("abstract") or ""))[:2000]}
                for i, p in enumerate(papers)]
    items, batch = [], max(1, FLASHRANK_BATCH_TOKENS // max_length)
    for start in range(0, len(passages), batch):
        items += ranker.rerank(RerankRequest(query=query, passages=passages[start:start + batch]))
    return [{"index": item["id"], "relevance_score": float(item["score"])} for item in items]


def _rerank_flashrank(query: str, papers: list[dict], top_n: int) -> list[dict]:
    """Rerank via FlashRank MiniLM (ONNX). Fallback when DashScope unavailable."""
    started = time.monotonic()
    try:
        reranked = _apply_rerank_results(_cached_local_scores(query, papers, FLASHRANK_MAX_TOKENS),
                                         papers, top_n, "flashrank", FLASHRANK_MODEL)
    except Exception as error:
        for p in papers:
            p["_rerank_score"] = 0.5
            p["_reranker_provider"] = "unavailable"
            p["_reranker_model"] = None
            p.pop("_rerank_rank", None)
        _note_reranker(
            provider="unavailable",
            model=None,
            fallback_reason=f"local reranker unavailable ({type(error).__name__})",
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return papers[:top_n]
    _note_reranker(
        provider="flashrank",
        model=FLASHRANK_MODEL,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return reranked


def _retire_close_reader(reason: str) -> None:
    """Read with MiniLM for the rest of the process, and free GTE's GPU memory."""
    global _close_error
    if _close_error is None:
        _close_error = reason[:200]
        _note_reranker(close_error=_close_error)
    for key in [key for key in _gpu_models if key[1] == CLOSE_MODEL]:
        del _gpu_models[key]


def _reader() -> tuple[str, int]:
    """CUDA serves the larger reader; interactive MPS/CPU keeps the light reader."""
    if _gpu() == 'cuda' and not _close_error:
        return CLOSE_MODEL, CLOSE_TOP_GPU
    return FLASHRANK_MODEL, FLASHRANK_CLOSE_TOP


def _read_leaders_closely(query: str, ranked: list[dict]) -> list[dict]:
    """Rescore the leaders of a ranked pool with the whole abstract in view.

    The first pass reads 128 tokens, which with the question leaves the title
    and about the first 60 words of an abstract. The leaders are read again at
    FLASHRANK_CLOSE_TOKENS; everyone below keeps their place under the leaders.
    """
    model, top = _reader()
    leaders, rest = ranked[:top], ranked[top:]
    if not leaders:
        return ranked
    try:
        closer = _apply_rerank_results(_cached_local_scores(query, leaders, FLASHRANK_CLOSE_TOKENS, model),
                                       leaders, len(leaders), "flashrank", model)
    except Exception:
        closer = None
    if closer is None:
        if model == FLASHRANK_MODEL:  # the first pass stands
            return ranked
        # GTE is retired by now; MiniLM reads, outside the handler, so nothing
        # of the failed attempt stays alive during the retry.
        _retire_close_reader("close read failed")
        return _read_leaders_closely(query, ranked)
    _note_reranker(model=model)
    floor = min(p["_rerank_score"] for p in closer)
    for paper in rest:
        paper["_rerank_score"] = min(paper["_rerank_score"], floor)
    _note_reranker(model=model)
    return closer + rest


FLASHRANK_MODEL = CPU_MODEL
GPU_MODEL = "cross-encoder/ms-marco-MiniLM-L12-v2"  # the checkpoint FlashRank's ONNX file is exported from
# The close reader on a GPU: 149M parameters, Apache 2.0. After MiniLM's skim it
# lifted nDCG@10 on 73 frozen real pools from 0.248 to 0.330 with collections held
# out (MiniLM reading 300 itself reaches 0.27 at best); six of seven collections gained.
CLOSE_MODEL = "Alibaba-NLP/gte-reranker-modernbert-base"
CLOSE_TOP_GPU = 300
# The weights belong to the scores they weigh (rank_final); the first reader found
# in a pool decides, so GTE's weights apply where it read the leaders. Fitted on the
# frozen real pools (RERANKING.md, Readers compared): GTE reads well enough that
# citations need less weight and recency none. MiniLM keeps the default weights
# (refitting them overfits) but needs a temperature: its probabilities crowd near 1
# for every plausible candidate, so a named paper's lead over cited neighbours
# vanished and citations decided. Temperature 8, chosen on v1, raised v2 from 0.416
# to 0.563 (known-paper searches 0.614 to 0.937; topics unchanged on v2, 0.03 lower
# on v1's review-reference topics).
MODEL_RANK_PARAMS = {CLOSE_MODEL: {"alpha": 0.02, "beta": 0.02, "delta": 0.0},
                     FLASHRANK_MODEL: {"temperature": 8.0}}
DASHSCOPE_CAP = 500
DASHSCOPE_TOKEN_BUDGET = 110_000  # Leave room below the documented 120K request cap.
FLASHRANK_CAP = 150
FLASHRANK_MAX_TOKENS = 128
FLASHRANK_BATCH_TOKENS = 4096  # tokens per ONNX run on the CPU (32 passages at 128, 10 at 384); see _flashrank_scores
# With a typical question, 384 tokens hold the title and the whole abstract for
# 96% of LitSearch candidates (128 holds it for 2%). On 20 frozen pools of 100,
# reading whole abstracts lifted Hit@20 from 0.45 to 0.60; reading only the
# leaders that way costs about 2.4 s per pass instead of rereading the pool.
FLASHRANK_CLOSE_TOKENS = 384
FLASHRANK_CLOSE_TOP = 100

# Both passes read many of the same documents. Reuse only an identical model
# input at the same token length, before fusion or close-reading changes scores.
# One lock also avoids loading duplicate GPU models for concurrent searches.
_local_score_cache: dict[bytes, float] = {}
_local_score_lock = threading.Lock()
LOCAL_SCORE_CACHE_SIZE = 4096


def _cached_local_scores(query: str, papers: list[dict], max_length: int,
                         model: str = FLASHRANK_MODEL) -> list[dict]:
    keys = [hashlib.sha256((f"{model}:{max_length}:{query}\0" +
            ((p.get("title") or "") + ". " + (p.get("abstract") or ""))[:2000]).encode()).digest()
            for p in papers]
    with _local_score_lock:
        missing = {}
        for key, paper in zip(keys, papers):
            if key not in _local_score_cache:
                missing.setdefault(key, paper)
        if missing:
            # The reader is checked again under the lock: a search that chose GTE
            # before another's failure must not load it a second time.
            if model != FLASHRANK_MODEL and (_close_error or not _gpu()):
                raise RuntimeError(_close_error or "no GPU for the close reader")
            try:
                items = (_flashrank_scores(query, list(missing.values()), max_length) if model == FLASHRANK_MODEL
                         else _gpu_scores(query, list(missing.values()), max_length, model))
                # Validate before caching, so a failed batch never becomes a saved score.
                _apply_rerank_results(items, list(missing.values()), len(missing),
                                      "flashrank", model)
            except Exception as error:
                if model != FLASHRANK_MODEL:
                    _retire_close_reader(f"{type(error).__name__}: {error}")
                raise
            fresh = {key: paper["_rerank_score"] for key, paper in zip(missing, missing.values())}
            _local_score_cache.update(fresh)
        device = _gpu() or 'cpu'
        if model == FLASHRANK_MODEL and (device, GPU_MODEL) in _gpu_models and _gpu_models[device, GPU_MODEL] is None:
            device = 'cpu'
        _note_reranker(device=device)
        scores = [{"index": i, "relevance_score": _local_score_cache[key]} for i, key in enumerate(keys)]
        while len(_local_score_cache) > LOCAL_SCORE_CACHE_SIZE:
            del _local_score_cache[next(iter(_local_score_cache))]
        return scores

def rerank(query: str, papers: list[dict], top_n: int = 50, intent: str = "") -> list[dict]:
    """Rank candidates and bind provenance to the returned records."""
    global _reranker_state
    if not papers:
        return papers
    started = time.monotonic()
    token = _active_reranker.set({"provider": None, "model": None, "fallback_reason": None,
                                  "dashscope_configured": bool(config.DASHSCOPE_API_KEY)})
    try:
        ranked = _rank_candidates(query, papers, top_n, intent)
        state = reranker_status()
        state["latency_ms"] = round((time.monotonic() - started) * 1000)
        _reranker_state = dict(state)
        for paper in ranked:
            paper["_reranker_meta"] = state
        return ranked
    finally:
        _active_reranker.reset(token)


def _rank_candidates(query: str, papers: list[dict], top_n: int, intent: str) -> list[dict]:
    """Score every candidate in provider-sized batches, then select globally."""
    batch_size = config.RERANK_BATCH_SIZE if config.RERANK_URL else (
        100 if config.RERANK_MODEL == "qwen3-vl-rerank" else DASHSCOPE_CAP)
    token_budget = min(DASHSCOPE_TOKEN_BUDGET, 27_000) if config.RERANK_MODEL == "gte-rerank-v2" else DASHSCOPE_TOKEN_BUDGET
    batches = []
    batch, tokens = [], 0
    for paper in papers:
        # Avoid a tokenizer/model dependency just to schedule HTTP requests.
        # This conservative estimate handles multilingual text; a provider
        # size rejection still triggers exact splitting above, without cuts.
        text = query[:500] + rerank_document(paper)
        non_ascii = sum(ord(character) > 127 for character in text)
        estimated = (len(text) - non_ascii + 2) // 3 + non_ascii * 3 + 128
        if batch and (len(batch) >= batch_size or (
                not config.RERANK_URL and tokens + estimated > token_budget)):
            batches.append(batch)
            batch, tokens = [], 0
        batch.append(paper)
        tokens += estimated
    if batch:
        batches.append(batch)
    if len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor
        def score(batch):
            token = _active_reranker.set({})
            try:
                result = _remote_batch(query, batch, len(batch), intent)
                return result, reranker_status()
            finally:
                _active_reranker.reset(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(score, batches))
        failed = next((state for result, state in outcomes if result is None), None)
        if failed is not None:
            # Preserve the failed batch's reason even if another batch finished later.
            _note_reranker(**failed)
            return _local_batches(query, papers, top_n)
        _note_reranker(**outcomes[-1][1])
        ranked = [paper for result, _ in outcomes for paper in result]
        ranked.sort(key=lambda p: -(p.get("_rerank_score") or 0))
        for rank, paper in enumerate(ranked, 1):
            paper["_rerank_rank"] = rank
        return ranked[:top_n]
    result = _remote_batch(query, papers, top_n, intent)
    if result is not None:
        return result
    return _local_batches(query, papers, top_n)


def _remote_batch(query: str, papers: list[dict], top_n: int, intent: str) -> list[dict] | None:
    if config.RERANK_URL:
        return _rerank_remote(
            query, papers, top_n, intent, url=config.RERANK_URL,
            model=config.RERANK_MODEL, api_key=config.RERANK_API_KEY,
            provider="custom", timeout=config.RERANK_TIMEOUT,
        )
    return _rerank_dashscope(query, papers, top_n, intent=intent)


def _local_batches(query: str, papers: list[dict], top_n: int) -> list[dict]:
    ranked = []
    for start in range(0, len(papers), FLASHRANK_CAP):
        batch = papers[start:start + FLASHRANK_CAP]
        ranked.extend(_rerank_flashrank(query, batch, len(batch)))
    if any(p.get("_reranker_provider") == "unavailable" for p in ranked):
        for paper in ranked:
            paper.update(_rerank_score=0.5, _reranker_provider="unavailable", _reranker_model=None)
            paper.pop("_rerank_rank", None)
        return rank_final(ranked)[:top_n]
    ranked.sort(key=lambda p: -(p.get("_rerank_score") or 0))
    ranked = _read_leaders_closely(query, ranked)
    for rank, paper in enumerate(ranked, 1):
        paper["_rerank_rank"] = rank
    return ranked[:top_n]


def rank_final(papers: list[dict]) -> list[dict]:
    """Apply learnable composite ranking formula and sort.

    final = rerank_score^γ × (1 + α × log(citations+1)) × (1 + β × source_count/N) × (1 + δ × recency),
    with rerank_score = sigmoid(logit / temperature). The weights and temperature
    are those of the model that read the leaders (MODEL_RANK_PARAMS), under any
    fitted rank_params.json.
    """
    readers = {p.get("_reranker_model") for p in papers}
    params = _load_rank_params(next((m for m in MODEL_RANK_PARAMS if m in readers), None))
    gamma, alpha, beta, delta = (
        params["gamma"], params["alpha"], params["beta"], params["delta"]
    )
    temperature = params.get("temperature", 1.0)

    current_year = datetime.now().year
    # Count distinct APIs, not query channels: one source answering four
    # queries is one source, and treating it as four inflates agreement.
    n_sources = max(len({
        channel.split("::", 1)[0]
        for p in papers for channel in (p.get("_source_ranks") or {})
    }), 1)

    for p in papers:
        r = max(p.get("_rerank_score", 0.0), 1e-6)
        if temperature != 1.0:
            r = min(r, 1 - 1e-6)
            r = 1 / (1 + math.exp(-math.log(r / (1 - r)) / temperature))
        cites = p.get("citation_count", 0) or 0
        src_count = record_ops.physical_source_count(p)
        try:
            year = int(p.get("year") or current_year)
        except (TypeError, ValueError):
            year = current_year
        recency = max(0, 1.0 - (current_year - year) / 10.0)

        score = (r ** gamma) * (1 + alpha * math.log(cites + 1)) * (1 + beta * src_count / n_sources) * (1 + delta * recency)
        p["_final_score"] = score

    papers.sort(key=lambda p: -p["_final_score"])

    if papers:
        max_s = papers[0]["_final_score"]
        if max_s > 0:
            for p in papers:
                p["_final_score"] = p["_final_score"] / max_s

    return papers


DOMAIN_KEYWORDS = {
    "computer science": {
        "algorithm", "neural", "network", "transformer", "attention",
        "model", "learning", "deep", "machine", "compute", "software",
        "code", "programming", "compiler", "architecture", "inference",
        "training", "benchmark", "llm", "language model", "gpu", "cpu",
        "optimization", "gradient", "embedding", "token", "bert", "gpt",
        "diffusion", "reinforcement", "classification", "detection",
        "segmentation", "generation", "retrieval", "reasoning", "dataset",
    },
    "mathematics": {
        "theorem", "proof", "conjecture", "algebra", "topology",
        "geometry", "calculus", "equation", "polynomial", "matrix",
        "convergence", "optimization", "combinatorics", "probability",
    },
    "physics": {
        "quantum", "particle", "relativity", "thermodynamic",
        "electromagnetic", "photon", "entropy", "hamiltonian",
        "lagrangian", "field theory", "cosmology",
    },
    "biology": {
        "gene", "protein", "cell", "genome", "mutation", "enzyme",
        "organism", "species", "evolution", "dna", "rna", "molecular",
        "phylogenetic", "transcription", "metabolic",
    },
    "medicine": {
        "patient", "clinical", "treatment", "diagnosis", "therapy",
        "disease", "symptom", "drug", "pharmaceutical", "surgery",
        "pathology", "epidemiology", "biomarker",
    },
}


ARXIV_CATEGORY_MAP = {
    "cs.": "computer science", "stat.ml": "computer science",
    "stat.": "mathematics", "math.": "mathematics",
    "physics.": "physics", "hep-": "physics", "astro-": "physics",
    "cond-mat": "physics", "quant-ph": "physics", "gr-qc": "physics",
    "q-bio.": "biology", "q-fin.": "economics",
    "eess.": "engineering", "nlin.": "physics",
}


def _normalize_field(field: str) -> str:
    """Map arXiv category tags and partial names to canonical field names."""
    fl = field.lower().strip()
    for prefix, canonical in ARXIV_CATEGORY_MAP.items():
        if fl.startswith(prefix):
            return canonical
    return fl


def filter_by_fields(papers: list[dict],
                     fields: list[str] | None) -> list[dict]:
    """Filter papers to match requested fields of study.
    Handles arXiv category tags (cs.CL, math.AG, etc.) by mapping to canonical names.
    If a paper has no field info, check title+abstract for domain keywords.
    """
    if not fields:
        return papers

    field_lower = {f.lower() for f in fields}

    filtered = []
    for p in papers:
        raw_fields = p.get("fields_of_study") or []
        if raw_fields:
            normalized = {_normalize_field(f) for f in raw_fields}
            if normalized & field_lower or any(
                any(fl in nf for fl in field_lower) for nf in normalized
            ):
                filtered.append(p)
            continue

        text = (p.get("title", "") + " " + p.get("abstract", "")).lower()
        for fl in field_lower:
            keywords = DOMAIN_KEYWORDS.get(fl)
            if keywords and any(kw in text for kw in keywords):
                filtered.append(p)
                break

    return filtered
