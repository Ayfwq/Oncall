export const metricLabels: Record<string, string> = {
  'host.exporter.up': '服务器采集器',
  'host.cpu.percent': '服务器 CPU 使用率',
  'host.memory.percent': '服务器内存使用率',
  'host.memory.available_bytes': '服务器可用内存',
  'host.disk.usage_percent': '服务器磁盘使用率',
  'host.disk.free_bytes': '服务器磁盘剩余空间',
  'host.disk.read_bytes_per_sec': '磁盘读取速度',
  'host.disk.write_bytes_per_sec': '磁盘写入速度',
  'host.net.rx_bytes_per_sec': '网络接收速度',
  'host.net.tx_bytes_per_sec': '网络发送速度',
  'host.load.1m': '系统 1 分钟负载',
  'host.gpu.exporter.up': 'GPU 采集器',
  'host.gpu.available': 'GPU 可用性',
  'host.gpu.utilization_percent': 'GPU 利用率',
  'host.gpu.memory_percent': 'GPU 显存使用率',
  'host.gpu.temperature_celsius': 'GPU 温度',
  'host.gpu.power_watts': 'GPU 功耗',
  'app.up': '应用指标接口',
  'app.http.rps': '接口请求速率',
  'app.http.error_rate': '接口错误率',
  'app.http.p95_ms': '接口 P95 延迟',
  'app.http.p99_ms': '接口 P99 延迟',
  'app.http.availability': '接口可用性',
  'process.target.alive': 'Python 进程存活状态',
  'process.target.count': 'Python 进程数量',
  'process.target.cpu_percent_sum': 'Python 进程 CPU 使用率',
  'process.target.rss_bytes_sum': 'Python 进程内存占用',
  'process.target.virtual_memory_bytes_sum': 'Python 进程虚拟内存',
  'process.target.open_fds_sum': 'Python 进程打开文件数',
  'process.target.uptime_seconds': 'Python 进程运行时长',
  'log.collector.up': '日志采集器',
  'log.error_count': '错误日志数量',
  'log.exception_count': '异常日志数量',
  'db.up': '数据库连接',
  'db.connections.active': '活跃数据库连接数',
  'db.connections.max': '数据库最大连接数',
  'db.connections.utilization_percent': '数据库连接使用率',
  'db.long_transactions': '数据库长事务',
  'db.lock_waits': '数据库锁等待',
  'db.deadlocks_total': '数据库死锁次数',
  'db.cache_hit_percent': '数据库缓存命中率',
  'db.replication_lag_seconds': '数据库复制延迟',
  'db.slow_queries': '慢查询数量',
}

export function metricLabel(key: string) {
  return metricLabels[key] || key.split('.').join(' · ')
}

const collectorLabels: Record<string, string> = {
  server: '服务器采集器',
  prometheus: '应用指标',
  observability: '日志与数据库',
}

export function collectorLabel(key: string) {
  return collectorLabels[key] || key
}
