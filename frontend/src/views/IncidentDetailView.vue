<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import type { IncidentDetail, IncidentTrace } from '../types'
const route = useRoute(), router = useRouter(), id = String(route.params.id), item = ref<IncidentDetail | null>(null), trace = ref<IncidentTrace | null>(null), busy = ref(false)
async function load() { item.value = await api<IncidentDetail>(`/incidents/${id}`); trace.value = await api<IncidentTrace>(`/incidents/${id}/trace`) }
async function investigate() { busy.value = true; try { await api(`/incidents/${id}/investigate`, { method: 'POST' }); await load() } finally { busy.value = false } }
async function resolve() { await api(`/incidents/${id}/resolve`, { method: 'POST' }); await load() }
async function chat() { const r = await api<{ conversation_id: string }>(`/incidents/${id}/conversation`, { method: 'POST' }); router.push('/?conversation=' + r.conversation_id) }
onMounted(load)
const sev = (s: string) => s === 'critical' ? 'err' : 'warn'
</script>

<template>
  <div class="page" v-if="item">
    <div class="page-head">
      <div>
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
          <h1 style="margin: 0">{{ item.anomaly_type }}</h1>
          <span class="badge" :class="sev(item.severity)">{{ item.severity }}</span>
          <span class="badge neutral">{{ item.status }}</span>
        </div>
        <p class="sub">{{ new Date(item.last_seen).toLocaleString() }}</p>
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
        <p>{{ item.summary }}</p>
        <h3 style="margin-top: 18px">证据</h3>
        <div class="evidence" v-for="e in item.evidence" :key="e.id">
          <b>{{ e.source }}</b>
          <div>{{ e.summary }}</div>
          <small class="muted">{{ new Date(e.observed_at).toLocaleString() }}</small>
        </div>
        <p v-if="!item.evidence?.length" class="muted">暂无证据</p>
      </div>

      <div class="card">
        <h3>诊断</h3>
        <template v-if="item.diagnosis">
          <p><b>{{ item.diagnosis.summary }}</b></p>
          <p>根因：{{ item.diagnosis.root_cause }}</p>
          <p class="muted">置信度 {{ Math.round((item.diagnosis.confidence || 0) * 100) }}%</p>
          <h4>处理步骤</h4>
          <ol><li v-for="x in item.diagnosis.remediation || []">{{ x }}</li></ol>
          <h4>验证</h4>
          <ul><li v-for="x in item.diagnosis.verification || []">{{ x }}</li></ul>
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
        <b>{{ run.mode }} · {{ run.status }}</b>
        <div class="tool-row" v-for="t in run.tools" :key="t.created_at + t.tool_name"><code>{{ t.tool_name }}</code><span>{{ t.status }} · {{ Math.round(t.latency_ms) }}ms · {{ t.summary }}</span></div>
        <div class="tool-row" v-for="r in run.retrievals" :key="r.created_at"><code>RAG</code><span>{{ r.hit_count }} hits · {{ r.query }}</span></div>
      </div>
      <p v-if="!trace?.agent_runs?.length" class="muted">暂无调查记录</p>
    </div>
  </div>
</template>
