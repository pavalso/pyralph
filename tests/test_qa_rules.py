"""Tests for QA rules loader with markdown file support."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyralph.config import CONF
from pyralph.qa.rules import (
    QARule,
    QARulesLoader,
    SUPPORTED_EXTENSIONS,
    SEVERITY_LEVELS,
    DEFAULT_SEVERITY,
    get_severity_priority,
    is_valid_severity,
)


class TestSeverityHelpers(unittest.TestCase):
    """Tests for severity helper functions."""

    def test_severity_levels_order(self):
        assert SEVERITY_LEVELS == ["critical", "major", "minor", "info"]

    def test_default_severity(self):
        assert DEFAULT_SEVERITY == "major"

    def test_get_severity_priority_valid(self):
        assert get_severity_priority("critical") == 0
        assert get_severity_priority("major") == 1
        assert get_severity_priority("minor") == 2
        assert get_severity_priority("info") == 3

    def test_get_severity_priority_invalid(self):
        # Invalid severity returns major priority (1)
        assert get_severity_priority("invalid") == 1
        assert get_severity_priority("") == 1

    def test_is_valid_severity(self):
        assert is_valid_severity("critical") is True
        assert is_valid_severity("major") is True
        assert is_valid_severity("minor") is True
        assert is_valid_severity("info") is True
        assert is_valid_severity("invalid") is False
        assert is_valid_severity("") is False


class TestQARule(unittest.TestCase):
    """Tests for QARule dataclass."""

    def test_rule_defaults(self):
        rule = QARule(id="test-rule", name="Test Rule")
        assert rule.id == "test-rule"
        assert rule.name == "Test Rule"
        assert rule.description == ""
        assert rule.enabled is True
        assert rule.severity == "major"
        assert rule.category == "general"
        assert rule.pattern is None
        assert rule.message == ""
        assert rule.metadata == {}
        assert rule.source_file is None

    def test_rule_to_dict(self):
        rule = QARule(
            id="test-rule",
            name="Test Rule",
            description="A test rule",
            severity="critical",
            category="security",
            pattern=r"\beval\s*\(",
            message="Avoid using eval",
            source_file="/path/to/rule.yaml",
        )
        result = rule.to_dict()
        assert result["id"] == "test-rule"
        assert result["name"] == "Test Rule"
        assert result["description"] == "A test rule"
        assert result["severity"] == "critical"
        assert result["category"] == "security"
        assert result["pattern"] == r"\beval\s*\("
        assert result["message"] == "Avoid using eval"
        assert result["source_file"] == "/path/to/rule.yaml"


class TestSupportedExtensions(unittest.TestCase):
    """Tests for supported file extensions."""

    def test_supported_extensions_includes_markdown(self):
        assert ".md" in SUPPORTED_EXTENSIONS

    def test_supported_extensions_includes_yaml(self):
        assert ".yaml" in SUPPORTED_EXTENSIONS
        assert ".yml" in SUPPORTED_EXTENSIONS

    def test_supported_extensions_includes_json(self):
        assert ".json" in SUPPORTED_EXTENSIONS


class QARulesLoaderTestCase(unittest.TestCase):
    """Base test case for QARulesLoader tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.qa_dir = self.temp_path / ".ralph" / "qa"
        self._original_qa_rules_dir = CONF.QA_RULES_DIR
        CONF.QA_RULES_DIR = self.qa_dir

    def tearDown(self):
        CONF.QA_RULES_DIR = self._original_qa_rules_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_rule_file(self, name: str, content: str) -> Path:
        """Create a rule file in the QA directory."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.qa_dir / name
        file_path.write_text(content, encoding="utf-8")
        return file_path


class TestQARulesLoaderBasic(QARulesLoaderTestCase):
    """Tests for basic QARulesLoader functionality."""

    def test_loader_creates_directory(self):
        assert not self.qa_dir.exists()
        loader = QARulesLoader()
        loader.ensure_directory()
        assert self.qa_dir.exists()

    def test_loader_empty_directory(self):
        self.qa_dir.mkdir(parents=True, exist_ok=True)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert rules == []

    def test_loader_custom_directory(self):
        custom_dir = self.temp_path / "custom_qa"
        loader = QARulesLoader(rules_dir=custom_dir)
        assert loader.rules_dir == custom_dir


class TestYAMLRuleLoading(QARulesLoaderTestCase):
    """Tests for YAML rule file loading."""

    def test_load_yaml_rule(self):
        yaml_content = """
