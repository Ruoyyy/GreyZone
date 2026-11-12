"""Compatibility fix for Dreadnode 2.0.46 and the pinned Gitea Go MCP SDK.

The SDK rejects the empty probe body as plain-text HTTP 400. That does not
mean it lacks Streamable HTTP. Preserve all other probe and auth handling.
Run using the Python interpreter from the Dreadnode installation.
"""
from importlib.metadata import version
from pathlib import Path


def main():
    if version("dreadnode") != "2.0.46":
        raise SystemExit("Only Dreadnode 2.0.46 is supported; recheck newer clients first")
    from dreadnode.agents.mcp import client
    path = Path(client.__file__)
    source = path.read_text()
    marker = "# GreyZone: recognize the Go MCP SDK's invalid-version probe rejection."
    if marker in source:
        print("MCP probe compatibility fix already installed")
        return
    old = '''        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            return False'''
    new = '''        content_type = response.headers.get("content-type", "").lower()
        # GreyZone: recognize the Go MCP SDK's invalid-version probe rejection.
        if "text/plain" in content_type and response.status_code == 400:
            return response.text.strip() == 'malformed payload: invalid message version tag ""; expected "2.0"'
        if "application/json" not in content_type:
            return False'''
    if source.count(old) != 1:
        raise SystemExit("Unexpected SDK source; refusing to patch")
    updated = source.replace(old, new)
    compile(updated, str(path), "exec")
    path.write_text(updated)
    print("Installed narrowly scoped MCP probe compatibility fix")


if __name__ == "__main__":
    main()
