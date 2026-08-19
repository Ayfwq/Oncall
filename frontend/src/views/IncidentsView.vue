<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const rows = ref<any[]>([]), router = useRouter()
onMounted(async () => rows.value = await api('/incidents'))
const sev = (s: string) => s === 'critical' ? 'err' : 'warn'
const sevTxt = (s: string) => s === 'critical' ? '严重' : '警告'
</script>

<template>
  <div class="page">
    <div class="page-head"><div><h1>告警</h1><p class="sub">由监控引擎确定性检测产生，可进入会话持续追问</p></div></div>
    <div class="grid" v-if="rows.length">
      <div class="card clickable" v-for="x in rows" :key="x.id" @click="router.push('/incidents/' + x.id)">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
          <span class="badge" :class="sev(x.severity)">{{ sevTxt(x.severity) }}</span>
          <span class="badge neutral">{{ x.status }}</span>
        </div>
        <b style="font-size: 15px">{{ x.anomaly_type }}</b>
        <p class="muted" style="margin: 6px 0 2px">{{ x.summary }}</p>
        <small class="muted">{{ new Date(x.last_seen).toLocaleString() }}</small>
      </div>
    </div>
    <div v-else class="card" style="text-align: center; color: var(--text-3); padding: 60px 20px">
      <div style="font-size: 40px; margin-bottom: 10px">✓</div>
      暂无告警，一切正常
    </div>
  </div>
</template>
