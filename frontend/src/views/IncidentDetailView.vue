<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { formatBeijingTime } from '../formatTime'
import { metricLabel, metricLabels } from '../metricLabels'
import type { IncidentDetail, IncidentTrace } from '../types'
const route = useRoute(), router = useRouter(), id = String(route.params.id), item = ref<IncidentDetail | null>(null), trace = ref<IncidentTrace | null>(null), busy = ref(false)
async function load() { item.value = await api<IncidentDetail>(`/incidents/${id}`); trace.value = await api<IncidentTrace>(`/incidents/${id}/trace`) }
async function investigate() { busy.value = true; try { await api(`/incidents/${id}/investigate`, { method: 'POST' }); await load() } finally { busy.value = false } }
async function resolve() { await api(`/incidents/${id}/resolve`, { method: 'POST' }); await load() }
async function chat() { const r = await api<{ conversation_id: string }>(`/incidents/${id}/conversation`, { method: 'POST' }); router.push('/?conversation=' + r.conversation_id) }
onMounted(load)
const sev = (s: string) => s === 'critical' ? 'err' : 'warn'
const toolLabels: Record<string, string> = {
  query_incident_context: '确认告警背景', query_database_health: '检查数据库', query_metric_history: '查看指标趋势',
  search_logs: '检查容器日志', query_service_health: '检查服务健康', query_current_metrics: '查看当前指标',
  query_runtime_resources: '检查容器和进程', search_knowledge: '查找处理建议',
}
function severityLabel(value: string) { return ({ critical: '严重', warning: '警告', info: '提示' } as Record<string, string>)[value] || value }
function statusLabel(value: string) {
  const normalized = String(value || '').trim().toLowerCase()
  return ({ open: '待处理', investigating: '调查中', diagnosed: '已诊断', resolved: '已恢复' } as Record<string, string>)[normalized] || value
}
function toolLabel(value: string) { return toolLabels[value] || '系统检查' }
function operationStatus(value: string) { return ({ succeeded: '已完成', success: '已完成', completed: '已完成', ok: '正常', failed: '失败', error: '失败', running: '进行中' } as Record<string, string>)[value] || value }
function readableText(value: string) {
  return Object.keys(metricLabels).reduce((text, key) => text.split(key).join(metricLabel(key)), value)
}
function recordData(e?: IncidentDetail['evidence'][number]) { return e?.data && typeof e.data === 'object' ? e.data as Record<string, any> : {} }
function resultData(e?: IncidentDetail['evidence'][number]) { const data = recordData(e); return data.result && typeof data.result === 'object' ? data.result as Record<string, any> : {} }
function numberText(value: unknown) { const n = Number(value); return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')) : '未知' }
function metricValueText(key: string, value: unknown) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '未知'
  if (key === 'db.long_transactions' || key === 'db.lock_waits' || key === 'log.exception_count') return `${numberText(n)} 个`
  if (key.endsWith('_percent') || key === 'app.http.availability') return `${n.toFixed(1)}%`
  if (key === 'app.http.error_rate') return `${(n * 100).toFixed(1)}%`
  if (key.endsWith('_ms')) return `${Math.round(n)} ms`
  if (key === 'db.replication_lag_seconds') return `${numberText(n)} 秒`
  return numberText(n)
}
function evidenceTitle(e: IncidentDetail['evidence'][number]) { return e.type === 'monitoring_signal' ? '触发原因' : toolLabel(e.source) }
function evidenceText(e: IncidentDetail['evidence'][number]) {
  const data = recordData(e)
  const result = resultData(e)
  if (e.type === 'monitoring_signal') {
    const key = String(data.metric_key || item.value?.anomaly_type || '')
    return `${metricLabel(key)}为 ${metricValueText(key, data.value)}，达到当前告警条件。`
  }
  if (e.source === 'query_incident_context') return '已确认本次告警的触发指标和当前处理状态。'
  if (e.source === 'query_database_health') {
    const signals = result.signals as Record<string, unknown> | undefined
    if (signals) {
      const parts: string[] = []
      if (signals['db.long_transactions'] !== undefined) parts.push(`发现 ${metricValueText('db.long_transactions', signals['db.long_transactions'])}持续超过 5 分钟的事务`)
      if (signals['db.lock_waits'] !== undefined) parts.push(`锁等待 ${metricValueText('db.lock_waits', signals['db.lock_waits'])}`)
      if (signals['db.connections.utilization_percent'] !== undefined) parts.push(`连接使用率 ${metricValueText('db.connections.utilization_percent', signals['db.connections.utilization_percent'])}`)
      if (parts.length) return `${parts.join('；')}。`
    }
    return '数据库连接正常，已完成事务、锁等待和连接使用情况检查。'
  }
  if (e.source === 'query_metric_history') {
    const rows = Array.isArray(data.result) ? data.result as Record<string, any>[] : []
    const row = rows.find(x => x.metric === item.value?.anomaly_type)
    if (row) return `过去 30 分钟记录了 ${numberText(row.count)} 次，当前为 ${metricValueText(String(row.metric), row.current)}，趋势${row.trend === 'stable' ? '基本稳定' : row.trend === 'up' ? '上升' : '变化'}。`
    return '已检查过去 30 分钟的指标变化。'
  }
  if (e.source === 'search_logs') {
    const lines = Array.isArray(result.lines) ? result.lines.length : 0
    const containers = Array.isArray(result.containers) ? result.containers.length : 0
    return `已检查 ${containers} 个容器日志，${lines ? `发现 ${lines} 条相关日志` : '没有发现相关错误日志'}。`
  }
  if (e.source === 'query_service_health') {
    const endpoint = Array.isArray(data.result) ? data.result[0] as Record<string, any> : null
    if (endpoint) return `健康检查${endpoint.ok ? '正常' : '失败'}，返回 ${endpoint.status_code || '未知'}，响应约 ${Math.round(Number(endpoint.latency_ms) || 0)} ms。`
  }
  return e.summary
}
const triggerEvidence = computed(() => item.value?.evidence.find(e => e.type === 'monitoring_signal'))
const triggerValue = computed(() => recordData(triggerEvidence.value).value)
const readableSummary = computed(() => {
  if (!item.value) return ''
  const key = item.value.anomaly_type
  if (key === 'db.long_transactions') return `数据库发现 ${metricValueText(key, triggerValue.value)}持续超过 5 分钟的事务。`
  return `${metricLabel(key)}出现异常，当前检测值为 ${metricValueText(key, triggerValue.value)}。`
})
const alertExplanation = computed(() => {
  if (!item.value || item.value.anomaly_type !== 'db.long_transactions') return ''
  const context = item.value.evidence.find(e => e.source === 'query_incident_context')
  const signals = context ? resultData(context).signals as Record<string, any>[] | undefined : undefined
  const rule = signals?.[0]?.rule
  const threshold = rule?.trigger_threshold ?? 3
  const triggerFor = rule?.trigger_for ?? 3
  const oldRule = Number(threshold) <= 1
  return oldRule
    ? `本次告警使用的是旧规则：连续 ${triggerFor} 次发现至少 ${threshold} 个超过 5 分钟的事务就告警。当前确实检测到 ${metricValueText(item.value.anomaly_type, triggerValue.value)}，所以被触发；但单个长事务也可能是正常后台任务，这个规则偏敏感。新的默认规则已调整为至少 3 个，并连续 3 次出现才告警。`
    : `系统连续 ${triggerFor} 次发现至少 ${threshold} 个超过 5 分钟的事务，因此触发告警。单个后台任务不会触发这条默认规则。`
})
</script>

