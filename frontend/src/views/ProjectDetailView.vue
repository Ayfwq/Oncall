<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { MetricsSource, MonitoringRule, ProjectConfig, ServiceEndpoint, SnapshotDTO } from '../types'

const route = useRoute()
const id = String(route.params.id)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const message = ref('')
const error = ref(false)
const snapshot = ref<SnapshotDTO | null>(null)
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
  rules: FormRule[]
  server?: ProjectConfig['server']
}

const cfg = ref<FormConfig>({ name: '', server_id: null, description: '', enabled: true, poll_interval: 30, service_endpoints: [], metrics_sources: [], rules: [] })
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
const metricCount = computed(() => HOST_METRICS.length + SERVICE_METRICS.length + APP_METRICS.length + PROCESS_METRICS.length + (cfg.value.server?.gpu_metrics_url ? GPU_METRICS.length : 0))

function setMessage(text: string, isError = false) { message.value = text; error.value = isError }
function failText(e: unknown) {
  const raw = e instanceof Error ? e.message : String(e || '未知错误')
  try { const body = JSON.parse(raw); return body.detail || body.message || raw } catch { return raw.replace(/^\s*"|"\s*$/g, '') }
}
function blankEndpoint(url = ''): FormEndpoint { return { id: null, service_id: null, name: '健康检查', url, method: 'GET', expected_status: 200, timeout_ms: 3000, enabled: true } }
function blankMetrics(url = ''): FormMetrics { return { id: null, service_id: null, name: 'app', url, auth_type: 'none', token: '', scrape_timeout_ms: 5000, route_label: 'handler', enabled: true } }
function blankRule(metric_key: string, operator: string, trigger_threshold: number, recovery_threshold: number, severity = 'warning', detection_mode: FormRule['detection_mode'] = 'threshold'): FormRule {
  return { id: null, metric_key, resource_key: 'default', operator, trigger_threshold, trigger_for: 2, recovery_threshold, recovery_for: 2, severity, enabled: true, detection_mode, baseline_window: 60, baseline_min_samples: 12, baseline_z_score: 3, baseline_recovery_z_score: 2, conditions: null }
}
function starterRules(): FormRule[] {
  const rules = [
    blankRule('host.exporter.up', '==', 0, 0, 'critical'), blankRule('host.cpu.percent', '>', 90, 80), blankRule('host.memory.percent', '>', 90, 80), blankRule('host.disk.usage_percent', '>', 90, 85),
    blankRule('service.consecutive_failures', '>=', 1, 0.5, 'critical'), blankRule('service.latency_ms', '>', 1000, 700, 'warning', 'hybrid'), blankRule('app.up', '==', 0, 0, 'critical'),
    blankRule('app.http.p95_ms', '>', 800, 500, 'warning', 'hybrid'), blankRule('app.http.p99_ms', '>', 2000, 1000, 'critical', 'hybrid'), blankRule('app.http.availability', '<', 95, 99),
    blankRule('process.target.cpu_percent_sum', '>', 10000, 9000, 'warning', 'baseline'), blankRule('process.target.rss_bytes_sum', '>', 1e15, 9e14, 'warning', 'baseline'),
  ]
  const errorRule = blankRule('app.http.error_rate', '>', 0.1, 0.05)
  errorRule.conditions = { all: [{ metric_key: 'app.http.rps', resource_key: 'default', operator: '>', threshold: 1 }, { metric_key: 'app.http.error_rate', resource_key: 'default', operator: '>', threshold: 0.1 }] }
  rules.splice(7, 0, errorRule)
  if (cfg.value.server?.gpu_metrics_url) rules.splice(4, 0, blankRule('host.gpu.exporter.up', '==', 0, 0, 'warning'), blankRule('host.gpu.memory_percent', '>', 90, 80), blankRule('host.gpu.temperature_celsius', '>', 85, 75, 'critical'))
  return rules
}
function normalize(data: ProjectConfig) {
  cfg.value = {
    name: data.name || '', server_id: data.server_id, description: data.description || '', enabled: data.enabled !== false, poll_interval: data.poll_interval || 30,
    server: data.server,
    service_endpoints: (data.service_endpoints || []).map(x => ({ ...x, name: x.name || '健康检查' })),
    metrics_sources: (data.metrics_sources || []).map(x => ({ ...x, name: x.name || 'app', token: '' })),
    rules: (data.rules || []).map(x => ({ ...x })),
  }
  quick.value = { healthUrl: cfg.value.service_endpoints.find(x => x.enabled)?.url || '', metricsUrl: cfg.value.metrics_sources.find(x => x.enabled)?.url || '', pollInterval: cfg.value.poll_interval }
}
async function load() {
  loading.value = true
  try { normalize(await api<ProjectConfig>(`/projects/${id}`)) } catch (e) { setMessage('加载失败：' + failText(e), true) } finally { loading.value = false }
}
function prepareQuick() {
  if (!quick.value.healthUrl.trim() || !quick.value.metricsUrl.trim()) { setMessage('健康检查地址和 Prometheus /metrics 地址都必填', true); return false }
  const endpoint = cfg.value.service_endpoints.find(x => x.enabled) || blankEndpoint()
  Object.assign(endpoint, { url: quick.value.healthUrl.trim(), enabled: true })
  if (!cfg.value.service_endpoints.includes(endpoint)) cfg.value.service_endpoints = [endpoint]
  const source = cfg.value.metrics_sources.find(x => x.enabled) || blankMetrics()
  Object.assign(source, { url: quick.value.metricsUrl.trim(), enabled: true })
  if (!cfg.value.metrics_sources.includes(source)) cfg.value.metrics_sources = [source]
  cfg.value.poll_interval = Number(quick.value.pollInterval) || 30
  if (!cfg.value.rules.length) cfg.value.rules = starterRules()
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
    rules: cfg.value.rules,
  }
}
async function save() {
  if (saving.value || !prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  saving.value = true; setMessage('保存中…')
  try { await api(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(toPayload()) }); setMessage('配置已保存'); await load() } catch (e) { setMessage('保存失败：' + failText(e), true) } finally { saving.value = false }
}
async function test() {
  if (testing.value || !prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  testing.value = true; setMessage('正在测试三个采集入口…')
  try {
    snapshot.value = await api<SnapshotDTO>(`/projects/${id}/test`, { method: 'POST', body: JSON.stringify(toPayload()) })
    const statuses = Object.values(snapshot.value.collector_status || {})
    const missing = cfg.value.rules.filter(x => x.enabled && !(x.metric_key in (snapshot.value?.signals || {}))).map(x => x.metric_key)
    testSummary.value = { passed: statuses.filter(x => x.ok).length, failed: statuses.filter(x => !x.ok).length, missing }
    setMessage(testSummary.value.failed || missing.length ? '测试完成，但有采集器或指标异常' : `测试通过，已收到 ${Object.keys(snapshot.value.signals || {}).length} 个指标` , !!(testSummary.value.failed || missing.length))
  } catch (e) { setMessage('测试失败：' + failText(e), true) } finally { testing.value = false }
}
async function latest() { try { snapshot.value = await api<SnapshotDTO>(`/projects/${id}/snapshot`) } catch (e) { setMessage('读取快照失败：' + failText(e), true) } }
onMounted(load)
</script>

<template>
  <div class="page project-detail">
    <div class="page-head">
      <div><div class="eyebrow">REMOTE PYTHON MONITOR</div><h1>{{ cfg.name || '项目监控' }}</h1><p class="sub">绑定一台服务器，再填写健康检查和 Prometheus 地址即可开始监控。</p></div>
      <div class="head-actions"><el-button :loading="testing" @click="test">测试采集</el-button><el-button @click="latest">最近快照</el-button><el-button type="primary" :loading="saving" @click="save">保存配置</el-button></div>
    </div>
    <el-alert v-if="message" :title="message" :type="error ? 'error' : 'success'" show-icon :closable="false" class="notice" />

    <div class="layout">
      <main>
        <section class="card setup-card">
          <div class="section-title"><div><span class="step">1</span><div><h2>项目基本信息</h2><p>这部分只描述你要监控的 Python 服务。</p></div></div><el-tag :type="cfg.enabled ? 'success' : 'info'">{{ cfg.enabled ? '监控中' : '已停用' }}</el-tag></div>
          <div class="form-grid"><el-form-item label="项目名称（必填）"><el-input v-model="cfg.name" placeholder="例如：股票行情 API" /></el-form-item><el-form-item label="采集间隔（秒）"><el-input-number v-model="cfg.poll_interval" :min="10" :max="86400" style="width:100%" /></el-form-item><el-form-item label="项目说明" class="full"><el-input v-model="cfg.description" placeholder="可选，例如：生产环境股票服务" /></el-form-item></div>
          <div class="server-banner"><div class="server-icon">⌁</div><div><b>{{ cfg.server?.name || '未绑定服务器' }}</b><p>服务器采集器：{{ cfg.server?.node_metrics_url || '未配置' }}<span v-if="cfg.server?.gpu_metrics_url"> · 已配置 GPU</span></p></div><el-switch v-model="cfg.enabled" active-text="启用监控" /></div>
        </section>

        <section class="card setup-card"><div class="section-title"><div><span class="step">2</span><div><h2>服务采集入口</h2><p>Oncall 会定时请求这两个地址；不用填写进程、日志或数据库信息。</p></div></div></div>
          <el-form-item label="健康检查地址（必填）"><el-input v-model="quick.healthUrl" placeholder="例如：https://stock.example.com/health" /></el-form-item><p class="hint">返回 2xx 且响应时间正常，代表服务探活通过；连续失败会触发告警。</p>
          <el-form-item label="Prometheus /metrics 地址（必填）"><el-input v-model="quick.metricsUrl" placeholder="例如：https://stock.example.com/metrics" /></el-form-item><p class="hint">必须能返回标准 Prometheus 文本格式；系统会从实际返回内容识别应用请求、延迟和 Python 进程指标。</p>
        </section>

        <section class="card setup-card"><div class="section-title"><div><span class="step">3</span><div><h2>告警规则</h2><p>系统只依据当前可采集指标判断异常；保存后可以在快照中验证。</p></div></div><el-button plain size="small" @click="cfg.rules = starterRules()">恢复默认规则</el-button></div>
          <div class="rule-summary"><div><b>{{ cfg.rules.filter(x => x.enabled).length }}</b><span>条启用规则</span></div><div><b>{{ metricCount }}</b><span>个标准指标{{ cfg.server?.gpu_metrics_url ? '（含 GPU）' : '' }}</span></div><div><b>3</b><span>个采集入口</span></div></div>
          <div class="rule-list"><div v-for="rule in cfg.rules.filter(x => x.enabled)" :key="rule.id || rule.metric_key" class="rule-row"><span class="rule-dot" :class="rule.severity"></span><span class="rule-name">{{ rule.metric_key }}</span><span class="rule-condition">{{ rule.operator }} {{ rule.trigger_threshold }} · 连续 {{ rule.trigger_for }} 次</span><el-tag size="small" effect="plain">{{ rule.detection_mode === 'threshold' ? '阈值' : '阈值 + 基线' }}</el-tag></div></div>
        </section>
        <section v-if="snapshot" class="card setup-card"><div class="section-title"><div><h2>最近采集快照</h2><p>{{ snapshot.observed_at }}</p></div></div><div class="collector-status"><span v-for="(v,k) in snapshot.collector_status" :key="k" :class="v.ok ? 'ok' : 'bad'">{{ k }} · {{ v.ok ? '正常' : '失败' }}</span></div><pre>{{ JSON.stringify(snapshot.signals, null, 2) }}</pre></section>
      </main>
      <aside>
        <section class="card side-card"><div class="side-label">监控范围</div><div class="metric-total">{{ metricCount }}<small> 项</small></div><p>从服务器采集器、健康检查和应用 `/metrics` 汇总而来。</p><div class="metric-group"><b>服务器 · {{ HOST_METRICS.length }} 项</b><span v-for="m in HOST_METRICS" :key="m[0]">{{ m[1] }}</span></div><div v-if="cfg.server?.gpu_metrics_url" class="metric-group gpu"><b>GPU · {{ GPU_METRICS.length }} 项</b><span v-for="m in GPU_METRICS" :key="m[0]">{{ m[1] }}</span></div><div class="metric-group"><b>服务 · {{ SERVICE_METRICS.length }} 项</b><span v-for="m in SERVICE_METRICS" :key="m[0]">{{ m[1] }}</span></div><div class="metric-group"><b>应用 · {{ APP_METRICS.length + PROCESS_METRICS.length }} 项</b><span>请求、错误率、P95/P99、可用性</span><span>Python 进程 CPU、内存、句柄、存活</span></div></section>
        <section class="card side-card tip-card"><b>配置顺序</b><ol><li>先在服务器页面配置 Node Exporter（可选 GPU Exporter）。</li><li>选择服务器并填写本项目两个 URL。</li><li>点击“测试采集”，通过后保存并启用。</li></ol></section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.project-detail { --green: #18a77a; --ink: #18352d; --muted: #6e817b; max-width: 1240px; }
.page-head { align-items: flex-end; }.eyebrow { color: var(--green); font-size: 11px; font-weight: 800; letter-spacing: .14em; margin-bottom: 8px; }.head-actions { display:flex; gap:8px; flex-wrap:wrap; }.notice { margin: 0 0 16px; }
.layout { display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:18px; }.card { border:1px solid #e1ebe7; box-shadow:0 8px 24px rgba(35,89,71,.05); }.setup-card { margin-bottom:16px; padding:22px; }.section-title { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:20px; }.section-title > div:first-child { display:flex; gap:12px; }.section-title h2 { color:var(--ink); margin:0 0 4px; font-size:18px; }.section-title p { color:var(--muted); margin:0; font-size:13px; }.step { width:28px; height:28px; display:grid; place-items:center; border-radius:9px; color:#fff; background:var(--green); font-weight:800; }.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:0 16px; }.form-grid .full { grid-column:1/-1; }.hint { color:var(--muted); font-size:12px; margin:-8px 0 14px; }.server-banner { background:#f1fbf7; border:1px solid #cdeee1; border-radius:12px; padding:13px 15px; display:flex; gap:12px; align-items:center; margin-top:8px; }.server-icon { width:32px; height:32px; border-radius:10px; display:grid; place-items:center; color:#fff; background:var(--green); font-size:22px; }.server-banner b { color:var(--ink); }.server-banner p { color:var(--muted); margin:3px 0 0; font-size:12px; }.server-banner .el-switch { margin-left:auto; }.rule-summary { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:16px; }.rule-summary > div { background:#f6faf8; border-radius:11px; padding:12px; }.rule-summary b { display:block; color:var(--green); font-size:22px; }.rule-summary span { color:var(--muted); font-size:12px; }.rule-row { display:flex; align-items:center; gap:9px; padding:10px 0; border-top:1px solid #edf2ef; font-size:12px; }.rule-dot { width:7px; height:7px; border-radius:50%; background:#f0a43c; }.rule-dot.critical { background:#e45a62; }.rule-dot.info { background:#5e9bd5; }.rule-name { color:var(--ink); font-family:ui-monospace,monospace; flex:1; }.rule-condition { color:var(--muted); }.side-card { padding:20px; margin-bottom:16px; }.side-label { color:var(--muted); font-size:12px; }.metric-total { color:var(--green); font-size:40px; font-weight:800; line-height:1.2; }.metric-total small { font-size:14px; }.side-card > p { color:var(--muted); font-size:12px; line-height:1.6; }.metric-group { border-top:1px solid #edf2ef; padding:13px 0 2px; display:flex; flex-direction:column; gap:5px; }.metric-group b { color:var(--ink); font-size:13px; margin-bottom:3px; }.metric-group span { color:var(--muted); font-size:12px; }.metric-group.gpu b { color:#8e6a16; }.tip-card { background:#f5fbf8; }.tip-card > b { color:var(--ink); }.tip-card ol { margin:10px 0 0; padding-left:18px; color:var(--muted); font-size:12px; line-height:1.8; }.collector-status { display:flex; gap:8px; flex-wrap:wrap; }.collector-status span { border-radius:999px; padding:5px 10px; font-size:12px; }.collector-status .ok { color:#137b58; background:#e5f7ef; }.collector-status .bad { color:#b13e48; background:#ffeded; }pre { background:#182b25; color:#d8f5e8; padding:14px; border-radius:10px; overflow:auto; font-size:12px; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; }.layout aside { order:-1; }.form-grid { grid-template-columns:1fr; }.form-grid .full { grid-column:auto; }.rule-summary { grid-template-columns:1fr 1fr; } }
@media (max-width:560px) { .page-head { align-items:flex-start; }.rule-summary { grid-template-columns:1fr; }.rule-condition { display:none; }.setup-card { padding:16px; } }
</style>
