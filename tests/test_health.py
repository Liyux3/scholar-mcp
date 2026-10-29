"""Health names what broke from the call record, and the probe asks every link directly."""
import json

import pytest

from scholar_mcp import config, health, sources, usage_log


@pytest.mark.parametrize('downloaded', [True, False])
def test_arxiv_pdf_health_checks_its_native_route(monkeypatch, downloaded):
    from scholar_mcp import pdf_utils
    from scholar_mcp.sources import europepmc
    seen = []
    def download(url, directory, filename):
        seen.append((url, filename))
        return f'{directory}/{filename}' if downloaded else None
    monkeypatch.setattr(pdf_utils, '_try_download', download)
    monkeypatch.setattr(pdf_utils, 'download_paper', lambda *a: pytest.fail('generic fallback masks native health'))
    monkeypatch.setattr(europepmc, 'pmc_pdf_urls', lambda _: ['dataset', 'web'])
    monkeypatch.setattr(config, 'get_openalex_email', lambda: '')
    monkeypatch.setattr(sources, 'resolve_pdf_candidates', lambda _: [('europepmc', 'pdf')])
    assert health._probe_pdf()['arxiv_download'] is downloaded
    assert seen == [('https://arxiv.org/pdf/1706.03762.pdf', 'arxiv.pdf')]


def calls(source: str, *answers, **extra) -> list[dict]:
    return [{"tool": "search_papers", "ms": 100, "sources": {source: list(answer)}, **extra} for answer in answers]


def kinds(rows) -> dict[str, str]:
    return {finding["link"]: finding["kind"] for finding in health.findings(rows)}


def test_a_healthy_history_has_no_findings():
    rows = calls("arxiv", *[("ok", 800, 20, 0.9)] * 30) + calls("pubmed", *[("empty", 300, 0)] * 4)
    assert health.findings(rows) == []


def test_a_failing_source_is_named_with_its_last_error():
    rows = calls("dblp", *[("ok", 900, 10, 0.0)] * 10, *[("error", 50, 0)] * 6)
    rows[-1]["errors"] = {"dblp": "HTTPStatusError: 403"}
    [finding] = health.findings(rows)
    assert finding["kind"] == "failing" and finding["last_error"] == "HTTPStatusError: 403"
    assert "6 of its last 10" in finding["detail"]


def test_three_failures_in_a_row_count_even_after_a_long_good_run():
    rows = calls("crossref", *[("ok", 900, 10, 0.5)] * 20, *[("timeout", 30000, 0)] * 3)
    assert kinds(rows) == {"crossref": "failing"}


def test_a_source_held_back_by_its_own_limit_is_not_called_broken():
    rows = calls("openaire", *[("ok", 900, 10, 0.5)] * 4, *[("paced", 2, 0)] * 6)
    [finding] = health.findings(rows)
    assert finding["kind"] == "paced" and "6 of its last 10" in finding["detail"]


def test_a_source_that_went_quiet_is_told_apart_from_one_that_never_answered():
    went_quiet = calls("zbmath", *[("ok", 900, 10, 0.9)] * 5, *[("empty", 900, 0)] * 8)
    never = calls("hal", *[("empty", 900, 0)] * 10)
    assert kinds(went_quiet + never) == {"zbmath": "silent", "hal": "never_answered"}


def test_lost_abstracts_and_slower_answers_are_changes_against_the_source_itself():
    rows = calls("openalex", *[("ok", 1000, 20, 0.9)] * 10, *[("ok", 25000, 20, 0.0)] * 10)
    found = {finding["kind"] for finding in health.findings(rows)}
    assert found == {"lost_abstracts", "slower"}
    always_bare = calls("dblp", *[("ok", 900, 10, 0.0)] * 30)
    assert health.findings(always_bare) == []


def test_reranker_fallback_pdf_misses_and_exceptions():
    rows = [{"tool": "search_papers", "reranker": {"provider": "dashscope"}},
            {"tool": "search_papers", "reranker": {"provider": "flashrank", "fallback_reason": "account in arrearage"}}]
    rows += [{"tool": "read_paper", "pdf": "none"}] * 5
    rows += [{"tool": "read_paper", "exception": "KeyError", "where": "paper_reader.py:412"}] * 2
    found = {finding["kind"]: finding["detail"] for finding in health.findings(rows)}
    assert "arrearage" in found["fallback"] and "last 5" in found["no_pdf"]
    assert found["exception"] == "raised KeyError at paper_reader.py:412 (2x in the last 100 calls)"
    unconfigured = [{"tool": "search_papers", "reranker": {"provider": "flashrank", "fallback_reason": "not configured"}}]
    assert health.findings(unconfigured) == []


def test_records_from_before_counts_were_kept_still_read():
    rows = calls("arxiv", *[("ok", 800)] * 12)
    assert health.findings(rows) == [] and health._answers(rows)["arxiv"][0] == ("ok", 800, None, None)


