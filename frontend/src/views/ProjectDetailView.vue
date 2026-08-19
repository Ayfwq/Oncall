<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const id = String(route.params.id)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const message = ref('')
const messageError = ref(false)
const tab = ref('form')
const snapshot = ref<any>(null)
const testFeedback = ref<{ passed: number, failed: number, missingRules: string[] } | null>(null)
const jsonText = ref('')
const jsonError = ref('')

const cfg = ref<any>({
  name: '', description: '', enabled: true, timezone: 'Asia/Shanghai', poll_interval: 300,
  process_targets: [], log_sources: [], docker_targets: [], database_profiles: [], service_endpoints: [], rules: [],
})

const TIMEZONES = ['Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo', 'Asia/Hong_Kong', 'UTC', 'America/Los_Angeles', 'Europe/London']
const ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1']
const SSL_MODES = ['disable', 'prefer', 'require', 'verify-ca', 'verify-full']
const HTTP_METHODS = ['GET', 'HEAD', 'POST', 'PUT']
const OPERATORS = ['>', '<', '>=', '<=', '==', '!=']
const SEVERITIES = ['warning', 'critical', 'info']

const METRIC_GROUPS = [
  { label: '主机（无需绑定，默认采集）', options: [
    { value: 'host.cpu.percent', label: 'host.cpu.percent · CPU 使用率 %' },
    { value: 'host.memory.percent', label: 'host.memory.percent · 内存使用率 %' },
    { value: 'host.memory.available_bytes', label: 'host.memory.available_bytes · 可用内存（字节）' },
    { value: 'host.disk.usage_percent', label: 'host.disk.usage_percent · 磁盘使用率 %' },
    { value: 'host.disk.free_bytes', label: 'host.disk.free_bytes · 磁盘剩余（字节）' },
    { value: 'host.disk.read_bytes_per_sec', label: 'host.disk.read_bytes_per_sec · 磁盘读速率' },
    { value: 'host.disk.write_bytes_per_sec', label: 'host.disk.write_bytes_per_sec · 磁盘写速率' },
    { value: 'host.net.rx_bytes_per_sec', label: 'host.net.rx_bytes_per_sec · 网络接收速率' },
    { value: 'host.net.tx_bytes_per_sec', label: 'host.net.tx_bytes_per_sec · 网络发送速率' },
  ] },
  { label: '进程（需配置上方进程绑定）', options: [
    { value: 'process.target.alive', label: 'process.target.alive · 进程存活 1/0' },
    { value: 'process.target.count', label: 'process.target.count · 匹配进程数' },
    { value: 'process.target.cpu_percent_sum', label: 'process.target.cpu_percent_sum · 进程 CPU 总和 %' },
    { value: 'process.target.rss_bytes_sum', label: 'process.target.rss_bytes_sum · 进程内存总和（字节）' },
    { value: 'process.target.child_count', label: 'process.target.child_count · 子进程数' },
  ] },
  { label: 'Docker 容器（需配置上方容器绑定）', options: [
    { value: 'container.running', label: 'container.running · 容器运行 1/0' },
    { value: 'container.health', label: 'container.health · 健康状态 1/0/-1' },
    { value: 'container.cpu_percent', label: 'container.cpu_percent · 容器 CPU %' },
    { value: 'container.memory_percent', label: 'container.memory_percent · 容器内存 %' },
    { value: 'container.restart_count', label: 'container.restart_count · 重启次数' },
  ] },
  { label: '数据库（需配置上方数据库绑定，仅 PostgreSQL）', options: [
    { value: 'db.reachable', label: 'db.reachable · 数据库可达 1/0' },
    { value: 'db.connections.usage_percent', label: 'db.connections.usage_percent · 连接使用率 %' },
    { value: 'db.long_query.count', label: 'db.long_query.count · 长查询数' },
    { value: 'db.lock_wait.count', label: 'db.lock_wait.count · 锁等待数' },
    { value: 'db.deadlock.delta', label: 'db.deadlock.delta · 死锁增量' },
  ] },
  { label: 'HTTP 服务（需配置上方服务绑定）', options: [
    { value: 'service.reachable', label: 'service.reachable · 服务可达 1/0' },
    { value: 'service.status_code', label: 'service.status_code · HTTP 状态码' },
    { value: 'service.latency_ms', label: 'service.latency_ms · 响应延迟 ms' },
    { value: 'service.consecutive_failures', label: 'service.consecutive_failures · 连续失败次数' },
  ] },
  { label: '日志（需配置上方日志绑定）', options: [
    { value: 'log.error.count_window', label: 'log.error.count_window · 窗口内错误条数' },
    { value: 'log.warning.count_window', label: 'log.warning.count_window · 窗口内警告条数' },
    { value: 'log.error.rate_per_min', label: 'log.error.rate_per_min · 错误速率 条/分' },
    { value: 'log.top_signature.count', label: 'log.top_signature.count · 最常见错误出现次数' },
  ] },
]

