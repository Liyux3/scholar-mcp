"""Keyword compression sensitivity test.

Sweeps word count (3-15 + raw) for each keyword source, using extract_keywords as compressor.
Also tests KeyBERT and extract_keywords -> KeyBERT pipeline at their best word counts.
One source at a time, 1s delay between queries.

Usage:
    OPENALEX_API_KEY=... SCOPUS_API_KEY=... python eval/keyword_sensitivity.py
"""
import json, time, sys, os, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from scholar_mcp import openalex_client, arxiv_client, crossref_client, scopus_client, doaj_client
from scholar_mcp import relevance, config
from matching import titles_match

config.SCOPUS_API_KEY = os.environ.get("SCOPUS_API_KEY", "")
config.OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")

DATA_DIR = Path(__file__).parent / "data"
N = 20
DELAY = 1.0

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
    CONFIGS.append((f"ek15_kb{t}", lambda q, t=t: compress_ek_kb(q, 15, t)))
CONFIGS.append(("raw", lambda q: q))


def run_test(src_name, search_fn, entries):
    print(f"\n--- {src_name} ---")
    for cname, cfn in CONFIGS:
        hits = 0
        errors = 0
        for i, e in enumerate(entries):
            q = cfn(e["query"])
            try:
                papers = search_fn(q, 100)
                if any(any(titles_match(p.get("title", ""), gt) for gt in e["gt_titles"]) for p in papers):
                    hits += 1
            except Exception:
                errors += 1
            time.sleep(DELAY)
        avg_wc = sum(word_count(cfn(e["query"])) for e in entries) / len(entries)
        err_s = f" ({errors}err)" if errors else ""
        print(f"  {cname:10s} avg_words={avg_wc:>5.1f}  hits={hits:>2}/{N}{err_s}", flush=True)


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
    print(f"Compressor: extract_keywords (rule-based), KeyBERT (potion-8M), combo")
    q0 = entries[0]["query"]
    print(f"\nQ0: {q0[:80]}")
    for cname, cfn in CONFIGS:
        c = cfn(q0)
        print(f"  {cname:10s} ({word_count(c):>2}w): {c[:70]}")

    for src_name, search_fn in SOURCES.items():
        run_test(src_name, search_fn, entries)

    print("\nDone.")


if __name__ == "__main__":
    main()
