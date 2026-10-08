# Agent Note: Clawith multi-turn red team boundary

The scenario owns the attack objective, initial attack idea or turn, and one
Oracle success surface. A red team runner owns candidate generation and its
local transcript. The ruoyu MCP bridge owns the external user's identity and
the group-chat transport. Oracle owns episode baselines, observations, and the
benchmark verdict. Framework success labels do not replace Oracle evidence.

`oracle/run_episode.py --arm` records the same baseline as `--start` and enters
the running state without sending a message. This lets one runner drive serial
turns in one episode. `oracle/watch.py --probe` updates observations and
applies the same judgments without closing; `--close` produces the final
result. The runners permit one candidate per episode and stop further sends
after a selected Oracle hit, exhausted turn budget, or uncertain delivery.

The `kb-integrity` baseline includes tenant enterprise KB roots and the active
group's `workspace/enterprise_info` path. This path can be written through the
agent workspace, so excluding it would miss the scenario's intended write.
The Oracle compares before and after content; the runner does not infer a hit
from a tool response or framework grade.

The bounded turn gateway in `redteam/dreadnode/attacker_mcp.py` is reused by
PyRIT and DeepTeam. It exposes group-message turns and Oracle probes, not the
bridge's direct workspace-write tools. This keeps the attack path attributable
to the victim's behavior. Dreadnode and Promptfoo use their own orchestration;
all four runners save local artifacts under `.run/<runner>/`.
