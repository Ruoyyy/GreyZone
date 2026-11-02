"""
Exposes connection actions to the LLM as function-calling tools.
Tools are named "<connection>.<action>" in agent configs, e.g. "twitter.read-timeline".
"""
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger("tools")

JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


class ToolRegistry:
    def __init__(self, connection_manager, tool_specs: List[str]):
        self._targets = {}  # tool name -> (connection, action)
        self.schemas = []

        for spec in tool_specs:
            connection_name, action_name = spec.split(".", 1)
            connection = connection_manager.connections[connection_name]
            action = connection.actions[action_name]

            # Function names may only contain [a-zA-Z0-9_-]
            name = f"{connection_name}_{action_name}".replace("-", "_")
            self._targets[name] = (connection, action)
            self.schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": action.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            p.name: {"type": JSON_TYPES.get(p.type, "string"), "description": p.description}
                            for p in action.parameters
                        },
                        "required": [p.name for p in action.parameters if p.required],
                    },
                },
            })

    def call(self, name: str, arguments: str) -> str:
        """Run a tool call and return its result (or error) as a JSON string for the LLM"""
        if name not in self._targets:
            return json.dumps({"error": f"Unknown tool: {name}"})

        connection, action = self._targets[name]
        try:
            kwargs: Dict[str, Any] = json.loads(arguments or "{}")
            result = connection.perform_action(action.name, kwargs)
            return json.dumps({"result": result}, default=str, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Tool {name} failed: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
