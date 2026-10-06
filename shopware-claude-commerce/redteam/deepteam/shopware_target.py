"""One DeepTeam callback equals one persistent Shopware seller write and Oracle run."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from deepteam.test_case import RTTurn

from settings import ROOT


class ShopwareTarget:
    def __init__(self, scenario_dir: Path):
        self.scenario_dir = scenario_dir
        self.attempted = False
        self.evaluation: dict | None = None

    async def send(self, input: str) -> RTTurn:
        if self.attempted:
            raise RuntimeError("This Shopware run accepts one payload only")
        if not input.strip():
            raise ValueError("DeepTeam generated an empty payload")
        self.attempted = True  # Never retry a possibly completed seller write.
        process = await asyncio.create_subprocess_exec(
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "redteam" / "payload_cli.py"),
            "--scenario", str(self.scenario_dir), "--source", "deepteam",
            cwd=ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate(input.encode())
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(
                f"Shopware evaluation exited {process.returncode}: "
                f"{stderr.decode(errors='replace')[-1200:]}"
            )
        self.evaluation = json.loads(stdout)
        result = self.evaluation
        return RTTurn(role="assistant", content=(
            f"Victim reply:\n{result['reply']}\n\n"
            f"Oracle scenario success: {str(result['oracle']['success']).lower()}\n"
            f"Oracle outcome: {result['oracle']['outcome']}"
        ))
