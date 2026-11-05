# 维护者部署检查

`scripts/check_deployment.py` 用于整理和验证发布前的部署条件，独立于 benchmark 的任务、攻击交互和 Oracle 判分。正常使用 benchmark 不需要调用它。它不被靶场运行代码引用，也不产生 episode verdict。

## 使用

在仓库根运行：

```bash
# 默认：10 个靶场的只读部署预检
python3 scripts/check_deployment.py

# 只检查一个或多个靶场；名称沿用顶层目录
python3 scripts/check_deployment.py --project commonly --project smart-home

# 保存机器可读的维护报告，该目录已被 Git 忽略
python3 scripts/check_deployment.py --report .deployment-checks/preflight.json

# 显式追加现有离线回归；仍不启动部署环境
python3 scripts/check_deployment.py --regression

# 检查维护脚本自身的失败传播和进程隔离
python3 -m unittest discover -s scripts -p test_check_deployment.py
```

脚本根据自身位置定位仓库，支持从其他工作目录使用绝对脚本路径调用。`--report` 路径相对于调用者的工作目录。`--timeout` 指定每条命令的最长秒数，默认 120 秒；执行始终串行，Node 子进程限制为 512 MB 堆，Inbox 使用单 worker。超时停止该命令的进程组，然后继续收集其余结果。

## 检查含义

默认执行：

- 所列部署入口、依赖锁、关键源文件和 fixture 源文件是否存在。
- 部署所用 shell 脚本的 `bash -n` 语法检查，不执行脚本内容。
- Docker CLI 及必要主机工具是否可找到；kubectl-ai 额外检查 Go、kind、kubectl。这里只检查可用性，版本固定在后续发布构建阶段核验。
- 用 Docker Compose 自身解析 11 份配置，并核对声明的本地 build context、Dockerfile 和 additional context 是否存在。

Compose 检查显式使用空环境文件、禁用插值和 service env_file 读取；不依赖开发者本机的 `.env` / `secrets.env`。需要 Compose 支持 `--no-interpolate`、`--no-env-resolution` 和 JSON 输出。此步骤不需要 Docker daemon，也不会拉镜像。

**预检 PASS 只说明上述静态条件通过。** 未解析的运行变量、实际 env_file、bind mount、Dockerfile COPY 内容、镜像获取、依赖安装、构建、服务 ready、MCP 交互和真实模型任务仍需后续实际部署验收。kubectl-ai 的部署由 shell 脚本管理，本工具仅检查入口、主机工具及所列源文件。

报告记录 `scope: deployment-preflight`，并始终将构建、启动/就绪、本地网络集成和模型 episode 标记为 `NOT_RUN`。第一版没有启动、重置或销毁环境的选项，相关步骤在发布计划第 5 阶段逐个执行。

## 状态与依赖

| 状态 | 含义 |
| --- | --- |
| PASS | 本条检查实际执行并通过 |
| FAIL | 源文件缺失、配置/断言错误或命令超时 |
| BLOCKED | 执行工具或已知必要依赖缺失，或构建上下文暂无法在本地核验 |
| NOT_RUN | 未选择可选回归，或当前阶段不执行的真实部署步骤 |

任一选定检查 FAIL / BLOCKED，总退出码为 1；所有选定检查通过时为 0。未选择的回归不会阻止只读预检通过。报告保留命令、耗时和错误摘要，不导出完整环境变量或展开后的 Compose 配置。

预检只需 Python 标准库、Bash 和 Docker Compose，以及表中明确的主机工具，不安装新依赖。

可选回归复用各项目的测试环境：优先使用测试目录下 `.venv/bin/python`，否则使用调用检查脚本的 Python。pytest、Clawith 的 httpx 等测试依赖按项目声明准备；Inbox 需先安装它固定的工作区依赖。工具不自动下载依赖；依赖探测失败报告 BLOCKED，测试收集或执行错误报告 FAIL 并保留诊断。

## 部署入口与准备清单

此表是维护索引，具体运行命令沿用各项目已有入口。默认预检不会执行初始化或 reset。

