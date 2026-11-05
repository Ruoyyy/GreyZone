# Benchmark release preparation plan

状态：阶段 1 已完成（部署预检与可选离线回归）；阶段 2–7 待执行。实际构建、启动就绪、网络集成及模型任务的验收尚未执行。

## 目标与当前基线

发布一个由开源 agent、外部模拟环境、MCP、任务/场景和 Oracle 组成的 benchmark。使用者能够按固定版本启动、执行任务、取得判定，并在需要时从源码重建。

- 清理前恢复点：`d09cdd652`（Before Clean）。
- 本计划起点：已 push 的 `3b83df497`；制定计划时 `main` 与 `origin/main` 一致，工作区干净。
- 原清理步骤 1–8 及后续 Clawith / commonly / Inbox 清理已提交。此状态是后续工作的恢复点，不等于发布验收完成。
- 上轮已通过：8 个靶场共 155 项 pytest 检查、kubectl 16 项自检、Inbox 62 项测试，以及部分 Compose、锁文件和构建辅助检查。
- 尚未完成：10 个靶场在干净环境中的完整复现；当前最终源码的全部发布镜像构建。此前 Inbox 的成功构建使用了受限资源和分开执行的类型检查，本计划不能把它当作当前全部发布路径已经通过。

完成标准：保留必要的维护测试，移除无用开发残留，构建输入可追踪，环境启动和判分可验证，发布路径有实际运行证据。

## 边界

- 根目录检查入口是维护者的部署工具，独立于 benchmark 本体；不参与任务执行、攻击交互或 Oracle 判分，正常运行靶场无需调用它。
- 根据运行依赖和验证价值决定去留；文件作者、`test` / `smoke` 名字、是否被运行源码 import 都不能单独决定删除。
- 保留 agent 提示词、工具注册、权限、业务行为及现有场景语义。发现其问题时单独记录，行为修复使用独立 commit 和验证证据。
- 保留任务使用的代码、fixture 及其生成/初始化/reset 工具。Letta 的目标代码测试也可能是任务的一部分。
- 保留已确认由 Compose 挂载的 commonly `external/`，以及 Shopware、Inbox 的运行数据。
- 现有上游许可、NOTICE 和来源记录随对应代码保留；不改写 Git 历史。
- README 放在代码入口稳定之后。场景 schema、各项目 CLI/env 命名统一、顶层目录改名和 Clawith 其余翻译另行规划。
- 本计划不包括为降低文件数而裁剪 agent 的业务功能，也不引入覆盖率指标或新的大型测试框架。

## 已确认的待办

| 事项 | 代码证据 | 处理方向 |
| --- | --- | --- |
| 全仓验证尚依赖临时编排 | 上轮验证脚本位于 `/tmp`，仓库根没有对应入口 | 收编为小型、串行的维护检查入口 |
| commonly demo 工具存在失效调用 | `scripts/verify-demo.sh` 调用已删除的 `e2e/reviewer-journey.spec.ts` | 连同所属旧 demo 工具链核对后收尾 |
| Clawith 存在临时调试测试 | `backend/test_sandbox_config.py` 只有打印，没有断言 | 确认无任务依赖后删除 |
| 部分上游集成示例仍存在 | banking 的 Azure 集成示例；kubectl 的上游 CI / 云部署工具；Inbox 的云安装器及相关测试 | 核对任务、运行和构建引用后决定；这一行不是自动删除清单 |
| 部分 smoke 不适合作为就绪验收 | commonly 的 smoke 接受 readiness 503，health 检查条件过宽 | 明确检查用途；发布就绪检查必须识别未就绪状态 |
| agent / 工具版本未完全固定 | Alpaca agent Dockerfile 未指定 Claude Code 版本；Inbox Dockerfile 的 pnpm / Prisma 与项目声明不同 | 选取已验证版本，统一来源并记录 |
| 部分构建仍会重新解析依赖 | commonly Dockerfile 使用 `npm install`；Inbox local Dockerfile 使用 `--no-frozen-lockfile`；Clawith 直接解析 Python 依赖 | 检查实际发布路径并采用锁定输入，不做顺带升级 |
| Inbox 构建资源和初始化职责需整理 | build 固定 16 GB Node 堆，包含数据库迁移；生产 Dockerfile 跳过类型检查并引用已删除 workflow | 建立独立类型检查、受限资源构建和明确的数据库准备步骤 |
| 发布路径尚未完成选择和验证 | 多项目存在多份 Compose / Dockerfile | 每个靶场指定一条推荐路径，验证后再写 README |

