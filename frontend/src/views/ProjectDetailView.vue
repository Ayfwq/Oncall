<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { formatBeijingTime } from '../formatTime'
import { collectorLabel, metricLabel } from '../metricLabels'
import type { MetricsSource, MonitoringRule, ProjectConfig, ServiceEndpoint, SnapshotDTO } from '../types'

const route = useRoute()
const id = String(route.params.id)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const loadingSnapshot = ref(false)
const message = ref('')
const error = ref(false)
const snapshot = ref<SnapshotDTO | null>(null)
const snapshotDialogVisible = ref(false)
const testSummary = ref<{ passed: number; failed: number; missing: string[] } | null>(null)

type FormEndpoint = ServiceEndpoint
type FormMetrics = MetricsSource
type FormRule = MonitoringRule
interface FormConfig {
  name: string
  server_id: string | null
  description: string
  enabled: boolean
  poll_interval: number
  service_endpoints: FormEndpoint[]
  metrics_sources: FormMetrics[]
  log_sources: NonNullable<ProjectConfig['log_sources']>
  database_profiles: NonNullable<ProjectConfig['database_profiles']>
  rules: FormRule[]
  server?: ProjectConfig['server']
}

const cfg = ref<FormConfig>({ name: '', server_id: null, description: '', enabled: true, poll_interval: 30, service_endpoints: [], metrics_sources: [], log_sources: [], database_profiles: [], rules: [] })
const quick = ref({ healthUrl: '', metricsUrl: '', pollInterval: 30 })

const HOST_METRICS = [
  ['host.exporter.up', '系统采集器可用'], ['host.cpu.percent', 'CPU 使用率'], ['host.memory.percent', '内存使用率'],
  ['host.memory.available_bytes', '可用内存'], ['host.disk.usage_percent', '磁盘使用率'], ['host.disk.free_bytes', '磁盘剩余空间'],
  ['host.disk.read_bytes_per_sec', '磁盘读取速率'], ['host.disk.write_bytes_per_sec', '磁盘写入速率'],
  ['host.net.rx_bytes_per_sec', '网络接收速率'], ['host.net.tx_bytes_per_sec', '网络发送速率'], ['host.load.1m', '系统 1 分钟负载'],
]
const GPU_METRICS = [
  ['host.gpu.exporter.up', 'GPU 采集器可用'], ['host.gpu.utilization_percent', 'GPU 利用率'], ['host.gpu.memory_percent', '显存使用率'],
  ['host.gpu.temperature_celsius', 'GPU 温度'], ['host.gpu.power_watts', 'GPU 功耗'], ['host.gpu.available', 'GPU 可用性'],
]
const SERVICE_METRICS = [['service.reachable', '服务可达'], ['service.status_code', 'HTTP 状态码'], ['service.latency_ms', '响应延迟'], ['service.consecutive_failures', '连续失败次数']]
const APP_METRICS = [['app.up', '应用指标端点可用'], ['app.http.rps', '请求速率'], ['app.http.error_rate', '错误率'], ['app.http.p95_ms', 'P95 延迟'], ['app.http.p99_ms', 'P99 延迟'], ['app.http.availability', '应用可用性']]
const PROCESS_METRICS = [['process.target.alive', '进程指标可用'], ['process.target.count', '进程数量'], ['process.target.cpu_percent_sum', '进程 CPU'], ['process.target.rss_bytes_sum', '进程内存'], ['process.target.virtual_memory_bytes_sum', '进程虚拟内存'], ['process.target.open_fds_sum', '打开文件数'], ['process.target.uptime_seconds', '进程运行时长']]
const OBSERVABILITY_METRICS = 13
const metricCount = computed(() => HOST_METRICS.length + SERVICE_METRICS.length + APP_METRICS.length + PROCESS_METRICS.length + (cfg.value.server?.gpu_metrics_url ? GPU_METRICS.length : 0) + (cfg.value.log_sources.length || cfg.value.database_profiles.length ? OBSERVABILITY_METRICS : 0))

