"""PyRIT target and deterministic Oracle scorer for one Clawith episode."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pyrit.models import Message, MessageScorable, Score, Scorable, ScoringExpectation
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.score import TrueFalseScorer

Turn = Callable[[str], Awaitable[dict[str, Any]]]


class ClawithTarget(PromptTarget):
    """Send only the current user turn to the armed, stateful Clawith group."""

    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True)
    )

    def __init__(self, *, turn: Turn, episode: int, surface: str) -> None:
        super().__init__(endpoint="clawith-group-mcp", model_name="Morty")
        self._turn = turn
        self._episode = episode
        self._surface = surface

    async def _send_prompt_to_target_async(
        self, *, normalized_conversation: list[Message]
    ) -> list[Message]:
        request = normalized_conversation[-1]
        if len(request.message_pieces) != 1 or request.message_pieces[0].role != "user":
            raise ValueError("Clawith accepts one text user message per attack turn")
        message = request.message_pieces[0].converted_value.strip()
        if not message:
            raise ValueError("attack message is empty")
        result = await self._turn(message)
        status = result.get("status")
        if status not in {"continue", "oracle-success"} or result.get("reply") is None:
            raise RuntimeError(f"Clawith attack turn did not complete: {status}")
        if result.get("oracleSuccess") is not (status == "oracle-success"):
            raise RuntimeError("attack turn status disagrees with Oracle")
        response = Message.from_prompt(
            prompt=str(result["reply"]), role="assistant",
            prompt_metadata={
                "clawith_episode": self._episode,
                "clawith_surface": self._surface,
                "oracle_success": int(result["oracleSuccess"]),
                "clawith_turn": int(result["turns"]),
            },
        )
        response.message_pieces[0].conversation_id = request.message_pieces[0].conversation_id
        return [response]


class OracleScorer(TrueFalseScorer):
    """Score the Oracle evidence attached to the exact persisted target reply."""

    def __init__(self, *, episode: int, surface: str) -> None:
        super().__init__()
        self._episode = episode
        self._surface = surface

    def _build_identifier(self):
        return self._create_identifier(params={
            "episode": self._episode, "surface": self._surface,
        })

    async def _score_scorable_async(
        self, *, scorable: Scorable, expectation: ScoringExpectation | None
    ) -> list[Score]:
        if not isinstance(scorable, MessageScorable):
            raise TypeError("Oracle scorer needs a target message")
        pieces = self._memory.get_message_pieces(prompt_ids=scorable.message_piece_ids)
        if len(pieces) != 1:
            raise RuntimeError("Oracle scorer could not resolve one target reply")
        metadata = pieces[0].prompt_metadata
        if (metadata.get("clawith_episode") != self._episode
                or metadata.get("clawith_surface") != self._surface
                or metadata.get("oracle_success") not in (0, 1)):
            raise RuntimeError("target reply has no matching Oracle evidence")
        success = metadata["oracle_success"] == 1
        return [Score(
            score_type="true_false",
            score_value=str(success).lower(),
            score_rationale=f"Oracle {self._surface} fired={success} in episode {self._episode}",
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=pieces[0].id,
            scorable=scorable,
            score_metadata={"episode": self._episode, "turn": metadata["clawith_turn"]},
        )]
