# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Tests for arXiv fallback client."""

import pytest

from scholar_mcp.sources import arxiv


@pytest.mark.integration
def test_search_papers():
    results = arxiv.search_papers("transformer", max_results=3)
    assert len(results) > 0
    paper = results[0]
    assert paper["source"] == "arxiv"
    assert paper["is_open_access"] is True
    assert "arxiv.org/pdf/" in paper["open_access_url"]


def test_get_pdf_url():
    url = arxiv.get_pdf_url("1706.03762")
    assert url == "https://arxiv.org/pdf/1706.03762.pdf"
