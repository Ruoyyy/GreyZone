"""Offline checks for missing goals, invalid scopes and drifted task inputs."""

import json
from pathlib import Path
import tempfile
import unittest

import check_scenarios as checker


class ScenarioChecks(unittest.TestCase):
    def test_repository_contracts(self):
        report = checker.audit()
        self.assertEqual(report["status"], "PASS", report["results"])
        self.assertEqual({row["project"] for row in report["results"]}, set(checker.RANGES))
        self.assertEqual(report["model_calls"], "NOT_RUN")

    def test_invalid_contracts_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "example"
            benchmark = project / "benchmark"
            directory = benchmark / "scenarios/case"
            directory.mkdir(parents=True)
            path = directory / "scenario.json"
            goal = directory / "objective.md"
            valid = {"id": "case", "objectiveFile": "objective.md", "attackerTurn": "hello",
                     "oracleSurfaces": ["effect"], "successCriteria": "A recorded effect"}
            cases = [{"objectiveFile": None}, {"objectiveFile": "missing.md"},
                     {"oracleSurfaces": ["unknown"]}, {"oracleSurfaces": []},
                     {"successCriteria": ""}, {"id": "another-case"}]
            for changes in cases:
                with self.subTest(changes=changes):
                    goal.write_text("Public attacker task")
                    path.write_text(json.dumps({**valid, **changes}))
                    with self.assertRaises((ValueError, OSError)):
                        checker.check_scenario(project, benchmark, path, "oracleSurfaces", {"effect"})
            path.write_text(json.dumps(valid))
            goal.write_text("\n ")
            with self.assertRaises(ValueError):
                checker.check_scenario(project, benchmark, path, "oracleSurfaces", {"effect"})
            path.write_text(json.dumps({**valid, "objectiveFile": "../../../outside.md"}))
            with self.assertRaises(ValueError):
                checker.check_scenario(project, benchmark, path, "oracleSurfaces", {"effect"})


if __name__ == "__main__":
    unittest.main()
