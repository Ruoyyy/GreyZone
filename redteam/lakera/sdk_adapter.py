"""Public SDK 0.9 APIs with a separate relay-idle deadline."""
from lakera_red_sdk import CrescendoStrategyOptions, CustomObjective, ReconContext, Scan


def scan_options(settings, objective, run_id):
    return dict(
        name=f"AgentThreatDataset-{run_id}",
        concurrency=1,
        strategy=CrescendoStrategyOptions(
            max_turns=settings.campaign.max_target_calls, max_retries=0,
            number_of_attacks_per_objective=settings.campaign.cases, early_stop_score=4),
        custom_objectives=[CustomObjective(
            key=settings.campaign.objective_key, name=settings.campaign.objective_name,
            attack_description=objective, success_indicators=settings.campaign.success_indicators)],
    )


async def create_scan(client, http_client, settings, objective, report, api_key, logger):
    target = await client.create_or_get_target(
        settings.red.target_name, app_context=ReconContext(**settings.profile.model_dump()))
    report.write("target.json", {"target_id": target.target_id, "name": target.name})
    created = await client.create_scan(target.target_id,
                                      **scan_options(settings, objective, report.run_id))
    report.write("scan.json", {"scan_id": created.scan_id, "target_id": target.target_id,
                              "dashboard_link": created.dashboard_link})
    return bind_scan(created, target.target_id, http_client, settings, api_key, logger)


def bind_scan(created, target_id, http_client, settings, api_key, logger):
    # create_scan does not expose idle_timeout_s. Rebind the created resource
    # through the exported public Scan constructor; no private field patching.
    return Scan(scan_id=created.scan_id, target_id=target_id, concurrency=1,
        base_url=settings.red.base_url, headers={"Authorization": f"Bearer {api_key}"},
        dashboard_link=created.dashboard_link, http_client=http_client,
        idle_timeout_s=settings.sdk_idle_seconds, logger=logger)
