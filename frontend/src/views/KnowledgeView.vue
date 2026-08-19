<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const docs = ref<any[]>([]), file = ref<File>(), busy = ref(false), message = ref('')
async function load() { docs.value = await api('/knowledge/documents') }
async function waitJob(id: string) {
  for (let i = 0; i < 120; i++) {
    const j = await api<any>(`/knowledge/jobs/${id}`)
    if (j.status === 'done') { await load(); return }
    if (j.status === 'dead') throw new Error(j.last_error || '入库任务失败')
    await new Promise(r => setTimeout(r, 1000))
  }
  throw new Error('等待入库超时，请检查 rag-worker')
}
function pick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] || undefined }
async function upload() {
  if (!file.value) return
  busy.value = true; message.value = '上传并解析中…'
  try {
    const fd = new FormData(); fd.append('file', file.value)
    const r = await fetch('/api/knowledge/documents', { method: 'POST', credentials: 'include', body: fd })
    if (!r.ok) throw new Error(await r.text())
    const x = await r.json()
    message.value = '已上传，正在 Docling 解析并写入 Milvus…'
    await waitJob(x.job_id); message.value = '已入库，可在对话中检索'
  } catch (e: any) { message.value = '失败：' + e.message }
  finally { busy.value = false }
}
async function reindex(id: string) { busy.value = true; try { const x = await api<any>(`/knowledge/documents/${id}/reindex`, { method: 'POST' }); await waitJob(x.job_id); message.value = '已重建索引' } catch (e: any) { message.value = '失败：' + e.message } finally { busy.value = false } }
async function remove(id: string) { if (!confirm('确认删除该知识文档及其索引？')) return; await api(`/knowledge/documents/${id}`, { method: 'DELETE' }); await load() }
onMounted(load)
const status = (s: string) => s === 'ready' ? 'ok' : s === 'processing' ? 'warn' : 'neutral'
</script>

<template>
  <div class="page">
    <div class="page-head"><div><h1>知识库</h1><p class="sub">上传运维文档，Agent 会用混合检索引用相关内容回答</p></div></div>

    <div class="card" style="margin-bottom: 16px">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
        <label class="file-pick">
          <input type="file" accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt,.xlsx" @change="pick" />
          <span>{{ file ? file.name : '选择文件' }}</span>
        </label>
        <el-button type="primary" :loading="busy" :disabled="!file" @click="upload">上传并解析</el-button>
        <span class="muted" style="font-size: 13px">支持 PDF / Word / PPT / Markdown / Excel 等</span>
      </div>
      <p v-if="message" style="margin: 12px 0 0; font-size: 13px; color: var(--text-2)">{{ message }}</p>
    </div>

    <div class="grid">
      <div class="card" v-for="d in docs" :key="d.id">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
          <b style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ d.title }}</b>
          <span class="badge" :class="status(d.status)">{{ d.status }}</span>
        </div>
        <div style="display: flex; gap: 8px">
          <el-button size="small" :disabled="busy" @click="reindex(d.id)">重建索引</el-button>
          <el-button size="small" type="danger" plain @click="remove(d.id)">删除</el-button>
        </div>
      </div>
      <div v-if="!docs.length" class="card" style="text-align: center; color: var(--text-3); padding: 40px">还没有文档，上传一份开始</div>
    </div>
  </div>
</template>

<style scoped>
.file-pick { display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--border-strong); border-radius: 10px; padding: 8px 14px; cursor: pointer; font-size: 13px; color: var(--text-2); background: #fbfbfc; }
.file-pick:hover { border-color: var(--accent); }
.file-pick input { display: none; }
</style>
