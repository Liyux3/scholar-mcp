"""Is every link still working: the sources, the reranker and the PDF chain.

Two views answer it. findings() reads the record real use leaves behind
(usage_log) and names what changed: a source that started failing, went
silent, lost its abstracts or slowed down, a reranker that fell back, a tool
that raised. probe() asks directly, with inputs known to have answers, only
what real use has not shown working in the last day, and never a link billed
per request: those are judged from real searches alone. So a break shows up
on a day nobody searched without the check itself costing anything.
`scholar-mcp doctor` prints the first, `doctor --network` adds the second, and
a scheduled CI run of it opens an issue when a probe fails.
"""

import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

from . import config, sources, usage_log

RECENT = 10        # answers that describe a source's current state
SILENT_AFTER = 8   # empty answers in a row, after earlier papers, that mean a source went quiet
SLOW_FLOOR_MS = 10_000
PROBE_BUDGET_S = 90
VOUCH_S = 24 * 3600  # a working answer from real use this recent spares its link the probe

PROBE_QUERY = "machine learning"
# GW150914 in Phys. Rev. Lett.: every citation and reference source indexes it,
# the physics and biomedical ones included.
PROBE_DOI = "10.1103/PhysRevLett.116.061102"
PROBE_TITLE = "Observation of Gravitational Waves from a Binary Black Hole Merger"
# An open paper in PMC, Unpaywall and the repositories, and one arXiv paper.
PROBE_OPEN = {"title": "Ten Simple Rules for Reproducible Computational Research",
              "external_ids": {"DOI": "10.1371/journal.pcbi.1003285", "PMC": "PMC3812051"}}
PROBE_ARXIV = {"title": "Attention Is All You Need", "year": 2017, "authors": ["Ashish Vaswani"],
               "external_ids": {"ArXiv": "1706.03762"}}


def _tail(values: list, q: float):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def _answers(rows: list[dict]) -> dict[str, list[tuple]]:
    """Each source's answers in time order, as (status, ms, count, abstract share)."""
    answers: dict[str, list[tuple]] = {}
    for row in rows:
        for name, entry in (row.get("sources") or {}).items():
            status, ms, *rest = entry
            answers.setdefault(name, []).append((status, ms, *(rest + [None, None])[:2]))
    return answers


def findings(rows: list[dict]) -> list[dict]:
    """What changed for the worse in the recorded calls, one plain sentence each."""
    found = []
    last_error = {}
    for row in rows:
        last_error.update(row.get("errors") or {})

    def add(link: str, kind: str, detail: str):
        found.append({"link": link, "kind": kind, "detail": detail,
                      **({"last_error": last_error[link]} if kind == "failing" and link in last_error else {})})

    for name, seen in sorted(_answers(rows).items()):
        recent, earlier = seen[-RECENT:], seen[:-RECENT]
        statuses = [answer[0] for answer in seen]
        failed = sum(status in usage_log.FAILED for status in statuses[-RECENT:])
        if len(recent) >= 3 and (2 * failed >= len(recent) or set(statuses[-3:]) <= usage_log.FAILED):
            add(name, "failing", f"{failed} of its last {len(recent)} answers failed")
            continue
        paced = statuses[-RECENT:].count("paced")
        if len(recent) >= 3 and 2 * paced >= len(recent):
            add(name, "paced", f"its published limit turned away {paced} of its last {len(recent)} calls; a key or a slower pace helps")
            continue
        # Only a search can go quiet; an empty citation list or PDF lookup is an ordinary answer.
        search = "/" not in name
        if (search and len(seen) > SILENT_AFTER and set(statuses[-SILENT_AFTER:]) == {"empty"}
                and {"ok", "partial"} & set(statuses[:-SILENT_AFTER])):
            add(name, "silent", f"no papers in its last {SILENT_AFTER} answers, after returning papers before")
        elif search and len(seen) >= RECENT and set(statuses) == {"empty"}:
            add(name, "never_answered", f"no papers in any of its {len(seen)} answers")
        shares = [share for _, _, count, share in seen if count and share is not None]
        if len(shares) >= RECENT + 5:
            before, now = statistics.mean(shares[:-RECENT]), statistics.mean(shares[-RECENT:])
            if before >= 0.6 and now < 0.2:
                add(name, "lost_abstracts", f"abstracts on {now:.0%} of recent papers, {before:.0%} before")
        if len(earlier) >= RECENT:
            now_ms, before_ms = _tail([a[1] for a in recent], .9), _tail([a[1] for a in earlier], .9)
            if now_ms > max(SLOW_FLOOR_MS, 2 * before_ms):
                add(name, "slower", f"slowest answers take {now_ms / 1000:.0f}s, {before_ms / 1000:.0f}s before")

    rankings = [row["reranker"] for row in rows if isinstance(row.get("reranker"), dict)][-5:]
    fallbacks = [r for r in rankings if r.get("provider") == "unavailable"
                 or r.get("fallback_reason") not in (None, "", "not configured")]
    if fallbacks and (len(fallbacks) >= 2 or fallbacks[-1] is rankings[-1]):
        add("reranker", "fallback", f"fell back in {len(fallbacks)} of the last {len(rankings)} searches: "
                                    f"{fallbacks[-1].get('fallback_reason') or 'no ranking model'}")

    reads = [row["pdf"] for row in rows if "pdf" in row][-RECENT:]
    if len(reads) >= 5 and set(reads) == {"none"}:
        add("pdf", "no_pdf", f"no PDF found for any of the last {len(reads)} reads and downloads")

    raised: dict[tuple, int] = {}
    for row in rows[-100:]:
        if row.get("exception"):
            key = (row.get("tool", "?"), row["exception"], row.get("where", ""))
            raised[key] = raised.get(key, 0) + 1
    for (tool, exception, where), times in raised.items():
        add(tool, "exception", f"raised {exception} at {where or 'an unknown place'} ({times}x in the last 100 calls)")
    return found


