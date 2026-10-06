"""One-shot PyRIT PromptTarget backed by the shared Shopware payload evaluator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

from settings import ROOT


class ShopwareTarget(PromptTarget):
    def __init__(self, *, scenario_dir: Path, endpoint: str):
        super().__init__(endpoint=endpoint, custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=True),
        ))
        self.scenario_dir = scenario_dir
        self.last_evaluation: dict | None = None
        self.attempted = False
        self.guard = asyncio.Lock()

    async def _send_prompt_to_target_async(
        self, *, normalized_conversation: list[Message]
    ) -> list[Message]:
        async with self.guard:
            if self.attempted:
                raise RuntimeError("This Shopware target accepts one payload per run")
            if not normalized_conversation:
                raise ValueError("PyRIT submitted no messages")
            request = normalized_conversation[-1].get_piece()
            if (request.role != "user" or len(normalized_conversation[-1].message_pieces) != 1
                    or request.converted_value_data_type != "text"):
                raise ValueError("Shopware target accepts one user text message")
            self.attempted = True  # Never retry a possibly completed seller write.
            process = await asyncio.create_subprocess_exec(
                str(ROOT / ".venv" / "bin" / "python"),
                str(ROOT / "redteam" / "payload_cli.py"),
                "--scenario", str(self.scenario_dir), "--source", "pyrit",
                cwd=ROOT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await process.communicate(request.converted_value.encode())
            except asyncio.CancelledError:
                process.terminate()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError(
                    f"Shopware evaluation exited {process.returncode}: "
                    f"{stderr.decode(errors='replace')[-1200:]}"
                )
            self.last_evaluation = json.loads(stdout)
            evaluation = self.last_evaluation
            response = (
                f"Victim reply:\n{evaluation['reply']}\n\n"
                f"Oracle scenario success: {str(evaluation['oracle']['success']).lower()}\n"
                f"Oracle outcome: {evaluation['oracle']['outcome']}"
            )
            return [construct_response_from_request(
                request=request, response_text_pieces=[response],
            )]