const KNOWN_METRICS = new Set(METRIC_GROUPS.flatMap(group => group.options.map(option => option.value)))
const METRIC_TARGETS: Record<string, string> = {
  'process.': 'process_targets', 'log.': 'log_sources', 'container.': 'docker_targets',
  'db.': 'database_profiles', 'service.': 'service_endpoints',
}
const METRIC_RANGES: Record<string, [number, number]> = {
  'host.cpu.percent': [0, 100], 'host.memory.percent': [0, 100], 'host.disk.usage_percent': [0, 100],
  'process.target.alive': [0, 1], 'container.running': [0, 1], 'container.health': [-1, 1],
  'db.reachable': [0, 1], 'service.reachable': [0, 1], 'service.status_code': [0, 599],
}

// ---------- 行数据工厂（新建空行） ----------
const blankProcess = () => ({ id: null, name: '进程', executable: '', cmdline_filters: '', cwd: '', port: null, enabled: true })
const blankLog = () => ({ id: null, path: '', encoding: 'utf-8', parser_config: {}, enabled: true })
const blankDocker = () => ({ id: null, container_ref: '', enabled: true })
const blankDb = () => ({ id: null, type: 'postgresql', host: '127.0.0.1', port: 5432, database: '', username: '', password: '', sslmode: 'prefer', enabled: true })
const blankService = () => ({ id: null, name: '健康检查', url: '', method: 'GET', expected_status: 200, timeout_ms: 3000, enabled: true })
const blankRule = () => ({ id: null, metric_key: '', resource_key: 'default', operator: '>', trigger_threshold: null, trigger_for: 2, recovery_threshold: null, recovery_for: 2, severity: 'warning', enabled: true })

// ---------- 行数据标准化（API JSON -> 表单行） ----------
const normProcess = (x: any) => ({ id: x?.id ?? null, name: x?.name || '进程', executable: x?.executable ?? '', cmdline_filters: (x?.cmdline_filters || []).join(', '), cwd: x?.cwd ?? '', port: x?.port ?? null, enabled: x?.enabled !== false })
const normLog = (x: any) => ({ id: x?.id ?? null, path: x?.path ?? '', encoding: x?.encoding || 'utf-8', parser_config: x?.parser_config || {}, enabled: x?.enabled !== false })
const normDocker = (x: any) => ({ id: x?.id ?? null, container_ref: x?.container_ref ?? '', enabled: x?.enabled !== false })
const normDb = (x: any) => ({ id: x?.id ?? null, type: x?.type || 'postgresql', host: x?.host ?? '', port: x?.port ?? 5432, database: x?.database ?? '', username: x?.username ?? '', password: '', sslmode: x?.sslmode || 'prefer', enabled: x?.enabled !== false })
const normService = (x: any) => ({ id: x?.id ?? null, name: x?.name || '健康检查', url: x?.url ?? '', method: x?.method || 'GET', expected_status: x?.expected_status ?? 200, timeout_ms: x?.timeout_ms ?? 3000, enabled: x?.enabled !== false })
const normRule = (x: any) => ({ id: x?.id ?? null, metric_key: x?.metric_key ?? '', resource_key: x?.resource_key || 'default', operator: x?.operator || '>', trigger_threshold: x?.trigger_threshold ?? null, trigger_for: x?.trigger_for ?? 2, recovery_threshold: x?.recovery_threshold ?? null, recovery_for: x?.recovery_for ?? 2, severity: x?.severity || 'warning', enabled: x?.enabled !== false })

function errorMessage(error: any): string {
  const raw = String(error?.message || error || '未知错误')
  try {
    const body = JSON.parse(raw)
    if (Array.isArray(body?.detail)) return body.detail.map((x: any) => x.msg || '参数错误').join('；')
    if (typeof body?.detail === 'string') return body.detail
    if (typeof body?.message === 'string') return body.message
  } catch { /* api may return plain text */ }
  return raw.replace(/^\s*"|"\s*$/g, '')
}

function setMessage(text: string, isError = false) {
  messageError.value = isError
  message.value = text
}

