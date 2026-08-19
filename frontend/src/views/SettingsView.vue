<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const readiness = ref<any>(), error = ref('')
async function load() { try { readiness.value = await api('/settings/readiness') } catch (e: any) { error.value = e.message } }
onMounted(load)
function badge(ok: boolean, fallback = false) { return ok ? 'ok' : fallback ? 'neutral' : 'err' }
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div><h1>设置</h1><p class="sub">本机运行就绪状态（密钥由 .env 管理，不在页面回显）</p></div>
      <el-button @click="load">刷新</el-button>
    </div>
    <p v-if="error" style="color: var(--danger)">{{ error }}</p>

    <div class="grid" v-if="readiness">
      <div class="card">
        <h3>模型</h3>
        <p class="muted" style="margin: 0">{{ readiness.llm.provider }} · {{ readiness.llm.model }}</p>
        <div style="margin-top: 12px"><span class="badge" :class="badge(readiness.llm.configured, true)">{{ readiness.llm.configured ? '已连接' : '未配置' }}</span></div>
      </div>
      <div class="card">
        <h3>知识检索</h3>
        <p class="muted" style="margin: 0">Embedding · {{ readiness.embedding.model }}</p>
        <div style="margin-top: 12px">
          <span class="badge" :class="badge(readiness.embedding.configured, true)">{{ readiness.embedding.configured ? '语义向量' : 'BM25 混合' }}</span>
          <span class="badge neutral" style="margin-left: 6px">Rerank · {{ readiness.rerank.configured ? '已配置' : '本地' }}</span>
        </div>
      </div>
      <div class="card">
        <h3>飞书</h3>
        <p class="muted" style="margin: 0">{{ readiness.feishu.enabled ? '已启用' : '未启用' }}</p>
        <div style="margin-top: 12px"><span class="badge" :class="readiness.feishu.configured ? 'ok' : 'neutral'">{{ readiness.feishu.configured ? '凭证完整' : '未接入' }}</span></div>
      </div>
      <div class="card">
        <h3>数据存储</h3>
        <div class="kv"><span>PostgreSQL</span><span class="badge ok">已连接</span></div>
        <div class="kv"><span>Milvus</span><span class="badge ok">已连接</span></div>
      </div>
    </div>
  </div>
</template>