function formatSignalValue(key: string, value: unknown) {
  const n = Number(value)
  if (value === null || value === undefined || value === '') return '暂无数据'
  if (key.endsWith('_percent') || key.endsWith('.percent') || key === 'app.http.availability') return Number.isFinite(n) ? `${n.toFixed(1)}%` : String(value)
  if (key === 'app.http.error_rate') return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : String(value)
  if (key.endsWith('_ms')) return Number.isFinite(n) ? `${Math.round(n)} ms` : String(value)
  if (key.endsWith('_seconds')) return Number.isFinite(n) ? `${Math.round(n)} 秒` : String(value)
  if (key.endsWith('_bytes') || key.endsWith('_bytes_per_sec')) {
    if (!Number.isFinite(n)) return String(value)
    const units = key.endsWith('_per_sec') ? ['B/s', 'KB/s', 'MB/s', 'GB/s'] : ['B', 'KB', 'MB', 'GB']
    let amount = n, index = 0
    while (Math.abs(amount) >= 1024 && index < units.length - 1) { amount /= 1024; index++ }
    return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`
  }
  if (typeof value === 'boolean') return value ? '正常' : '异常'
  if ((key.endsWith('.up') || key.endsWith('.alive') || key.endsWith('.reachable') || key === 'host.gpu.available') && (value === 0 || value === 1)) return value === 1 ? '正常' : '异常'
  return Number.isFinite(n) && Number.isInteger(n) ? String(n) : Number.isFinite(n) ? n.toFixed(1) : String(value)
}
const snapshotSignals = computed(() => Object.entries(snapshot.value?.signals || {}).map(([key, value]) => ({ key, label: metricLabel(key), value: formatSignalValue(key, value) })))

function ruleThresholdText(rule: FormRule) {
  const value = Number(rule.trigger_threshold ?? 0)
  if (rule.metric_key === 'app.http.error_rate') return `${(value * 100).toFixed(0)}%`
  if (rule.metric_key.endsWith('_percent') || rule.metric_key.endsWith('.percent') || rule.metric_key === 'app.http.availability') return `${value}%`
  if (rule.metric_key.endsWith('_ms')) return `${value} ms`
  if (rule.metric_key.endsWith('_seconds')) return `${value} 秒`
  return String(value)
}
function ruleConditionText(rule: FormRule) {
  if (rule.detection_mode === 'baseline') return `明显偏离历史正常水平，持续 ${rule.trigger_for} 次采集`
  if (['host.exporter.up', 'host.gpu.exporter.up', 'app.up', 'log.collector.up', 'db.up'].includes(rule.metric_key)) return `连续 ${rule.trigger_for} 次采集失败`
  if (rule.metric_key === 'service.consecutive_failures') return `健康检查连续失败 ${rule.trigger_threshold} 次`
  const operator = ({ '>': '超过', '>=': '达到或超过', '<': '低于', '<=': '不高于', '==': '等于' } as Record<string, string>)[rule.operator] || rule.operator
  return `${operator} ${ruleThresholdText(rule)}，持续 ${rule.trigger_for} 次采集`
}

function setMessage(text: string, isError = false) { message.value = text; error.value = isError }
function failText(e: unknown) {
  const raw = e instanceof Error ? e.message : String(e || '未知错误')
  try { const body = JSON.parse(raw); return body.detail || body.message || raw } catch { return raw.replace(/^\s*"|"\s*$/g, '') }
}
function blankEndpoint(url = ''): FormEndpoint { return { id: null, service_id: null, name: '健康检查', url, method: 'GET', expected_status: 200, timeout_ms: 3000, enabled: true } }
function blankMetrics(url = ''): FormMetrics { return { id: null, service_id: null, name: 'app', url, auth_type: 'none', token: '', scrape_timeout_ms: 5000, route_label: 'handler', enabled: true } }
async function resetRules(showMessage = true) {
  try {
    const result = await api<{ rules: FormRule[] }>(`/projects/${id}/rules/defaults`)
    cfg.value.rules = result.rules
    if (showMessage) setMessage(`已载入后端统一维护的 ${result.rules.length} 条默认规则，请保存后生效`)
    return true
  } catch (e) {
    setMessage('载入默认规则失败：' + failText(e), true)
    return false
  }
}
function normalize(data: ProjectConfig) {
  cfg.value = {
    name: data.name || '', server_id: data.server_id, description: data.description || '', enabled: data.enabled !== false, poll_interval: data.poll_interval || 30,
    server: data.server,
    service_endpoints: (data.service_endpoints || []).map(x => ({ ...x, name: x.name || '健康检查' })),
    metrics_sources: (data.metrics_sources || []).map(x => ({ ...x, name: x.name || 'app', token: '' })),
    log_sources: data.log_sources || [],
    database_profiles: data.database_profiles || [],
    rules: (data.rules || []).map(x => ({ ...x })),
  }
  quick.value = { healthUrl: cfg.value.service_endpoints.find(x => x.enabled)?.url || '', metricsUrl: cfg.value.metrics_sources.find(x => x.enabled)?.url || '', pollInterval: cfg.value.poll_interval }
}
async function load() {
  loading.value = true
  try { normalize(await api<ProjectConfig>(`/projects/${id}`)) } catch (e) { setMessage('加载失败：' + failText(e), true) } finally { loading.value = false }
}
async function prepareQuick() {
  if (!quick.value.healthUrl.trim() || !quick.value.metricsUrl.trim()) { setMessage('健康检查地址和 Prometheus /metrics 地址都必填', true); return false }
  const endpoint = cfg.value.service_endpoints.find(x => x.enabled) || blankEndpoint()
  Object.assign(endpoint, { url: quick.value.healthUrl.trim(), enabled: true })
  if (!cfg.value.service_endpoints.includes(endpoint)) cfg.value.service_endpoints = [endpoint]
  const source = cfg.value.metrics_sources.find(x => x.enabled) || blankMetrics()
  Object.assign(source, { url: quick.value.metricsUrl.trim(), enabled: true })
  if (!cfg.value.metrics_sources.includes(source)) cfg.value.metrics_sources = [source]
  cfg.value.poll_interval = Number(quick.value.pollInterval) || 30
  if (!cfg.value.rules.length && !await resetRules(false)) return false
  return true
}
function validUrl(value: string) { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) && !!u.hostname } catch { return false } }
function validate() {
  const errs: string[] = []
  if (!cfg.value.name.trim()) errs.push('项目名称必填')
  if (!cfg.value.server_id) errs.push('未绑定服务器')
  if (!validUrl(quick.value.healthUrl)) errs.push('健康检查地址必须是完整的 http(s) URL')
  if (!validUrl(quick.value.metricsUrl)) errs.push('Prometheus /metrics 地址必须是完整的 http(s) URL')
  if (!Number.isInteger(Number(cfg.value.poll_interval)) || Number(cfg.value.poll_interval) < 10) errs.push('采集间隔至少为 10 秒')
  return errs
}
function toPayload() {
  return {
    name: cfg.value.name.trim(), server_id: cfg.value.server_id, description: cfg.value.description || '', environment: 'production', enabled: cfg.value.enabled, timezone: 'Asia/Shanghai', poll_interval: Number(cfg.value.poll_interval),
    service_endpoints: cfg.value.service_endpoints.map(x => ({ ...x, token: undefined })),
    metrics_sources: cfg.value.metrics_sources.map(x => ({ ...x, token: x.token || null })),
    log_sources: cfg.value.log_sources,
    database_profiles: cfg.value.database_profiles,
    rules: cfg.value.rules,
  }
}
async function save() {
  if (saving.value || !await prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  saving.value = true; setMessage('保存中…')
  try { await api(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(toPayload()) }); setMessage('配置已保存'); await load() } catch (e) { setMessage('保存失败：' + failText(e), true) } finally { saving.value = false }
}
async function test() {
  if (testing.value || !await prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  testing.value = true; setMessage('正在测试服务器、应用、日志和数据库采集…')
  try {
    snapshot.value = await api<SnapshotDTO>(`/projects/${id}/test`, { method: 'POST', body: JSON.stringify(toPayload()) })
    const statuses = Object.values(snapshot.value.collector_status || {})
    const missing = cfg.value.rules.filter(x => x.enabled && !(x.metric_key in (snapshot.value?.signals || {}))).map(x => x.metric_key)
    testSummary.value = { passed: statuses.filter(x => x.ok).length, failed: statuses.filter(x => !x.ok).length, missing }
    setMessage(testSummary.value.failed || missing.length ? '测试完成，但有采集器或指标异常' : `测试通过，已收到 ${Object.keys(snapshot.value.signals || {}).length} 个指标` , !!(testSummary.value.failed || missing.length))
  } catch (e) { setMessage('测试失败：' + failText(e), true) } finally { testing.value = false }
}
async function latest() {
  loadingSnapshot.value = true
  try {
    const result = await api<{ snapshot: Partial<SnapshotDTO> | null; collector_status: SnapshotDTO['collector_status'] | null; observed_at: string | null }>(`/projects/${id}/snapshot`)
    snapshot.value = result.snapshot ? {
        ...result.snapshot,
        project_id: result.snapshot.project_id || id,
        observed_at: result.observed_at || result.snapshot.observed_at || '',
        signals: result.snapshot.signals || {},
        resources: result.snapshot.resources || {},
        resource_signals: result.snapshot.resource_signals || {},
        collector_status: result.collector_status || result.snapshot.collector_status || {},
      } as SnapshotDTO : null
    snapshotDialogVisible.value = true
  } catch (e) { setMessage('读取快照失败：' + failText(e), true) }
  finally { loadingSnapshot.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page project-detail">
    <div class="page-head">
      <div><div class="eyebrow">REMOTE PYTHON MONITOR</div><h1>{{ cfg.name || '项目监控' }}</h1><p class="sub">绑定一台服务器，再填写健康检查和 Prometheus 地址即可开始监控。</p></div>
      <div class="head-actions"><el-button :loading="testing" @click="test">测试采集</el-button><el-button :loading="loadingSnapshot" @click="latest">最近快照</el-button><el-button type="primary" :loading="saving" @click="save">保存配置</el-button></div>
    </div>
    <el-alert v-if="message" :title="message" :type="error ? 'error' : 'success'" show-icon :closable="false" class="notice" />

    <div class="layout">
      <main>
        <section class="card setup-card">
          <div class="section-title"><div><span class="step">1</span><div><h2>项目基本信息</h2><p>这部分只描述你要监控的 Python 服务。</p></div></div><el-tag :type="cfg.enabled ? 'success' : 'info'">{{ cfg.enabled ? '监控中' : '已停用' }}</el-tag></div>
          <div class="form-grid"><el-form-item label="项目名称（必填）"><el-input v-model="cfg.name" placeholder="例如：股票行情 API" /></el-form-item><el-form-item label="采集间隔（秒）"><el-input-number v-model="cfg.poll_interval" :min="10" :max="86400" style="width:100%" /></el-form-item><el-form-item label="项目说明" class="full"><el-input v-model="cfg.description" placeholder="可选，例如：生产环境股票服务" /></el-form-item></div>
          <div class="server-banner"><div class="server-icon">⌁</div><div><b>{{ cfg.server?.name || '未绑定服务器' }}</b><p>服务器采集器：{{ cfg.server?.node_metrics_url || '未配置' }}<span v-if="cfg.server?.gpu_metrics_url"> · 已配置 GPU</span></p></div><el-switch v-model="cfg.enabled" active-text="启用监控" /></div>
        </section>

        <section class="card setup-card"><div class="section-title"><div><span class="step">2</span><div><h2>服务采集入口</h2><p>PulseOps 会定时请求健康检查和指标地址；Docker 日志与数据库由 Collector 自动采集。</p></div></div></div>
          <el-form-item label="健康检查地址（必填）"><el-input v-model="quick.healthUrl" placeholder="例如：https://stock.example.com/health" /></el-form-item><p class="hint">返回 2xx 且响应时间正常，代表服务探活通过；连续失败会触发告警。</p>
          <el-form-item label="Prometheus /metrics 地址（必填）"><el-input v-model="quick.metricsUrl" placeholder="例如：https://stock.example.com/metrics" /></el-form-item><p class="hint">必须能返回标准 Prometheus 文本格式；系统会从实际返回内容识别应用请求、延迟和 Python 进程指标。</p>
        </section>

        <section class="card setup-card"><div class="section-title"><div><span class="step">3</span><div><h2>告警规则</h2><p>系统只依据当前可采集指标判断异常；保存后可以在快照中验证。</p></div></div><el-button plain size="small" @click="resetRules()">恢复默认规则</el-button></div>
          <div class="alert-policy-note"><b>降噪策略</b><span>短暂波动只记录，不生成告警</span><span>常规异常需要连续多次出现</span><span>数据库长事务还必须伴随锁等待、连接压力、慢查询或服务异常</span></div>
          <div class="rule-summary"><div><b>{{ cfg.rules.filter(x => x.enabled).length }}</b><span>条启用规则</span></div><div><b>{{ metricCount }}</b><span>个标准指标{{ cfg.server?.gpu_metrics_url ? '（含 GPU）' : '' }}</span></div><div><b>5</b><span>个采集入口</span></div></div>
          <div class="rule-list"><div v-for="rule in cfg.rules.filter(x => x.enabled)" :key="rule.id || rule.metric_key" class="rule-row"><span class="rule-dot" :class="rule.severity"></span><span class="rule-name">{{ metricLabel(rule.metric_key) }}</span><span class="rule-condition">{{ ruleConditionText(rule) }}</span><el-tag size="small" effect="plain">{{ rule.detection_mode === 'threshold' ? '固定规则' : '规则 + 历史基线' }}</el-tag></div></div>
        </section>
      </main>
      <aside>
        <section class="card side-card"><div class="side-label">监控范围</div><div class="metric-total">{{ metricCount }}<small> 项</small></div><p>从服务器采集器、健康检查、应用 `/metrics`、Docker 日志和 PostgreSQL 汇总而来。</p><div class="metric-group"><b>服务器 · {{ HOST_METRICS.length }} 项</b><span v-for="m in HOST_METRICS" :key="m[0]">{{ m[1] }}</span></div><div v-if="cfg.server?.gpu_metrics_url" class="metric-group gpu"><b>GPU · {{ GPU_METRICS.length }} 项</b><span v-for="m in GPU_METRICS" :key="m[0]">{{ m[1] }}</span></div><div class="metric-group"><b>服务 · {{ SERVICE_METRICS.length }} 项</b><span v-for="m in SERVICE_METRICS" :key="m[0]">{{ m[1] }}</span></div><div class="metric-group"><b>应用 · {{ APP_METRICS.length + PROCESS_METRICS.length }} 项</b><span>请求、错误率、P95/P99、可用性</span><span>Python 进程 CPU、内存、句柄、存活</span></div><div v-if="cfg.log_sources.length || cfg.database_profiles.length" class="metric-group observability"><b>日志 / 数据库 · {{ OBSERVABILITY_METRICS }} 项</b><span>错误日志、异常堆栈、连接数、锁等待、慢 SQL 能力</span></div></section>
      </aside>
    </div>

    <el-dialog v-model="snapshotDialogVisible" title="最近采集快照" width="min(760px, 92vw)" destroy-on-close :teleported="false" class="snapshot-dialog">
      <div v-if="snapshot" class="snapshot-dialog-content">
        <div class="snapshot-meta"><div><span>采集时间（北京时间）</span><b>{{ formatBeijingTime(snapshot.observed_at) }}</b></div><div><span>指标数量</span><b>{{ Object.keys(snapshot.signals || {}).length }} 项</b></div></div>
        <div class="collector-status"><span v-for="(v,k) in snapshot.collector_status" :key="k" :class="v.ok ? 'ok' : 'bad'">{{ collectorLabel(k) }} · {{ v.ok ? '正常' : '失败' }}</span></div>
        <div class="snapshot-signals-title">采集指标</div>
        <div class="snapshot-signal-list"><div v-for="signal in snapshotSignals" :key="signal.key" class="snapshot-signal-row"><span>{{ signal.label }}</span><b>{{ signal.value }}</b></div><p v-if="!snapshotSignals.length" class="muted">暂无指标数据</p></div>
      </div>
      <el-empty v-else description="暂无采集快照，请先测试采集或启用监控" />
      <template #footer><el-button type="primary" @click="snapshotDialogVisible = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.project-detail { --green: #18a77a; --ink: #18352d; --muted: #6e817b; max-width: 1240px; }
.page-head { align-items: flex-end; }.eyebrow { color: var(--green); font-size: 11px; font-weight: 800; letter-spacing: .14em; margin-bottom: 8px; }.head-actions { display:flex; gap:8px; flex-wrap:wrap; }.notice { margin: 0 0 16px; }
.layout { display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:18px; }.card { border:1px solid #e1ebe7; box-shadow:0 8px 24px rgba(35,89,71,.05); }.setup-card { margin-bottom:16px; padding:22px; }.section-title { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:20px; }.section-title > div:first-child { display:flex; gap:12px; }.section-title h2 { color:var(--ink); margin:0 0 4px; font-size:18px; }.section-title p { color:var(--muted); margin:0; font-size:13px; }.step { width:28px; height:28px; display:grid; place-items:center; border-radius:9px; color:#fff; background:var(--green); font-weight:800; }.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:0 16px; }.form-grid .full { grid-column:1/-1; }.hint { color:var(--muted); font-size:12px; margin:-8px 0 14px; }.server-banner { background:#f1fbf7; border:1px solid #cdeee1; border-radius:12px; padding:13px 15px; display:flex; gap:12px; align-items:center; margin-top:8px; }.server-icon { width:32px; height:32px; border-radius:10px; display:grid; place-items:center; color:#fff; background:var(--green); font-size:22px; }.server-banner b { color:var(--ink); }.server-banner p { color:var(--muted); margin:3px 0 0; font-size:12px; }.server-banner .el-switch { margin-left:auto; }.rule-summary { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:16px; }.rule-summary > div { background:#f6faf8; border-radius:11px; padding:12px; }.rule-summary b { display:block; color:var(--green); font-size:22px; }.rule-summary span { color:var(--muted); font-size:12px; }.rule-row { display:flex; align-items:center; gap:9px; padding:10px 0; border-top:1px solid #edf2ef; font-size:12px; }.rule-dot { width:7px; height:7px; border-radius:50%; background:#f0a43c; }.rule-dot.critical { background:#e45a62; }.rule-dot.info { background:#5e9bd5; }.rule-name { color:var(--ink); font-family:ui-monospace,monospace; flex:1; }.rule-condition { color:var(--muted); }.side-card { padding:20px; margin-bottom:16px; }.side-label { color:var(--muted); font-size:12px; }.metric-total { color:var(--green); font-size:40px; font-weight:800; line-height:1.2; }.metric-total small { font-size:14px; }.side-card > p { color:var(--muted); font-size:12px; line-height:1.6; }.metric-group { border-top:1px solid #edf2ef; padding:13px 0 2px; display:flex; flex-direction:column; gap:5px; }.metric-group b { color:var(--ink); font-size:13px; margin-bottom:3px; }.metric-group span { color:var(--muted); font-size:12px; }.metric-group.gpu b { color:#8e6a16; }.tip-card { background:#f5fbf8; }.tip-card > b { color:var(--ink); }.tip-card ol { margin:10px 0 0; padding-left:18px; color:var(--muted); font-size:12px; line-height:1.8; }.collector-status { display:flex; gap:8px; flex-wrap:wrap; }.collector-status span { border-radius:999px; padding:5px 10px; font-size:12px; }.collector-status .ok { color:#137b58; background:#e5f7ef; }.collector-status .bad { color:#b13e48; background:#ffeded; }pre { background:#182b25; color:#d8f5e8; padding:14px; border-radius:10px; overflow:auto; font-size:12px; }
.snapshot-dialog-content { display:flex; flex-direction:column; gap:16px; }.snapshot-meta { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.snapshot-meta > div { padding:12px 14px; border-radius:10px; background:#f4f8f6; }.snapshot-meta span,.snapshot-meta b { display:block; }.snapshot-meta span { color:var(--muted); font-size:11px; margin-bottom:4px; }.snapshot-meta b { color:var(--ink); font-size:13px; }.snapshot-signals-title { color:var(--ink); font-weight:700; font-size:13px; }.snapshot-dialog-content pre { max-height:48vh; margin:0; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; }.layout aside { order:-1; }.form-grid { grid-template-columns:1fr; }.form-grid .full { grid-column:auto; }.rule-summary { grid-template-columns:1fr 1fr; } }
@media (max-width:560px) { .page-head { align-items:flex-start; }.rule-summary { grid-template-columns:1fr; }.rule-condition { display:none; }.setup-card { padding:16px; } }
.rule-name { font-family: inherit; }
.alert-policy-note { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; margin:-4px 0 16px; padding:11px 13px; border:1px solid #d8ece4; border-radius:10px; background:#f3faf7; color:var(--muted); font-size:12px; }
.alert-policy-note b { color:var(--green); }
.alert-policy-note span::before { content:'✓'; color:var(--green); margin-right:5px; font-weight:800; }
.snapshot-signal-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; max-height:48vh; overflow:auto; }
.snapshot-signal-row { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:10px 12px; border:1px solid #e5efeb; border-radius:9px; background:#f8fbfa; }
.snapshot-signal-row span { color:var(--muted); font-size:12px; }
.snapshot-signal-row b { color:var(--ink); font-size:13px; white-space:nowrap; }
@media (max-width:560px) { .snapshot-signal-list { grid-template-columns:1fr; } }
</style>
