# 用外部邮箱测试 Inbox Zero

这份文档说明怎么在本地用**外部邮箱**(默认 `ruoyu@gmail.com`)发信给 Inbox Zero,看它怎么处理、怎么回复,并用交互式 CLI 持续对话。

背景与架构见 [`plan.md`](./plan.md);踩坑记录见 [`local-mail-loop-resume.md`](./local-mail-loop-resume.md)。

---

## 架构(2026-09 当前)

```
┌───────────────── docker compose: mail-loop ─────────────────┐
│ google-emulator      :4002  patched 模拟器(受管邮箱)        │
│ mail-backbone        :4005  邮件骨干(入站/通知/出站)        │
│ customer-mail-server :4100  客户邮件服务器(outbox 挂载宿主机)│
└─────────────────────────────────────────────────────────────┘
        ↑ 骨干经 host.docker.internal 投 webhook
宿主机 next dev :3000      ← 应用(不在 compose 里)
宿主机 CLI(tsx)            ← 外部客户操作入口
```

依赖的基础设施(Postgres/Redis)由主 `docker-compose.yml` 提供。

---

## 启动

```bash
cd /home/ruoyu/inbox-zero

# 1. 基础设施(数据库 / Redis)
docker compose up -d db redis serverless-redis-http

# 2. 邮件闭环三件套
docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d

# 3. 应用(宿主机源码方式,含 LLM 修复)
cd apps/web
PW=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '"')
RT=$(grep -E '^UPSTASH_REDIS_TOKEN=' .env | cut -d= -f2- | tr -d '"')
DATABASE_URL="postgresql://postgres:${PW}@127.0.0.1:5433/inboxzero?schema=public" \
  DIRECT_URL="postgresql://postgres:${PW}@127.0.0.1:5433/inboxzero?schema=public" \
  UPSTASH_REDIS_URL="http://127.0.0.1:8079" UPSTASH_REDIS_TOKEN="$RT" \
  nohup npx cross-env NODE_OPTIONS=--max_old_space_size=3072 next dev --turbopack \
  > .tmp/mail-loop-logs/app.log 2>&1 &
```

**然后你在浏览器做一步**:

1. 打开 http://localhost:3000,用 `admin@gmail.com` 登录
2. 建立 watch:
   ```bash
   curl -s "http://localhost:3000/api/watch/all" \
     -H "Authorization: Bearer $(grep -E '^CRON_SECRET=' apps/web/.env | cut -d= -f2-)"
   ```

> 模拟器是内存态的,重启后 OAuth token 失效,必须重新登录。

---

## 交互式收件箱 CLI(外部客户)

```bash
cd apps/web
TRANSPORT_TOKEN=$(cat .tmp/customer-mail/.transport-token) \
  CUSTOMER_EMAIL=ruoyu@gmail.com \
  npx tsx scripts/customer-mail-cli.ts
```

命令:

```
list        列收件箱(索引来自 log.jsonl,正文来自配套 .eml)
read <n>    读第 n 封(headers + 正文 + 线程引用)
reply <n>   回复第 n 封(自动带 In-Reply-To / References / Re:)
send        发新邮件(交互输入收件人/主题/正文)
quit        退出
```

示例(回复收件箱第 3 封):

```
> reply 3
Reply to admin@gmail.com (empty line to finish):
Could you send me the updated pricing sheet for Q4?
(空行结束)
sent ✓
```

**注意**:回复后 Inbox Zero 是否再回,取决于 AI 对线程状态的判定 —— 内容**带着问题**会触发 `TO_REPLY` 并回复;内容只是确认/道别(如 "Speak soon")会被判定 `ACTIONED`,只打标签不回复。这是产品行为,不是故障。

---

## 发一封新邮件(非交互)

```bash
cd apps/web
TRANSPORT=$(cat .tmp/customer-mail/.transport-token)

# 写信(不指定 --from 就用默认外部邮箱)
npx tsx scripts/customer-mail-server.ts compose \
  --to admin@gmail.com --subject "Invoice question" \
  --text "Could you confirm the status of invoice 1234?" \
  --out .tmp/customer-mail/test.eml

# 投递(经骨干的传输面)
npx tsx scripts/customer-mail-server.ts send \
  --url http://127.0.0.1:4005 --token "$TRANSPORT" \
  --to admin@gmail.com .tmp/customer-mail/test.eml
```

---

## 看结果

**客户收到了什么**(端到端成功的判据):

```bash
cd apps/web
tail -1 .tmp/customer-mail/log.jsonl | python3 -m json.tool   # 收件索引
ls -t .tmp/customer-mail/*.eml | head -1                       # 最新邮件原文
```