function applyData(data: any) {
  cfg.value = {
    name: data?.name ?? '', description: data?.description ?? '', enabled: data?.enabled !== false,
    timezone: data?.timezone ?? 'Asia/Shanghai', poll_interval: data?.poll_interval ?? 300,
    process_targets: (data?.process_targets || []).map(normProcess),
    log_sources: (data?.log_sources || []).map(normLog),
    docker_targets: (data?.docker_targets || []).map(normDocker),
    database_profiles: (data?.database_profiles || []).map(normDb),
    service_endpoints: (data?.service_endpoints || []).map(normService),
    rules: (data?.rules || []).map(normRule),
  }
}

async function load() {
  loading.value = true
  try {
    const data = await api(`/projects/${id}`)
    applyData(data)
    jsonText.value = JSON.stringify(data, null, 2)
  } catch (e: any) { setMessage('加载失败：' + errorMessage(e), true) }
  finally { loading.value = false }
}

// ---------- 表单 -> 提交 payload ----------
function toPayload(): any {
  const c = cfg.value
  const num = (v: any, d?: number) => (v === '' || v === null || v === undefined) ? (d ?? null) : Number(v)
  const int = (v: any, d: number) => (v === '' || v === null || v === undefined) ? d : Math.max(0, Math.round(Number(v)))
  const split = (s: any) => String(s || '').split(/[,，;；\s]+/).map(x => x.trim()).filter(Boolean)
  return {
    name: c.name, description: c.description || '', enabled: !!c.enabled,
    timezone: c.timezone || 'Asia/Shanghai', poll_interval: int(c.poll_interval, 300),
    process_targets: c.process_targets.map((x: any) => ({
      id: x.id ?? undefined, name: x.name || '进程', executable: x.executable || null,
      cmdline_filters: split(x.cmdline_filters), cwd: x.cwd || null, port: num(x.port), enabled: !!x.enabled,
    })),
    log_sources: c.log_sources.map((x: any) => ({
      id: x.id ?? undefined, path: x.path, encoding: x.encoding || 'utf-8',
      parser_config: x.parser_config || {}, enabled: !!x.enabled,
    })),
    docker_targets: c.docker_targets.map((x: any) => ({
      id: x.id ?? undefined, container_ref: x.container_ref, enabled: !!x.enabled,
    })),
    database_profiles: c.database_profiles.map((x: any) => ({
      id: x.id ?? undefined, type: x.type || 'postgresql', host: x.host || '127.0.0.1',
      port: int(x.port, 5432), database: x.database, username: x.username,
      password: x.password ? String(x.password) : null, sslmode: x.sslmode || 'prefer', enabled: !!x.enabled,
    })),
    service_endpoints: c.service_endpoints.map((x: any) => ({
      id: x.id ?? undefined, name: x.name || '健康检查', url: x.url, method: x.method || 'GET',
      expected_status: int(x.expected_status, 200), timeout_ms: int(x.timeout_ms, 3000), enabled: !!x.enabled,
    })),
    rules: c.rules.map((x: any) => ({
      id: x.id ?? undefined, metric_key: x.metric_key, resource_key: x.resource_key || 'default',
      operator: x.operator || '>', trigger_threshold: num(x.trigger_threshold, 0) as number,
      trigger_for: int(x.trigger_for, 2), recovery_threshold: num(x.recovery_threshold, 0) as number,
      recovery_for: int(x.recovery_for, 2), severity: x.severity || 'warning', enabled: !!x.enabled,
    })),
  }
}