<template>
  <div class="page" v-if="item">
    <div class="page-head">
      <div>
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
          <h1 style="margin: 0">{{ metricLabel(item.anomaly_type) }}</h1>
          <span class="badge" :class="sev(item.severity)">{{ severityLabel(item.severity) }}</span>
          <span class="badge neutral">{{ statusLabel(item.status) }}</span>
        </div>
        <p class="sub">{{ formatBeijingTime(item.last_seen) }}（北京时间）</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button @click="chat">继续追问</el-button>
        <el-button :loading="busy" @click="investigate">重新调查</el-button>
        <el-button type="success" @click="resolve">标记恢复</el-button>
      </div>
    </div>

    <div class="two-col">
      <div class="card">
        <h3>概述</h3>
        <p>{{ readableSummary }}</p>
        <div v-if="alertExplanation" class="alert-explanation"><b>为什么会告警？</b><p>{{ alertExplanation }}</p></div>
        <h3 style="margin-top: 18px">证据</h3>
        <div class="evidence" v-for="e in item.evidence" :key="e.id">
          <b>{{ evidenceTitle(e) }}</b>
          <div>{{ evidenceText(e) }}</div>
          <small class="muted">{{ formatBeijingTime(e.observed_at) }}</small>
        </div>
        <p v-if="!item.evidence?.length" class="muted">暂无证据</p>
      </div>

      <div class="card">
        <h3>诊断</h3>
        <template v-if="item.diagnosis">
          <p><b>{{ readableText(item.diagnosis.summary) }}</b></p>
          <p>根因：{{ readableText(item.diagnosis.root_cause) }}</p>
          <p class="muted">置信度 {{ Math.round((item.diagnosis.confidence || 0) * 100) }}%</p>
          <h4>处理步骤</h4>
          <ol><li v-for="x in item.diagnosis.remediation || []">{{ readableText(x) }}</li></ol>
          <h4>验证</h4>
          <ul><li v-for="x in item.diagnosis.verification || []">{{ readableText(x) }}</li></ul>
          <template v-if="item.diagnosis.knowledge_refs?.length">
            <h4>知识库引用</h4>
            <ul><li v-for="x in item.diagnosis.knowledge_refs">{{ x.title || x.document_id }} <span v-if="x.page_range" class="muted">p.{{ x.page_range }}</span></li></ul>
          </template>
        </template>
        <p v-else class="muted">尚未完成诊断</p>
      </div>
    </div>

    <div class="card" style="margin-top: 16px">
      <h3>调查过程</h3>
      <div v-for="run in trace?.agent_runs || []" :key="run.id" class="trace-run">
        <b>{{ run.mode === 'investigate' ? '自动调查' : run.mode }} · {{ operationStatus(run.status) }}</b>
        <div class="tool-row" v-for="t in run.tools" :key="t.created_at + t.tool_name"><code>{{ toolLabel(t.tool_name) }}</code><span>{{ operationStatus(t.status) }} · {{ t.summary }}</span></div>
        <div class="tool-row" v-for="r in run.retrievals" :key="r.created_at"><code>知识库</code><span>找到 {{ r.hit_count }} 条相关资料</span></div>
      </div>
      <p v-if="!trace?.agent_runs?.length" class="muted">暂无调查记录</p>
    </div>
  </div>
</template>

<style scoped>
.alert-explanation { margin: 12px 0 4px; padding: 12px 14px; border: 1px solid #f1dfb8; border-radius: 10px; background: #fffaf0; color: #755a20; }
.alert-explanation b { display: block; margin-bottom: 4px; color: #654b16; }
.alert-explanation p { margin: 0; line-height: 1.6; }
</style>
