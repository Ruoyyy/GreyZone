"""OpenAI-compatible models with the schema contract required by DeepTeam."""
import json
import os

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI


class CompatibleModel(DeepEvalBaseLLM):
    def __init__(self, config):
        self.config = config
        options = dict(api_key=os.environ[config.api_key_env], base_url=config.base_url,
                       timeout=config.timeout_seconds, max_retries=1)
        self.client = OpenAI(**options)
        self.async_client = AsyncOpenAI(**options)
        self.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
        super().__init__(config.model)

    def load_model(self):
        return self.client

    def get_model_name(self):
        return self.config.model

    def _arguments(self, prompt, schema):
        messages = []
        if schema is not None:
            messages.append({"role": "system", "content":
                "Return only valid JSON matching this JSON Schema: "
                + json.dumps(schema.model_json_schema())})
        messages.append({"role": "user", "content": prompt})
        args = dict(model=self.config.model, messages=messages,
                    max_tokens=self.config.max_tokens, extra_body=self.config.extra_body)
        if schema is not None:
            args["response_format"] = {"type": "json_object"}
        return args

    def _parse(self, response, schema):
        self.usage["requests"] += 1
        if response.usage:
            self.usage["prompt_tokens"] += response.usage.prompt_tokens
            self.usage["completion_tokens"] += response.usage.completion_tokens
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ValueError("Model output truncated; increase max_tokens")
        text = choice.message.content
        if not text:
            raise ValueError("Model returned no content")
        return schema.model_validate_json(text) if schema is not None else text

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