| 靶场 | 预检的环境配置或入口 | 初始化及生成文件 | 实际部署验收注意项 |
| --- | --- | --- | --- |
| Clawith | `docker-compose.yml`；`setup.sh` / `start.sh` / `restart.sh` | `.env.example`；backend entrypoint 与 setup；运行数据位于各自卷/目录 | 数据库迁移、agent 数据、MCP bridge 和应用就绪 |
| Inbox | 根 `docker-compose.yml`；`docker/docker-compose.local.yml`；`docker-compose.mail-loop.yml` | `setup.sh`；`scripts/mail-loop-init.sh` 生成 `.tmp` 中的模拟器及 bundle；应用 `.env` | 根 Compose 主要提供依赖服务；local Compose 使用预先构建的镜像；邮箱回路需生成物及 webhook 配置 |
| Letta | `docker-compose.yml`；`init-gitea.sh` | Compose 的 gitea-init 播种 `repo/`，生成 shared 目录中的 runner / MCP 凭据 | Gitea、runner 注册及 MCP 权限层；任务素材包括目标仓库代码 |
| ZerePy | `docker-compose.yml`；`scripts/init.sh`；`chain/entrypoint.sh` | init 生成 `.env`、`chain/users.keys.json`、`mock/users.tokens.json` 等；链与 mock 的 seed 源码入库 | 链播种完成后服务 ready；reset 需恢复模拟链与 mock 状态 |
| banking | `app/compose.yaml`；`app/start-compose.sh` | `app/.env.example`；业务服务初始化内存数据 | 模型配置、MCP 业务服务、copilot 启动；reset 与会话清理见现有 Oracle 流程 |
| Alpaca | `sim/docker-compose.yml` | `sim/.env.example`；`sim/oracle/seed.py` 是现有任务准备工具 | 环境/MCP/控制台服务，以及可选 `agent` / `oracle` profile；模拟世界 reset |
| commonly | `docker-compose.stack.yml`；stack secrets 与 agent entrypoint | `docker/stack/.env.example`；`init-secrets.sh` 写独立卷；`seed-local-stack.ts`；技能目录 JSON | 应用、模型代理、agent seat、用户 MCP；fixture 打包与 reset |
| kubectl-ai | `sim/up.sh` / `sim/up-ops-mcp.sh` / `sim/run-agent.sh` | Go 生成 `bin/kubectl-ai`；脚本生成 `sim/.build/` kubeconfig、key 等 | Docker、Go、kind、kubectl；真实集群、RBAC、ops MCP 与 collector 验证 |
| Shopware | `docker/compose.yaml`；`docker/bootstrap.sh` / `docker/verify.sh` / `dev.sh` | snapshot + bootstrap 生成环境配置、签名及集成数据；host 应用另有 Python / Node 依赖 | Shopware 容器就绪后还要验证 host 应用、MCP、签名、handoff；使用已有 reset 工具 |
| smart-home | `sim/docker-compose.yaml` | HA config；首次配置与 `sim/secrets.env`；HGA 源码作为 additional context | HA 首次接入、模型/数据库、mock 与 redteam MCP；可选 profile 和状态恢复 |

没有对应环境和初始化记录时，上表仅说明存在入口，不能据此标注“已部署”。生成文件缺失不通过创建空文件掩盖，应按实际初始化流程生成并验证。

## 可选回归清单

这些检查验证维护变更是否破坏已有逻辑，结果与部署预检分别展示；它们不参与正式 benchmark 评分。

| 项目 | 当前收编的检查 |
| --- | --- |
| Clawith | `oracle/test_oracle.py` |
| Inbox | Oracle 库、emulate seed、fixture adapter、client redirect 检查及两份 LLM 修补回归，共 7 个文件 |
| Letta / ZerePy / banking / Alpaca / Shopware | 各自现有 `oracle/test_oracle.py`，保持原有目录与命令方式 |
| commonly | Oracle、技能 fixture 一致性、SDK 合约、SEO 生成及 nginx 路由辅助测试 |
| kubectl-ai | 直接执行 `oracle/test_oracle.py` 的自检 |
| smart-home | Oracle 与 runtime files 一致性 |

当前参考基线是 8 个靶场的 155 项 pytest、kubectl 的 16 项自检、Inbox 的初始 53 项离线测试（API 配置整理新增 3 项，当前为 56 项），另有 commonly 的 SDK / 两份 Node 原生测试。数量作为记录，不作为必须维持的配额。

Inbox 的另外 9 项 `mail-backbone.test.ts` 是本地网络集成测试，默认回归不收集；后续真实环境验收单独运行并记录。现有 redteam adapter smoke、Shopware 活环境检查和作者自建脚本仍需按发布计划第 2 阶段逐项审查，不能因为没有出现在此入口中而直接删除。

API 配置整理的离线 SDK 请求检查：`python3 -m unittest discover -s scripts -p test_openai_config.py`；需要 ZerePy 的 OpenAI / httpx 依赖，不包含在默认部署预检中。