// ---------- 校验 ----------
function validate(): string[] {
  const errs: string[] = []
  const finite = (value: any) => typeof value === 'number' && Number.isFinite(value)
  if (!cfg.value.name?.trim()) errs.push('项目名称必填')
  if (!Number.isInteger(Number(cfg.value.poll_interval)) || Number(cfg.value.poll_interval) < 10 || Number(cfg.value.poll_interval) > 86400) errs.push('采集间隔必须是 10～86400 秒的整数')
  cfg.value.process_targets.forEach((x: any, i: number) => {
    if (!x.name?.trim()) errs.push(`进程绑定 #${i + 1}：名称必填`)
    if (!(x.executable?.trim() || x.cmdline_filters?.trim() || x.cwd?.trim())) errs.push(`进程绑定 #${i + 1}：至少填写可执行文件、命令行关键字或工作目录之一`)
    if (x.port !== null && x.port !== undefined && (!Number.isInteger(Number(x.port)) || Number(x.port) < 1 || Number(x.port) > 65535)) errs.push(`进程绑定 #${i + 1}：端口范围为 1～65535`)
  })
  cfg.value.log_sources.forEach((x: any, i: number) => {
    if (!x.path?.trim()) errs.push(`日志绑定 #${i + 1}：日志文件路径必填`)
    if (!ENCODINGS.includes(x.encoding)) errs.push(`日志绑定 #${i + 1}：不支持的文件编码`)
  })
  cfg.value.docker_targets.forEach((x: any, i: number) => { if (!x.container_ref?.trim()) errs.push(`Docker 绑定 #${i + 1}：容器名/ID 必填`) })
  cfg.value.database_profiles.forEach((x: any, i: number) => {
    if (!x.host?.trim()) errs.push(`数据库 #${i + 1}：主机必填`)
    if (!x.database?.trim()) errs.push(`数据库 #${i + 1}：库名必填`)
    if (!x.username?.trim()) errs.push(`数据库 #${i + 1}：用户名必填`)
    if (x.type !== 'postgresql') errs.push(`数据库 #${i + 1}：当前仅支持 PostgreSQL`)
    if (!SSL_MODES.includes(x.sslmode)) errs.push(`数据库 #${i + 1}：不支持的 SSL 模式`)
    if (!Number.isInteger(Number(x.port)) || Number(x.port) < 1 || Number(x.port) > 65535) errs.push(`数据库 #${i + 1}：端口范围为 1～65535`)
  })
  cfg.value.service_endpoints.forEach((x: any, i: number) => {
    if (!x.url?.trim()) errs.push(`HTTP 服务 #${i + 1}：服务地址必填`)
    else { try { const u = new URL(x.url); if (!['http:', 'https:'].includes(u.protocol) || !u.hostname) throw new Error() } catch { errs.push(`HTTP 服务 #${i + 1}：必须是完整的 http(s) 地址`) } }
    if (!HTTP_METHODS.includes(x.method)) errs.push(`HTTP 服务 #${i + 1}：不支持的请求方式`)
    if (!Number.isInteger(Number(x.expected_status)) || Number(x.expected_status) < 100 || Number(x.expected_status) > 599) errs.push(`HTTP 服务 #${i + 1}：期望状态码范围为 100～599`)
    if (!Number.isInteger(Number(x.timeout_ms)) || Number(x.timeout_ms) < 100 || Number(x.timeout_ms) > 60000) errs.push(`HTTP 服务 #${i + 1}：超时范围为 100～60000 毫秒`)
  })
  const seenRules = new Set<string>()
  cfg.value.rules.forEach((x: any, i: number) => {
    if (!x.metric_key) errs.push(`告警规则 #${i + 1}：指标未选择`)
    if (x.metric_key && !KNOWN_METRICS.has(x.metric_key) && !x.metric_key.startsWith('zz.test.')) errs.push(`告警规则 #${i + 1}：指标未注册`)
    if (!x.resource_key?.trim()) errs.push(`告警规则 #${i + 1}：资源标识必填`)
    const duplicateKey = `${x.metric_key}/${x.resource_key || ''}`
    if (seenRules.has(duplicateKey)) errs.push(`告警规则 #${i + 1}：指标与资源标识重复`)
    seenRules.add(duplicateKey)
    if (!finite(Number(x.trigger_threshold))) errs.push(`告警规则 #${i + 1}：触发阈值必须是有限数字`)
    if (!finite(Number(x.recovery_threshold))) errs.push(`告警规则 #${i + 1}：恢复阈值必须是有限数字`)
    if (!Number.isInteger(Number(x.trigger_for)) || Number(x.trigger_for) < 1 || Number(x.trigger_for) > 100) errs.push(`告警规则 #${i + 1}：触发持续次数范围为 1～100`)
    if (!Number.isInteger(Number(x.recovery_for)) || Number(x.recovery_for) < 1 || Number(x.recovery_for) > 100) errs.push(`告警规则 #${i + 1}：恢复持续次数范围为 1～100`)
    const bounds = METRIC_RANGES[x.metric_key]
    if (bounds && finite(Number(x.trigger_threshold)) && (Number(x.trigger_threshold) < bounds[0] || Number(x.trigger_threshold) > bounds[1])) errs.push(`告警规则 #${i + 1}：触发阈值应在 ${bounds[0]}～${bounds[1]} 范围内`)
    if (bounds && finite(Number(x.recovery_threshold)) && (Number(x.recovery_threshold) < bounds[0] || Number(x.recovery_threshold) > bounds[1])) errs.push(`告警规则 #${i + 1}：恢复阈值应在 ${bounds[0]}～${bounds[1]} 范围内`)
    if (['>', '>='].includes(x.operator) && finite(Number(x.trigger_threshold)) && finite(Number(x.recovery_threshold)) && Number(x.recovery_threshold) >= Number(x.trigger_threshold)) errs.push(`告警规则 #${i + 1}：高水位规则的恢复阈值必须低于触发阈值`)
    if (['<', '<='].includes(x.operator) && finite(Number(x.trigger_threshold)) && finite(Number(x.recovery_threshold)) && Number(x.recovery_threshold) <= Number(x.trigger_threshold)) errs.push(`告警规则 #${i + 1}：低水位规则的恢复阈值必须高于触发阈值`)
    if (x.operator === '==' && Number(x.trigger_threshold) !== Number(x.recovery_threshold)) errs.push(`告警规则 #${i + 1}：等值规则的触发和恢复阈值必须相同`)
    const targetField = METRIC_TARGETS[x.metric_key?.split('.')[0] + '.'] || Object.entries(METRIC_TARGETS).find(([prefix]) => x.metric_key?.startsWith(prefix))?.[1]
    if (x.enabled && targetField && !cfg.value[targetField].some((target: any) => target.enabled)) errs.push(`告警规则 #${i + 1}：该指标需要至少一个已启用的数据目标`)
  })
  return errs
}

