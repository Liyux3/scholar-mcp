"""Tests for the Obsidian vault export.

The vault is the human-facing projection of the paper graph, so what matters
is that links resolve, that hand-written notes survive a refresh, and that
titles from disagreeing sources do not produce phantom edges.
"""

from scholar_mcp import vault


def _paper(title="A Paper", year=2020, cites=10, abstract="An abstract."):
    return {"title": title, "year": year, "citation_count": cites,
            "abstract": abstract, "authors": ["Author One"],
            "external_ids": {"DOI": "10.1234/abc"}, "venue": "NeurIPS",
            "url": "https://example.com", "source": "openalex"}


class TestNoteName:
    def test_strips_filesystem_unsafe_characters(self):
        assert "/" not in vault.note_name("Retrieval/Generation: A Survey")
        assert ":" not in vault.note_name("Retrieval/Generation: A Survey")

    def test_collapses_whitespace(self):
        assert vault.note_name("Deep   Residual\nLearning") == "Deep Residual Learning"

    def test_truncates_very_long_titles(self):
        assert len(vault.note_name("x" * 400)) <= 120

    def test_empty_title_is_still_usable(self):
        assert vault.note_name("") == "untitled"

    def test_is_stable_across_calls(self):
        """Wikilinks resolve by filename, so this must not vary."""
        title = "Attention Is All You Need"
        assert vault.note_name(title) == vault.note_name(title)


class TestRenderNote:
    def test_includes_frontmatter(self):
        note = vault.render_note(_paper(title="Test Paper", year=2021))
        assert note.startswith("---")
        assert 'title: "Test Paper"' in note
        assert "year: 2021" in note

    def test_quotes_titles_containing_colons(self):
        """An unquoted colon makes the YAML frontmatter unparseable."""
        note = vault.render_note(_paper(title="BERT: Pre-training of Transformers"))
        assert 'title: "BERT: Pre-training of Transformers"' in note

    def test_renders_relations_as_wikilinks(self):
        note = vault.render_note(_paper(), {
            "peers": [{"title": "Deep Residual Learning", "_relation_strength": 19}]})
        assert "## Cited alongside" in note
        assert "- [[Deep Residual Learning]] (19x)" in note

    def test_omits_self_links(self):
        """A paper reached through its own relations must not link to itself."""
        note = vault.render_note(_paper(title="Attention is All you Need"), {
            "peers": [{"title": "Attention Is All You Need", "_relation_strength": 19}]})
        assert "## Cited alongside" not in note

    def test_deduplicates_title_spellings(self):
        note = vault.render_note(_paper(), {"peers": [
            {"title": "Deep Residual Learning", "_relation_strength": 24},
            {"title": "deep residual learning", "_relation_strength": 3},
        ]})
        assert note.count("[[Deep Residual Learning]]") == 1
        assert "deep residual learning" not in note.replace("Deep Residual Learning", "")

    def test_empty_relation_section_is_skipped(self):
        note = vault.render_note(_paper(), {"peers": [], "kin": []})
        assert "## Cited alongside" not in note

    def test_always_ends_with_a_notes_section(self):
        assert vault.NOTES_MARKER in vault.render_note(_paper())


class TestWriteNote:
    def test_preserves_handwritten_notes_on_refresh(self, tmp_path, monkeypatch):
        """Re-exporting must not destroy what the user wrote."""
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        paper = _paper(title="Persistent Paper")

        path = vault.write_note(paper, "c")
        path.write_text(path.read_text() + "\nMy own reading notes here.\n")

        vault.write_note({**paper, "citation_count": 999}, "c")
        refreshed = path.read_text()
        assert "My own reading notes here." in refreshed
        assert "citations: 999" in refreshed, "metadata should still refresh"

    def test_writes_into_a_per_collection_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        path = vault.write_note(_paper(), "my-collection")
        assert path.parent.name == "my-collection"
        assert path.suffix == ".md"


class TestExportCollection:
    def test_writes_a_note_per_paper_plus_an_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        papers = [_paper(title=f"Paper {i}", cites=i) for i in range(3)]

        result = vault.export_collection(papers, "c")
        assert result["notes_written"] == 3

        files = {p.name for p in (tmp_path / "c").glob("*.md")}
        assert "_index.md" in files
        assert len(files) == 4

    def test_index_is_ordered_by_citations(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        vault.export_collection([_paper(title="Low", cites=1),
                                 _paper(title="High", cites=500)], "c")
        index = (tmp_path / "c" / "_index.md").read_text()
        assert index.index("[[High]]") < index.index("[[Low]]")

    def test_skips_untitled_papers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        result = vault.export_collection([_paper(), {"title": ""}], "c")
        assert result["notes_written"] == 1
