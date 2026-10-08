"""One stateful Clawith conversation exposed as a DeepTeam callback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepteam.attacks.multi_turn.progression import MetricVerdict
from deepteam.test_case import RTTurn

Turn = Callable[[str], Awaitable[dict[str, Any]]]


class ClawithConversation:
    """Reject replay and branching while recording Oracle evidence per turn."""

    def __init__(self, *, turn: Turn, episode: int, surface: str) -> None:
        self._turn = turn
        self.episode = episode
        self.surface = surface
        self.history: list[RTTurn] = []
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _content(turns: list[RTTurn]) -> list[tuple[str, str]]:
        return [(turn.role, turn.content.strip() if turn.role == "user" else turn.content)
                for turn in turns]

    async def callback(self, input: str, turns: list[RTTurn] | None = None) -> RTTurn:
        message = input.strip()
        if not message:
            raise ValueError("attack message is empty")
        if self.events and self.events[-1]["oracleSuccess"]:
            raise RuntimeError("Oracle already succeeded; no further attack turns are allowed")
        expected = self._content(self.history)
        supplied = self._content(turns or [])
        first_seed = not expected and supplied == [("user", message)]
        if supplied != expected and not first_seed:
            raise RuntimeError("DeepTeam history would replay or branch the Clawith group")
        result = await self._turn(message)
        status = result.get("status")
        if status not in {"continue", "oracle-success"} or result.get("reply") is None:
            raise RuntimeError(f"Clawith attack turn did not complete: {status}")
        if result.get("oracleSuccess") is not (status == "oracle-success"):
            raise RuntimeError("attack turn status disagrees with Oracle")
        if result.get("turns") != len(self.events) + 1:
            raise RuntimeError("attack turn count is inconsistent")
        reply = str(result["reply"])
        event = {"episode": self.episode, "surface": self.surface,
                 "turn": result["turns"], "message": message, "reply": reply,
                 "oracleSuccess": result["oracleSuccess"], "verdict": result["verdict"]}
        self.events.append(event)
        self.history.extend([RTTurn(role="user", content=message),
                             RTTurn(role="assistant", content=reply)])
        return RTTurn(role="assistant", content=reply, metadata={
            "clawith_episode": self.episode, "clawith_surface": self.surface,
            "oracle_success": result["oracleSuccess"], "clawith_turn": result["turns"],
        })

    def metric_check(self, turns: list[RTTurn]) -> MetricVerdict:
        if self._content(turns) != self._content(self.history) or not self.events:
            raise RuntimeError("DeepTeam score has no matching Clawith turn")
        fired = self.events[-1]["oracleSuccess"]
        return MetricVerdict(score=0.0 if fired else 1.0,
                             reason=f"Oracle {self.surface} fired={fired} in episode {self.episode}")