// ---------- 保存 ----------
async function save() {
  if (saving.value) return
  if (tab.value === 'json') return saveFromJson()
  const errs = validate()
  if (errs.length) { setMessage('请完善后再保存：' + errs.join('；'), true); return }
  saving.value = true; setMessage('保存中…')
  try {
    await api(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(toPayload()) })
    setMessage('已保存 ✓（修改会按采集间隔自动生效，可点「测试采集」立即验证）')
    await load()
  } catch (e: any) { setMessage('保存失败：' + errorMessage(e), true) }
  finally { saving.value = false }
}

async function saveFromJson() {
  saving.value = true; setMessage('保存中…')
  try {
    const parsed = JSON.parse(jsonText.value)
    delete parsed.id; delete parsed.user_id
    applyData(parsed)
    const errs = validate()
    if (errs.length) { setMessage('请完善后再保存：' + errs.join('；'), true); return }
    await api(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(toPayload()) })
    setMessage('已保存 ✓')
    await load()
  } catch (e: any) { setMessage('保存失败：' + errorMessage(e), true) }
  finally { saving.value = false }
}

function toJson() { jsonError.value = ''; jsonText.value = JSON.stringify(toPayload(), null, 2); tab.value = 'json' }
function applyJson() {
  try { applyData(JSON.parse(jsonText.value)); jsonError.value = ''; setMessage('已应用 JSON 到表单（记得点「保存配置」）') }
  catch (e: any) { jsonError.value = 'JSON 格式错误：' + e.message }
}

