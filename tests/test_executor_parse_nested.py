"""TDD — M4: _parse_action com JSON aninhado (ex. dialog com `then`).

O regex final `\\{[^{}]+\\}` (e o do code block `\\{.*?\\}` non-greedy)
não casam chaves aninhadas: a ação `dialog` com `then` interno vira
None → "[FAILED]" + retries desperdiçando chamadas LLM.
"""

from src.agent.executor import _parse_action


class TestM4NestedJson:
    def test_plain_nested_dialog(self):
        raw = (
            '{"action": "dialog", "decision": "accept", '
            '"then": {"action": "click", "selector": "#ok"}}'
        )
        assert _parse_action(raw) == {
            "action": "dialog",
            "decision": "accept",
            "then": {"action": "click", "selector": "#ok"},
        }

    def test_nested_inside_prose(self):
        raw = (
            "Sure, here it is: "
            '{"action": "dialog", "decision": "accept", '
            '"then": {"action": "click", "selector": "#ok"}} done.'
        )
        parsed = _parse_action(raw)
        assert parsed is not None
        assert parsed["then"] == {"action": "click", "selector": "#ok"}

    def test_nested_inside_code_block(self):
        raw = (
            "```json\n"
            '{"action": "dialog", "decision": "dismiss", '
            '"then": {"action": "type", "selector": "#q", "text": "x"}}'
            "\n```"
        )
        parsed = _parse_action(raw)
        assert parsed is not None
        assert parsed["then"]["action"] == "type"

    def test_deeply_nested_with_braces_in_strings(self):
        raw = (
            '{"action": "type", "selector": "#q", '
            '"text": "a {b} c", '
            '"then": {"action": "click", "selector": "#ok"}}'
        )
        parsed = _parse_action(raw)
        assert parsed is not None
        assert parsed["text"] == "a {b} c"

    def test_garbage_still_returns_none(self):
        assert _parse_action("no json here at all") is None
        assert _parse_action("{not json") is None

    def test_flat_still_works(self):
        assert _parse_action('{"action": "click", "selector": "#a"}') == {
            "action": "click",
            "selector": "#a",
        }
