"""Whole-operation reset deadlines must reach Compose and stop CLI descendants."""
import subprocess
from unittest.mock import Mock

import pytest

from redteam import common, reset


def test_inherited_capture_budget_reaches_compose_and_decreases(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(common.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("COMMONLY_RESET_DEADLINE", "3700")
    monkeypatch.setattr(reset, "ROOT", tmp_path)
    processes = []
    def launch(*args, **kwargs):
        process = Mock(pid=12345)
        process.wait.return_value = 0
        processes.append(process)
        return process
    monkeypatch.setattr(reset.subprocess, "Popen", launch)
    monkeypatch.setattr(reset.os, "killpg", Mock())
    # A child reset must inherit the parent's 3600s budget, not reset to 1200/180.
    with common.reset_budget(1200):
        reset.compose("down", "-v", "--timeout", "10")
        clock[0] += 200
        reset.compose("up", "-d")
    assert processes[0].wait.call_args_list[0].kwargs["timeout"] == 3600
    assert processes[1].wait.call_args_list[0].kwargs["timeout"] == 3400
    assert common.os.environ["COMMONLY_RESET_DEADLINE"] == "3700"


def test_standalone_reset_budget_is_restored_after_failure(monkeypatch):
    monkeypatch.delenv("COMMONLY_RESET_DEADLINE", raising=False)
    monkeypatch.setattr(common.time, "monotonic", lambda: 100)
    with pytest.raises(RuntimeError):
        with common.reset_budget(600):
            assert common.reset_time_remaining() == 600
            raise RuntimeError("simulated reset failure")
    assert "COMMONLY_RESET_DEADLINE" not in common.os.environ


def test_expired_budget_prevents_next_compose_command(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONLY_RESET_DEADLINE", "100")
    monkeypatch.setattr(common.time, "monotonic", lambda: 101)
    monkeypatch.setattr(reset, "ROOT", tmp_path)
    launch = Mock()
    monkeypatch.setattr(reset.subprocess, "Popen", launch)
    with pytest.raises(TimeoutError, match="deadline"):
        reset.compose("run", "seed")
    launch.assert_not_called()


def test_compose_timeout_kills_command_group_and_reports_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONLY_RESET_DEADLINE", "3700")
    monkeypatch.setattr(common.time, "monotonic", lambda: 100)
    monkeypatch.setattr(reset, "ROOT", tmp_path)
    process = Mock(pid=12345)
    process.wait.side_effect = [subprocess.TimeoutExpired("docker compose down", 3600), 0, 0]
    monkeypatch.setattr(reset.subprocess, "Popen", Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(reset.os, "killpg", kill)
    with pytest.raises(RuntimeError, match="compose down failed.*deadline"):
        reset.compose("down", "-v", "--timeout", "10")
    assert kill.call_count == 2
    assert "compose down" in (tmp_path / ".run/reset.log").read_text()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_reset_budget_is_rejected(value):
    with pytest.raises(ValueError):
        with common.reset_budget(value):
            pytest.fail("Invalid reset budget was accepted")