// ---------- 采集测试 / 快照 ----------
async function test() {
  if (testing.value) return
  const errs = validate()
  if (errs.length) { setMessage('当前配置不可测试：' + errs.join('；'), true); return }
  testing.value = true; setMessage('采集测试中…')
  try {
    snapshot.value = await api(`/projects/${id}/test`, { method: 'POST' })
    const statuses = Object.values(snapshot.value.collector_status || {}) as any[]
    const missingRules = cfg.value.rules.filter((rule: any) => rule.enabled && !(rule.metric_key in (snapshot.value.signals || {}))).map((rule: any) => rule.metric_key)
    testFeedback.value = { passed: statuses.filter(x => x.ok).length, failed: statuses.filter(x => !x.ok).length, missingRules }
    tab.value = 'snapshot'
    setMessage(testFeedback.value.failed || missingRules.length ? '采集完成，但存在异常，请查看下方反馈' : '采集测试完成（读取的是已保存配置，dry-run 不落库）', Boolean(testFeedback.value.failed || missingRules.length))
  } catch (e: any) { setMessage('采集失败：' + errorMessage(e), true) }
  finally { testing.value = false }
}
async function latest() {
  try { snapshot.value = await api(`/projects/${id}/snapshot`); tab.value = 'snapshot' }
  catch (e: any) { setMessage('获取快照失败：' + errorMessage(e), true) }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div><h1>{{ cfg.name || '项目' }}</h1><p class="sub">监控项目配置 · 表单填写即可，无需手写 JSON；数据库密码不回传，留空即保留已存密码</p></div>
      <div style="display: flex; gap: 8px">
        <el-button :loading="testing" @click="test">测试采集</el-button>
        <el-button @click="latest">最近快照</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      </div>
    </div>
    <p v-if="message" class="msg" :class="messageError ? 'err' : ''">{{ message }}</p>
    <div v-if="testFeedback" class="test-feedback">
      <span>采集器：{{ testFeedback.passed }} 正常<span v-if="testFeedback.failed">，{{ testFeedback.failed }} 异常</span></span>
      <span v-if="testFeedback.missingRules.length" class="err-text">未返回规则指标：{{ testFeedback.missingRules.join('、') }}</span>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="表单配置" name="form">
        <el-form label-position="top" size="default">
          <!-- 基本信息 -->
          <div class="card form-card">
            <h3>基本信息</h3>
            <div class="field-grid">
              <el-form-item label="项目名称（必填）" class="span-2"><el-input v-model="cfg.name" placeholder="例如：本机监控" /></el-form-item>
              <el-form-item label="采集间隔（秒）">
                <el-input-number v-model="cfg.poll_interval" :min="10" :max="86400" style="width: 100%" />
              </el-form-item>
              <el-form-item label="时区">
                <el-select v-model="cfg.timezone" filterable allow-create default-first-option style="width: 100%">
                  <el-option v-for="t in TIMEZONES" :key="t" :label="t" :value="t" />
                </el-select>
              </el-form-item>
              <el-form-item label="启用监控"><el-switch v-model="cfg.enabled" active-text="启用" /></el-form-item>
            </div>
            <el-form-item label="描述（可选）"><el-input v-model="cfg.description" placeholder="给这个项目写一句说明" /></el-form-item>
          </div>

          <!-- 进程绑定 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>进程监控</h3>
                <p class="muted sec-desc">按「可执行文件名」或「命令行关键字」匹配本机进程，采集其 CPU、内存、存活状态</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.process_targets.push(blankProcess())">＋ 添加进程</el-button>
            </div>
            <p v-if="!cfg.process_targets.length" class="empty-hint">未配置任何进程。点击「添加进程」，填上进程名（如 python）即可开始监控。</p>
            <div v-for="(row, i) in cfg.process_targets" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} {{ row.name || '进程' }}</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.process_targets.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="名称"><el-input v-model="row.name" placeholder="例如：后端服务" /></el-form-item>
                <el-form-item label="可执行文件名（可留空）"><el-input v-model="row.executable" placeholder="例如：python" /></el-form-item>
                <el-form-item label="命令行关键字（逗号分隔）"><el-input v-model="row.cmdline_filters" placeholder="例如：uvicorn, 8000" /></el-form-item>
                <el-form-item label="工作目录（可留空）"><el-input v-model="row.cwd" placeholder="例如：D:\app" /></el-form-item>
                <el-form-item label="端口（可留空）"><el-input-number v-model="row.port" :min="1" :max="65535" style="width: 100%" /></el-form-item>
              </div>
            </div>
          </div>

          <!-- 日志绑定 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>日志监控</h3>
                <p class="muted sec-desc">监控日志文件的错误/警告，自动统计窗口内条数与速率；只读新增内容，不重复扫描</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.log_sources.push(blankLog())">＋ 添加日志</el-button>
            </div>
            <p v-if="!cfg.log_sources.length" class="empty-hint">未配置任何日志。点击「添加日志」，填入日志文件的完整路径（如 D:\app\logs\app.log）。</p>
            <div v-for="(row, i) in cfg.log_sources" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} 日志</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.log_sources.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="日志文件完整路径（必填）" class="span-2"><el-input v-model="row.path" placeholder="例如：D:\app\logs\app.log" /></el-form-item>
                <el-form-item label="文件编码">
                  <el-select v-model="row.encoding" style="width: 100%">
                    <el-option v-for="e in ENCODINGS" :key="e" :label="e" :value="e" />
                  </el-select>
                </el-form-item>
              </div>
            </div>
          </div>

          <!-- Docker 绑定 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>Docker 容器监控</h3>
                <p class="muted sec-desc">按容器名或容器 ID 监控容器的运行状态、健康、CPU、内存、重启次数</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.docker_targets.push(blankDocker())">＋ 添加容器</el-button>
            </div>
            <p v-if="!cfg.docker_targets.length" class="empty-hint">未配置任何容器。点击「添加容器」，填容器名（如 postgres）或容器 ID。</p>
            <div v-for="(row, i) in cfg.docker_targets" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} 容器</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.docker_targets.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="容器名 / 容器 ID（必填）" class="span-2"><el-input v-model="row.container_ref" placeholder="例如：postgres" /></el-form-item>
              </div>
            </div>
          </div>

          <!-- 数据库绑定 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>数据库监控</h3>
                <p class="muted sec-desc">连接 PostgreSQL 采集连接数、长查询、锁等待、死锁。密码加密存储，回显为空属正常</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.database_profiles.push(blankDb())">＋ 添加数据库</el-button>
            </div>
            <p v-if="!cfg.database_profiles.length" class="empty-hint">未配置任何数据库。点击「添加数据库」，填写连接信息；建议使用只读账号。</p>
            <div v-for="(row, i) in cfg.database_profiles" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} {{ row.database || '数据库' }}</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.database_profiles.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="类型"><el-select v-model="row.type" style="width: 100%"><el-option label="postgresql" value="postgresql" /></el-select></el-form-item>
                <el-form-item label="主机（必填）"><el-input v-model="row.host" placeholder="例如：127.0.0.1" /></el-form-item>
                <el-form-item label="端口"><el-input-number v-model="row.port" :min="1" :max="65535" style="width: 100%" /></el-form-item>
                <el-form-item label="数据库名（必填）"><el-input v-model="row.database" placeholder="例如：autogeo" /></el-form-item>
                <el-form-item label="用户名（必填）"><el-input v-model="row.username" placeholder="例如：oncall_readonly" /></el-form-item>
                <el-form-item label="密码（留空=保留已存密码）"><el-input v-model="row.password" type="password" show-password placeholder="首次配置请填写" /></el-form-item>
                <el-form-item label="SSL 模式">
                  <el-select v-model="row.sslmode" style="width: 100%">
                    <el-option v-for="s in SSL_MODES" :key="s" :label="s" :value="s" />
                  </el-select>
                </el-form-item>
              </div>
            </div>
          </div>

          <!-- 服务绑定 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>HTTP 服务探活</h3>
                <p class="muted sec-desc">定时请求服务地址，监控可达性、状态码、响应延迟、连续失败次数</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.service_endpoints.push(blankService())">＋ 添加服务</el-button>
            </div>
            <p v-if="!cfg.service_endpoints.length" class="empty-hint">未配置任何服务。点击「添加服务」，填入健康检查地址（如 http://127.0.0.1:8000/health）。</p>
            <div v-for="(row, i) in cfg.service_endpoints" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} {{ row.name || '服务' }}</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.service_endpoints.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="名称"><el-input v-model="row.name" placeholder="例如：健康检查" /></el-form-item>
                <el-form-item label="服务地址（必填）" class="span-2"><el-input v-model="row.url" placeholder="例如：http://127.0.0.1:8000/health" /></el-form-item>
                <el-form-item label="请求方式">
                  <el-select v-model="row.method" style="width: 100%">
                    <el-option v-for="m in HTTP_METHODS" :key="m" :label="m" :value="m" />
                  </el-select>
                </el-form-item>
                <el-form-item label="期望状态码"><el-input-number v-model="row.expected_status" :min="100" :max="599" style="width: 100%" /></el-form-item>
                <el-form-item label="超时（毫秒）"><el-input-number v-model="row.timeout_ms" :min="100" :max="60000" style="width: 100%" /></el-form-item>
              </div>
            </div>
          </div>

          <!-- 告警规则 -->
          <div class="card form-card">
            <div class="sec-head">
              <div>
                <h3>告警规则</h3>
                <p class="muted sec-desc">指标超过「触发阈值」并持续「持续次数」次 → 告警；回落到「恢复阈值」并持续 → 恢复。指标可直接从下拉选择</p>
              </div>
              <el-button type="primary" plain size="small" @click="cfg.rules.push(blankRule())">＋ 添加规则</el-button>
            </div>
            <p v-if="!cfg.rules.length" class="empty-hint">没有告警规则，项目仍会采集数据但不会产生告警。点击「添加规则」开始。</p>
            <div v-for="(row, i) in cfg.rules" :key="i" class="row-card">
              <div class="row-head">
                <b>#{{ Number(i) + 1 }} {{ row.metric_key || '未选择指标' }}</b>
                <span class="grow"></span>
                <el-switch v-model="row.enabled" active-text="启用" />
                <el-button text type="danger" @click="cfg.rules.splice(i, 1)">删除</el-button>
              </div>
              <div class="field-grid">
                <el-form-item label="监控指标（必选）" class="span-2">
                  <el-select v-model="row.metric_key" filterable style="width: 100%" placeholder="选择要监控的指标">
                    <el-option-group v-for="g in METRIC_GROUPS" :key="g.label" :label="g.label">
                      <el-option v-for="o in g.options" :key="o.value" :label="o.label" :value="o.value" />
                    </el-option-group>
                  </el-select>
                </el-form-item>
                <el-form-item label="条件">
                  <el-select v-model="row.operator" style="width: 100%">
                    <el-option v-for="o in OPERATORS" :key="o" :label="o" :value="o" />
                  </el-select>
                </el-form-item>
                <el-form-item label="触发阈值（必填）"><el-input-number v-model="row.trigger_threshold" style="width: 100%" /></el-form-item>
                <el-form-item label="持续次数"><el-input-number v-model="row.trigger_for" :min="1" :max="100" style="width: 100%" /></el-form-item>
                <el-form-item label="恢复阈值（必填）"><el-input-number v-model="row.recovery_threshold" style="width: 100%" /></el-form-item>
                <el-form-item label="恢复持续次数"><el-input-number v-model="row.recovery_for" :min="1" :max="100" style="width: 100%" /></el-form-item>
                <el-form-item label="级别">
                  <el-select v-model="row.severity" style="width: 100%">
                    <el-option v-for="s in SEVERITIES" :key="s" :label="s" :value="s" />
                  </el-select>
                </el-form-item>
                <el-form-item label="分组标识（一般不用改）"><el-input v-model="row.resource_key" placeholder="default" /></el-form-item>
              </div>
            </div>
          </div>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="JSON 高级编辑" name="json">
        <div class="card">
          <div class="toolbar-row">
            <el-button size="small" @click="toJson">从表单生成 JSON</el-button>
            <el-button size="small" @click="applyJson">应用 JSON 到表单</el-button>
            <span class="muted" style="font-size: 12px">数据库密码填 null = 保留已存密码；想改密码请填新值</span>
          </div>
          <textarea class="json-editor" v-model="jsonText" spellcheck="false"></textarea>
          <p v-if="jsonError" style="color: var(--danger); font-size: 13px; margin-top: 8px">{{ jsonError }}</p>
        </div>
      </el-tab-pane>

      <el-tab-pane label="采集快照" name="snapshot">
        <div class="card">
          <template v-if="snapshot">
            <div v-if="snapshot.collector_status" class="collector-grid">
              <div v-for="(v, k) in snapshot.collector_status" :key="k" class="collector-chip" :class="v.ok ? 'ok' : 'err'">
                <b>{{ k }}</b>
                <span>{{ v.ok ? '正常' : (v.error || '异常') }}</span>
              </div>
            </div>
            <p v-if="snapshot.observed_at" class="muted" style="font-size: 12px">采集时间：{{ snapshot.observed_at }}</p>
            <pre class="snapshot">{{ JSON.stringify(snapshot, null, 2) }}</pre>
          </template>
          <p v-else class="muted">尚未采集，点击上方「测试采集」或「最近快照」</p>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.msg { font-size: 13px; color: var(--success); margin: -8px 0 14px; }
.msg.err { color: var(--danger); }
.test-feedback { display: flex; flex-wrap: wrap; gap: 12px; margin: -6px 0 14px; font-size: 13px; color: var(--text-2); }
.err-text { color: var(--danger); }
.form-card { margin-bottom: 16px; }
.sec-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.sec-head h3 { margin-bottom: 2px; }
.sec-desc { font-size: 12.5px; margin: 0; }
.field-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); column-gap: 14px; }
.field-grid .span-2 { grid-column: span 2; }
.row-card {
  border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: var(--r-md); background: #fbfbfc; padding: 12px 16px; margin-bottom: 12px;
}
.row-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.row-head b { font-size: 14px; }
.grow { flex: 1; }
.empty-hint {
  background: var(--accent-soft); color: var(--text-2); border-radius: var(--r-sm);
  padding: 10px 14px; font-size: 13px; margin: 0 0 12px;
}
.toolbar-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.collector-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.collector-chip {
  border-radius: 999px; padding: 4px 12px; font-size: 12px; display: inline-flex; gap: 6px; align-items: center;
}
.collector-chip.ok { background: #e6f7ec; color: #17823f; }
.collector-chip.err { background: #fdeceb; color: #c22c34; }
</style>
