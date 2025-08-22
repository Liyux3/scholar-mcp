"""Quick Exa vs OA+arXiv comparison on first 20 LitSearch queries.
Uses Exa MCP via subprocess calling the exa web_search_exa tool."""
import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)

from datasets import load_dataset
from scholar_mcp import openalex_client, arxiv_client, relevance

GT_CACHE_FILE = Path(__file__).parent / "gt_papers_cache.json"

def norm(t):
    if not t: return ''
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', '', t.lower().strip()))

def match_title(title, cids, gt_cache):
    rt = norm(title)
    for cid in cids:
        gt = gt_cache.get(str(cid))
        if not gt: continue
        gt_t = norm(gt.get('title',''))
        if gt_t and rt and (gt_t == rt or (len(min(gt_t,rt,key=len))>20 and min(gt_t,rt,key=len) in max(gt_t,rt,key=len))):
            return True
    return False

def main():
    ds = load_dataset("princeton-nlp/LitSearch", "query", split="full")
    gt_cache = json.loads(GT_CACHE_FILE.read_text())

    # We'll print results for manual Exa testing
    n = 20
    for i in range(n):
        q = ds[i]
        gt_titles = []
        for cid in q['corpusids']:
            gt = gt_cache.get(str(cid))
            if gt: gt_titles.append(gt['title'])

        # OA+arXiv search
        query = relevance.optimize_query(q['query'])
        oa_r = openalex_client.search_papers(query, limit=20)
        try:
            ax_r = arxiv_client.search_papers(query, max_results=20)
        except:
            ax_r = []
        all_r = oa_r + ax_r

        oa_hit = any(match_title(p.get('title',''), q['corpusids'], gt_cache) for p in all_r)
        oa_rank = None
        for rank, p in enumerate(all_r):
            if match_title(p.get('title',''), q['corpusids'], gt_cache):
                oa_rank = rank + 1
                break

        print(f"Q{i}: {q['query'][:80]}")
        print(f"  GT: {[t[:50] for t in gt_titles]}")
        print(f"  OA+arXiv: {'HIT@'+str(oa_rank) if oa_rank else 'MISS'}")
        print(f"  [Test with Exa: search for academic paper about: {query[:60]}]")
        print()

        time.sleep(0.5)

if __name__ == "__main__":
    main()
