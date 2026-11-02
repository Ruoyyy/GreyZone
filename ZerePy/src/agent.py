import json
import random
import time
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from src.connection_manager import ConnectionManager
from src.helpers import print_h_bar
from src.action_handler import execute_action
from src.prompts import AGENT_OPERATING_PROMPT, AGENT_CYCLE_PROMPT
from src.tools import ToolRegistry
from src.run_log import RunLog
import src.actions.twitter_actions  
import src.actions.echochamber_actions
import src.actions.solana_actions
from datetime import datetime

REQUIRED_FIELDS = ["name", "bio", "traits", "examples", "loop_delay", "config", "tasks"]

logger = logging.getLogger("agent")

class ZerePyAgent:
    def __init__(
            self,
            agent_name: str
    ):
        try:
            agent_path = Path("agents") / f"{agent_name}.json"
            agent_dict = json.load(open(agent_path, "r"))

            missing_fields = [field for field in REQUIRED_FIELDS if field not in agent_dict]
            if missing_fields:
                raise KeyError(f"Missing required fields: {', '.join(missing_fields)}")

            self.name = agent_dict["name"]
            self.bio = agent_dict["bio"]
            self.traits = agent_dict["traits"]
            self.examples = agent_dict["examples"]
            self.example_accounts = agent_dict["example_accounts"]
            self.loop_delay = agent_dict["loop_delay"]
            self.connection_manager = ConnectionManager(agent_dict["config"])
            self.use_time_based_weights = agent_dict["use_time_based_weights"]
            self.time_based_multipliers = agent_dict["time_based_multipliers"]

            has_twitter_tasks = any("tweet" in task["name"] for task in agent_dict.get("tasks", []))
            
            twitter_config = next((config for config in agent_dict["config"] if config["name"] == "twitter"), None)
            
            if has_twitter_tasks and twitter_config:
                self.tweet_interval = twitter_config.get("tweet_interval", 900)
                self.own_tweet_replies_count = twitter_config.get("own_tweet_replies_count", 2)

            # Extract Echochambers config
            echochambers_config = next((config for config in agent_dict["config"] if config["name"] == "echochambers"), None)
            if echochambers_config:
                self.echochambers_message_interval = echochambers_config.get("message_interval", 60)
                self.echochambers_history_count = echochambers_config.get("history_read_count", 50)

            self.is_llm_set = False

            # Cache for system prompt
            self._system_prompt = None

            # Extract loop tasks
            self.tasks = agent_dict.get("tasks", [])
            self.task_weights = [task.get("weight", 0) for task in self.tasks]
            self.logger = logging.getLogger("agent")

            # Set up empty agent state
            self.state = {}

            # Tool-driven mode: the LLM picks actions itself via function calling
            self.tools = ToolRegistry(self.connection_manager, agent_dict["tools"]) if agent_dict.get("tools") else None
            self.max_tool_steps = agent_dict.get("max_tool_steps", 8)
            self.memory_cycles = agent_dict.get("memory_cycles", 5)
            self._cycle_history = []  # past cycles' messages, oldest first
            self._run_log = None  # created on the first cycle
            self._cycle_no = 0

        except Exception as e:
            logger.error("Could not load ZerePy agent")
            raise e

    def _setup_llm_provider(self):
        # Get first available LLM provider and its model
        llm_providers = self.connection_manager.get_model_providers()
        if not llm_providers:
            raise ValueError("No configured LLM provider found")
        self.model_provider = llm_providers[0]

        # Load Twitter username for self-reply detection if Twitter tasks exist
        if any("tweet" in task["name"] for task in self.tasks):
            load_dotenv()
            self.username = os.getenv('TWITTER_USERNAME', '').lower()
            if not self.username:
                logger.warning("Twitter username not found, some Twitter functionalities may be limited")

    def _construct_system_prompt(self) -> str:
        """Construct the system prompt from agent configuration"""
        if self._system_prompt is None:
            prompt_parts = []
            prompt_parts.extend(self.bio)

            if self.traits:
                prompt_parts.append("\nYour key traits are:")
                prompt_parts.extend(f"- {trait}" for trait in self.traits)

            if self.examples or self.example_accounts:
                prompt_parts.append("\nHere are some examples of your style (Please avoid repeating any of these):")
                if self.examples:
                    prompt_parts.extend(f"- {example}" for example in self.examples)

                if self.example_accounts:
                    for example_account in self.example_accounts:
                        tweets = self.connection_manager.perform_action(
                            connection_name="twitter",
                            action_name="get-latest-tweets",
                            params=[example_account]
                        )
                        if tweets:
                            prompt_parts.extend(f"- {tweet['text']}" for tweet in tweets)

            self._system_prompt = "\n".join(prompt_parts)

        return self._system_prompt
    
    def _adjust_weights_for_time(self, current_hour: int, task_weights: list) -> list:
        weights = task_weights.copy()
        
        # Reduce tweet frequency during night hours (1 AM - 5 AM)
        if 1 <= current_hour <= 5:
            weights = [
                weight * self.time_based_multipliers.get("tweet_night_multiplier", 0.4) if task["name"] == "post-tweet"
                else weight
                for weight, task in zip(weights, self.tasks)
            ]
            
        # Increase engagement frequency during day hours (8 AM - 8 PM) (peak hours?🤔)
        if 8 <= current_hour <= 20:
            weights = [
                weight * self.time_based_multipliers.get("engagement_day_multiplier", 1.5) if task["name"] in ("reply-to-tweet", "like-tweet")
                else weight
                for weight, task in zip(weights, self.tasks)
            ]
        
        return weights

    def prompt_llm(self, prompt: str, system_prompt: str = None) -> str:
        """Generate text using the configured LLM provider"""
        system_prompt = system_prompt or self._construct_system_prompt()

        return self.connection_manager.perform_action(
            connection_name=self.model_provider,
            action_name="generate-text",
            params=[prompt, system_prompt]
        )

    def perform_action(self, connection: str, action: str, **kwargs) -> None:
        return self.connection_manager.perform_action(connection, action, **kwargs)
    
    def select_action(self, use_time_based_weights: bool = False) -> dict:
        task_weights = [weight for weight in self.task_weights.copy()]
        
        if use_time_based_weights:
            current_hour = datetime.now().hour
            task_weights = self._adjust_weights_for_time(current_hour, task_weights)
        
        return random.choices(self.tasks, weights=task_weights, k=1)[0]

    def run_cycle(self) -> None:
        """One tool-driven cycle: the LLM observes via tools and acts until it stops calling them"""
        llm = self.connection_manager.connections[self.model_provider]
        system = {"role": "system", "content": self._construct_system_prompt() + "\n\n" + AGENT_OPERATING_PROMPT}
        cycle = [{"role": "user", "content": AGENT_CYCLE_PROMPT.format(now=datetime.now().strftime("%Y-%m-%d %H:%M"))}]

        if self._run_log is None:
            self._run_log = RunLog(self.name)
            logger.info(f"\n📝 Run log: {self._run_log.path}")
            self._run_log.event("run_start", agent=self.name, provider=self.model_provider,
                                model=llm.config.get("model"), tools=[t["function"]["name"] for t in self.tools.schemas],
                                max_tool_steps=self.max_tool_steps, memory_cycles=self.memory_cycles,
                                system_prompt=system["content"])
        self._cycle_no += 1
        log = lambda event, **fields: self._run_log.event(event, cycle=self._cycle_no, **fields)
        log("cycle_start", trigger=cycle[0]["content"], memory_cycles_held=len(self._cycle_history))

        try:
            for step in range(1, self.max_tool_steps + 1):
                history = [m for past in self._cycle_history for m in past]
                message = llm.chat([system] + history + cycle, tools=self.tools.schemas)
                cycle.append(message.model_dump(exclude_none=True))
                log("llm_response", step=step, content=message.content,
                    tool_calls=[{"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                                for c in message.tool_calls or []])

                if not message.tool_calls:
                    if message.content:
                        logger.info(f"\n💭 {message.content}")
                    break

                for call in message.tool_calls:
                    logger.info(f"\n🔧 {call.function.name}({call.function.arguments})")
                    started = time.monotonic()
                    result = self.tools.call(call.function.name, call.function.arguments)
                    logger.info(f"   ↳ {result[:300]}")
                    log("tool_result", step=step, id=call.id, name=call.function.name,
                        arguments=call.function.arguments, ok="error" not in json.loads(result),
                        duration_ms=round((time.monotonic() - started) * 1000), result=result)
                    cycle.append({"role": "tool", "tool_call_id": call.id, "content": result})
            else:
                logger.warning(f"\n⚠️ Cycle stopped after {self.max_tool_steps} steps")
            log("cycle_end", steps=step, hit_step_limit=bool(message.tool_calls))

        except Exception as e:
            log("cycle_error", error=f"{type(e).__name__}: {e}")
            raise

        # Remember the last few cycles so the agent knows what it already did
        self._cycle_history = (self._cycle_history + [cycle])[-self.memory_cycles:]

    def loop(self):
        """Main agent loop for autonomous behavior"""
        if not self.is_llm_set:
            self._setup_llm_provider()

        if self.tools:
            return self._tool_loop()

        logger.info("\n🚀 Starting agent loop...")
        logger.info("Press Ctrl+C at any time to stop the loop.")
        print_h_bar()

        time.sleep(2)
        logger.info("Starting loop in 5 seconds...")
        for i in range(5, 0, -1):
            logger.info(f"{i}...")
            time.sleep(1)

        try:
            while True:
                success = False
                try:
                    # REPLENISH INPUTS
                    # TODO: Add more inputs to complexify agent behavior
                    if "timeline_tweets" not in self.state or self.state["timeline_tweets"] is None or len(self.state["timeline_tweets"]) == 0:
                        if any("tweet" in task["name"] for task in self.tasks):
                            logger.info("\n👀 READING TIMELINE")
                            self.state["timeline_tweets"] = self.connection_manager.perform_action(
                                connection_name="twitter",
                                action_name="read-timeline",
                                params=[]
                            )

                    if "room_info" not in self.state or self.state["room_info"] is None:
                        if any("echochambers" in task["name"] for task in self.tasks):
                            logger.info("\n👀 READING ECHOCHAMBERS ROOM INFO")
                            self.state["room_info"] = self.connection_manager.perform_action(
                                connection_name="echochambers",
                                action_name="get-room-info",
                                params={}
                            )

                    # CHOOSE AN ACTION
                    # TODO: Add agentic action selection
                    
                    action = self.select_action(use_time_based_weights=self.use_time_based_weights)
                    action_name = action["name"]

                    # PERFORM ACTION
                    success = execute_action(self, action_name)

                    logger.info(f"\n⏳ Waiting {self.loop_delay} seconds before next loop...")
                    print_h_bar()
                    time.sleep(self.loop_delay if success else 60)

                except Exception as e:
                    logger.error(f"\n❌ Error in agent loop iteration: {e}")
                    logger.info(f"⏳ Waiting {self.loop_delay} seconds before retrying...")
                    time.sleep(self.loop_delay)

        except KeyboardInterrupt:
            logger.info("\n🛑 Agent loop stopped by user.")
            return

    def _tool_loop(self):
        logger.info("\n🚀 Starting agent loop...")
        logger.info("Press Ctrl+C at any time to stop the loop.")
        print_h_bar()

        try:
            while True:
                try:
                    self.run_cycle()
                except Exception as e:
                    logger.error(f"\n❌ Error in agent cycle: {e}")

                logger.info(f"\n⏳ Waiting {self.loop_delay} seconds before next cycle...")
                print_h_bar()
                time.sleep(self.loop_delay)

        except KeyboardInterrupt:
            logger.info("\n🛑 Agent loop stopped by user.")
            return