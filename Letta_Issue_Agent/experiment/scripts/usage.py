"""Read framework-side token usage without estimating from prompt text."""
import json
import math
import re

from common import read_json


def count(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def combine(records, source):
    if not records:
        return {"status": "unavailable", "source": source}
    values = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        parts = [record.get(field) for record in records]
        values[field] = sum(parts) if all(count(v) for v in parts) else None
    status = "reported" if all(v is not None for v in values.values()) else "partial"
    return {"status": status, "source": source, **values}


def normalize(record, input_key, output_key, total_key="total_tokens"):
    inputs, outputs = record.get(input_key), record.get(output_key)
    total = record.get(total_key)
    if total is None and count(inputs) and count(outputs):
        total = inputs + outputs
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": total}


def framework_usage(path, framework):
    directory = path / "framework"
    try:
        if framework == "promptfoo":
            records = []
            for file in directory.glob("*.json"):
                usage = read_json(file).get("results", {}).get("stats", {}).get("tokenUsage", {})
                # The MCP target has no LLM usage. Count each framework bucket once;
                # never add the per-case grader totals to these aggregate buckets.
                for name in ("attacker", "assertions", "generation"):
                    if name in usage:
                        records.append(normalize(usage[name], "prompt", "completion", "total"))
            return combine(records, "promptfoo.stats.tokenUsage")
        if framework == "deepteam":
            records = []
            for file in directory.glob("*/summary.json"):
                records.extend(normalize(v, "prompt_tokens", "completion_tokens")
                               for v in read_json(file).get("model_usage", {}).values())
            return combine(records, "deepteam.model_usage")
        if framework == "pyrit":
            file = directory / "usage.jsonl"
            records = [json.loads(line) for line in file.read_text().splitlines() if line.strip()] if file.exists() else []
            return combine(records, "pyrit.response.usage")
        if framework == "dreadnode":
            text = (path / "console.log").read_text()
            matches = re.findall(r"(?:^| · )(\d+(?:\.\d+)?)([kM]?) tokens(?: · |$)", text, re.MULTILINE)
            if matches:
                number, suffix = matches[-1]
                total = round(float(number) * {"": 1, "k": 1000, "M": 1000000}[suffix])
                return {"status": "approximate" if suffix else "reported", "source": "dreadnode.console",
                        "input_tokens": None, "output_tokens": None, "total_tokens": total}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"status": "unavailable", "source": framework, "error": str(exc)}
    return {"status": "unavailable", "source": framework}


def install_pyrit_usage_hook(path):
    """Install after PyRIT initialization; leave native configuration untouched."""
    from pyrit.prompt_target.openai.openai_target import OpenAITarget

    original = OpenAITarget._capture_response_metadata

    def capture(self, *, response, pieces):
        original(self, response=response, pieces=pieces)
        usage = getattr(response, "usage", None)
        record = normalize(usage.model_dump() if usage is not None else {},
                           "prompt_tokens", "completion_tokens")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    OpenAITarget._capture_response_metadata = capture
