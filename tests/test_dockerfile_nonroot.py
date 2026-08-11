"""Regression tests for VAL-QUAL-007: Dockerfile runs as non-root user
without dev dependencies.

The production Docker image must not run as root and must not install
development-only extras (pytest, ruff, etc.).  These tests perform a
static inspection of the ``Dockerfile`` (mirroring the manual-inspection
evidence required by the validation contract).
"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"


def _read_dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------

def test_dockerfile_has_user_directive():
    """Dockerfile MUST contain a ``USER`` instruction referencing a
    non-root account."""
    text = _read_dockerfile()
    # Match  USER <name>  (case-insensitive, at start of line after spaces)
    user_matches = re.findall(r"(?im)^\s*USER\s+(\S+)", text)
    assert user_matches, (
        "Dockerfile must declare a USER instruction to drop root privileges."
    )
    # The last USER directive is the effective one.
    effective_user = user_matches[-1]
    assert effective_user.lower() not in ("root", "0"), (
        f"Effective USER is '{effective_user}'; must reference a non-root user."
    )


def test_dockerfile_creates_nonroot_user():
    """Dockerfile MUST create the non-root user before switching to it."""
    text = _read_dockerfile()
    # Must use useradd or adduser to create a dedicated user.
    assert re.search(r"\b(useradd|adduser)\b", text), (
        "Dockerfile must create a dedicated non-root user via useradd/adduser."
    )


# ---------------------------------------------------------------------------
# No dev extras
# ---------------------------------------------------------------------------

def test_dockerfile_does_not_install_dev_extras():
    """No ``pip install`` line may include ``[dev]`` or any extras bracket."""
    text = _read_dockerfile()
    pip_lines = [line for line in text.splitlines() if "pip install" in line]
    assert pip_lines, "Expected at least one pip install line."
    for line in pip_lines:
        # Reject any extras specification like  .[dev]  or  [test]
        assert not re.search(r"\[\w+\]", line), (
            f"pip install line contains extras (dev deps): {line.strip()}"
        )


# ---------------------------------------------------------------------------
# Ownership of app and output directories
# ---------------------------------------------------------------------------

def test_dockerfile_grants_ownership_to_nonroot_user():
    """The non-root user must own the app and output directories.

    Either ``COPY --chown`` or an explicit ``chown`` ``RUN`` must appear
    so that the non-root ``USER`` can read the application code and write
    to ``/app/output``.
    """
    text = _read_dockerfile()
    assert (
        "--chown" in text
        or re.search(r"\bchown\b", text)
    ), (
        "Dockerfile must grant ownership of the app directory to the "
        "non-root user via COPY --chown or a chown RUN step."
    )