def report(limit: int = 500) -> dict:
    """Latency per tool, how each source has been answering, and the findings."""
    rows = usage_log.read(limit)
    if not rows:
        return {}
    tools: dict[str, list[int]] = {}
    for row in rows:
        tools.setdefault(row.get("tool", "?"), []).append(row.get("ms", 0))
    links = {}
    for name, seen in sorted(_answers(rows).items()):
        statuses = [answer[0] for answer in seen]
        shares = [share for _, _, count, share in seen if count and share is not None]
        links[name] = {"answers": len(seen),
                       "failed": round(sum(s in usage_log.FAILED for s in statuses) / len(seen), 2),
                       "empty": round(statuses.count("empty") / len(seen), 2),
                       "p90_s": round(_tail([a[1] for a in seen], .9) / 1000, 1),
                       **({"abstracts": round(statistics.mean(shares), 2)} if shares else {})}
    return {
        "calls": len(rows),
        "since": rows[0].get("at"),
        "tools": {name: {"calls": len(ms), "p50_s": round(_tail(ms, .5) / 1000, 1), "p90_s": round(_tail(ms, .9) / 1000, 1)}
                  for name, ms in sorted(tools.items())},
        "sources": links,
        "findings": findings(rows),
    }


def _vouched(rows: list[dict]) -> set[str]:
    """Links that answered with papers in the last VOUCH_S seconds."""
    since = time.time() - VOUCH_S
    recent = [row for row in rows if row.get("at")
              and datetime.fromisoformat(row["at"].replace("Z", "+00:00")).timestamp() > since]
    last = {name: entry for row in recent for name, entry in (row.get('sources') or {}).items()}
    return {name for name, entry in last.items()
            if entry[0] in ('ok', 'partial') and (len(entry) < 3 or entry[2] > 0)}


def _recorded_reranker(rows: list[dict]) -> dict:
    """A billed reranker's state from the last real search that used one."""
    last = next((row["reranker"] for row in reversed(rows) if row.get("reranker")), None)
    if not last:
        return {"working": None, "reason": "billed, so not probed; no search has used it yet"}
    reason = last.get("fallback_reason")
    return {"provider": last.get("provider"), "model": last.get("model"), "working": not reason,
            "seen": "in real use", **({"reason": reason} if reason else {})}


def _probe_reranker() -> dict:
    from . import relevance

    papers = [{"title": "Attention Is All You Need", "abstract": "A network architecture based solely on attention."},
              {"title": PROBE_TITLE, "abstract": "A binary black hole merger seen by LIGO."}]
    relevance.rerank("transformer attention architecture", papers, top_n=2)
    state = relevance.reranker_status()
    reason = state.get("fallback_reason")
    working = state.get("provider") not in (None, "unavailable") and reason in (None, "", "not configured")
    return {"provider": state.get("provider"), "model": state.get("model"), "working": working,
            **({"reason": reason} if reason else {})}