id: no-print
name: No Print Statements
description: Disallow print() calls
severity: major
category: code_style
pattern: "\\\\bprint\\\\s*\\\\("
message: Use logging instead
"""
        self.create_rule_file("no_print.yaml", yaml_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.id == "no-print"
        assert rule.name == "No Print Statements"
        assert rule.severity == "major"
        assert rule.category == "code_style"

    def test_load_yml_extension(self):
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yml", yaml_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        assert rules[0].id == "test-rule"


class TestJSONRuleLoading(QARulesLoaderTestCase):
    """Tests for JSON rule file loading."""

    def test_load_json_rule(self):
        json_content = '{"id": "json-rule", "name": "JSON Rule", "severity": "critical"}'
        self.create_rule_file("rule.json", json_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.id == "json-rule"
        assert rule.name == "JSON Rule"
        assert rule.severity == "critical"


class TestMarkdownRuleLoading(QARulesLoaderTestCase):
    """Tests for Markdown rule file loading."""

    def test_load_markdown_rule_with_frontmatter(self):
        """Test that a valid markdown file with YAML frontmatter is recognized and applied."""
        md_content = """---
id: md-rule
name: Markdown Rule
description: A rule defined in markdown
severity: minor
category: documentation
---

## Rule Details

This rule ensures documentation is complete.
"""
        self.create_rule_file("doc_rule.md", md_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.id == "md-rule"
        assert rule.name == "Markdown Rule"
        assert rule.description == "A rule defined in markdown"
        assert rule.severity == "minor"
        assert rule.category == "documentation"
        # Body should be stored in metadata
        assert "body" in rule.metadata
        assert "Rule Details" in rule.metadata["body"]

    def test_load_markdown_rule_minimal(self):
        """Test loading a markdown rule with minimal frontmatter."""
        md_content = """---
id: minimal-rule
name: Minimal Rule
---
"""
        self.create_rule_file("minimal.md", md_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.id == "minimal-rule"
        assert rule.name == "Minimal Rule"
        # Default values should be applied
        assert rule.severity == "major"
        assert rule.category == "general"
        assert rule.enabled is True

    def test_empty_markdown_file_skipped_with_warning(self):
        """Test that an empty .md file is skipped with a warning about empty rule definition."""
        self.create_rule_file("empty.md", "")
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        # Verify warning was logged
        mock_logger.warning.assert_called()
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("empty" in call.lower() for call in warning_calls)

    def test_whitespace_only_markdown_file_skipped_with_warning(self):
        """Test that a whitespace-only .md file is skipped with a warning."""
        self.create_rule_file("whitespace.md", "   \n\n   \t  \n")
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()

    def test_markdown_without_frontmatter_skipped_with_warning(self):
        """Test that a markdown file without frontmatter is skipped with a warning."""
        md_content = """# Just a Header

Some content without frontmatter.
"""
        self.create_rule_file("no_frontmatter.md", md_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("frontmatter" in call.lower() for call in warning_calls)

    def test_markdown_with_empty_frontmatter_skipped_with_warning(self):
        """Test that a markdown file with empty frontmatter is skipped with warning."""
        md_content = """---
---

# Content after empty frontmatter
"""
        self.create_rule_file("empty_frontmatter.md", md_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()

    def test_markdown_missing_closing_frontmatter(self):
        """Test that a markdown file without closing --- marker is warned."""
        md_content = """---
id: broken-rule
name: Broken Rule

This never closes the frontmatter.
"""
        self.create_rule_file("broken.md", md_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()

    def test_markdown_with_extra_metadata(self):
        """Test that extra fields in frontmatter are preserved in metadata."""
        md_content = """---
id: extra-fields
name: Extra Fields Rule
custom_field: custom_value
another_field: 123
---
"""
        self.create_rule_file("extra.md", md_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.metadata.get("custom_field") == "custom_value"
        assert rule.metadata.get("another_field") == 123


class TestMixedRuleFiles(QARulesLoaderTestCase):
    """Tests for loading multiple rule files of different types."""

    def test_load_mixed_formats(self):
        """Test loading YAML, JSON, and Markdown rules together."""
        yaml_content = """
id: yaml-rule
name: YAML Rule
"""
        json_content = '{"id": "json-rule", "name": "JSON Rule"}'
        md_content = """---
id: md-rule
name: Markdown Rule
---
"""
        self.create_rule_file("rule.yaml", yaml_content)
        self.create_rule_file("rule.json", json_content)
        self.create_rule_file("rule.md", md_content)

        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 3
        rule_ids = {rule.id for rule in rules}
        assert rule_ids == {"yaml-rule", "json-rule", "md-rule"}


class TestRuleValidation(QARulesLoaderTestCase):
    """Tests for rule validation and error handling."""

    def test_rule_missing_id(self):
        yaml_content = """
