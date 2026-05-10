"""
Tests for documentation export formats.
"""

import pytest

from src.docs.export import export_docs, SUPPORTED_FORMATS, ExportResult


@pytest.fixture
def sample_doc(tmp_path):
    """Create a sample docs.md for testing."""
    doc = tmp_path / "output" / "test-run" / "docs.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("""# Test Documentation

## Step 1: Login
Click the login button.

![screenshot](screenshots/step-01.png)
""")
    return doc


@pytest.fixture
def sample_screenshots(tmp_path, sample_doc):
    """Create fake screenshot files."""
    ss_dir = sample_doc.parent / "screenshots"
    ss_dir.mkdir(exist_ok=True)
    (ss_dir / "step-01.png").write_bytes(b"fake png")
    (ss_dir / "step-02.png").write_bytes(b"fake png")
    return ss_dir


class TestSupportedFormats:
    def test_supported_formats_list(self):
        assert "markdown" in SUPPORTED_FORMATS
        assert "docusaurus" in SUPPORTED_FORMATS
        assert "nextra" in SUPPORTED_FORMATS
        assert "mintlify" in SUPPORTED_FORMATS
        assert "starlight" in SUPPORTED_FORMATS

    def test_unknown_format_raises(self, sample_doc, tmp_path):
        with pytest.raises(ValueError, match="Unknown format 'unknown'"):
            export_docs(sample_doc, "unknown", tmp_path / "out")

    def test_missing_doc_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            export_docs(tmp_path / "missing.md", "markdown", tmp_path / "out")


class TestMarkdownExport:
    def test_exports_plain_markdown(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "markdown-out"
        result = export_docs(sample_doc, "markdown", out)

        assert isinstance(result, ExportResult)
        assert result.format == "markdown"
        assert (out / "docs.md").exists()
        assert (out / "screenshots" / "step-01.png").exists()

    def test_content_preserved(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "markdown-out"
        export_docs(sample_doc, "markdown", out)

        content = (out / "docs.md").read_text()
        assert "# Test Documentation" in content
        assert "Click the login button" in content


class TestDocusaurusExport:
    def test_creates_docs_dir_with_frontmatter(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "docusaurus-out"
        result = export_docs(sample_doc, "docusaurus", out)

        assert (out / "docs" / "test-documentation.md").exists()
        assert (out / "docs" / "screenshots" / "step-01.png").exists()
        assert result.format == "docusaurus"

    def test_frontmatter_includes_slug(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "docusaurus-out"
        export_docs(sample_doc, "docusaurus", out)

        content = (out / "docs" / "test-documentation.md").read_text()
        assert "sidebar_position: 1" in content
        assert "slug: /test-documentation" in content


class TestNextraExport:
    def test_creates_mdx_with_metadata(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "nextra-out"
        result = export_docs(sample_doc, "nextra", out)

        assert (out / "pages" / "docs" / "index.mdx").exists()
        assert (out / "pages" / "docs" / "screenshots" / "step-01.png").exists()
        assert result.format == "nextra"

    def test_mdx_has_metadata_export(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "nextra-out"
        export_docs(sample_doc, "nextra", out)

        content = (out / "pages" / "docs" / "index.mdx").read_text()
        assert "export const metadata" in content
        assert "Test Documentation" in content


class TestMintlifyExport:
    def test_creates_mdx_with_frontmatter(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "mintlify-out"
        result = export_docs(sample_doc, "mintlify", out)

        assert (out / "overview.mdx").exists()
        assert (out / "screenshots" / "step-01.png").exists()
        assert result.format == "mintlify"

    def test_frontmatter_has_title(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "mintlify-out"
        export_docs(sample_doc, "mintlify", out)

        content = (out / "overview.mdx").read_text()
        assert 'title: "Test Documentation"' in content


class TestStarlightExport:
    def test_creates_astro_content_dir(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "starlight-out"
        result = export_docs(sample_doc, "starlight", out)

        assert (out / "src" / "content" / "docs" / "index.md").exists()
        assert (out / "src" / "content" / "docs" / "screenshots" / "step-01.png").exists()
        assert result.format == "starlight"

    def test_frontmatter_for_starlight(self, sample_doc, sample_screenshots, tmp_path):
        out = tmp_path / "starlight-out"
        export_docs(sample_doc, "starlight", out)

        content = (out / "src" / "content" / "docs" / "index.md").read_text()
        assert "title: Test Documentation" in content
        assert "tableOfContents:" in content


class TestCustomScreenshotsDir:
    def test_uses_custom_screenshots_dir(self, sample_doc, tmp_path):
        custom_ss = tmp_path / "custom-ss"
        custom_ss.mkdir()
        (custom_ss / "custom-01.png").write_bytes(b"fake")

        out = tmp_path / "out"
        export_docs(sample_doc, "markdown", out, screenshots_dir=custom_ss)

        assert (out / "screenshots" / "custom-01.png").exists()
