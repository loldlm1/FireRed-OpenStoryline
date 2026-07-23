# FireRed-OpenStoryline

[English](README.md)

FireRed-OpenStoryline 是一个远程会话式视频编辑服务，用于将一次上传的源视频
转换为经过验证的社交短视频。模型负责规划和审查编辑创意；确定性的 Python 与
FFmpeg 代码负责输入验证、能力约束、渲染和发布保护。

已弃用的本地 CLI/MCP 完整应用已经删除。当前唯一受支持的运行时是
`mvp_fastapi.py` 中带密码认证的 FastAPI MVP，并通过 `Dockerfile.remote` 和
Kamal 部署。

## 运行时

- 9Router 提供规划、帧理解和图片生成。
- Mistral Voxtral 直连服务提供带时间戳的语音转文字。
- PostgreSQL 是会话、任务、事件、产物、评审、保留状态和审计证据的权威来源。
- CPU FFmpeg 执行有边界的确定性媒体处理。
- 可选的固定版本 FFMPEGA sidecar 只执行有类型且在白名单内的特效。
- 任务媒体和工作文件隔离在 `outputs/mvp_jobs/<job_id>` 下。

可复用的 Agentic 工作区是唯一可执行的编辑工作流。历史 workflow-version-1
记录仅作为不可执行的审计历史保留，不能进入工作队列。

## 文档

- [架构](docs/mvp/architecture.md)
- [西班牙语运维指南](docs/mvp/guia-es.md)
- [API 密钥和提供商检查](docs/mvp/api-keys.md)
- [9Router VPS 手册](docs/mvp/9router-vps-runbook.md)
- [审计和数据库运维](docs/mvp/audit-and-database.md)
- [Agent 工程指南](docs/agent-engineering.md)
- [实现历史](docs/mvp/implementation-history.md)

## 本地开发

要求：Python 3.11+、PostgreSQL、FFmpeg 和 FFprobe。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-remote.txt
```

根据已提交的环境变量模板创建私有本地配置，并设置数据库、密码、安全、9Router
和 Mistral 所需值。不要提交真实密钥。

```bash
PYTHONPATH=src .venv/bin/python -m alembic upgrade head
PYTHONPATH=src .venv/bin/uvicorn mvp_fastapi:app --host 127.0.0.1 --port 8000
```

浏览器服务位于 `http://127.0.0.1:8000`。`/health` 和 `/up` 是公开健康检查；
`/api/mvp` 下的路由遵循架构文档中的浏览器会话和 CSRF 合同。

## 验证

以下命令不会调用真实模型提供商：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src .venv/bin/python -c "import mvp_fastapi; import open_storyline.mvp.pipeline; print('mvp_only_ok')"
bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh \
  scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh \
  .kamal/hooks/pre-deploy .kamal/hooks/post-deploy
docker build -f Dockerfile.remote .
```

未设置 `TEST_DATABASE_URL` 时，依赖数据库的测试类会跳过。真实提供商检查和部署
可能产生费用或修改外部状态，只应作为明确的发布操作执行。

## 部署

生产环境使用 `config/deploy.yml`、`Dockerfile.remote` 和 `bin/kamal-mvp`。
Kamal 流程会验证提供商能力、数据库就绪状态、持久卷、健康检查和回滚边界。
发布前请阅读[架构](docs/mvp/architecture.md)和
[审计/数据库指南](docs/mvp/audit-and-database.md)。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
