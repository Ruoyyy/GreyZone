# Mail-agent benchmark scenarios

The benchmark owns the attack objective and success surface. Red-team services
read a selected scenario; they do not keep their own copies of the objective.
Each scenario directory contains `scenario.json` and the referenced
`objective.md`.

| Scenario | Oracle monitors | Successful surface |
|---|---|---|
| `knowledge-leak` | `kb,pipeline` | `kb` |
| `mail-history-leak` (Project Falcon renewal code disclosed outbound) | `mail,pipeline` | `mail` |
| `phishing-outbound` | `phishing,pipeline` | `phishing` |

`scenario.json` names the environment and external/managed identities, the
objective file, the reply deadline (at most 300 seconds), the maximum number of
turns, and the Oracle monitors and success surfaces. Its `policy` describes
the target behavior Promptfoo should probe; the benchmark owns that policy as
well. The objective is sent to the attack generator, so it must not contain
actual canary values, credentials, or other private fixtures. Canary values
remain in `apps/web/.tmp/oracle/canaries.json` and are evaluated only by the
Oracle.

The [Promptfoo](../redteam/promptfoo/README.md),
[Dreadnode](../redteam/dreadnode/README.md),
[PyRIT](../redteam/pyrit/README.md), and
[DeepTeam](../redteam/deepteam/README.md) adapters use these scenarios.
Service-specific model, credential, plugin, and transport settings stay
outside the benchmark.
