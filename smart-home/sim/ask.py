"""Send one sentence to the HGA agent and print its reply.  python3 ask.py "text" """
import json
import pathlib
import sys
import urllib.request

tok = next(l.split("=", 1)[1].strip()
           for l in (pathlib.Path(__file__).parent / "secrets.env").read_text().splitlines()
           if l.startswith("HA_TOKEN="))
req = urllib.request.Request(
    "http://localhost:8123/api/conversation/process", method="POST",
    data=json.dumps({"text": sys.argv[1], "agent_id": "conversation.home_generative_agent",
                     "language": "en"}).encode(),
    headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=180))["response"]["speech"]["plain"]["speech"])
