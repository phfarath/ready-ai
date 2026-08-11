"""Tests for batch CLI --output and --headless flags (VAL-ROB-013, VAL-CROSS-006).

The CI workflows invoke ``ready-ai batch --config ... --output ./output --headless``,
but the batch subparser historically only accepted ``--config`` and ``--verbose``.
These tests assert the subparser accepts both new flags with sensible defaults.
"""

from main import _build_parser, parse_args


def _batch_help_text() -> str:
    """Return the rendered help text for the ``batch`` subcommand."""
    parser = _build_parser()
    subparsers_action = parser._subparsers._group_actions[0]
    batch_subparser = subparsers_action.choices["batch"]
    return batch_subparser.format_help()


def test_batch_help_shows_output_flag():
    """``batch --help`` must advertise the ``--output`` option."""
    help_text = _batch_help_text()
    assert "--output" in help_text


def test_batch_help_shows_headless_flag():
    """``batch --help`` must advertise the ``--headless`` option."""
    help_text = _batch_help_text()
    assert "--headless" in help_text


def test_batch_parses_output_and_headless_flags(tmp_path):
    """``batch --config x.yaml --output ./out --headless`` parses without error."""
    config_path = tmp_path / "batch.yaml"
    config_path.write_text("flows: []\n", encoding="utf-8")

    args = parse_args(
        ["batch", "--config", str(config_path), "--output", "./out", "--headless"]
    )

    assert args.command == "batch"
    assert args.output == "./out"
    assert args.headless is True


def test_batch_defaults_applied_when_flags_omitted(tmp_path):
    """Omitting the new flags must apply defaults (output=./output, headless=False)."""
    config_path = tmp_path / "batch.yaml"
    config_path.write_text("flows: []\n", encoding="utf-8")

    args = parse_args(["batch", "--config", str(config_path)])

    assert hasattr(args, "output")
    assert hasattr(args, "headless")
    assert args.output == "./output"
    assert args.headless is False


def test_batch_verbose_still_supported(tmp_path):
    """Existing ``--verbose`` flag must keep working alongside the new flags."""
    config_path = tmp_path / "batch.yaml"
    config_path.write_text("flows: []\n", encoding="utf-8")

    args = parse_args(
        ["batch", "--config", str(config_path), "--verbose", "--headless"]
    )

    assert args.verbose is True
    assert args.headless is True


def test_batch_output_with_short_alias(tmp_path):
    """The ``--output`` flag should accept a path value via long form."""
    config_path = tmp_path / "batch.yaml"
    config_path.write_text("flows: []\n", encoding="utf-8")

    args = parse_args(["batch", "--config", str(config_path), "--output", str(tmp_path)])

    assert args.output == str(tmp_path)