def _probe_pdf() -> dict:
    from . import config, pdf_utils
    from .sources import europepmc

    with tempfile.TemporaryDirectory(prefix="scholar-probe-") as directory:
        arxiv = pdf_utils._try_download(f"https://arxiv.org/pdf/{PROBE_ARXIV['external_ids']['ArXiv']}.pdf",
                                        directory, "arxiv.pdf")
    doi = PROBE_OPEN["external_ids"]["DOI"]
    return {
        "arxiv_download": bool(arxiv),
        # The listing answers with the article's own file only while the dataset is reachable.
        "pmc_dataset": len(europepmc.pmc_pdf_urls(PROBE_OPEN["external_ids"]["PMC"])) > 1,
        "unpaywall": bool(pdf_utils._unpaywall_locations(doi)) if config.get_openalex_email() else "not configured",
        "repositories": sorted({entry[0] for entry in sources.resolve_pdf_candidates(PROBE_OPEN)}),
    }


def probe(skip: set[str] = frozenset()) -> dict:
    """Ask each search, citation and reference source, the reranker and the PDF chain directly.

    Every fixture is known to have answers at every source it goes to (all
    returned papers on 2026-10-17), so an empty answer means something changed,
    and it is named apart from a failed request. Problems map each broken link
    to what went wrong; links that have not answered by PROBE_BUDGET_S count as
    timed out instead of holding the run.
    """
    jobs = {}
    for source in sources.search_sources():
        jobs[source.name] = (source.name, source.search, (source.probe_query or PROBE_QUERY, 3), {})
    for source in sources.citation_sources():
        jobs[f"{source.name}/citations"] = (source.name, source.get_citations, (source.probe_id or PROBE_DOI,),
                                            {"limit": 3, "title": PROBE_TITLE})
    for source in sources.reference_sources():
        jobs[f"{source.name}/references"] = (source.name, source.get_references, (source.probe_id or PROBE_DOI,), {"limit": 3})
    rows = usage_log.read()
    vouched = _vouched(rows)
    paid = {source.name for source in sources.all_sources() if source.paid}
    not_probed = {"in use": sorted(set(jobs) & vouched), "billed": sorted(link for link in jobs if link.split("/")[0] in paid)}
    jobs = {link: job for link, job in jobs.items()
            if link.split("/")[0] not in skip | paid and link not in vouched}
    billed_reranker = bool(config.RERANK_URL or config.DASHSCOPE_API_KEY)

    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scholar-probe")
    try:
        with usage_log.call("probe", "doctor", {}):
            futures = {link: pool.submit(sources._timed_call, name, call, *args, **kwargs)
                       for link, (name, call, args, kwargs) in jobs.items()}
            checks = {"reranker": pool.submit(_recorded_reranker, rows) if billed_reranker else pool.submit(_probe_reranker),
                      "pdf": pool.submit(_probe_pdf)}
            wait([*futures.values(), *checks.values()], timeout=PROBE_BUDGET_S)
            answers = {link: future.result() if future.done() else
                       sources.SourceResult(link, "timeout", [], PROBE_BUDGET_S * 1000, "no answer within the probe")
                       for link, future in futures.items()}
            for link, answer in answers.items():
                usage_log.note_source(link, answer.status, answer.latency_ms, len(answer.results),
                                      answer.abstracts, answer.error or "")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    reranker = checks["reranker"].result() if checks["reranker"].done() else {"working": False, "reason": "timeout"}
    pdf = checks["pdf"].result() if checks["pdf"].done() else {"chain": "timeout"}

    links = {link: {"status": answer.status, "count": len(answer.results), "ms": answer.latency_ms,
                    **({"abstracts": answer.abstracts} if answer.abstracts is not None else {}),
                    **({"error": answer.error} if answer.error else {})}
             for link, answer in sorted(answers.items())}
    problems = {link: answer.status for link, answer in sorted(answers.items()) if not answer.results}
    if reranker["working"] is False:
        problems["reranker"] = reranker.get("reason") or "unavailable"
    for step, result in pdf.items():
        if result is False or result == [] or result == "timeout":
            problems[f"pdf/{step}"] = "timeout" if result == "timeout" else "failed"
    return {"problems": problems, "links": links, "not_probed": not_probed, "reranker": reranker, "pdf": pdf}
