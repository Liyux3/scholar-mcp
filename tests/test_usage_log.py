"""The local call record stays small, holds no research content by default and never breaks a call."""
import json

import pytest

from scholar_mcp import config, server, sources, usage_log


@pytest.fixture
def record_to(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "on")
    return tmp_path / "usage" / "calls.jsonl"


def test_a_call_leaves_one_line_without_the_query(record_to):
    with usage_log.call("search_papers", "codex", {"query": "secret topic words", "limit": 5, "year": "2024"}) as record:
        usage_log.note(sources={"arxiv": ["ok", 812]})
    row = json.loads(record_to.read_text())
    assert row["tool"] == "search_papers" and row["client"] == "codex" and row["ms"] >= 0
    assert row["args"] == {"limit": 5, "year": "2024", "query_words": 3}
    assert "secret" not in record_to.read_text()
    assert row["sources"] == {"arxiv": ["ok", 812]} and record is not None


def test_full_mode_keeps_arguments_and_off_writes_nothing(record_to, monkeypatch):
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "full")
    with usage_log.call("read_paper", "", {"paper_id": "arXiv:1", "pages": "1-10"}):
        pass
    assert json.loads(record_to.read_text())["args"] == {"paper_id": "arXiv:1", "pages": "1-10"}
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "off")
    with usage_log.call("read_paper", "", {}):
        pass
    assert len(record_to.read_text().splitlines()) == 1


def test_the_record_is_bounded(record_to, monkeypatch):
    monkeypatch.setattr(usage_log, "MAX_BYTES", 2_000)
    for _ in range(200):
        with usage_log.call("paper_info", "claude-code", {"include": "detail"}):
            pass
    files = sorted(p.name for p in record_to.parent.iterdir() if p.suffix != ".lock")
    assert files == ["calls.jsonl", "calls.jsonl.1"]
    assert sum(p.stat().st_size for p in record_to.parent.iterdir()) < 5_000


def test_tool_calls_are_recorded_with_sources_and_reranker(record_to, monkeypatch):
    def pipeline(*args, **kwargs):
        flow = sources.SourceFlow({
            "arxiv": [lambda: [{"title": "A paper", "_rerank_score": 1.0}]],
            "dblp": [lambda: (_ for _ in ()).throw(RuntimeError("503 for https://dblp.org/search?key=x"))],
        }, 2.0, hydrate=False)
        papers, reports = [], []
        server._fold_results(flow.collect(), papers, reports)
        return papers, reports

    monkeypatch.setattr(server, "_pipeline", pipeline)
    monkeypatch.setattr(server.metadata, "hydrate", lambda papers, fields=None: papers)
    monkeypatch.setattr(server.relevance, "rank_final", lambda papers: papers)
    import asyncio
    adapter = asyncio.run(server.mcp.get_tool("search_papers")).fn
    adapter(query="anything at all")
    row = json.loads(record_to.read_text())
    assert row["tool"] == "search_papers" and row["n"] == 1
    assert row["sources"]["arxiv"][::2] == ["ok", 1] and row["sources"]["arxiv"][3] == 0.0
    assert row["sources"]["dblp"][0] == "error"
    assert row["errors"] == {"dblp": "RuntimeError: 503 for <url>"} and "provider" in row["reranker"]


def test_a_raising_call_records_where_it_raised(record_to):
    from scholar_mcp import health
    with pytest.raises(ValueError):
        with usage_log.call("paper_info", "", {}):
            health._answers([{"sources": {"broken": []}}])
    row = json.loads(record_to.read_text())
    assert row["exception"] == "ValueError" and row["where"].startswith("health.py:")


def test_a_broken_record_never_breaks_the_call(record_to, monkeypatch):
    monkeypatch.setattr(usage_log, "_append", lambda line: (_ for _ in ()).throw(OSError("disk full")))
    with usage_log.call("search_papers", "", {}):
        value = 42
    assert value == 42


def test_a_damaged_line_costs_only_itself(record_to):
    record_to.parent.mkdir(parents=True)
    record_to.write_text('{"tool": "a"}\n{"tool": "b", "ms\n{"tool": "c"}\n')
    assert [row["tool"] for row in usage_log.read()] == ["a", "c"]


def test_processes_sharing_the_file_lose_no_records(record_to, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(usage_log, "MAX_BYTES", 4_000)
    script = ("import sys; from scholar_mcp import config, usage_log; config.DATA_DIR = sys.argv[1]; usage_log.MAX_BYTES = 4000\n"
              "for i in range(60):\n    with usage_log.call('paper_info', sys.argv[2], {}): pass")
    writers = [subprocess.Popen([sys.executable, "-c", script, str(record_to.parent.parent), f"p{n}"],
                                env={**__import__("os").environ, "SCHOLAR_USAGE_LOG": "on"}) for n in range(3)]
    assert all(writer.wait(60) == 0 for writer in writers)
    rows = usage_log.read(limit=10_000)
    # Rotation keeps the newest records; every line that remains is whole.
    assert rows and all(row["tool"] == "paper_info" for row in rows)
    assert sum(1 for line in record_to.read_text().splitlines() if line) == len(
        [line for line in record_to.read_text().splitlines() if line.startswith("{") and line.endswith("}")])