**Inbox Zero 做了什么**:

| 位置 | 看什么 |
|---|---|
| `apps/web/.tmp/mail-loop-logs/app.log` | 处理链路、规则匹配、AI 调用 |
| `/debug/rule-history` 页面 | 规则执行历史 |
| `/mail` 页面 | Inbox Zero 自己的邮件界面 |

DB 里查执行的规则与动作:

```bash
docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -c \
  'select er.status, ea.type from "ExecutedRule" er
   left join "ExecutedAction" ea on ea."executedRuleId" = er.id
   order by er."createdAt" desc limit 5;'
```

> **两个容易误判的点**:
> - 这张表是**累积**的。看结果要认准**最新那几行**的时间戳。
> - **Postgres 容器跑在 UTC**(Etc/UTC),宿主机是 CST(+8)。容器里 03:00 = 本地 11:00。

---

## 环境管理

```bash
# 状态
docker compose --profile mail-loop -f docker-compose.mail-loop.yml ps
bash scripts/mail-loop.sh status

# 日志
docker compose -f docker-compose.mail-loop.yml logs -f mail-backbone
tail -f apps/web/.tmp/mail-loop-logs/app.log

# 停邮件闭环(容器保留)
docker compose --profile mail-loop -f docker-compose.mail-loop.yml stop
docker compose stop db redis serverless-redis-http

# 彻底移除(容器删除,数据卷保留)
docker compose --profile mail-loop -f docker-compose.mail-loop.yml down
```

模拟器是纯内存的,**每次重启**会清空 OAuth token、用户标签和 history。重启后:

```bash
# 标签按名重建并回写 DB(必做,否则规则报 Invalid label IDs)
bash scripts/sync-labels.sh
# 重新登录 + 重建 watch(见"启动"一节)
```

> ⚠️ **watch 订阅 24 小时过期**,之后骨干会静默停止通知(游标照常推进,无日志)。
> 长跑(隔天)测试前先重建 watch:`curl /api/watch/all`。

---

## 四个一定会踩的坑

### 1. 冷邮件规则会拦下陌生地址

Inbox Zero 有一道**冷邮件前置检查**,优先级高于所有其他规则,命中就短路。
AI 判成冷邮件后,结论会被写入 `GroupItem` **固化** —— 之后每封都直接判冷,连 AI 都不再调用。

**解法**:冷邮件页面点「Not cold email」,或:

```bash
docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -c \
  "update \"GroupItem\" set exclude=true where value='ruoyu@gmail.com';"
```

注意:`gmail.com` 是公共邮箱域名,不会命中"同组织"豁免。

### 2. 回复动作需要规则的 From 条件是邮箱形式

`REPLY`/`SEND_EMAIL`/`FORWARD` 要求规则 From 条件必须是邮箱或域名形式,
为空或通配会被判低信任并拦下(见 `utils/rule/static-from-risk.ts`)。

### 3. 让应用"自主发送"要改规则

默认规则是 `DRAFT_EMAIL`(只起草)。要自动回复,把动作改成 `REPLY` 并给
`content` 填 `{{模板变量}}`(AI 只给含 `{{...}}` 的字段生成内容):

```sql
update "Action"
set type='REPLY',
    content='{{write a short, friendly reply that acknowledges the sender''s message}}'
where id='<action-id>';
```

### 4. 应用生成的回复可能没有 From 头

发件人身份在传输 envelope 里。解析 `.eml` 时 `From` 头缺失是正常的,
收件人信息应以 envelope 为准(CLI 已按此实现)。

---

## 收不到回复时怎么排查

按顺序看,每步有明确判据:

| 检查 | 命令/位置 | 期望 |
|---|---|---|
| 邮件进了模拟器 | `select count(*) from "EmailMessage";` | 递增 |
| 通知到了应用 | `grep "Received webhook" app.log` | 有记录 |
| 应用找到 history | `grep "Processing history" app.log` | 出现 |
| 命中哪条规则 | `grep "AI choose rule completed" -A 8 app.log` | `returnedRuleNames` |
| 冷邮件拦了吗 | `grep -E "Known cold email\|explicitly excluded" app.log` | 见坑 1 |
| 动作执行了吗 | `select status,type from "ExecutedRule"...` | `APPLIED`+`REPLY` |
| 骨干投递了吗 | `docker compose -f docker-compose.mail-loop.yml logs mail-backbone \| grep "delivered outbound mail"` | 递增 |
| 客户收到了吗 | `wc -l < .tmp/customer-mail/log.jsonl` | 递增 |

计数都是**累积**的,排查看**增量**。
