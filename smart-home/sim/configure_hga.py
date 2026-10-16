"""One-shot Home Generative Agent setup for the hga-sim stack.

Fill in the CONFIG section, then from the sim/ directory run:

    python3 configure_hga.py

On the host it re-runs itself inside the homeassistant container (which has
aiohttp), so nothing is installed locally. It is safe to re-run: existing
providers are reconfigured in place and the feature setup is redone.

Steps: onboarding (or login) -> add integration -> model providers ->
+ Setup (Advanced: features + database) -> integration options ->
Assist pipeline -> one test sentence.
"""

# =============================== CONFIG =====================================

# HA admin account. Created on a fresh instance; used to log in otherwise.
HA_NAME = "Admin"
HA_USERNAME = "admin"
HA_PASSWORD = "admin_password"

# Model providers, keyed by the name shown in HA. The chat provider below is an
# EXAMPLE (DeepSeek via its OpenAI-compatible endpoint) — replace it with
# whatever you have. `provider_type` is one of:
#   openai_compatible  -> deployment "edge", settings: base_url + api_key
#   ollama             -> deployment "edge", settings: base_url
#   openai | gemini | anthropic -> deployment "cloud", settings: api_key
# Then point the FEATURES below at this provider's name and a model it serves.
PROVIDERS = {
    "Chat": {
        "deployment": "edge",  # OpenAI Compatible is listed under Edge
        "provider_type": "openai_compatible",
        "settings": {
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-REPLACE_ME",
            "openai_compatible_embedding_dims": 768,
        },
    },
    # Fake vision model from mock-vlm/ (replies with mock-vlm/caption.txt).
    # Leave as-is; it is how the lab controls what the agent "sees".
    "MockVLM": {
        "deployment": "edge",
        "provider_type": "ollama",
        "settings": {"base_url": "http://localhost:11500"},
    },
}

# Features: provider name (a PROVIDERS key) + a model that provider serves.
# None = feature disabled. "reasoning" (Conversation only): "default" sends no
# thinking parameters. Set model_name to a model your provider actually serves
# (the examples below are DeepSeek's).
FEATURES = {
    "conversation": {
        "provider": "Chat",
        "model_name": "deepseek-chat",
        "temperature": 0.2,
        "reasoning": "default",
    },
    "conversation_summary": {
        "provider": "Chat",
        "model_name": "deepseek-chat",
        "temperature": 0.2,
    },
    "camera_image_analysis": {
        "provider": "MockVLM",
        "model_name": "mock-vlm:latest",
        "temperature": 0.2,
    },
    # Enable by pointing at an embedding-capable provider; off here because the
    # example chat provider (DeepSeek) has no embeddings endpoint.
    "embedding": None,
}

# Integration options to change (everything else keeps its current value).
OPTIONS = {
    "video_analyzer_mode": "always_notify",  # disable | notify_on_anomaly | always_notify
}

# Sentence sent to the agent at the end to prove it works ("" to skip).
TEST_SENTENCE = "How many lights are on?"

# ============================================================================

import asyncio
import json
import pathlib
import subprocess
import sys

HA = "http://localhost:8123"
CLIENT_ID = f"{HA}/"
DOMAIN = "home_generative_agent"
AGENT_ID = f"conversation.{DOMAIN}"

# Fixed: matches docker-compose.yaml (HA shares pgvector's network namespace).
DATABASE = {
    "username": "admin_user",
    "password": "admin_password",
    "host": "localhost",
    "port": 5432,
    "db_name": "admin_db",
    "db_params": [{"key": "sslmode", "value": "disable"}],
}

FEATURE_NAMES = {
    "Conversation": "conversation",
    "Camera Image Analysis": "camera_image_analysis",
    "Conversation Summary": "conversation_summary",
    "Embeddings": "embedding",
}

if not pathlib.Path("/.dockerenv").exists():
    # On the host: run this same file inside the HA container, so it always
    # targets this compose project's HA (and uses the container's aiohttp).
    here = pathlib.Path(__file__).resolve().parent
    with open(__file__, "rb") as src:
        sys.exit(
            subprocess.call(
                ["docker", "compose", "exec", "-T", "homeassistant", "python3", "-"],
                stdin=src,
                cwd=here,
            )
        )

