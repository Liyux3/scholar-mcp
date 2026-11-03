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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial

from . import config, records, relevance, sources, traversal
from .sources import s2


_channel_pool = ThreadPoolExecutor(max_workers=15, thread_name_prefix='scholar-expansion')


class Expanded(dict):
    def __init__(self, rows, reports):
        super().__init__(rows)
        self.reports = reports


def _combine(answers: list) -> list[dict]:
    papers = [p for answer in answers for p in answer.results]
    if any(a.status not in {'ok', 'empty'} for a in answers):
        return sources.PartialResults(papers, 'some expansion providers or identities remain unavailable')
    return papers


@dataclass
class ExpansionContext:
    """Everything a channel needs beyond the seed paper itself."""

    intent: str = ""
    per_seed_limit: int = 20
    seeds: list[dict] = field(default_factory=list)
    metadata_fields: set[str] = field(default_factory=set)
    search_filters: dict = field(default_factory=dict)


def _seed_id(paper: dict) -> str:
    ext = paper.get("external_ids") or {}
    for key in ("DOI", "OpenAlex", "ArXiv"):
        if ext.get(key):
            return ext[key]
    if ext.get("PMID") or ext.get("PubMed"):
        return "PMID:" + str(ext.get("PMID") or ext["PubMed"])
    pid = paper.get("paper_id", "")
    if pid and not pid.startswith("W"):
        return pid
    return ""


def references(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """What the seed cites. Reaches backwards, towards foundations."""
    pid = _seed_id(seed)
    if not pid:
        return []
    return _combine(sources.parallel_references(pid, limit=ctx.per_seed_limit, metadata_fields=ctx.metadata_fields))


def citations(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """What cites the seed. Reaches forwards, towards descendants.

    The title is passed through because OpenAlex cannot resolve an arXiv id on
    its own, and without OpenAlex this falls back to S2, which returns
    citations recency-first rather than impact-first.
    """
    pid = _seed_id(seed)
    if not pid:
        return []
    native = (seed.get('external_ids') or {}).get('OpenAlex')
    return _combine(sources.parallel_citations(pid, limit=ctx.per_seed_limit,
        title=seed.get('title', ''), metadata_fields=ctx.metadata_fields,
        **({'openalex_id': native} if native else {})))


def _title_query(title: str) -> tuple[str, str, str]:
    """A title is natural language, so it is routed like any other query,
    verbatim to semantic sources and compressed for keyword ones. Handing every
    source one string gives most of them the wrong form.
    """
    return title, title, relevance.optimize_query_short(title)


def title_search_all(ctx: ExpansionContext) -> list[dict]:
    """Search every seed title in one flow.

    Each source still answers one title at a time, so it sees the same
    requests at the same pace as when titles ran one after another. Sources
    no longer wait for each other between titles, so the channel takes as long
    as its slowest source's titles, not the sum of each title's slowest source.
    """
    queries = [_title_query(seed["title"]) for seed in ctx.seeds if seed.get("title")]
    if not queries:
        return []
    # One expansion round has one wall-clock budget. Multiplying the whole
    # round by the number of seeds made a stalled web lane block all channels
    # for 90+ seconds even after the other providers had finished.
    flow = sources.search_flow(queries, limit=ctx.per_seed_limit,
                               budget_s=config.SOURCE_BUDGET_S / len(queries), metadata_fields=ctx.metadata_fields,
                               intent=ctx.intent, operation='title', **ctx.search_filters)
    try:
        return _combine(flow.take())
    finally:
        flow.close()


def recommendations(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """SPECTER2 embedding neighbours of the seed."""
    pid = _seed_id(seed)
    if not pid:
        return []
    try:
        return s2.get_recommendations(pid, limit=ctx.per_seed_limit)
    except Exception as error:
        return sources.PartialResults([], f'recommendation provider unavailable ({type(error).__name__})')


def peers(seed: dict, ctx: ExpansionContext) -> list[dict]:
    """Papers cited alongside the seed by the same citing works.

    Unlike the channels above, this does not follow an edge from the seed. It
    asks what the community treats as belonging together, which surfaces
    canonical work the seed itself never cites.
    """
    pid = (seed.get('external_ids') or {}).get('OpenAlex') or _seed_id(seed)
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


# Per-seed channels, run once for each seed paper. References and peers
# recover the most missed papers per request; citations and recommendations
# add fewer but cheap ones.
CHANNELS = {
    "references": references,
    "citations": citations,
    "recommendations": recommendations,
    # Recovers as efficiently as references at the same cost, and is the only
    # default channel that does not follow an edge from the seed, so it can
    # reach work the seed neither cites nor is cited by.
    "peers": peers,
}

# Channels that read all seeds at once and run a single time.
GLOBAL_CHANNELS = {
    "title_search": title_search_all,
    "frequent_terms": frequent_terms,
}

# kin recovers as much as any channel but issues a query per sampled
# reference, about ten times the requests of peers for one more paper. The
# relation crosses field boundaries better than anything else; its cost is
# what keeps it off by default.
OPTIONAL_CHANNELS = {
    "kin": kin,
}


def expand(seeds: list[dict], intent: str = "", per_seed_limit: int = 20,
           channels: list[str] | None = None,
           max_workers: int = 15, metadata_fields: set[str] | None = None,
           search_filters: dict | None = None) -> dict[str, list[dict]]:
    """Run the expansion channels over the seeds, returning results per channel.

    Keyed by channel so callers can attribute what each contributed; the
    pipeline flattens it, the evaluation harness does not.
    """
    if not seeds:
        return {}

    ctx = ExpansionContext(intent=intent, per_seed_limit=per_seed_limit, seeds=seeds,
                           metadata_fields=set(metadata_fields or ()), search_filters=dict(search_filters or {}))
    available = {**CHANNELS, **OPTIONAL_CHANNELS}
    if channels is not None:
        names = channels
    else:
        # Build this dynamically so channel registries remain composable in
        # tests and extensions. frequent_terms stays opt-in.
        names = [*CHANNELS]
        if "title_search" in GLOBAL_CHANNELS:
            names.append("title_search")

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
        papers = fn(ctx) if seed is None else fn(seed, ctx)
        for rank, paper in enumerate(papers):
            paper.setdefault('_retrieval_ranks', {})[f'expansion/{name}'] = rank
        return papers

    # Source I/O and channel orchestration use different bounded pools. Nested
    # source flows publish ready rows into these channel lanes before a slow
    # provider or metadata lookup completes.
    calls = {f'{job[0]}/{index}': [partial(run, job)] for index, job in enumerate(jobs)}
    flow = sources.SourceFlow(calls, config.SOURCE_BUDGET_S, hydrate=False, op='expansion',
                              executor=_channel_pool, max_workers=max_workers)
    answers = flow.collect()
    for answer in answers:
        name = answer.source.split('/', 1)[0]
        facet = 'title' if name == 'title_search' else name
        records.tag_source_ranks(answer.results, 'expansion', facet=facet, positions=answer.positions)
        if name == 'title_search':
            # Nested flows retain API and query index even when only a partial
            # snapshot reached this channel's deadline. Keep all attribution
            # under the existing expansion source so agreement is unchanged.
            for paper in answer.results:
                for path, rank in (paper.get('_retrieval_ranks') or {}).items():
                    if path.startswith('title/'):
                        _, api, index = path.split('/', 2)
                        records.tag_source_ranks([paper], 'expansion', facet=f'title::{api}::{index}',
                                                 positions=[rank])
        results.setdefault(name, []).extend(answer.results)
    return Expanded(results, answers)
