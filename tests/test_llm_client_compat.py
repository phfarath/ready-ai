from pydantic import BaseModel

from src.llm import client  # noqa: F401
import openai._base_client as openai_base_client
import openai._compat as openai_compat


class _Model(BaseModel):
    value: int = 1


def test_openai_model_dump_allows_none_by_alias() -> None:
    dumped = openai_compat.model_dump(_Model(), by_alias=None)
    assert dumped == {"value": 1}


def test_openai_base_client_uses_patched_model_dump() -> None:
    dumped = openai_base_client.model_dump(_Model(), by_alias=None)
    assert dumped == {"value": 1}


def test_patch_is_noop_without_compat_shim(monkeypatch) -> None:
    """openai>=3 can drop ``openai._compat`` — the patch must degrade
    to a no-op instead of crashing the import (T-SDK-FOLLOWUPS/6)."""
    import src.llm.client as llm_client

    monkeypatch.setattr(llm_client, "openai_compat", None)
    # Must not raise.
    llm_client._patch_openai_model_dump()
