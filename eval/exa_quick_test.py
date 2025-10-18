"""Exa baseline comparison on LitSearch queries.

Usage:
    python eval/exa_quick_test.py --n 50
    python eval/exa_quick_test.py --n 50 --category "research paper"
"""
import json, sys, time, argparse, logging, re, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.getLogger("httpx").setLevel(logging.WARNING)

from exa_py import Exa
from matching import normalize_title, titles_match, find_gt_rank, compute_metrics

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = Path(__file__).parent / "cache"

EXA_API_KEY = "f05e6c56-3086-4cd7-a34d-96e86d27c90b"


def clean_exa_title(title: str) -> str:
    if not title:
        return ""
    t = re.sub(r'^\[[\d.]+\]\s*', '', title)
    t = re.sub(r'^\[PDF\]\s*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^Paper page\s*[-|]\s*', '', t)
    t = re.sub(r'\s*[|]\s*https?://\S+', '', t)
    t = re.sub(r'\s*[-|]\s*(arXiv|arxiv\.org|NIPS|NeurIPS|OpenReview|Semantic Scholar|ACL Anthology|ADS|Papers With Code|Hugging Face|PMLR|AAAI|IJCAI|IEEE|ACM).*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*[-|]\s*$', '', t)
    return t.strip()


def extract_arxiv_id(url: str) -> str | None:
    m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', url)
    return m.group(1) if m else None


_arxiv_title_cache: dict[str, str] = {}

def resolve_missing_titles(papers: list[dict]) -> list[dict]:
    """For papers with no title but an arxiv URL, batch resolve via S2."""
    import httpx
    need = [p["arxiv_id"] for p in papers if not p["title"] and p.get("arxiv_id") and p["arxiv_id"] not in _arxiv_title_cache]
    need = list(set(need))
    if need:
        s2_key = os.environ.get("S2_API_KEY", "")
        headers = {"x-api-key": s2_key} if s2_key else {}
        for i in range(0, len(need), 50):
            batch = need[i:i+50]
            ids = [f"ArXiv:{aid}" for aid in batch]
            try:
                resp = httpx.post("https://api.semanticscholar.org/graph/v1/paper/batch",
                                  json={"ids": ids}, params={"fields": "title"},
                                  headers=headers, timeout=10)
                if resp.status_code == 200:
                    for aid, paper in zip(batch, resp.json()):
                        if paper and paper.get("title"):
                            _arxiv_title_cache[aid] = paper["title"]
            except Exception:
                pass
            time.sleep(0.3)
    for p in papers:
        if not p["title"] and p.get("arxiv_id") and p["arxiv_id"] in _arxiv_title_cache:
            p["title"] = _arxiv_title_cache[p["arxiv_id"]]
    return papers


def load_litsearch(n=None):
    queries = json.loads((DATA_DIR / "litsearch_queries.json").read_text())
    title_map = json.loads((DATA_DIR / "litsearch_title_map.json").read_text())
    entries = []
    for q in queries:
        gt_titles = [title_map[str(c)] for c in q["corpusids"] if str(c) in title_map]
        if gt_titles:
            entries.append({"query": q["query"], "gt_titles": gt_titles,
                            "metadata": {"query_set": q.get("query_set", ""), "specificity": q.get("specificity", 0)}})
    return entries[:n] if n else entries


def search_exa(exa, query, limit=20, category=None):
    kwargs = {"query": query, "num_results": limit, "type": "auto"}
    if category:
        kwargs["category"] = category
    try:
        results = exa.search(**kwargs)
        papers = []
        for r in results.results:
            raw_title = r.title or ""
            cleaned = clean_exa_title(raw_title)
            papers.append({
                "title": cleaned,
                "raw_title": raw_title,
                "url": r.url or "",
                "arxiv_id": extract_arxiv_id(r.url or ""),
                "score": r.score if hasattr(r, 'score') else 0,
            })
        return papers
    except Exception as e:
        print(f"  Exa error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--category", type=str, default="research paper")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--cache-file", type=str, default=None)
    args = parser.parse_args()

    exa = Exa(api_key=EXA_API_KEY)
    entries = load_litsearch(args.n)
    print(f"Loaded {len(entries)} queries")

    cache_file = Path(args.cache_file) if args.cache_file else CACHE_DIR / "exa_baseline.jsonl"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    cached = set()
    if cache_file.exists():
        for line in cache_file.read_text().strip().split("\n"):
            if line.strip():
                cached.add(json.loads(line)["query_idx"])
        print(f"Resuming: {len(cached)} already cached")

    hits = 0
    total = 0
    for i, entry in enumerate(entries):
        if i in cached:
            continue

        query = entry["query"]
        papers = search_exa(exa, query, limit=args.limit, category=args.category)
        papers = resolve_missing_titles(papers)
        gt_rank = find_gt_rank(papers, entry["gt_titles"])

        total += 1
        if gt_rank:
            hits += 1
        status = f"HIT@{gt_rank}" if gt_rank else "MISS"
        print(f"Q{i}: {status} ({hits}/{total}) | {query[:70]}")

        record = {
            "query_idx": i,
            "query": query,
            "gt_titles": entry["gt_titles"],
            "exa_titles": [p["title"] for p in papers[:20]],
            "exa_urls": [p["url"] for p in papers[:20]],
            "gt_rank": gt_rank,
            "n_results": len(papers),
            "metadata": entry.get("metadata", {}),
        }

        with open(cache_file, "a") as f:
            f.write(json.dumps(record) + "\n")

        time.sleep(args.delay)

    all_results = []
    for line in cache_file.read_text().strip().split("\n"):
        if line.strip():
            all_results.append(json.loads(line))

    metrics = compute_metrics(all_results)

    print(f"\n{'='*60}")
    print(f"Exa Baseline (n={len(all_results)}, category={args.category})")
    print(f"{'='*60}")
    for k, v in metrics.items():
        if k == "n":
            continue
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")

    print(f"\n{'System':<25} {'R@5':>8} {'R@10':>8} {'R@20':>8} {'MRR':>8}")
    print(f"{'-'*57}")
    print(f"{'Exa (this run)':<25} {metrics.get('R@5',0):>8.3f} {metrics.get('R@10',0):>8.3f} {metrics.get('R@20',0):>8.3f} {metrics.get('MRR',0):>8.3f}")
    print(f"{'Scholar-MCP v0.7':<25} {'TBD':>8} {'TBD':>8} {'TBD':>8} {'TBD':>8}")
    print(f"{'Google Search (paper)':<25} {'0.231':>8} {'---':>8} {'---':>8} {'---':>8}")
    print(f"{'Google Scholar (paper)':<25} {'0.205':>8} {'---':>8} {'---':>8} {'---':>8}")


if __name__ == "__main__":
    main()
