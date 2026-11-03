"""Citation-graph traversal primitives.

"Find me similar papers" is not one question. These are five different
relations, each answering something different, and collapsing them into a
single similarity call loses most of the structure:

    references      what this paper builds on          -> foundations
    citations       what built on this paper           -> descendants
    co_citation     what gets cited alongside it       -> peers
    bibliographic_coupling  what cites the same works  -> methodological kin
    similar         embedding neighbours (SPECTER2)    -> semantic neighbours

Co-citation and bibliographic coupling are the two that cross field
boundaries. A protein-folding paper and a machine-translation paper can share
a reference list without sharing any vocabulary, so an embedding model will
never connect them, while coupling makes the link obvious. They are also both
computable from OpenAlex, which returns `referenced_works` inline.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import httpx

from . import records as record_ops
from .sources import openalex as oa
from .sources import s2

OA_BASE = "https://api.openalex.org/works"

# How many citing papers to inspect when computing co-citation. Each carries
# its full reference list, so this is one request, not N.
CO_CITATION_SAMPLE = 50

# Minimum times two papers must co-occur before the link is meaningful. At 1,
# every reference of every citing paper qualifies and the result is noise.
MIN_CO_OCCURRENCE = 2

# Candidates carried into the metadata fetch. OpenAlex accepts 50 ids per
# batched request, and overshooting the caller's limit is deliberate: dead ids
# and duplicate records both cost candidates, so trimming earlier would let
# them eat the answer.
FETCH_BATCH = 50


def _wid(paper_id: str, title: str = "") -> str | None:
    return oa._resolve_to_wid(paper_id, title=title)


def _fetch_works(wids: list[str], fields: str = oa.OA_SELECT_FIELDS) -> dict:
    """Batch-resolve OpenAlex ids to work records, keyed by full id.

    Ids that OpenAlex no longer serves are recovered through Semantic Scholar
    where possible; see _recover_dead_works for why that matters.
    """
    if not wids:
        return {}
    params = oa._params_base()
    params["filter"] = "openalex_id:" + "|".join(wids[:50])
    params["select"] = fields
    params["per_page"] = min(len(wids), 50)
    response = oa._request(OA_BASE, params)
    if response.status_code != 200:
        return {}
    works = {w["id"]: w for w in response.json().get("results", [])}

    missing = [w for w in wids[:50] if f"https://openalex.org/{w}" not in works]
    if missing:
        works.update(_recover_dead_works(missing))
    return works


# OpenAlex ids minted during the MAG import keep the MAG identifier as their
# numeric part, so a dead W2xxxxxxxxx can be looked up elsewhere as MAG:xxxxxxxxx.
# Ids allocated later by OpenAlex itself (W6 and above) carry no such mapping,
# so there is nothing to recover them with.
_MAG_ERA_PREFIX = "W2"


def _recover_dead_works(wids: list[str]) -> dict:
    """Rebuild records for ids OpenAlex dropped, via their MAG identifiers.

    OpenAlex returns neither a 404 body nor a redirect for these, and the
    batch filter simply omits them, so without this they disappear silently.
    They are not marginal: the two strongest co-citation edges for BERT are
    "Attention Is All You Need" (186k citations) and ELMo, and both are dead
    ids. Dropping them removes precisely the papers the relation exists to
    surface. Measured across four seeds, 8 of 21 dead edges come back, and the
    recovered ones carry the highest co-occurrence counts.
    """
    candidates = [w for w in wids if w.startswith(_MAG_ERA_PREFIX)]
    if not candidates:
        return {}

    def lookup(wid: str) -> tuple[str, dict] | None:
        try:
            paper = s2.get_paper(f"MAG:{wid[1:]}")
        except Exception:
            return None
        if not paper or not paper.get("title"):
            return None
        doi = (paper.get("external_ids") or {}).get("DOI") or ""
        return f"https://openalex.org/{wid}", {
            "id": f"https://openalex.org/{wid}",
            "title": paper["title"],
            "cited_by_count": paper.get("citation_count") or 0,
            "publication_year": paper.get("year"),
            "doi": f"https://doi.org/{doi}" if doi else None,
            "_recovered_paper": paper,
        }

    # S2 serialises behind a 1.05s gate, so a serial loop over seven dead ids
    # costs eight seconds. The gate still spaces the requests; overlapping the
    # waiting is what saves the time.
    with ThreadPoolExecutor(max_workers=min(len(candidates), 8)) as pool:
        return dict(entry for future in [pool.submit(copy_context().run, lookup, wid) for wid in candidates]
                    if (entry := future.result()))


def _as_paper(work: dict, relation: str, strength: int) -> dict:
    paper = dict(work['_recovered_paper']) if work.get('_recovered_paper') else oa.format_paper(work)
    if work.get('_recovered_paper'):
        paper.update(source='openalex', _metadata_source='semantic_scholar')
    paper.update(_relation=relation, _relation_strength=strength,
                 _relation_support={work['id']: strength})
    return paper


def _materialise(candidates: list[tuple[str, int]], relation: str, limit: int) -> list[dict]:
    """Turn (openalex_id, strength) pairs into papers, deduped, honouring limit.

    Fetches more candidates than requested because some ids resolve to nothing
    and some collapse into each other. Trimming to `limit` before this point
    would spend slots on ids that yield no paper.

    Duplicates are real and common: OpenAlex holds several work records for the
    same paper (preprint, conference version, a stray MAG import), each with
    its own id, so a single paper can appear two or three times with its
    co-occurrence count split across them. VGG shows up twice in ResNet's peers
    at 32 and 20 votes. Merging them by title and summing the strength both
    removes the duplicate and restores the true edge weight.
    """
    works = _fetch_works([ref.split("/")[-1] for ref, _ in candidates])

    papers = []
    for ref, strength in candidates:
        work = works.get(ref)
        if not work or not work.get("title"):
            continue
        papers.append(_as_paper(work, relation, strength))
    out = sorted(record_ops.deduplicate(papers), key=lambda p: -p['_relation_strength'])
    return out[:limit]


def co_citation(paper_id: str, title: str = "", limit: int = 20,
                sample: int = CO_CITATION_SAMPLE) -> list[dict]:
    """Papers frequently cited alongside this one.

    Takes the most-cited papers that cite the seed, counts what else they
    cite, and returns the most frequent. This surfaces the seed's intellectual
    peers: for "Attention Is All You Need" it returns ResNet, ImageNet,
    AlexNet and LSTM, the deep-learning canon that transformer papers cite
    together with it.
    """
    wid = _wid(paper_id, title)
    if not wid:
        return []

    try:
        citing = oa.citing_works(wid, max(50, min(sample, 100)))[:min(sample, 100)]
    except httpx.HTTPStatusError:
        return []

    seed_full_id = f"https://openalex.org/{wid}"
    counts = Counter()
    for work in citing:
        for ref in work.get("referenced_works") or []:
            if ref != seed_full_id:
                counts[ref] += 1

    candidates = [(ref, n) for ref, n in counts.most_common(limit * 3)
                  if n >= MIN_CO_OCCURRENCE][:FETCH_BATCH]
    return _materialise(candidates, "co_citation", limit)


def bibliographic_coupling(paper_id: str, title: str = "", limit: int = 20) -> list[dict]:
    """Papers that cite many of the same works as this one.

    Shared references indicate shared methodology even when the applications
    are unrelated, which is what makes this the most useful relation for
    crossing domain boundaries: two papers can be coupled without sharing any
    topical vocabulary.
    """
    wid = _wid(paper_id, title)
    if not wid:
        return []

    params = oa._params_base()
    params["select"] = "referenced_works"
    seed = oa._request(f"{OA_BASE}/{wid}", params)
    if seed.status_code != 200:
        return []
    references = seed.json().get("referenced_works") or []
    if not references:
        return []

    # Sample the seed's references, then ask who else cites each. Papers
    # appearing under many of them are strongly coupled.
    sampled = references[:20]

    def citers_of(ref_full_id: str) -> list[str]:
        p = oa._params_base()
        p["filter"] = f"cites:{ref_full_id.split('/')[-1]}"
        p["sort"] = "cited_by_count:desc"
        p["per_page"] = 25
        p["select"] = "id"
        r = oa._request(OA_BASE, p)
        if r.status_code != 200:
            return []
        return [w["id"] for w in r.json().get("results", [])]

    counts = Counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(copy_context().run, citers_of, ref) for ref in sampled]:
            counts.update(future.result())

    seed_full_id = f"https://openalex.org/{wid}"
    candidates = [(wid_, n) for wid_, n in counts.most_common(limit * 3)
                  if wid_ != seed_full_id and n >= MIN_CO_OCCURRENCE][:FETCH_BATCH]
    return _materialise(candidates, "bibliographic_coupling", limit)
