# 本地邮件闭环 —— 恢复手册

这份是 2026-09-16 那次会话的暂停快照。方案本身见 `plan.md`。

## 暂停时的状态

所有容器**已优雅停止**(`Exited (0)`),容器和数据卷都保留:

```
inbox-zero-google-emulator              Exited (0)   模拟器(node:alpine,跑 patched 0.11.1)
inbox-zero-emulator-tls                 Exited (0)   nginx,443 TLS 终结
inbox-zero-services-db-1                Exited (0)   postgres,数据在卷 inbox-zero-services_database-data
inbox-zero-services-redis-1             Exited (0)
inbox-zero-services-serverless-redis-http-1  Exited (0)
inbox-zero-services-cron-1              Exited (0)
inbox-zero-services-web-1               Exited (143) 预构建镜像,本来就停着
```

宿主机进程(骨干 / 客户服务器 / dev server)是 `nohup` 起的,**已全部停止,没有开机自启**。

`.docker-emulator-tls/nginx.conf` 已恢复直连模拟器(`127.0.0.1:4002`),所以单纯重启容器应用就能用。备份在 `nginx.conf.bak`。

## 恢复步骤

**一条命令拉起**(幂等,可重复跑):

```bash
cd /home/ruoyu/inbox-zero
bash scripts/mail-loop.sh up       # 起全套
bash scripts/mail-loop.sh status   # 看状态(含游标与 watch 订阅)
bash scripts/mail-loop.sh logs     # 跟踪日志
bash scripts/mail-loop.sh down     # 停全套(容器与数据卷保留)

# 模拟器重启后,应用 DB 里的标签 ID 会失效,规则报 Invalid label IDs
bash scripts/sync-labels.sh        # 按名字把 DB 标签 ID 对齐到模拟器
```

脚本会处理:启动容器、按需重建标签、启动骨干、把 nginx 切到骨干、重置错位的游标、启动客户服务器与应用。

启动后**仍需你在浏览器做一步**:打开 `http://localhost:3000`,用受管邮箱登录,然后建 watch(脚本会把命令打出来)。

## 端到端已打通 ✅

2026-09-17 验证:客户投递 → 骨干 import → 模拟器 → 骨干通知 → 应用找 history →
规则匹配(`ExecutedRule: APPLIED`)→ AI 生成回复 → `REPLY` 动作真实发送 →
骨干检测 SENT → 投递给客户。客户实际收到带正确线程头的回复,全程零错误。

### 让应用"自主发送"要改规则

Inbox Zero **有自主发信能力**(`REPLY` / `SEND_EMAIL` / `FORWARD`,受
`NEXT_PUBLIC_EMAIL_SEND_ENABLED` 控制),但默认规则配的是 `DRAFT_EMAIL` ——
只起草不发送。要自动回复,把规则动作改成 `REPLY`,并给 `content` 填
`{{模板变量}}`:AI 只给含 `{{...}}` 的字段生成内容,而 `REPLY` 需要
`content` 非空才会真的发送。

本仓库当前配置(已改):

```sql
-- To Reply 规则: DRAFT_EMAIL -> REPLY
update "Action"
set type='REPLY',
    content='{{write a short, friendly reply that acknowledges the sender''s message and answers any question they asked}}'
where id='cmu2aq5jh003q01nr8qxqm3sv';
```

回滚:`update "Action" set type='DRAFT_EMAIL', content=null where id='cmu2aq5jh003q01nr8qxqm3sv';`

**注意**:`SEND_EMAIL` 需要 `to` 和 `subject` 也非空,否则 `send_email()` 静默返回
不发送 —— 用 `REPLY` 更省事,收件人和主题由线程自动带出。

<details>
<summary>手工步骤(脚本失效时的兜底)</summary>

