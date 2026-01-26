from .helpers import TempConfigTestCase


class TestFormatAcceptanceCriteria(TempConfigTestCase):
    def test_format_acceptance_criteria_with_criteria(self):
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task',
            'acceptanceCriteria': ['Criterion 1', 'Criterion 2', 'Criterion 3']
        }
        result = orch._format_acceptance_criteria(task)
        assert "- Criterion 1" in result
        assert "- Criterion 2" in result
        assert "- Criterion 3" in result

    def test_format_acceptance_criteria_empty(self):
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task',
            'acceptanceCriteria': []
        }
        result = orch._format_acceptance_criteria(task)
        assert "No acceptance criteria specified" in result

    def test_format_acceptance_criteria_missing(self):
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task'
        }
        result = orch._format_acceptance_criteria(task)
        assert "No acceptance criteria specified" in result