import aiohttp


def die(msg):
    sys.exit(f"ERROR: {msg}")


class HA_API:
    def __init__(self, session):
        self.s = session
        self.token = None

    async def req(self, method, path, body=None, *, form=False, auth=True):
        headers = {"Authorization": f"Bearer {self.token}"} if auth else {}
        kwargs = {"data": body} if form else {"json": body}
        async with self.s.request(method, HA + path, headers=headers, **kwargs) as r:
            text = await r.text()
            if r.status >= 400:
                die(f"{method} {path} -> HTTP {r.status}: {text[:500]}")
            return json.loads(text) if text else None

    async def token_from_code(self, code):
        res = await self.req(
            "POST", "/auth/token",
            {"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
            form=True, auth=False,
        )
        self.token = res["access_token"]

    async def login(self):
        # HA removes the onboarding API once onboarding is complete (404).
        async with self.s.get(HA + "/api/onboarding") as r:
            steps = await r.json() if r.status == 200 else []
        done = {s["step"] for s in steps if s["done"]}
        if r.status == 404:
            done = {"user", "core_config", "analytics", "integration"}
        if "user" not in done:
            res = await self.req("POST", "/api/onboarding/users", {
                "client_id": CLIENT_ID, "name": HA_NAME, "username": HA_USERNAME,
                "password": HA_PASSWORD, "language": "en",
            }, auth=False)
            await self.token_from_code(res["auth_code"])
            print(f"onboarding: created user {HA_USERNAME!r}")
        else:
            flow = await self.req("POST", "/auth/login_flow", {
                "client_id": CLIENT_ID, "handler": ["homeassistant", None],
                "redirect_uri": CLIENT_ID,
            }, auth=False)
            res = await self.req("POST", f"/auth/login_flow/{flow['flow_id']}", {
                "client_id": CLIENT_ID, "username": HA_USERNAME, "password": HA_PASSWORD,
            }, auth=False)
            if res.get("type") != "create_entry":
                die(f"login as {HA_USERNAME!r} failed: {res.get('errors')}")
            await self.token_from_code(res["result"])
            print(f"login: {HA_USERNAME!r}")
        for step in ("core_config", "analytics"):
            if step not in done:
                await self.req("POST", f"/api/onboarding/{step}", {})
        if "integration" not in done:
            await self.req("POST", "/api/onboarding/integration",
                           {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID})

    async def run_flow(self, base, start_body, answer, label):
        """Walk a data-entry flow: each form gets its current values plus answer()."""
        res = await self.req("POST", base, start_body)
        for _ in range(30):
            if res["type"] != "form":
                break
            fields = res.get("data_schema") or []
            names = {f["name"] for f in fields}
            payload = {}
            for f in fields:
                value = (f.get("description") or {}).get("suggested_value")
                if value is None:
                    value = f.get("default")
                if value is not None:
                    payload[f["name"]] = value
            wanted = answer(res["step_id"], res) or {}
            unknown = set(wanted) - names
            if unknown:
                feature = (res.get("description_placeholders") or {}).get("feature_name", "")
                die(f"{label}: step {res['step_id']!r} {feature} has no field(s) {sorted(unknown)}; "
                    f"available: {sorted(names)}")
            payload.update(wanted)
            step = res["step_id"]
            res = await self.req("POST", f"{base}/{res['flow_id']}", payload)
            if res["type"] == "form" and res["step_id"] == step and res.get("errors"):
                die(f"{label}: step {step!r} rejected: {res['errors']} "
                    f"{res.get('description_placeholders') or ''}")
        if res["type"] == "abort" and res["reason"] not in (
            "reconfigure_successful", "setup_complete", "single_instance_allowed",
            "already_configured",
        ):
            die(f"{label}: aborted ({res['reason']})")
        print(f"{label}: {res.get('reason') or res['type']}")
        return res

    async def entry(self):
        entries = await self.req("GET", f"/api/config/config_entries/entry?domain={DOMAIN}")
        return entries[0] if entries else None

    async def subentries(self, entry_id):
        (subs,) = await ws_call(
            self, [{"type": "config_entries/subentries/list", "entry_id": entry_id}]
        )
        return subs


async def ws_call(api, msgs):
    """Send websocket commands in order; return their results."""
    out = []
    async with api.s.ws_connect(HA.replace("http", "ws") + "/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": api.token})
        if (await ws.receive_json())["type"] != "auth_ok":
            die("websocket auth failed")
        for i, msg in enumerate(msgs, 1):
            await ws.send_json({"id": i, **msg})
            while (res := await ws.receive_json()).get("id") != i:
                pass
            if not res["success"]:
                die(f"websocket {msg['type']}: {res['error']}")
            out.append(res["result"])
    return out


