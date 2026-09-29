# GitHub 拉取式部署

生产配置为仓库根目录的 `compose.server.yaml`。Python 依赖固定在 `uv.lock`，前端依赖固定在 `frontend/package-lock.json`；Docker 构建会复用未变化的依赖层。服务器只需能访问 GitHub、Docker 镜像源和依赖镜像源，不需要安装 Python 或 Node。

## 首次部署

服务器安装 Git、Docker Engine（含 Compose 插件）和 systemd 后，以有 Docker 权限的用户执行。仓库为公开仓库时可直接使用 HTTPS；私有仓库需先为服务器配置只读 deploy key 或 Git 凭证。

```bash
sudo mkdir -p /opt/oncall-ai-sre
sudo git clone https://github.com/Ayfwq/Oncall.git /opt/oncall-ai-sre/current
cd /opt/oncall-ai-sre/current
sudo cp .env.server.example .env
sudo editor .env                         # 填写真实密码、密钥和服务地址
sudo bash deploy/pull.sh
sudo bash deploy/install-pull-timer.sh  # 每 5 分钟拉取 main 并增量更新
sudo systemctl start oncall-pull.service
sudo systemctl status oncall-pull.service
curl -fsS http://127.0.0.1:3000/api/health
```

`sudo git clone` 让目录归 root 所有，便于 timer 以 root 执行。若用其他用户克隆，请确保 timer 的运行用户对仓库和 Docker 有权限，且 Git 凭证属于同一用户。

已有通过旧 SFTP 流程部署的服务器：先保存 `/opt/oncall-ai-sre/current/.env`，把旧 `current` 目录改名备份，再按上述步骤克隆并将原 `.env` 复制回新目录。不要清理 Docker 命名卷；Compose 项目名固定为 `oncall`，PostgreSQL、Milvus、MinIO 等数据卷会继续使用。

## 日常更新

本机提交并推送 `main` 到 GitHub 后，服务器 timer 会在约 5 分钟内运行 `deploy/pull.sh`。也可以立即执行：

```bash
sudo systemctl start oncall-pull.service
sudo journalctl -u oncall-pull.service -n 100 --no-pager
```

`pull.sh` 使用 fast-forward-only 更新；服务器上有已修改的跟踪文件或分叉提交时会停止，避免覆盖现场改动。`.env` 由 `.gitignore` 排除，不会被 Git 更新。定时任务只在远端提交发生变化时调用 `update.sh`。

`update.sh` 根据文件校验和决定动作：

| 更新内容 | 服务器动作 |
| --- | --- |
| `backend/` Python 代码 | 执行迁移并重启后端进程，复用依赖镜像 |
| `pyproject.toml`、`uv.lock`、`Dockerfile.backend` | 重建后端镜像、迁移并重启 |
| 前端源码或依赖 | 重建前端镜像 |
| `compose.server.yaml` 或 `.env` | 手动运行 `sudo bash deploy/update.sh`，由 Compose 协调变更 |

依赖和静态资源在 Docker 镜像内，应用数据在 Docker 命名卷内。每次构建 Docker 会复用已有层；初次构建下载 PyTorch/Docling 依赖可能较久。构建、迁移或启动失败时校验和不会提交，下次运行会继续尝试。查看状态：

```bash
docker compose -f compose.server.yaml ps
docker compose -f compose.server.yaml logs --tail=100 api
```
