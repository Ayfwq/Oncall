<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { formatBeijingTime } from '../formatTime'
import { metricLabel } from '../metricLabels'
import type { IncidentSummary } from '../types'
const rows = ref<IncidentSummary[]>([]), router = useRouter(), deleting = ref<string | null>(null)
async function load() { rows.value = await api<IncidentSummary[]>('/incidents') }
onMounted(load)
const sev = (s: string) => s === 'critical' ? 'err' : 'warn'
const sevTxt = (s: string) => s === 'critical' ? '严重' : '警告'
const statusTxt = (s: string) => {
  const normalized = String(s || '').trim().toLowerCase()
  return ({ open: '待处理', investigating: '调查中', diagnosed: '已诊断', resolved: '已恢复' } as Record<string, string>)[normalized] || s
}
function incidentSummary(row: IncidentSummary) {
  return row.summary.split(row.anomaly_type).join(metricLabel(row.anomaly_type)).replace('still firing', '仍在触发').replace('triggered', '已触发')
}
async function removeIncident(row: IncidentSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除“${metricLabel(row.anomaly_type)}”告警吗？相关证据、诊断和调查会话会一并删除，但监控规则和历史指标会保留。`,
      '删除告警',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
    deleting.value = row.id
    await api(`/incidents/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter(item => item.id !== row.id)
    ElMessage.success('告警已删除')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleting.value = null
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head"><div><h1>告警</h1><p class="sub">由监控引擎确定性检测产生，可进入会话持续追问</p></div></div>
    <div class="grid" v-if="rows.length">
      <div class="card clickable incident-card" v-for="x in rows" :key="x.id" @click="router.push('/incidents/' + x.id)">
        <div class="incident-card-head">
          <div class="incident-badges"><span class="badge" :class="sev(x.severity)">{{ sevTxt(x.severity) }}</span><span class="badge neutral">{{ statusTxt(x.status) }}</span></div>
          <el-button type="danger" plain size="small" :loading="deleting === x.id" @click.stop="removeIncident(x)">删除</el-button>
        </div>
        <b style="font-size: 15px">{{ metricLabel(x.anomaly_type) }}</b>
        <p class="muted" style="margin: 6px 0 2px">{{ incidentSummary(x) }}</p>
        <small class="muted">{{ formatBeijingTime(x.last_seen) }}</small>
      </div>
    </div>
    <div v-else class="card" style="text-align: center; color: var(--text-3); padding: 60px 20px">
      <div style="font-size: 40px; margin-bottom: 10px">✓</div>
      暂无告警，一切正常
    </div>
  </div>
</template>

<style scoped>
.incident-card { position: relative; transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease; }
.incident-card:hover { border-color: #cfe4dc; box-shadow: 0 10px 28px rgba(35, 89, 71, .08); transform: translateY(-1px); }
.incident-card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.incident-badges { display: flex; align-items: center; gap: 8px; }
</style>
