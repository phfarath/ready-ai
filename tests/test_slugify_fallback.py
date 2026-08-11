"""
Tests for the _slugify fallback behaviour (VAL-QUAL-008).

Non-word titles (emoji, punctuation only, empty) previously produced an
empty slug, which caused file collisions and broken output paths when
exporting docs.  _slugify must now always return a non-empty slug,
falling back to ``"untitled"`` when the title contains no usable words.
"""

import pytest

from src.docs.export import _slugify


class TestSlugifyFallback:
    def test_emoji_title_returns_nonempty(self):
        """VAL-QUAL-008: emoji-only titles must yield a non-empty slug."""
        result = _slugify("🎉🎉🎉")
        assert result, "expected non-empty slug for emoji-only title"
        assert result == "untitled"

    def test_hello_world_unchanged(self):
        """Regression guard: a normal title still slugifies as before."""
        assert _slugify("Hello World") == "hello-world"

    def test_empty_string_fallback(self):
        """An empty title falls back to 'untitled'."""
        assert _slugify("") == "untitled"

    def test_punctuation_only_fallback(self):
        """A title made only of non-word characters falls back to 'untitled'."""
        assert _slugify("---") == "untitled"
        assert _slugify("!@#$%") == "untitled"

    def test_normal_title_with_punctuation_still_slugified(self):
        """Punctuation inside an otherwise-normal title is stripped, not
        turned into the fallback."""
        assert _slugify("Hello, World!") == "hello-world"

    @pytest.mark.parametrize(
        "title",
        [
            "🎉🎉🎉",
            "",
            "---",
            "!@#$%",
            "😀😀",
            "   ",
            "。。。",
        ],
    )
    def test_slug_never_empty(self, title):
        """Any input — including pathological ones — must produce a slug
        that is truthy (non-empty)."""
        assert _slugify(title)


class TestSlugifyExportIntegration:
    """Two non-word-title docs must not collapse to an empty (``.md``)
    filename — the slug fallback guarantees a usable, non-empty base name
    for every exported page."""

    def _write_doc(self, run_dir, title):
        run_dir.mkdir(parents=True, exist_ok=True)
        docs_md = run_dir / "docs.md"
        docs_md.write_text(f"# {title}\n\nSome content.\n")
        (run_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        return docs_md

    def test_two_nonword_docusaurus_exports_have_nonempty_names(self, tmp_path):
        """Each non-word-title doc gets a real (non-empty) filename instead
        of a literal ``.md`` collision."""
        from src.docs.export import _export_docusaurus

        doc_a = self._write_doc(tmp_path / "a", "🎉🎉🎉")
        doc_b = self._write_doc(tmp_path / "b", "🎊🎊🎊")

        out = tmp_path / "out"
        files_a = _export_docusaurus(doc_a, out, doc_a.parent / "screenshots")
        files_b = _export_docusaurus(doc_b, out, doc_b.parent / "screenshots")

        # Every produced markdown path must have a non-empty stem.
        for f in files_a + files_b:
            assert f.stem, f"empty filename stem produced for {f}"
