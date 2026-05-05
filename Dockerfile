FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 保持版本参数
ARG HERMES_REF=v2026.4.23

# 设置环境变量，确保 Databricks 能识别二进制路径
ENV PATH="/usr/local/bin:/usr/bin:/app/.local/bin:${PATH}"
ENV HERMES_HOME="/data/.hermes"

# 1. 安装基础依赖
# Databricks 环境中，确保编译工具和 node 环境完整
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates git tini nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# 2. 安装 hermes-agent
# 修改点：去掉 -e 参数。Databricks 中文件系统映射可能不稳，直接全量安装到系统 site-packages 更可靠。
RUN git clone --depth 1 --branch ${HERMES_REF} https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent && \
    cd /opt/hermes-agent && \
    uv pip install --system --no-cache ".[all]" && \
    # 关键修改：显式创建软链接到系统标准 PATH，解决 image_1ccce0.png 中的 [Errno 2] 找不到文件问题
    ln -s $(which hermes) /usr/bin/hermes || true && \
    ln -s $(which hermes) /usr/local/bin/hermes || true

# 3. 预构建 UI（保持原有逻辑，确保 Chat 面板可用）
RUN cd /opt/hermes-agent/web && npm install && npm run build && \
    cd /opt/hermes-agent/ui-tui && npm install && npm run build && \
    # 减少镜像体积，但保留运行时需要的 dist
    rm -rf /root/.npm

# 4. 安装业务依赖
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache -r /app/requirements.txt

# 5. 权限与目录配置
# 修改点：Databricks 运行时可能使用非 root UID，必须开放权限确保子进程能写日志和配置
RUN mkdir -p /data/.hermes /app/templates && \
    chmod -R 777 /data /opt/hermes-agent /app

COPY server.py /app/server.py
COPY templates/ /app/templates/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# 6. 入口点配置
# 使用 tini 确保 Databricks 集群销毁时能正常关闭子进程
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
# 建议直接启动 python 以获得更好的日志捕获
CMD ["python", "server.py"]
