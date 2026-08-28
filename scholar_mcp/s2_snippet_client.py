"""Semantic Scholar snippet search: hybrid retrieval over paper full text.

Every other source in the registry matches against titles and abstracts. This
one searches ~285M passages drawn from the body text of ~11.7M open-access
papers, and returns the matching passage itself rather than just metadata.

That makes it the only source that can answer "which paper explains X" when
X is discussed in a methods section but never appears in any title.

It behaves like a semantic source, not a keyword one. Measured on the same
question phrased two ways:

    raw question    top scores 0.82 / 0.77 / 0.72, all on-topic
    compressed kw   top scores 0.59 / 0.59 / 0.57, third result unrelated

so it is registered as QUERY_RAW.
"""

import httpx

from . import s2_client

SNIPPET_URL = "https://api.semanticscholar.org/graph/v1/snippet/search"

# The endpoint accepts up to 1000, far above the 100 most sources allow.
SNIPPET_MAX_LIMIT = 1000

# Passages are ~500 words. Keep enough for the reranker to judge relevance
# without flooding it, since these arrive alongside title/abstract candidates.
SNIPPET_TEXT_CHARS = 1200


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    """Search paper full text, returning one result per matching passage.

    Several passages can come from the same paper; deduplication happens
    downstream in relevance.deduplicate, which merges by DOI and title.
    """
    params = {"query": query, "limit": min(limit, SNIPPET_MAX_LIMIT)}
    data = s2_client._get(SNIPPET_URL, params=params)

    papers = []
    for item in data.get("data") or []:
        paper = _format(item)
        if paper:
            papers.append(paper)
    return papers[:limit]


def enrich_metadata(papers: list[dict]) -> None:
    """Fill snippet-only metadata with one S2 batch call when available."""
    targets = []
    paper_ids = []
    for paper in papers:
        corpus_id = (paper.get("external_ids") or {}).get("CorpusId")
        incomplete = (
            not paper.get("authors")
            or paper.get("year") in (None, "")
            or not paper.get("venue")
            or not paper.get("_citation_count_known")
        )
        if corpus_id and incomplete:
            targets.append(paper)
            paper_ids.append(f"CorpusId:{corpus_id}")
    if not targets:
        return
    try:
        details = s2_client.get_papers_batch(paper_ids)
    except (httpx.HTTPError, TimeoutError, s2_client.S2CooldownError):
        return
    fields = (
        "paper_id", "authors", "year", "venue", "citation_count",
        "_citation_count_known", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date", "tldr", "url",
    )
    for paper, raw in zip(targets, details):
        if not raw:
            continue
        enriched = s2_client.format_paper(raw)
        for field in fields:
            if enriched.get(field) not in (None, "", []):
                paper[field] = enriched[field]
        paper["external_ids"] = {
            **(paper.get("external_ids") or {}),
            **(enriched.get("external_ids") or {}),
        }


def _format(item: dict) -> dict | None:
    paper = item.get("paper") or {}
    snippet = item.get("snippet") or {}
    title = paper.get("title") or ""
    if not title:
        return None

    corpus_id = paper.get("corpusId")
    external_ids = {}
    for key, value in (paper.get("externalIds") or {}).items():
        if value:
            external_ids[key] = value
    if corpus_id and "CorpusId" not in external_ids:
        external_ids["CorpusId"] = str(corpus_id)

    open_access = paper.get("openAccessInfo") or {}

    authors_raw = paper.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]

    # The matched passage stands in for the abstract, which this endpoint does
    # not return. It is what the reranker scores against, and it is usually
    # more on-point than an abstract would be, being the text that matched.
    text = (snippet.get("text") or "").replace("\n", " ").strip()
    section = snippet.get("section") or ""
    excerpt = f"[{section}] {text}" if section else text

    return {
        "paper_id": external_ids.get("DOI") or (f"CorpusId:{corpus_id}" if corpus_id else ""),
        "title": title.replace("\n", " ").strip(),
        "authors": authors,
        "abstract": excerpt[:SNIPPET_TEXT_CHARS],
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "citation_count": paper.get("citationCount") or 0,
        "_citation_count_known": paper.get("citationCount") is not None,
        "influential_citations": 0,
        "is_open_access": bool(open_access.get("license") or open_access.get("openAccessUrl")),
        "open_access_url": open_access.get("openAccessUrl"),
        "fields_of_study": [],
        "publication_date": None,
        "tldr": None,
        "external_ids": external_ids,
        "url": open_access.get("openAccessUrl") or "",
        "source": "s2_snippet",
        "_snippet_kind": snippet.get("snippetKind"),
        "_snippet_section": section or None,
        "_snippet_score": item.get("score"),
    }
