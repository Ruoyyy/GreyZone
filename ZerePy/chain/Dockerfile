# anvil + 确定性历史播种（seed.py）。每次容器启动都会重新生成同一段历史
FROM ghcr.io/foundry-rs/foundry:latest AS foundry

FROM python:3.11-slim
RUN pip install --no-cache-dir eth-account
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil

WORKDIR /chain
# 只拷脚本；users.keys.json 运行时挂载，不进镜像
COPY seed.py entrypoint.sh ./

ENTRYPOINT ["sh", "/chain/entrypoint.sh"]