## 插入任务：模型 API 配置整理

优先于阶段 2：支持 OpenAI / OpenAI 兼容接口的靶场统一公开配置为
`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，默认 API 地址为
`https://api.openai.com/v1`。模型由使用者显式选择。
用户补充要求：优先原生支持和已验证接入方式。Alpaca、Shopware 保留原生
Messages API；Letta 固定版本的原生 OpenAI 接入已用本地假服务验证。已有的
commonly 模型网关继续使用，本次不为统一名称新增协议网关。
原生例外在 README 中如实说明。源码中的可选上游提供方实现保留。

## 执行顺序

### 1. 建立维护者部署清单与预检入口

交付物：

- 一份覆盖 10 个靶场的维护清单，记录现有启动、准备、reset、任务、Oracle、测试入口和环境要求。
- 一个轻量串行部署预检入口，默认检查源文件、部署脚本语法、主机工具、Compose 和本地构建上下文；现有 Oracle / 合约回归显式选择后单独执行。
- 每项检查记录成功、失败、未执行及原因；缺依赖或缺环境不能计作通过。

入口为 `scripts/check_deployment.py`，维护清单为 `docs/deployment-checks.md`。清单采用简单文档和脚本中的明确映射，不引入新的场景格式和插件体系。

验收：静态预检及显式选择的回归可复现；失败能定位靶场和命令；默认不读取本机 env 文件，不调用模型或外部业务服务。构建、启动/就绪和 episode 明确记录为未执行。维护脚本自身只测试失败传播、超时、工作目录与环境隔离等必要行为。

commit：`test: add maintainer deployment preflight checks`。

阶段 1 验证记录：10 个靶场共 49 项预检通过，其中包括 11 份 Compose 配置；从仓库外使用新入口追加离线回归，通过 155 项 pytest、kubectl 16 项自检、Inbox 53 项，以及 commonly 的 SDK / 两份 Node 原生测试。维护工具自身 7 项 unittest 通过。检查过程串行，追加回归时整个进程组限制为 1.6 GB 内存。结果仅覆盖预检与离线回归，报告中的实际部署阶段均为 NOT_RUN。

### 2. 审查作者自建测试及上游残留，再按项目收尾

先逐项标记，再删除。每个候选记录：用途、是否是任务素材、运行/构建/脚本调用、外部服务要求、断言内容、是否重复及最终决定。

| 类别 | 处理规则 |
| --- | --- |
| Oracle 校准：正反例、边界、证据解析 | 保留现有有价值检查；已知判分分支缺口明确记录 |
| MCP / adapter / 模拟环境合约 | 保留影响任务交互和结果解释的检查 |
| 启动、fixture、reset、状态隔离检查 | 保留；生成或准备环境的脚本归为运行工具 |
| benchmark 修改过的 agent 行为回归 | 保留与这些修改直接相关的测试 |
| 本地环境 smoke | 保留能识别真实失败、可重复运行的代表性流程 |
| 真实模型端到端 smoke | 作为显式选择的集成验收，与默认离线检查分开运行 |
| 重复探测、只打印结果、旧接口试验、单次故障调试 | 删除或先将有价值断言并入正式测试，再删原脚本 |
| 与 benchmark 无关的上游产品 / 云发布 / 营销 demo 测试 | 确认无保留价值和引用后删除 |

优先检查 commonly demo 工具链、Clawith 调试脚本、banking 云集成示例、kubectl 上游工具；再核对 Inbox、Shopware、Letta 及其余靶场的作者自建脚本。Shopware 的协议/签名/handoff 检查和 redteam adapter 测试不能按名称批量删除。

删除脚本时同步清理调用入口、配置和确已不用的依赖，保持锁文件一致。保留的测试暂不统一移动目录，避免提前进入命名轮次。

验收：该项目基线检查通过；保留代码无新增失效引用；任务素材和运行 fixture 未受损；package 与锁文件一致。修改过的就绪检查应能拒绝未就绪/错误响应，保留成功路径。

