"""Quick smoke test for scholar-mcp server.

Run after installing or updating to verify all tools work.
Usage: S2_API_KEY=... uv run python scripts/smoke_test.py
"""

import json
import sys
import time

sys.path.insert(0, ".")
from scholar_mcp import server


def test(name, fn):
    try:
        t0 = time.time()
        result = fn()
        elapsed = time.time() - t0
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        ok = "error" not in (result if isinstance(result, dict) else {})
        status = "OK" if ok else "WARN"
        print(f"  [{status}] {name} ({elapsed:.1f}s)")
        if not ok and isinstance(result, dict):
            print(f"       {result.get('error', '')[:60]}")
        return ok
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {str(e)[:60]}")
        return False


print("Scholar-MCP Smoke Test")
print("=" * 40)

passed = 0
total = 0

total += 1
if test("search_papers", lambda: server.search_papers("machine learning", limit=2)):
    passed += 1

time.sleep(2)
total += 1
if test("get_paper (ArXiv)", lambda: server.get_paper("ArXiv:2201.11903")):
    passed += 1

time.sleep(2)
total += 1
if test("get_citations", lambda: server.get_citations("ArXiv:2201.11903", limit=2)):
    passed += 1

time.sleep(2)
total += 1
if test("get_references", lambda: server.get_references("ArXiv:2201.11903", limit=2)):
    passed += 1

time.sleep(2)
total += 1
if test("recommend_papers", lambda: server.recommend_papers("ArXiv:2201.11903", limit=2)):
    passed += 1

total += 1
if test("search_openreview", lambda: server.search_openreview("transformer", limit=2)):
    passed += 1

total += 1
if test("build_paper_graph", lambda: server.build_paper_graph("chain of thought prompting", max_hops=1, max_papers=5)):
    passed += 1

total += 1
if test("save_papers", lambda: server.save_papers("Attention Is All You Need", collection="_smoke_test")):
    passed += 1

total += 1
if test("list_saved_papers", lambda: server.list_saved_papers(collection="_smoke_test")):
    passed += 1

# Cleanup
from scholar_mcp import knowledge_base as kb
kb.remove_collection("_smoke_test")

print()
print(f"Result: {passed}/{total} passed")
if passed == total:
    print("All good!")
else:
    print(f"  {total - passed} tool(s) need attention")
