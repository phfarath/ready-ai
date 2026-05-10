"""Global test configuration."""

import sys
import pytest


@pytest.fixture(autouse=True)
def _restore_pil_module():
    """
    Ensure PIL modules are restored to their real implementations
    before every test.  Some test files mock PIL via sys.modules to
    avoid heavy imports; this fixture prevents the mock from leaking
    into later tests that need the real PIL.Image.
    """
    pil_was_mock = False
    if "PIL" in sys.modules and hasattr(sys.modules["PIL"], "_mock_name"):
        pil_was_mock = True
        for k in list(sys.modules):
            if k.startswith("PIL"):
                del sys.modules[k]

    yield

    # Optionally restore after test as well (belt-and-suspenders)
    if pil_was_mock:
        for k in list(sys.modules):
            if k.startswith("PIL"):
                del sys.modules[k]