commit：每个项目单独 `cleanup: <scope> remove obsolete development scripts`；同一项目涉及 smoke 行为修正时另做 `fix:` commit。

### 3. 固定发布构建输入

交付物：

- 每个靶场记录 agent 来源与版本、相对上游的 benchmark 修改、工具链版本、模型配置要求、基础镜像与依赖锁定方式。
- 将发布路径中的浮动 agent 工具、重算锁文件和版本冲突逐项收敛。依据已经工作过的版本选择，不以当前 latest 作为默认答案。
- 已有锁文件直接复用；需要补锁的项目单独处理，核对解析结果。来源无法恢复完整上游 SHA 时，记录可恢复的本仓库 commit/tree，不编造版本。
- 发布镜像记录不可变 digest；确认目标架构后解析和核验实际镜像，不凭空填写 digest。

验收：锁定安装不会修改锁文件；保留依赖没有未经说明的升级；runtime agent、MCP 与工具列表和基线一致；许可证及来源文件仍在。

commit：按项目拆分 `build: <scope> pin release build inputs`。版本/依赖变化与文件清理分开提交。

### 4. 修正实际构建与启动路径

优先处理 Inbox，再处理其他靶场清单中发现的问题：

- 统一推荐 Dockerfile、工具链和 package 声明；保留确有用途的源码构建路径。
- 将类型检查作为明确可执行的验收项；镜像跳过检查时，发布流程必须先有对应的成功记录。
- 替换 Inbox 写死的高内存构建设置，采用可配置、保守的构建并发和内存策略。
- 先查清数据库迁移及预渲染读库依赖，再把数据库准备放在明确的初始化阶段。若构建确实依赖数据库，显式记录并验证其准备过程。
- 核对干净 checkout 的 COPY、挂载和生成文件依赖，防止本机未入库文件掩盖缺项。
- 核对生产镜像内容，排除仅维护测试、缓存和本机状态；运行必需 fixture、迁移和初始化工具必须在。

验收：推荐路径完成真实构建；首次启动和重复启动均正常；数据库准备顺序保持正确；资源需求有测量值。构建失败如由资源不足导致，记录为未通过并转到足够资源的执行环境。

commit：按项目、按问题拆分 `build:` / `fix:`，例如 `build: make Inbox release builds resource configurable`。

### 5. 进行 10 个靶场的干净环境验收

仅离线测试通过不能代替本阶段。使用从 Git 导出的临时 checkout 或 worktree；每次只启动一个靶场，使用独立服务名/卷，结束后清理该次资源，保留原开发数据。

每个靶场记录：

1. 全新 checkout 能否获取依赖或镜像，准备 fixture 并启动。
2. 服务真正 ready，所需 MCP 工具可列举，代表性调用返回正确结构。
3. 指定 benign 任务能够完成；记录 agent / 模型 / 配置，按行为和证据检查，不要求输出逐字相同。
4. 已知正反例能驱动现有 Oracle 得到预期判定；环境错误与攻击失败按已有语义处理并记录局限。
5. reset 后任务数据、canary、状态与初始条件一致；第二次 episode 不受第一次污染。
6. 构建/启动/任务耗时、峰值内存、必要密钥或联网条件、限制及结果文件位置。

真实模型、云依赖和离线检查分开记录；未执行的项目明确保留未执行状态。模型调用结果的随机性与环境、协议、判分代码故障分别记录。

验收：10 个靶场各有完整记录，所选发布路径均成功；失败项修复后重跑相关检查，直到符合发布范围。已有合成正例不能替代真实启动检查。

交付物：维护清单更新及可复核验收记录；测试输出和 episode 证据作为生成产物保存，不批量入库运行日志。

### 6. README 与开发约定

入口稳定后，先以 Shopware 完成一份实际执行过的 README，再推广到其余 9 个靶场和根入口。

内容覆盖：benchmark 组成、任务、攻击入口与约束、依赖/资源、准备与启动、交互、Oracle、reset、验证、已知限制、来源与修改说明。命令必须来自前述成功记录。

根目录补充数据集级开发约定，说明保留测试、版本固定、逐项目 commit、资源限制及验证要求。若采用根 `AGENTS.md`，处理现有忽略规则并明确只跟踪这一份统一约定。

