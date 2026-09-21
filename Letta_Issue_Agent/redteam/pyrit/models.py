"""Explicit model endpoints: no global PyRIT defaults or Azure dependency."""
import os
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


def build_model(config):
    return OpenAIChatTarget(
        endpoint=config.base_url,
        model_name=config.model,
        api_key=os.environ.get(config.api_key_env, "offline-placeholder"),
        # PyRIT expands this dict as SDK keyword arguments. Vendor fields must
        # travel through the SDK's extra_body, which merges them into HTTP JSON.
        extra_body_parameters={"max_tokens": config.max_tokens, "extra_body": config.extra_body},
        httpx_client_kwargs={"timeout": config.timeout_seconds},
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(
            supports_multi_turn=True, supports_editable_history=True,
            supports_system_prompt=True, supports_json_output=True)),
    )


def build_attack(settings, target, attacker, evaluator):
    from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, RedTeamingAttack
    from pyrit.score import SelfAskTrueFalseScorer, TrueFalseQuestion
    scorer = SelfAskTrueFalseScorer.from_question(
        chat_target=evaluator,
        question=TrueFalseQuestion(true_description=settings.campaign.success_criteria))
    return RedTeamingAttack(
        objective_target=target,
        attack_adversarial_config=AttackAdversarialConfig(target=attacker),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=True),
        max_turns=settings.campaign.max_target_calls,
    )
