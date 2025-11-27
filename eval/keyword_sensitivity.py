"""Keyword compression sensitivity test.

For each config (compression method x params), runs ALL sources in parallel.
Only same-source queries are sequential (to respect per-source rate limits).
3 compression architectures: extract_keywords, KeyBERT, extract_keywords -> KeyBERT.

Usage:
    OPENALEX_API_KEY=... SCOPUS_API_KEY=... python eval/keyword_sensitivity.py
"""
import json, time, sys, os, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from scholar_mcp import openalex_client, arxiv_client, crossref_client, scopus_client, doaj_client
from scholar_mcp import relevance, config
from matching import titles_match

config.SCOPUS_API_KEY = os.environ.get("SCOPUS_API_KEY", "")
config.OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")

DATA_DIR = Path(__file__).parent / "data"
N = 20
SOURCE_DELAYS = {
    "arxiv": 3.0,
    "scopus": 1.0,
    "openalex": 1.0,
    "crossref": 1.0,
    "doaj": 1.0,
}

from keybert import KeyBERT
from model2vec import StaticModel
_kb = KeyBERT(StaticModel.from_pretrained("minishlab/potion-base-8M"))


def compress_ek(query, max_kw):
    return " ".join(relevance.extract_keywords(query, max_keywords=max_kw))


def compress_kb(query, top_n):
    kws = _kb.extract_keywords(query, keyphrase_ngram_range=(1, 3), stop_words="english",
                                top_n=top_n, use_mmr=True, diversity=0.5)
    return " ".join(kw for kw, _ in kws) if kws else query


def compress_ek_kb(query, ek_max, kb_top_n):
    cleaned = compress_ek(query, ek_max)
    kws = _kb.extract_keywords(cleaned, keyphrase_ngram_range=(1, 3), stop_words="english",
                                top_n=kb_top_n, use_mmr=True, diversity=0.5)
    return " ".join(kw for kw, _ in kws) if kws else cleaned


def word_count(s):
    return len(re.findall(r"[a-zA-Z0-9][\w\-]*", s))


SOURCES = {
    "openalex": lambda q, lim: openalex_client.search_papers(q, limit=lim),
    "arxiv": lambda q, lim: arxiv_client.search_papers(q, max_results=lim),
    "crossref": lambda q, lim: crossref_client.search_papers(q, limit=lim),
    "scopus": lambda q, lim: scopus_client.search_papers(q, limit=lim),
    "doaj": lambda q, lim: doaj_client.search_papers(q, limit=lim),
}

CONFIGS = []
for k in [3, 4, 5, 6, 8, 10, 12]:
    CONFIGS.append((f"ek_{k}", lambda q, k=k: compress_ek(q, k)))
for t in [2, 3, 5]:
    CONFIGS.append((f"kb_{t}", lambda q, t=t: compress_kb(q, t)))
for t in [2, 3, 5]:
    CONFIGS.append((f"ek_kb_{t}", lambda q, t=t: compress_ek_kb(q, 15, t)))
CONFIGS.append(("raw", lambda q: q))


def test_one_source(src_name, search_fn, compressed_queries, entries):
    """Run all queries for one source sequentially (respect per-source rate limit)."""
    delay = SOURCE_DELAYS.get(src_name, 1.0)
    hits = 0
    errors = 0
    for i in range(len(entries)):
        q = compressed_queries[i]
        try:
            papers = search_fn(q, 100)
            if any(any(titles_match(p.get("title", ""), gt) for gt in entries[i]["gt_titles"]) for p in papers):
                hits += 1
        except Exception:
            errors += 1
        time.sleep(delay)
    return hits, errors


def run_config(cname, cfn, entries):
    """Run one config across ALL sources in parallel."""
    compressed = [cfn(e["query"]) for e in entries]
    avg_wc = sum(word_count(q) for q in compressed) / len(compressed)

    src_results = {}
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {
            pool.submit(test_one_source, sname, sfn, compressed, entries): sname
            for sname, sfn in SOURCES.items()
        }
        for f in as_completed(futures):
            sname = futures[f]
            hits, errors = f.result()
            src_results[sname] = (hits, errors)
            err_s = f"({errors}err)" if errors else ""
            print(f"    {cname:10s} {sname:12s} {hits}/{N}{err_s}", flush=True)

    parts = []
    for sname in SOURCES:
        h, e = src_results[sname]
        parts.append(f"{sname}={h}" + (f"({e}err)" if e else ""))
    print(f"  {cname:10s} avg_wc={avg_wc:>5.1f}  {' '.join(parts)}", flush=True)
    return cname, avg_wc, src_results


def main():
    queries = json.loads((DATA_DIR / "litsearch_queries.json").read_text())
    title_map = json.loads((DATA_DIR / "litsearch_title_map.json").read_text())
    entries = []
    for q in queries:
        gt = [title_map[str(c)] for c in q["corpusids"] if str(c) in title_map]
        if gt:
            entries.append({"query": q["query"], "gt_titles": gt})
    entries = entries[:N]

    print(f"Keyword Sensitivity Test ({N}q, {len(CONFIGS)} configs, {len(SOURCES)} sources)")
    print(f"Architecture: extract_keywords | KeyBERT (potion-8M) | ek->KeyBERT")
    print(f"Sources parallel per config, queries sequential per source")
    q0 = entries[0]["query"]
    print(f"\nQ0: {q0[:80]}")
    for cname, cfn in CONFIGS:
        c = cfn(q0)
        print(f"  {cname:10s} ({word_count(c):>2}w): {c[:70]}")
    print()

    all_results = {}
    for cname, cfn in CONFIGS:
        _, avg_wc, src_results = run_config(cname, cfn, entries)
        all_results[cname] = (avg_wc, src_results)

    print(f"\n{'='*80}")
    print(f"SUMMARY ({N}q)")
    print(f"{'='*80}")
    header = f"{'Config':10s} {'wc':>5} " + " ".join(f"{s:>10}" for s in SOURCES)
    print(header)
    print("-" * len(header))
    for cname, cfn in CONFIGS:
        avg_wc, src_results = all_results[cname]
        row = f"{cname:10s} {avg_wc:>5.1f} "
        for sname in SOURCES:
            h, e = src_results[sname]
            cell = f"{h}/{N}" if e == 0 else f"{h}/{N}e{e}"
            row += f"{cell:>10} "
        print(row)


if __name__ == "__main__":
    main()