def test_report_tables_each_link(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "on")
    for status, ms, count in (("ok", 1000, 5), ("timeout", 3000, 0), ("ok", 2000, 5)):
        with usage_log.call("search_papers", "", {}):
            usage_log.note_source("europepmc", status, ms, count, 1.0 if count else None)
    report = health.report()
    assert report["calls"] == 3 and report["findings"] == []
    assert report["sources"]["europepmc"] == {"answers": 3, "failed": 0.33, "empty": 0.0, "p90_s": 3.0,
                                              "abstracts": 1.0}


@pytest.fixture
def fleet(monkeypatch):
    def search(query, limit, **kwargs):
        return [{"title": "A", "abstract": "x"}]

    fleet = [sources.Source("good", search=search, get_citations=lambda pid, limit, title="": [{"title": "C"}]),
             sources.Source("quiet", search=lambda query, limit, **kwargs: []),
             sources.Source("down", search=lambda query, limit, **kwargs: (_ for _ in ()).throw(
                 RuntimeError("503 for https://api.example.org/?api_key=SECRET")))]
    monkeypatch.setattr(sources, "search_sources", lambda: fleet)
    monkeypatch.setattr(sources, "citation_sources", lambda: fleet[:1])
    monkeypatch.setattr(sources, "reference_sources", lambda: [])
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", None)
    monkeypatch.setattr(config, "RERANK_URL", "")
    monkeypatch.setattr(health, "_probe_reranker", lambda: {"provider": "flashrank", "working": False,
                                                             "reason": "account in arrearage"})
    monkeypatch.setattr(health, "_probe_pdf", lambda: {"arxiv_download": True, "pmc_dataset": False,
                                                        "unpaywall": "not configured", "repositories": ["doaj"]})
    return fleet


def test_probe_names_every_broken_link_and_leaves_a_record(fleet, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "on")
    result = health.probe()
    assert result["problems"] == {"down": "error", "quiet": "empty", "reranker": "account in arrearage",
                                  "pdf/pmc_dataset": "failed"}
    assert result["links"]["good/citations"]["count"] == 1 and result["links"]["good"]["abstracts"] == 1.0
    assert "SECRET" not in json.dumps(result) and "<url>" in result["links"]["down"]["error"]
    row = json.loads((tmp_path / "usage" / "calls.jsonl").read_text())
    assert row["tool"] == "probe" and row["sources"]["quiet"] == ["empty", row["sources"]["quiet"][1], 0]
    assert "SECRET" not in json.dumps(row)


def test_probe_skips_named_sources(fleet):
    assert set(health.probe({"down", "quiet"})["links"]) == {"good", "good/citations"}


def test_the_probe_spends_nothing_that_real_use_already_shows(fleet, monkeypatch):
    """A link real use saw working today, a billed source and a billed reranker are not asked again."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = [{"tool": "search_papers", "at": now, "sources": {"good": ["ok", 900, 5, 1.0]},
               "reranker": {"provider": "flashrank", "fallback_reason": "account in arrearage"}}]
    monkeypatch.setattr(health.usage_log, "read", lambda: record)
    fleet.append(sources.Source("billed", search=lambda query, limit, **kwargs: 1 / 0, paid=True))
    monkeypatch.setattr(sources, "all_sources", lambda: fleet)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "key")
    monkeypatch.setattr(health, "_probe_reranker", lambda: 1 / 0)
    result = health.probe({"down", "quiet"})
    assert set(result["links"]) == {"good/citations"}
    assert result["not_probed"] == {"in use": ["good"], "billed": ["billed"]}
    assert result["reranker"]["seen"] == "in real use" and result["problems"]["reranker"] == "account in arrearage"


def test_a_recent_failure_retires_an_older_success_vouch():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    rows = [{'at': now, 'sources': {'changed': ['ok', 100, 3], 'steady': ['ok', 100, 2]}},
            {'at': now, 'sources': {'changed': ['error', 100, 0], 'incomplete': ['partial', 100, 0]}}]
    assert health._vouched(rows) == {'steady'}


def test_a_hanging_link_counts_as_timed_out_without_holding_the_probe(fleet, monkeypatch):
    import threading
    import time
    release = threading.Event()
    fleet.append(sources.Source("hangs", search=lambda query, limit, **kwargs: release.wait(10) and []))
    monkeypatch.setattr(health, "PROBE_BUDGET_S", 0.3)
    t0 = time.monotonic()
    result = health.probe({"down", "quiet"})
    release.set()
    assert time.monotonic() - t0 < 2 and result["problems"]["hangs"] == "timeout"


def test_empty_citation_and_pdf_answers_are_ordinary():
    rows = calls("zenodo/pdf", *[("empty", 300, 0)] * 12) + calls("openalex/citations", *[("ok", 900, 5)] * 5, *[("empty", 900, 0)] * 9)
    assert health.findings(rows) == []
