"""Field discovery: automated landscape mapping from a topic string.

Combines survey-seeded search, reference expansion, and citation forward
to build a structured view of a research field.
"""

from . import relevance, graph, sources
from . import knowledge_base as kb

# Per-source fetch limit for the two seed searches. The old values of 10 and
# 15 starved the ranking stage: the field's defining papers are not reliably
# in any single source's top 15.
SEARCH_LIMIT = 100

# Upper bound on the candidate pool before ranking. Guards memory and reranker
# cost without cutting so early that relevance never gets a say.
POOL_CAP = 800


def discover_field(
    topic: str,
    max_papers: int = 30,
    recent_years: int = 3,
) -> dict:
    """Map a research field by finding surveys, expanding references, and tracing citations.

    Strategy:
    1. Search for survey/review papers on the topic
    2. Search for recent high-cited papers on the topic
    3. Expand references from top papers (foundational works)
    4. Expand citations from foundational works (recent advances)
    5. Build a citation graph connecting everything

    Returns summary + mermaid graph + categorized paper lists.
    """
    all_papers = []
    seen_titles = set()
    from datetime import datetime
    current_year = datetime.now().year

    def _add_unique(papers):
        """Collect distinct papers. Deliberately uncapped: truncating here
        discards candidates in arrival order, before anything has judged
        relevance. The pool is cut down after ranking instead.
        """
        added = 0
        for p in papers:
            nt = relevance._normalize_title(p.get("title", ""))
            if nt and nt not in seen_titles and len(all_papers) < POOL_CAP:
                seen_titles.add(nt)
                all_papers.append(p)
                added += 1
        return added

    survey_query = f"survey review {topic}"
    recent_cutoff = current_year - recent_years

    # Route queries the way _pipeline does. Passing only the positional query
    # sends the same string to every source, so semantic endpoints get a
    # keyword string and lose the recall they exist to provide. Without this,
    # "retrieval augmented generation" missed Lewis et al. entirely and
    # surfaced unrelated high-citation biomedical papers instead.
    def _search(query: str, limit: int):
        return sources.parallel_search(
            relevance.optimize_query(query),
            limit=limit,
            raw_query=query,
            short_query=relevance.optimize_query_short(query),
        )

    for sr in _search(survey_query, limit=SEARCH_LIMIT):
        if sr.results:
            _add_unique(sr.results)

    for sr in _search(topic, limit=SEARCH_LIMIT):
        if sr.results:
            _add_unique(sr.results)

    # Rank by relevance to the topic, then by the usual metadata factors.
    # Sorting on citation count alone let any heavily cited paper that merely
    # matched a keyword outrank the field's actual foundations: a genome
    # annotation pipeline with 5,700 citations was the top result for
    # "retrieval augmented generation".
    deduped = relevance.deduplicate(all_papers)
    deduped = relevance.rerank(topic, deduped, top_n=max_papers * 2)
    deduped = relevance.rank_final(deduped)

    # Step 3: Expand references from top papers (find foundations)
    top_papers = deduped[:5]
    for p in top_papers:
        pid = p.get("paper_id", "")
        if not pid:
            continue
        try:
            refs = graph._fetch_related(p, "references", 5, 0.3)
            _add_unique(refs)
        except Exception:
            pass

    # Step 4: Build citation graph
    seed_papers = deduped[:3]
    citation_graph = graph.build_graph(
        seed_papers,
        max_hops=1,
        max_papers=min(max_papers, 25),
        direction="both",
        min_citations=10,
        citations_per_paper=5,
        references_per_paper=5,
        topic_filter=topic,
    )

    # Categorize papers (filter by topic relevance first)
    deduped = relevance.deduplicate(all_papers)
    topic_words = [w.lower() for w in topic.split() if len(w) > 3]
    if len(topic_words) >= 2:
        filtered = []
        for p in deduped:
            title_lower = (p.get("title") or "").lower()
            abstract_lower = (p.get("abstract") or "").lower()
            text = title_lower + " " + abstract_lower
            n_matches = sum(1 for w in topic_words if w in text)
            title_matches = sum(1 for w in topic_words if w in title_lower)
            if n_matches >= 2 or title_matches >= 1:
                filtered.append(p)
        deduped = filtered
    deduped.sort(key=lambda p: p.get("citation_count", 0) or 0, reverse=True)

    foundational = [p for p in deduped if (p.get("citation_count", 0) or 0) > 500
                    and (p.get("year") or 0) < recent_cutoff][:5]
    recent_hot = [p for p in deduped if (p.get("year") or 0) >= recent_cutoff
                  and (p.get("citation_count", 0) or 0) > 10][:5]
    surveys_found = [p for p in deduped
                     if any(w in (p.get("title") or "").lower() for w in ("survey", "review", "tutorial", "overview"))][:3]

    # Build output
    lines = [f"Field: {topic}", f"Papers found: {len(deduped)}", ""]

    if surveys_found:
        lines.append("Surveys:")
        for p in surveys_found:
            lines.append(f"  [{p.get('year')}] {p['title'][:60]} ({p.get('citation_count',0)}c)")
        lines.append("")

    if foundational:
        lines.append("Foundational:")
        for p in foundational:
            lines.append(f"  [{p.get('year')}] {p['title'][:60]} ({p.get('citation_count',0)}c)")
        lines.append("")

    if recent_hot:
        lines.append("Recent:")
        for p in recent_hot:
            lines.append(f"  [{p.get('year')}] {p['title'][:60]} ({p.get('citation_count',0)}c)")
        lines.append("")

    # Auto-save to KB
    safe_topic = topic.replace(" ", "-")[:30]
    kb.add_papers(deduped, collection=f"discover-{safe_topic}", notes=f"Auto-discovered: {topic}")

    summary = "\n".join(lines) + "\n" + citation_graph["summary"]
    summary += f"\n\nSaved to KB collection: discover-{safe_topic}"

    return {
        "summary": summary,
        "mermaid": citation_graph["mermaid"],
        "papers": {
            "surveys": [{"title": p["title"], "year": p.get("year"), "citations": p.get("citation_count", 0)}
                        for p in surveys_found],
            "foundational": [{"title": p["title"], "year": p.get("year"), "citations": p.get("citation_count", 0)}
                             for p in foundational],
            "recent": [{"title": p["title"], "year": p.get("year"), "citations": p.get("citation_count", 0)}
                       for p in recent_hot],
        },
        "total_papers": len(deduped),
    }
