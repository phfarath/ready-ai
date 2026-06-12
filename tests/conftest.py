"""Global test configuration."""
# ruff: noqa: E402

import os
import sys

# Disable API key authentication during tests (all endpoints are 200)
os.environ["AUTH_DISABLED"] = "true"

# Ensure PIL is never mocked at import time — some test files mock it via
# sys.modules to avoid heavy imports, which leaks into later tests.
for _pil_k in list(sys.modules):
    if _pil_k.startswith("PIL"):
        del sys.modules[_pil_k]

import pytest


def pytest_configure(config):
    """Called once at plugin registration — ensures PIL is clean."""
    for _pil_k in list(sys.modules):
        if _pil_k.startswith("PIL"):
            del sys.modules[_pil_k]


@pytest.fixture(autouse=True)
def _restore_pil_module():
    """
    Ensure PIL modules are restored to their real implementations
    before every test.  Some test files mock PIL via sys.modules to
    avoid heavy imports; this fixture prevents the mock from leaking
    into later tests that need the real PIL.Image.
    """
    # 1. Remove any stale PIL mocks from sys.modules
    for _pil_k in list(sys.modules):
        if _pil_k.startswith("PIL"):
            del sys.modules[_pil_k]

    # 2. Force a fresh import of the *real* Pillow package so that
    # format plugins (PNG, JPEG, …) are registered before any test
    # that relies on Image.open / Image.save runs.
    import PIL.Image as _real_image
    _real_image.init()  # full init — registers PNG/JPEG/WebP/etc.

    yield

    # 3. Clean up again after the test
    for _pil_k in list(sys.modules):
        if _pil_k.startswith("PIL"):
            del sys.modules[_pil_k]
