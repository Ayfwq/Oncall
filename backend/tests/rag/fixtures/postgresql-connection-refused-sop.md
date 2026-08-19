# PostgreSQL Connection Refused 处理手册

## 1. 概述

本文档描述客户端连接 PostgreSQL 时报 `connection refused`（连接被拒绝）时的标准排查与恢复流程。覆盖 PostgreSQL 13~16，包括自建实例与容器化部署（docker compose / K8s）。

## 2. 常见错误码（SQLSTATE）

| SQLSTATE | 含义 | 典型场景 |
| --- | --- | --- |
| 08001 | SQL Client 无法建立连接 | 端口未监听、网络不通 |
| 08003 | 连接不存在 | 连接已被服务端关闭 |
| 28000 | 无效授权（invalid_authorization_specification） | 用户名/密码错误 |
| 57P03 | cannot connect now | 实例正在启动或恢复（starting up） |
| 53300 | 连接数超限（too_many_connections） | max_connections 打满 |

应用日志中常见的底层错误文本：`psycopg2.OperationalError: could not connect to server: Connection refused`、`Is the server running on host "db" and accepting TCP/IP connections on port 5432?`。

## 3. 快速诊断步骤

1. 在应用所在主机执行 `pg_isready -h <host> -p 5432`，确认服务端口是否可达。
2. 执行 `telnet <host> 5432` 或 `Test-NetConnection <host> -Port 5432`（Windows 侧）验证 TCP 连通性。
3. 在数据库主机执行 `ss -lntp | grep 5432`，确认 postgres 是否在监听；监听地址应为 `0.0.0.0:5432` 或具体网卡。
4. 查看 PostgreSQL 日志（默认 `log/postgresql.log`，或 `pg_log` 目录），搜索 `connection refused` / `FATAL` / `PANIC`。
5. 若端口未监听，检查 `pg_ctl status` 与 `postgresql.conf` 中的 `listen_addresses` 与 `port` 配置。

## 4. 常见原因与处理

### 4.1 服务未启动或启动失败

- 检查：`systemctl status postgresql` 或 `pg_ctl status`。
- 处理：查看启动日志中的 `FATAL: data directory ... has invalid permissions` 或 `could not create lock file` 等错误，修复后 `systemctl start postgresql`。

### 4.2 listen_addresses 配置错误

- 现象：`ss -lntp` 显示只监听 `127.0.0.1:5432`，外部连接全部 refused。
- 处理：编辑 `postgresql.conf` 设置 `listen_addresses = '*'`，重启后确认监听 `0.0.0.0:5432`。

### 4.3 max_connections 打满

- 现象：`psql` 本地可以连，但应用侧报 `too many connections` / `53300`。
- 处理：`SELECT count(*) FROM pg_stat_activity;` 查看当前连接；定位 idle in transaction 连接并清理；评估调高 `max_connections` 与 `shared_buffers` 的匹配关系。

### 4.4 pg_hba.conf 认证失败

- 现象：报 `28000` 或 `password authentication failed`。
- 处理：检查 `pg_hba.conf` 中对应网段的认证方式（`scram-sha-256` / `md5` / `trust`），确认应用连接串中的用户名密码正确；修改后执行 `SELECT pg_reload_conf();` 热加载。

### 4.5 防火墙 / 安全组拦截

- 现象：`pg_isready` 超时或 refused，但数据库主机本机可连。
- 处理：检查 iptables / firewalld / 云安全组是否放行 5432 端口；容器化部署检查 `docker compose` 的 `ports` 映射与 K8s Service/NetworkPolicy。

### 4.6 容器内 IP 变化 / DNS 解析异常

- 现象：`could not translate host name "db" to address` 或 `Connection refused` 间歇出现。
- 处理：确认容器使用服务名（service name）而非容器 IP；K8s 中检查 headless Service 与 Endpoints；避免在连接串中硬编码已销毁的 Pod IP。

## 5. 恢复后验证

- 重新执行 `pg_isready` 返回 `accepting connections`。
- 应用侧用最小连接串测试：`psql "host=<host> port=5432 user=<user> dbname=<db> sslmode=prefer" -c "select 1"`。
- 观察连接池（如 PgBouncer）与应用的 `max_connections` 配置是否匹配。

## 6. 预防措施

- 为数据库配置监控告警：连接数使用率、端口存活探测。
- 关键实例启用自动重启（systemd `Restart=always`）。
- 变更 `postgresql.conf` / `pg_hba.conf` 前先备份并走变更评审。
