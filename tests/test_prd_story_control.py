import argparse

from pyralph.orchestrator import RalphOrchestrator


class TestPrdStoryControlFlags:
    def setup_method(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--schema", type=str)
        self.parser.add_argument("--min-criteria", type=int)
        self.parser.add_argument("--label", nargs="+")

    def test_flag_parsing(self):
        args = self.parser.parse_args(["--schema", "/s.json", "--min-criteria", "3", "--label", "t=bug", "p"])
        assert args.schema == "/s.json"
        assert args.min_criteria == 3
        assert args.label == ["t=bug", "p"]

    def test_defaults(self):
        args = self.parser.parse_args([])
        assert args.schema is None
        assert args.min_criteria is None
        assert args.label is None
