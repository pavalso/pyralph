import pytest

from pyralph.prd import JsonUtils


class TestJsonUtils:
    CASES = [
        ('{"key": "value"}', {"key": "value"}),
        ('{}', {}),
        ('{"active": true}', {"active": True}),
        ('```\n{"key": "value"}\n```', {"key": "value"}),
        ('```json\n{"key": "value"}\n```', {"key": "value"}),
        ('Here is:\n{"key": "value"}', {"key": "value"}),
        ('{"key": "value"// comment\n}', {"key": "value"}),
    ]
    INVALID = ['not json', '{"key": ', 'text']

    @pytest.mark.parametrize("input_text,expected", CASES)
    def test_parse_valid(self, input_text, expected):
        assert JsonUtils.parse(input_text) == expected

    @pytest.mark.parametrize("input_text", INVALID)
    def test_parse_invalid(self, input_text):
        with pytest.raises(Exception):
            JsonUtils.parse(input_text)
