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


class TestInternalRelations:
    """Relations among the papers a user actually saved.

    A collection is not a random sample, so connections inside it are the ones
    worth drawing. Without them every note is an isolated node and the graph
    view shows a cloud of unconnected dots.
    """

    def _paper(self, title, abstract="", authors=None):
        return {"title": title, "abstract": abstract, "authors": authors or []}

    def test_abstract_naming_another_title_is_directional(self):
        papers = [
            self._paper("A Careful Study of Retrieval Augmented Generation Systems",
                        abstract="We extend Dense Passage Retrieval for Open Domain "
                                 "Question Answering with a reranking stage."),
            self._paper("Dense Passage Retrieval for Open Domain Question Answering"),
        ]
        relations = vault.build_internal_relations(papers)

        assert relations["A Careful Study of Retrieval Augmented Generation Systems"]["mentions"][0]["title"] \
            == "Dense Passage Retrieval for Open Domain Question Answering"
        assert relations["Dense Passage Retrieval for Open Domain Question Answering"]["mentioned_by"][0]["title"] \
            == "A Careful Study of Retrieval Augmented Generation Systems"

    def test_field_name_titles_do_not_become_hubs(self):
        """The failure this guards against was measured, not imagined.

        In a 451-paper RAG collection, the paper titled "Retrieval-Augmented
        Generation" matched 128 other abstracts, because that phrase is the
        name of the field rather than a citation. It and one other generic
        title produced almost every edge in the graph, and the largest
        connected component was held together entirely by them.
        """
        generic = "Retrieval Augmented Generation For Knowledge Tasks"
        papers = [self._paper(generic)] + [
            self._paper(f"Paper {i}",
                        abstract=f"We apply {generic.lower()} to a new domain.")
            for i in range(vault.MAX_MENTION_HITS + 3)
        ]
        relations = vault.build_internal_relations(papers)
        assert "mentioned_by" not in relations.get(generic, {})

    def test_a_few_mentions_still_count(self):
        title = "Retrieval Augmented Generation For Knowledge Tasks"
        papers = [self._paper(title)] + [
            self._paper(f"Paper {i}", abstract=f"We build on {title.lower()} here.")
            for i in range(2)
        ]
        relations = vault.build_internal_relations(papers)
        assert len(relations[title]["mentioned_by"]) == 2

    def test_short_titles_are_not_matched(self):
        """Below a few words a title is a phrase, and phrases recur in prose
        for reasons unrelated to citation.
        """
        papers = [
            self._paper("Segment Anything"),
            self._paper("Another Paper", abstract="we segment anything in the image"),
        ]
        assert vault.build_internal_relations(papers) == {}

    def test_shared_authors_link_both_ways(self):
        papers = [
            self._paper("First Paper", authors=["Ada Lovelace", "Alan Turing"]),
            self._paper("Second Paper", authors=["Ada Lovelace"]),
        ]
        relations = vault.build_internal_relations(papers)
        assert relations["First Paper"]["coauthored"][0]["title"] == "Second Paper"
        assert relations["Second Paper"]["coauthored"][0]["title"] == "First Paper"

    def test_hyperprolific_author_lists_are_skipped(self):
        """A paper with hundreds of authors would otherwise link to everything
        else that shares any one of them.
        """
        crowd = [f"Author {i}" for i in range(vault.MAX_AUTHORS_FOR_COAUTHOR_EDGE + 1)]
        papers = [
            self._paper("Consortium Report", authors=crowd),
            self._paper("Small Paper", authors=["Author 0"]),
        ]
        assert vault.build_internal_relations(papers) == {}

    def test_a_paper_never_links_to_itself(self):
        papers = [self._paper("Solo Paper", abstract="solo paper studies solo paper",
                              authors=["One Person"])]
        assert vault.build_internal_relations(papers) == {}


class TestCitationRelations:
    def test_builds_edges_from_reference_lists(self, monkeypatch):
        papers = [
            {"title": "Citing Work", "doi": "10.1/a", "authors": []},
            {"title": "Cited Work", "doi": "10.1/b", "authors": []},
        ]
        monkeypatch.setattr(vault.s2_client, "get_references",
                            lambda pid, limit=100:
                            [{"title": "Cited Work"}] if pid == "10.1/a" else [])

        relations = vault.build_citation_relations(papers, max_workers=1)
        assert relations["Citing Work"]["foundations"][0]["title"] == "Cited Work"
        assert relations["Cited Work"]["descendants"][0]["title"] == "Citing Work"

    def test_references_outside_the_collection_are_ignored(self, monkeypatch):
        """Only edges between saved papers are drawable; a reference to a paper
        with no note would produce a wikilink to nothing.
        """
        papers = [{"title": "Citing Work", "doi": "10.1/a", "authors": []}]
        monkeypatch.setattr(vault.s2_client, "get_references",
                            lambda pid, limit=100: [{"title": "Some Paper Elsewhere"}])
        assert vault.build_citation_relations(papers, max_workers=1) == {}

    def test_a_failed_lookup_does_not_lose_the_others(self, monkeypatch):
        papers = [
            {"title": "Broken", "doi": "10.1/x", "authors": []},
            {"title": "Working", "doi": "10.1/a", "authors": []},
            {"title": "Cited Work", "doi": "10.1/b", "authors": []},
        ]

        def refs(pid, limit=100):
            if pid == "10.1/x":
                raise RuntimeError("S2 down")
            return [{"title": "Cited Work"}] if pid == "10.1/a" else []

        monkeypatch.setattr(vault.s2_client, "get_references", refs)
        relations = vault.build_citation_relations(papers, max_workers=1)
        assert "Broken" not in relations
        assert relations["Working"]["foundations"][0]["title"] == "Cited Work"

    def test_titles_match_across_spellings(self, monkeypatch):
        """Sources disagree on capitalisation and punctuation, so an exact
        comparison would miss most real edges.
        """
        papers = [
            {"title": "Citing Work", "doi": "10.1/a", "authors": []},
            {"title": "Attention Is All You Need", "doi": "10.1/b", "authors": []},
        ]
        monkeypatch.setattr(vault.s2_client, "get_references",
                            lambda pid, limit=100:
                            [{"title": "Attention is all you need."}] if pid == "10.1/a" else [])
        relations = vault.build_citation_relations(papers, max_workers=1)
        assert relations["Citing Work"]["foundations"][0]["title"] == "Attention Is All You Need"

    def test_export_leaves_citations_alone_unless_asked(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", str(tmp_path))
        monkeypatch.setattr(vault.s2_client, "get_references",
                            lambda *a, **kw: pytest.fail("should not call S2"))
        vault.export_collection([{"title": "A Paper", "authors": []}], collection="c")