async def main():
    async with aiohttp.ClientSession() as s:
        api = HA_API(s)
        await api.login()

        # 1. Add the integration (single instance; aborts if already added).
        if await api.entry() is None:
            await api.run_flow("/api/config/config_entries/flow",
                               {"handler": DOMAIN}, lambda *_: {}, "integration")
        entry_id = (await api.entry())["entry_id"]
        sub_base = "/api/config/config_entries/subentries/flow"

        # 2. Model providers: create, or reconfigure the one with the same name.
        for name, p in PROVIDERS.items():
            existing = {s["title"]: s["subentry_id"] for s in await api.subentries(entry_id)
                        if s["subentry_type"] == "model_provider"}
            body = {"handler": [entry_id, "model_provider"]}
            if name in existing:
                body["subentry_id"] = existing[name]
            answers = {
                "deployment": {"deployment": p["deployment"]},
                "provider": {"provider_type": p["provider_type"], "name": name},
                "settings": p["settings"],
            }
            await api.run_flow(sub_base, body, lambda step, _: answers.get(step),
                               f"provider {name}")

        provider_ids = {s["title"]: s["subentry_id"] for s in await api.subentries(entry_id)
                        if s["subentry_type"] == "model_provider"}

        # 3. + Setup -> Advanced: enable features, pick provider/model each, database.
        def setup_answer(step, res):
            if step == "setup_mode":
                return {"setup_mode": "advanced"}
            if step == "feature_enable":
                return {ft: cfg is not None for ft, cfg in FEATURES.items()
                        if ft != "conversation"}
            if step in ("feature_provider", "feature_model"):
                ft = FEATURE_NAMES[res["description_placeholders"]["feature_name"]]
                cfg = FEATURES[ft]
                if step == "feature_provider":
                    return {"model_provider_id": provider_ids[cfg["provider"]]}
                return {k: v for k, v in cfg.items() if k != "provider"}
            if step == "database":
                return DATABASE
            return {}

        await api.run_flow(sub_base, {"handler": [entry_id, "feature"]},
                           setup_answer, "setup (advanced)")

        # 4. Integration options.
        if OPTIONS:
            await api.run_flow("/api/config/config_entries/options/flow",
                               {"handler": entry_id}, lambda *_: OPTIONS, "options")

        # 5. Assist pipeline: make HGA the conversation agent of the preferred one.
        (pl,) = await ws_call(api, [{"type": "assist_pipeline/pipeline/list"}])
        pipeline = next(p for p in pl["pipelines"] if p["id"] == pl["preferred_pipeline"])
        update = {k: v for k, v in pipeline.items() if k != "id"}
        update["conversation_engine"] = AGENT_ID
        await ws_call(api, [{"type": "assist_pipeline/pipeline/update",
                             "pipeline_id": pipeline["id"], **update}])
        print(f"pipeline {pipeline['name']!r}: conversation agent -> {AGENT_ID}")

        # 6. Wait for the reload the flows scheduled, then talk to the agent.
        for _ in range(60):
            e = await api.entry()
            if e["state"] == "loaded":
                break
            await asyncio.sleep(2)
        else:
            die(f"integration did not load (state: {e['state']}); "
                "see: docker compose logs homeassistant")
        if TEST_SENTENCE:
            await asyncio.sleep(5)
            res = await api.req("POST", "/api/conversation/process", {
                "text": TEST_SENTENCE, "agent_id": AGENT_ID, "language": "en",
            })
            print(f"test> {TEST_SENTENCE}\nagent> {res['response']['speech']['plain']['speech']}")
        print("done: http://localhost:8123")


asyncio.run(main())
