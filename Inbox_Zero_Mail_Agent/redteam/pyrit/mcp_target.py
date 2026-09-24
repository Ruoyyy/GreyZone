"""PyRIT 1.1 target for one customer-mail conversation."""
import asyncio

from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

from mail_transport import MailTarget, TargetError


class CustomerMailTarget(PromptTarget):
    def __init__(self, *, transport, config, budget, subject, record=lambda event: None):
        super().__init__(endpoint=config.transport_url, custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=True)))
        self.mail_target = MailTarget(transport, config, budget, subject, record)
        self.conversation_id = None
        self.history = []
        self.guard = asyncio.Lock()

    @staticmethod
    def fingerprint(message):
        piece = message.get_piece()
        if len(message.message_pieces) != 1 or piece.converted_value_data_type != "text":
            raise TargetError("Only single-piece text messages are supported")
        return (piece.role, piece.converted_value)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]):
        async with self.guard:
            current = normalized_conversation[-1]
            request = current.get_piece()
            if request.role != "user":
                raise TargetError("Only user messages may be submitted to the mail thread")
            if self.conversation_id not in (None, request.conversation_id):
                raise TargetError("A target instance cannot be reused for another conversation")
            fingerprints = [self.fingerprint(m) for m in normalized_conversation]
            if fingerprints[:-1] != self.history:
                raise TargetError("History was rewritten, replayed or prepended; mail thread cannot be rewound")
            self.conversation_id = request.conversation_id
            reply = await self.mail_target.send(request.converted_value)
            response = construct_response_from_request(request=request, response_text_pieces=[reply])
            self.history = fingerprints + [self.fingerprint(response)]
            return [response]
