"""Citation-graph expansion: widening the candidate pool beyond keyword search.

A keyword search can only return papers whose text matches the query. Much of
what a researcher wants is one hop away from that: the work a good result
builds on, the work that built on it, the papers it sits alongside in other
people's reference lists. Expansion takes the strongest initial results as
seeds and pulls in their neighbours.

This matters more than it might sound. On the cached benchmark run, 9 of 42
ground-truth papers were reachable only through expansion, so a fifth of the
answer is invisible to search alone.

Each channel is a plain function of (seed, context) so it can be called,
measured and tested on its own. They are registered in CHANNELS rather than
being closures inside the pipeline, because an evaluation harness that has to
reconstruct the pipeline to measure a channel will get it wrong: the first
version of the harness skipped reranking, ranked seeds by citation count, and
concluded that four channels contributed nothing at all. They should measure
the same code that runs in production.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import relevance
from . import s2_client
from . import sources
from . import traversal


@dataclass
class ExpansionContext:
    """Everything a channel needs beyond the seed paper itself."""

    intent: str = ""
    per_seed_limit: int = 20
    seeds: list[dict] = field(default_factory=list)


# Minimum citations an expanded paper needs to be worth carrying, by intent.
# A search for foundational work has no use for a two-week-old preprint with
# no citations; a search for recent method papers does.
_MIN_CITATIONS_BY_INTENT = {"foundational": 10, "survey": 5, "method": 3}


def _seed_id(paper: dict) -> str:
    ext = paper.get("external_ids") or {}
    for key in ("DOI", "OpenAlex", "ArXiv"):
        if ext.get(key):
            return ext[key]
    pid = paper.get("paper_id", "")
    if pid and not pid.startswith("W"):
        return pid
    return ""


def references(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """What the seed cites. Reaches backwards, towards foundations."""
    pid = _seed_id(seed)
    if not pid:
        return []
    return [p for sr in sources.parallel_references(pid, limit=ctx.per_seed_limit)
            for p in sr.results]


def citations(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """What cites the seed. Reaches forwards, towards descendants.

    The title is passed through because OpenAlex cannot resolve an arXiv id on
    its own, and without OpenAlex this falls back to S2, which returns
    citations recency-first rather than impact-first.
    """
    pid = _seed_id(seed)
    if not pid:
        return []
    floor = _MIN_CITATIONS_BY_INTENT.get(ctx.intent, 1)
    out = []
    for sr in sources.parallel_citations(pid, limit=ctx.per_seed_limit,
                                         title=seed.get("title", "")):
        out.extend(p for p in sr.results
                   if (p.get("citation_count") or 0) >= floor)
    return out


def title_search(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """Re-search using the seed's title as the query.

    By far the most expensive channel: a full fan-out across every source, per
    seed. A title is natural language, so it is routed like any other query,
    verbatim to semantic sources and compressed for keyword ones. Handing every
    source one string gives most of them the wrong form.
    """
    title = seed.get("title", "")
    if not title:
        return []
    return [p for sr in sources.parallel_search(
        relevance.optimize_query(title),
        limit=ctx.per_seed_limit,
        raw_query=title,
        short_query=relevance.optimize_query_short(title),
    ) for p in sr.results]


def recommendations(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """SPECTER2 embedding neighbours of the seed."""
    pid = _seed_id(seed)
    if not pid:
        return []
    try:
        return s2_client.get_recommendations(pid, limit=ctx.per_seed_limit)
    except Exception:
        return []


def peers(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """Papers cited alongside the seed by the same citing works.

    Unlike the channels above, this does not follow an edge from the seed. It
    asks what the community treats as belonging together, which surfaces
    canonical work the seed itself never cites.
    """
    pid = _seed_id(seed)
    if not pid:
        return []
    return traversal.co_citation(pid, title=seed.get("title", ""),
                                 limit=ctx.per_seed_limit)


def kin(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """Papers whose reference lists overlap the seed's.

    Shared references indicate shared method even when the applications are
    unrelated, so this is the one relation that reliably crosses field
    boundaries. Every other channel stays inside the seed's topical
    neighbourhood.
    """
    pid = _seed_id(seed)
    if not pid:
        return []
    return traversal.bibliographic_coupling(pid, title=seed.get("title", ""),
                                            limit=ctx.per_seed_limit)


def frequent_terms(ctx: ExpansionContext) -> list[dict]:
    """One extra search built from terms common across all seeds.

    Global rather than per-seed: it describes what the seeds have in common,
    which no single seed's title does. The query is a bag of terms with no
    natural-language form, so there is no raw variant for semantic sources and
    they get the keyword string too.
    """
    terms = Counter()
    for paper in ctx.seeds:
        text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
        for word in relevance.extract_keywords(text, max_keywords=5):
            terms[word] += 1
    top = [w for w, _ in terms.most_common(8)]
    if not top:
        return []
    return [p for sr in sources.parallel_search(" ".join(top), limit=50)
            for p in sr.results]


# Per-seed channels, run once for each seed paper. Measured over 25 LitSearch
# queries against the 17 ground-truth papers the initial search missed:
#
#   channel          recovered   requests   per 100 requests
#   references           7          150          4.67
#   kin                  7         1575          0.44
#   peers                6          150          4.00
#   title_search         4         1050          0.38
#   citations            3          150          2.00
#   recommendations      1           75          1.33
#
CHANNELS = {
    "references": references,
    "citations": citations,
    "title_search": title_search,
    "recommendations": recommendations,
    # Recovers as efficiently as references at the same cost, and is the only
    # default channel that does not follow an edge from the seed, so it can
    # reach work the seed neither cites nor is cited by.
    "peers": peers,
}

# Channels that read all seeds at once and run a single time.
GLOBAL_CHANNELS = {
    "frequent_terms": frequent_terms,
}

# kin recovers as much as any channel but issues a query per sampled
# reference, which is 1575 requests against peers' 150 for one more paper. The
# relation is worth having and crosses field boundaries better than anything
# else; the implementation is what makes it too expensive to run by default.
OPTIONAL_CHANNELS = {
    "kin": kin,
}


def expand(seeds: list[dict], intent: str = "", per_seed_limit: int = 20,
           channels: list[str] | None = None,
           max_workers: int = 15) -> dict[str, list[dict]]:
    """Run the expansion channels over the seeds, returning results per channel.

    Keyed by channel so callers can attribute what each contributed; the
    pipeline flattens it, the evaluation harness does not.
    """
    if not seeds:
        return {}

    ctx = ExpansionContext(intent=intent, per_seed_limit=per_seed_limit, seeds=seeds)
    available = {**CHANNELS, **OPTIONAL_CHANNELS}
    names = channels if channels is not None else list(CHANNELS)

    results: dict[str, list[dict]] = {name: [] for name in names}
    jobs = []
    for name in names:
        if name in available:
            jobs.extend((name, available[name], seed) for seed in seeds)
        elif name in GLOBAL_CHANNELS:
            jobs.append((name, GLOBAL_CHANNELS[name], None))

    if not jobs:
        return results

    def run(job):
        name, fn, seed = job
        try:
            return name, (fn(ctx) if seed is None else fn(seed, ctx))
        except Exception:
            return name, []

    with ThreadPoolExecutor(max_workers=min(len(jobs), max_workers)) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            name, papers = future.result()
            results.setdefault(name, []).extend(papers)

    return results
