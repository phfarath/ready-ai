"""TDD — M9: checkpoint corrompido != checkpoint ausente.

`RunState.from_file` retorna None nos dois casos, e o loop cria um
RunState novo que sobrescreve o arquivo corrompido no próximo
`_save_checkpoint` — progresso parcial perdido sem rastro. Fix:
corrompido preserva sidecar `.corrupt` e o resume falha alto em vez
de seguir como run nova.
"""

from pathlib import Path

import pytest

from src.agent.state import RunState


class TestM9CorruptCheckpoint:
    def test_missing_still_returns_none(self, tmp_path: Path):
        assert RunState.from_file(tmp_path / "nope.json") is None

    def test_corrupt_preserves_sidecar(self, tmp_path: Path):
        path = tmp_path / "run_state.json"
        raw = "{corrupted!!!"
        path.write_text(raw)
        with pytest.raises(ValueError, match="corrupt"):
            RunState.from_file(path)
        sidecar = path.with_suffix(path.suffix + ".corrupt")
        assert sidecar.exists()
        assert sidecar.read_text() == raw

    def test_corrupt_does_not_delete_original(self, tmp_path: Path):
        path = tmp_path / "run_state.json"
        path.write_text("{corrupted!!!")
        with pytest.raises(ValueError, match="corrupt"):
            RunState.from_file(path)
        assert path.exists()

    def test_roundtrip_still_works(self, tmp_path: Path):
        state = RunState(run_id="r", goal="g", url="https://example.com")
        path = tmp_path / "state.json"
        state.to_file(path)
        restored = RunState.from_file(path)
        assert restored is not None
        assert restored.run_id == "r"

    def test_resume_from_corrupt_fails_loud(self, tmp_path: Path):
        from src.agent.loop import AgenticLoop

        bad = tmp_path / "bad_state.json"
        bad.write_text("{corrupted!!!")
        with pytest.raises(ValueError, match="corrupt"):
            AgenticLoop(
                goal="g",
                url="https://example.com",
                output_dir=str(tmp_path),
                resume_from=str(bad),
            )