```bash
cd /home/ruoyu/inbox-zero

# 1. 起容器
docker start inbox-zero-services-db-1 inbox-zero-services-redis-1 \
  inbox-zero-services-serverless-redis-http-1 inbox-zero-emulator-tls \
  inbox-zero-google-emulator

# 2. 起骨干(把 nginx 指向它,这是邮件闭环的开关)
#    先改 .docker-emulator-tls/nginx.conf: proxy_pass -> http://127.0.0.1:4005
#    再 docker restart inbox-zero-emulator-tls
cd apps/web
TOK=$(grep -E '^GOOGLE_PUBSUB_VERIFICATION_TOKEN=' .env | cut -d= -f2- | tr -d '"')
TRANSPORT=$(cat .tmp/customer-mail/.transport-token)
MANAGED_EMAIL=admin@gmail.com TRANSPORT_TOKEN="$TRANSPORT" \
  EMULATOR_AUTH_TOKEN="backbone-$(date +%s)" \
  GOOGLE_WEBHOOK_URL=http://localhost:3000/api/google/webhook \
  GOOGLE_PUBSUB_VERIFICATION_TOKEN="$TOK" \
  CUSTOMER_RECEIVE_URL=http://127.0.0.1:4100/receive \
  EMULATOR_BASE_URL=http://127.0.0.1:4002 \
  nohup npx tsx scripts/run-mail-backbone.ts > .tmp/mail-backbone/backbone.log 2>&1 &

# 3. 起客户服务器
nohup npx tsx scripts/customer-mail-server.ts serve --port 4100 \
  --outbox .tmp/customer-mail --token "$TRANSPORT" > .tmp/customer-mail/serve.log 2>&1 &

# 4. 起应用(源码方式,含 LLM 修复)
PW=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '"')
RT=$(grep -E '^UPSTASH_REDIS_TOKEN=' .env | cut -d= -f2- | tr -d '"')
DATABASE_URL="postgresql://postgres:${PW}@127.0.0.1:5433/inboxzero?schema=public" \
  DIRECT_URL="postgresql://postgres:${PW}@127.0.0.1:5433/inboxzero?schema=public" \
  UPSTASH_REDIS_URL="http://127.0.0.1:8079" UPSTASH_REDIS_TOKEN="$RT" \
  nohup npx cross-env NODE_OPTIONS=--max_old_space_size=3072 next dev --turbopack \
  > /tmp/app-dev.log 2>&1 &
```

</details>

## 三个明天会咬人的坑

**① 模拟器重启 = 要重新登录。** 内存态,重启清空已签发 OAuth token,应用 refresh 必然 `invalid_grant`,账号被标记断开,`/api/watch/all` 返回 `results: []`。浏览器打开 `http://localhost:3000` 重新走 Google 登录。

**② 模拟器重启 = 标签要重建。** 应用 DB 里 `Action.labelId`、`MailSplitFilter.value` 存的是旧标签 ID,会报 `Invalid label IDs: Label_xxx`,`ExecutedRule` 变 ERROR。需要在模拟器里建同名标签再回写 DB。10 个标签名:`To Reply / Cold Email / Awaiting Reply / Actioned / FYI / Marketing / Newsletter / Notification / Receipt / Calendar`。

**③ 游标要重置。** 骨干的 `.tmp/mail-backbone/state.json` 和 DB 的 `EmailAccount.lastSyncedHistoryId` 都停在旧值,不重置两边都会过滤掉一切:
```bash
rm -f apps/web/.tmp/mail-backbone/state.json    # 骨干重启时自动重新初始化
docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero \
  -c 'update "EmailAccount" set "lastSyncedHistoryId" = null;'
```

## 已验证 / 待验证

**已跑通**:客户投递 → 骨干 import → 模拟器 → 骨干通知 → 应用找 history → 规则匹配(`ExecutedRule: APPLIED`)→ LLM 调用成功 → `DRAFT_EMAIL` + `LABEL` 动作执行 → 模拟器里出现同 thread 的回复草稿。

**待验证**:出口(客户实际收到回复)。需要把草稿通过应用入口发出去,骨干检测到 SENT 后投递给客户。以及阶段 B 的多轮对话。

## 环境改动留痕

- **DB schema 比镜像新**:为从源码 HEAD 跑,应用了 9 个未应用的迁移(`prisma migrate deploy`,全部增量)。若切回 `ghcr.io/elie222/inbox-zero:1f2280e` 镜像,旧代码不认识新增列(通常无害)。
- **DB 数据**:为修复标签悬空,UPDATE 过 `Action.labelId`、`MailSplitFilter.value`、`ExecutedAction.labelId`。
- **配置**:`apps/web/.env` 的 LLM 指向 GLM-5.3-flash。API key 明文在文件里,若这段记录外传过建议轮换。
