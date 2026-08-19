# CPU 高负载处理手册

## 1. 概述

本文档描述服务器 CPU 高负载（High Load）告警的标准处理流程。适用于所有 Linux 服务器（CentOS 7 / Ubuntu 22.04 / Debian 12）。目标是在 15 分钟内完成初步定位，30 分钟内给出处置结论。

## 2. 告警触发条件

- `host.cpu.percent` 连续 3 个采样周期（每个周期 60 秒）大于 85%。
- Load Average（1 分钟）超过 CPU 核数的 4 倍，例如 8 核机器 load 超过 32。
- `iowait` 占比持续高于 30%，且 CPU `steal` 时间大于 10%。

## 3. 快速诊断步骤

1. 登录服务器，先执行 `uptime` 查看 load average 与 1/5/15 分钟趋势。
2. 执行 `top -c -d 1` 观察 CPU 使用率排序，确认是 user、system 还是 iowait 主导。
3. 使用 `top -H -p <pid>` 查看进程内线程占用，定位热点线程。
4. 执行 `mpstat -P ALL 1 3` 检查是否单核打满（存在 CPU 绑核现象）。
5. 若是数据库或中间件进程，使用 `pidstat -t -p <pid> 1` 观察线程级 CPU 分布。

## 4. 常见原因与处理

### 4.1 业务进程 CPU 打满

- 现象：单个 Java / Node / Python 进程 user CPU 接近 100%。
- 处理：`jstack <pid>` 抓取线程栈（Java），或用 `py-spy dump --pid <pid>` 抓取 Python 栈。
- 若为死循环或 GC 异常，优先 `kill -3 <pid>` 获取 dump，再评估重启。

### 4.2 Load Average 高但 CPU 空闲（iowait / D 状态）

- 现象：`top` 中 iowait 高，大量进程处于 D 状态。
- 处理：执行 `iostat -x 1 5` 检查磁盘 util 与 await；检查 NFS / 云盘挂载是否抖动。
- 若磁盘饱和，联系存储团队扩容或降低日志写入频率。

### 4.3 容器 CPU 限额（cgroup）

- 现象：宿主机 CPU 不高，但容器内 CPU 使用率 100%。
- 处理：检查 `docker stats` 与 cgroup quota：`cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us`。
- 若 quota 不足，调高容器的 `--cpus` 或 K8s 的 `resources.limits.cpu`。

### 4.4 虚拟机 CPU steal

- 现象：`top` 中 `%steal` 长期高于 10%。
- 处理：属于宿主机资源争抢，联系云厂商迁移实例或调整规格。

## 5. 应急降载

- 关闭非关键定时任务：`crontab -e` 注释大任务。
- 降级日志级别（如 log4j 从 DEBUG 降为 INFO）。
- 扩容副本或临时缩容流量（配合负载均衡灰度）。

## 6. 长期优化建议

- 为关键进程配置 `ulimit` 与线程池上限。
- 使用 Grafana 建立 CPU 使用率、load、iowait 关联面板。
- 对数据库慢查询建立索引，减少全表扫描导致的 CPU 消耗。

## 7. 处理 Checklist

- [ ] 已确认 load average 趋势与触发阈值
- [ ] 已定位热点进程与线程
- [ ] 已区分 user / system / iowait / steal
- [ ] 已执行应急降载并确认指标回落
- [ ] 已记录根因并归档到事后报告
