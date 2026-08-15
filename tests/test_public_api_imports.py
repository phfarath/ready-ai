"""Public SDK surface (READY-AI-T-13, DoD 1): importable façade.

The distributed package is ``ready-ai`` but the engine ships under
``src``. This card adds a public, importable SDK named ``ready_ai`` so
consumers never touch ``src.*``. These tests pin the documented public
surface and its package-level documentation.
"""

from __future__ import annotations


PUBLIC_NAMES = ("ReadyAI", "Flow", "FlowStep", "RunResult", "BrowserOptions")


def test_public_names_importable_from_top_level():
    from ready_ai import BrowserOptions, Flow, FlowStep, ReadyAI, RunResult

    assert all(name is not None for name in (BrowserOptions, Flow, FlowStep, ReadyAI, RunResult))


def test_package_documents_public_surface():
    """DoD 1 — the surface must be *documented*, not just importable."""
    import ready_ai

    doc = ready_ai.__doc__ or ""
    for name in PUBLIC_NAMES:
        assert name in doc, (
            f"public surface name {name!r} must appear in the ready_ai package docstring"
        )


def test_version_exposed():
    import ready_ai

    assert isinstance(ready_ai.__version__, str)
    assert ready_ai.__version__


def test_star_import_exports_public_surface():
    import ready_ai

    assert set(PUBLIC_NAMES) <= set(ready_ai.__all__)
    # Everything advertised by __all__ must actually resolve.
    for name in ready_ai.__all__:
        assert hasattr(ready_ai, name), f"__all__ references missing attribute {name!r}"


def test_surface_types_are_public_contract_models():
    import ready_ai

    # The five names must be real SDK types (not None / not engine types).
    assert ready_ai.Flow.__module__.startswith("ready_ai")
    assert ready_ai.FlowStep.__module__.startswith("ready_ai")
    assert ready_ai.RunResult.__module__.startswith("ready_ai")
    assert ready_ai.BrowserOptions.__module__.startswith("ready_ai")
    assert ready_ai.ReadyAI.__module__.startswith("ready_ai")


def test_import_line_from_definition_of_done_verbatim():
    # The exact import line mandated by the card.
    from ready_ai import ReadyAI, Flow, FlowStep, RunResult, BrowserOptions  # noqa: F401
