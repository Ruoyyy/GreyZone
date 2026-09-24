#!/usr/bin/env python3
"""Lakera Red -> local SDK relay -> MCP -> Gitea issue -> Letta Code."""
import argparse
import asyncio
import importlib.metadata
import os
from pathlib import Path
import sys

import httpx
from lakera_red_sdk import LakeraRedClient
from pydantic import ValidationError

from campaign import Campaign, collect_results, validate_results
from issue_transport import connect
from reporting import Report, now, exception_details, exception_summary
from sdk_adapter import create_scan, scan_options
from settings import HERE, MissingCredentialError, load_settings


class ReportLogger:
    def __init__(self, report):
        self.report = report
        self.idle_timeout = False

    def log(self, level, message, fields=None):
        if "idle timeout reached" in message:
            self.idle_timeout = True
        self.report.event("sdk", {"event": "sdk_log", "level": level,
                                  "message": message, "fields": fields or {}})

    def debug(self, message, fields=None):
        pass

    def info(self, message, fields=None):
        self.log("info", message, fields)

    def warn(self, message, fields=None):
        self.log("warn", message, fields)

    def error(self, message, fields=None):
        self.log("error", message, fields)


async def check(settings, objective, online):
    from lakera_red_sdk import ReconContext
    options = scan_options(settings, objective, "offline")
    assert options["strategy"].max_retries == 0 and options["concurrency"] == 1
    ReconContext(**settings.profile.model_dump()).to_json()
    print(f"SDK {importlib.metadata.version('lakera-red-sdk')}, configuration and scan options: OK")
    print(f"Target reply deadline: {settings.target.reply_timeout_seconds:g}s; "
          f"separate SDK relay idle deadline: {settings.sdk_idle_seconds:g}s")
    if online:
        async with connect(settings.target):
            print("MCP initialization and issue_write/issue_read discovery: OK")
    present = bool(os.getenv(settings.red.api_key_env, "").strip())
    print(f"Red API credential present: {present}; access/quota not verified")
    print("No Red API calls or issue writes were made.")
    return 0


async def execute(settings, objective):
    report = Report(HERE / "results")
    logger = ReportLogger(report)
    state = {"run_id": report.run_id, "status": "running", "started_at": now(),
             "oracle": {"status": "not_collected"}}
    report.write("manifest.json", {"run_id": report.run_id, "created_at": now(),
        "settings": settings.model_dump(), "objective": objective,
        "versions": {p: importlib.metadata.version(p) for p in ("lakera-red-sdk", "mcp")},
        "sdk_idle_seconds": settings.sdk_idle_seconds,
        "isolation": "One issue per session; Letta state and repository are not reset",
        "transport": "MCP issue_write/issue_read; no direct Gitea HTTP writes"})
    report.write("summary.json", state)
    print(f"Results: {report.path}", flush=True)
    campaign = None
    api_key = os.environ[settings.red.api_key_env].strip()
    try:
        # MCP context managers remain in this task for their entire lifetime.
        async with connect(settings.target) as transport:
            async with LakeraRedClient(api_key=api_key, base_url=settings.red.base_url, logger=logger) as client:
                async with httpx.AsyncClient() as http_client:
                    campaign = Campaign(settings, transport, report)
                    async with asyncio.timeout(settings.campaign.timeout_seconds):
                        scan = await create_scan(client, http_client, settings, objective, report, api_key, logger)
                        state.update(scan_id=scan.scan_id, dashboard_link=scan.dashboard_link)
                        report.write("summary.json", state)
                        print(f"Red report: {scan.dashboard_link}", flush=True)
                        await scan.run(campaign.handle)
                    state["status"] = "collecting_results"
                    report.write("summary.json", state)
                    try:
                        results = await collect_results(scan, report, settings.campaign.results_timeout_seconds)
                        state["status"] = ("completed" if campaign.complete and not logger.idle_timeout
                                           and validate_results(results, settings) else "error")
                    except TimeoutError:
                        state.update(status="results_timeout", error="Red evaluation did not become ready")
    except asyncio.CancelledError:
        state.update(status="interrupted", error="Interrupted; inspect pending issue and Red dashboard")
        raise
    except TimeoutError:
        state.update(status="campaign_timeout", error="Campaign deadline exceeded")
    except Exception as exc:
        state.update(status="error", error=exception_summary(exc), error_details=exception_details(exc))
    finally:
        state.update(finished_at=now(), sdk_idle_timeout=logger.idle_timeout,
                     sessions=list(campaign.sessions.values()) if campaign else [])
        report.write("summary.json", state)
        if state.get("error"):
            print("Failure: " + report._json(state["error"]), file=sys.stderr, flush=True)
        print(f"Campaign: {state['status']}; details: {report.path / 'summary.json'}", flush=True)
    return 0 if state["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--scenario", help="Benchmark scenario name under benchmark/scenarios")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline-check", action="store_true")
    group.add_argument("--check", action="store_true", help="Read-only MCP check; no Red scan or posts")
    args = parser.parse_args()
    os.umask(0o077)
    settings, objective = load_settings(args.config, require_credentials=not (args.check or args.offline_check),
                                        scenario_id=args.scenario)
    if args.check or args.offline_check:
        return asyncio.run(check(settings, objective, args.check))
    sys.path.insert(0, str(HERE.parent))
    from run_locked import acquire
    descriptor = acquire()
    try:
        return asyncio.run(execute(settings, objective))
    finally:
        os.close(descriptor)


def startup_error(exc):
    if isinstance(exc, MissingCredentialError):
        return (f"Missing credential: {exc.variable}. Set it in {HERE / '.env'} "
                "or export it in the environment. Use a Check Point Red Team API key.")
    if isinstance(exc, ValidationError):
        # Do not print Pydantic's input values, which may contain credentials.
        fields = [f"{'.'.join(map(str, e['loc']))} ({e['type']})"
                  for e in exc.errors(include_input=False, include_context=False, include_url=False)]
        return "Invalid configuration: " + "; ".join(fields)
    if isinstance(exc, FileNotFoundError):
        return f"Configuration or objective file not found: {exc.filename}"
    if isinstance(exc, ValueError) and str(exc) == "Objective file is empty":
        return "Objective file is empty; fill in the benchmark scenario's objective.md."
    return f"Startup failed ({type(exc).__name__}); check configuration and MCP connectivity."


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(startup_error(exc), file=sys.stderr)
        sys.exit(1)
