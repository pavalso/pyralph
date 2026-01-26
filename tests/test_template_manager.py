from pyralph.templates import TemplateManager


class TestTemplateManager:
    def test_template_load(self):
        template = TemplateManager.load("developer.txt")
        assert "# ROLE" in template
        assert "Developer" in template
        assert "{{task_id}}" in template

    def test_template_render(self):
        result = TemplateManager.render("architect.txt", user_intent="Test", file_tree="tree")
        assert "Test" in result
        assert "tree" in result

    def test_default_templates_exist(self):
        for name in ["architect.txt", "planner.txt", "developer.txt"]:
            template = TemplateManager.load(name)
            assert isinstance(template, str)
            assert len(template) > 0
