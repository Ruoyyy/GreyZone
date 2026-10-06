"""OpenAI-compatible model adapter for DeepTeam and DeepEval."""

import json
import os

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI


class CompatibleModel(DeepEvalBaseLLM):
    def __init__(self, config: dict, *, offline: bool = False):
        self.config = config
        options = dict(
            api_key="offline-placeholder" if offline else os.environ[config["api_key_env"]],
            base_url=config["base_url"],
            timeout=config.get("timeout_seconds", 120),
            max_retries=1,
        )
        self.client = OpenAI(**options)
        self.async_client = AsyncOpenAI(**options)
        super().__init__(config["model"])

    def load_model(self):
        return self.client

    def get_model_name(self):
        return self.config["model"]

    def _arguments(self, prompt, schema):
        messages = []
        if schema is not None:
            messages.append({"role": "system", "content":
                "Return only valid JSON matching this JSON Schema: "
                + json.dumps(schema.model_json_schema())})
        messages.append({"role": "user", "content": prompt})
        args = dict(
            model=self.config["model"], messages=messages,
            max_tokens=self.config.get("max_tokens", 4096),
            extra_body=self.config.get("extra_body", {}),
        )
        if schema is not None:
            args["response_format"] = {"type": "json_object"}
        return args

    def _parse(self, response, schema):
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ValueError("Model output truncated; increase max_tokens")
        value = choice.message.content
        if not value:
            raise ValueError("Model returned no content")
        return schema.model_validate_json(value) if schema is not None else value

    def generate(self, prompt, schema=None):
        return self._parse(self.client.chat.completions.create(
            **self._arguments(prompt, schema)), schema)

    async def a_generate(self, prompt, schema=None):
        response = await self.async_client.chat.completions.create(
            **self._arguments(prompt, schema))
        return self._parse(response, schema)

    async def close(self):
        self.client.close()
        await self.async_client.close()
