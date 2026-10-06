"""PyRIT attacker and advisory evaluator, using explicit OpenAI-compatible models."""

import os

from pyrit.prompt_target import OpenAIChatTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


def build_model(config: dict) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        endpoint=config["base_url"],
        model_name=config["model"],
        api_key=os.environ.get(config["api_key_env"], "offline-placeholder"),
        extra_body_parameters={
            "max_tokens": config.get("max_tokens", 4096),
            "extra_body": config.get("extra_body", {}),
        },
        httpx_client_kwargs={"timeout": config.get("timeout_seconds", 120)},
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(
            supports_multi_turn=True, supports_editable_history=True,
            supports_system_prompt=True, supports_json_output=True,
        )),
    )


def build_attack(target, attacker, evaluator, success_criteria: str):
    from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, RedTeamingAttack
    from pyrit.score import SelfAskTrueFalseScorer, TrueFalseQuestion

    scorer = SelfAskTrueFalseScorer.from_question(
        chat_target=evaluator,
        question=TrueFalseQuestion(true_description=success_criteria),
    )
    return RedTeamingAttack(
        objective_target=target,
        attack_adversarial_config=AttackAdversarialConfig(target=attacker),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=scorer, use_score_as_feedback=True,
        ),
        max_turns=1,
    )