name: No ID Rule
"""
        self.create_rule_file("no_id.yaml", yaml_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()

    def test_rule_missing_name(self):
        yaml_content = """
id: no-name-rule
"""
        self.create_rule_file("no_name.yaml", yaml_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 0
        mock_logger.warning.assert_called()

    def test_invalid_severity_defaults_to_major(self):
        yaml_content = """
id: invalid-severity
name: Invalid Severity Rule
severity: invalid_value
"""
        self.create_rule_file("invalid_sev.yaml", yaml_content)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        assert len(rules) == 1
        assert rules[0].severity == "major"
        mock_logger.warning.assert_called()

    def test_duplicate_rule_id_warns(self):
        yaml1 = """
id: duplicate-id
name: First Rule
"""
        yaml2 = """
id: duplicate-id
name: Second Rule
"""
        self.create_rule_file("first.yaml", yaml1)
        self.create_rule_file("second.yaml", yaml2)
        loader = QARulesLoader()
        with patch("pyralph.qa.rules.Logger") as mock_logger:
            rules = loader.load_rules()
        # Only one rule should remain (the later one takes precedence)
        assert len(rules) == 1
        mock_logger.warning.assert_called()
        # Check that duplicate warning was issued
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("duplicate" in call.lower() for call in warning_calls)


class TestRuleFiltering(QARulesLoaderTestCase):
    """Tests for rule filtering methods."""

    def setUp(self):
        super().setUp()
        yaml_content = """
- id: rule-1
  name: Rule 1
  category: security
  severity: critical
  enabled: true
- id: rule-2
  name: Rule 2
  category: code_style
  severity: minor
  enabled: false
- id: rule-3
  name: Rule 3
  category: security
  severity: major
  enabled: true
"""
        self.create_rule_file("rules.yaml", yaml_content)
        self.loader = QARulesLoader()
        self.loader.load_rules()

    def test_get_enabled_rules(self):
        enabled = self.loader.get_enabled_rules()
        assert len(enabled) == 2
        enabled_ids = {r.id for r in enabled}
        assert enabled_ids == {"rule-1", "rule-3"}

    def test_get_rules_by_category(self):
        security_rules = self.loader.get_rules_by_category("security")
        assert len(security_rules) == 2
        assert all(r.category == "security" for r in security_rules)

    def test_get_rules_by_severity(self):
        critical_rules = self.loader.get_rules_by_severity("critical")
        assert len(critical_rules) == 1
        assert critical_rules[0].id == "rule-1"

    def test_get_rule_by_id(self):
        rule = self.loader.get_rule_by_id("rule-2")
        assert rule is not None
        assert rule.name == "Rule 2"

    def test_get_rule_by_id_not_found(self):
        rule = self.loader.get_rule_by_id("nonexistent")
        assert rule is None

    def test_get_all_rule_ids(self):
        ids = self.loader.get_all_rule_ids()
        assert set(ids) == {"rule-1", "rule-2", "rule-3"}


class TestUnsupportedFiles(QARulesLoaderTestCase):
    """Tests for unsupported file handling."""

    def test_unsupported_extension_ignored(self):
        self.create_rule_file("rule.txt", "id: txt-rule\nname: TXT Rule")
        self.create_rule_file("rule.yaml", "id: yaml-rule\nname: YAML Rule")
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        assert rules[0].id == "yaml-rule"

    def test_directories_ignored(self):
        self.qa_dir.mkdir(parents=True, exist_ok=True)
        (self.qa_dir / "subdir").mkdir()
        self.create_rule_file("rule.yaml", "id: yaml-rule\nname: YAML Rule")
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1


class TestReload(QARulesLoaderTestCase):
    """Tests for rule reloading."""

    def test_reload_rules(self):
        yaml_content = """
id: original-rule
name: Original Rule
"""
        self.create_rule_file("rule.yaml", yaml_content)
        loader = QARulesLoader()
        rules = loader.load_rules()
        assert len(rules) == 1
        assert rules[0].id == "original-rule"

        # Modify the file
        (self.qa_dir / "rule.yaml").write_text("""
id: updated-rule
name: Updated Rule
""", encoding="utf-8")

        # Reload
        rules = loader.reload()
        assert len(rules) == 1
        assert rules[0].id == "updated-rule"


if __name__ == "__main__":
    unittest.main()