验收：未参与开发的人能够照文档完成选定入口；试点文档中的命令在干净环境中通过后再推广。

commit：Shopware 试点、其余文档、根入口与开发约定分别提交。

### 7. 制作发布候选并正式发布

推荐分发方式：源码仓库保留完整复现和必要维护测试；常规使用提供固定 digest 的镜像；源码构建服务于修改与复核。先验证实际目标平台，再确定首版支持架构和镜像存放位置。

交付物：

- 源码 commit 与镜像 digest 的对应清单，以及必要的来源、许可证和 NOTICE。
- 有限且明确的首版使用路径、系统资源要求、验证记录和已知限制。
- 对发布包/镜像内容做实际检查，确认包含任务与运行数据、排除本机凭据和运行状态。
- 用最终分发形式执行一次启动、任务和 Oracle 验收；源码构建和镜像路径分别标注验证结果。

完成前述工作后，再执行推镜像、tag 和 Release 等对外发布动作。本阶段与当前仅规划的工作分开。

## 10 个靶场的检查入口清单起点

以下是已确认存在的入口，不代表每条路径已经完成干净环境验收。第 1 阶段继续补全准备、reset、episode 和模型要求。

| 靶场 | 现有环境入口 | 现有 Oracle / 回归入口 |
| --- | --- | --- |
| Clawith | `docker-compose.yml`、`setup.sh`、`start.sh` | `oracle/test_oracle.py` |
| Inbox | `docker-compose.yml`、`docker/docker-compose.local.yml`、`docker-compose.mail-loop.yml` | web Vitest 保留的 7 个文件 / 62 项；`scripts/oracle-*.ts` |
| Letta | `docker-compose.yml` | `oracle/test_oracle.py`；redteam adapter smoke 待核验 |
| ZerePy | `docker-compose.yml` | `oracle/test_oracle.py` |
| banking | `app/compose.yaml`、`app/start-compose.sh` | `oracle/test_oracle.py` |
| Alpaca | `sim/docker-compose.yml` | `sim/oracle/test_oracle.py` |
| commonly | `docker-compose.stack.yml` | `oracle/test_oracle.py`、`oracle/test_fixtures.py`、MCP / SDK 合约 |
| kubectl-ai | `sim/up.sh`、`sim/up-ops-mcp.sh`、`sim/run-agent.sh` | `oracle/test_oracle.py`、`sim/ops-mcp/verify.py` |
| Shopware | `docker/compose.yaml`、`docker/bootstrap.sh`、`docker/verify.sh` | `oracle/test_oracle.py`；协议与环境 smoke 待筛选 |
| smart-home | `sim/docker-compose.yaml` | `sim/oracle/test_oracle.py`、`sim/oracle/test_runtime_files.py` |

## 资源与提交纪律

- 串行进行重操作；不并行运行多个靶场、多个生产构建或整仓安装。
- 日常离线检查单 worker；Node 从小堆限制起步。已知 Inbox 锁解析需约 1 GB 堆，之前以 1.6 GB 总内存限制完成。
- 重构建前检查主机可用内存。Inbox 可参考此前成功的单 worker、2.5 GiB 内存与 1 GiB swap 额度，使用进程组总内存限制；不足时转用足够资源的环境。
- 按影响执行检查；配置小改先做静态/合约检查，进入构建阶段再进行完整构建。没有新变更和未解决问题时，不重复运行已通过的重检查。
- 每个项目、每种独立目的一个 commit；正文记录影响和实际验证。删除文件、依赖固定、行为修复、文档和发布各自可追踪。
- 阶段进度以证据更新。文件数下降、离线测试通过或一次本机构建成功，都不单独作为发布完成条件。

## 建议推进批次

1. **代码收尾批次**：阶段 1–4。先检查入口，再临时脚本，再锁定输入，最后实际构建修复。
2. **复现验收批次**：阶段 5。形成 10 个靶场逐项记录，修复实际发现的阻断。
3. **文档与发布批次**：阶段 6–7。Shopware 试点后推广，制作并验收发布候选。

每个批次结束报告已完成项目、实际通过的检查、未执行项和剩余阻断；无需在每个常规删除或轻量验证之前重复确认。
