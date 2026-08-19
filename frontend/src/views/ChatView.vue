<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { api, streamChat } from '../api'
import { useRoute } from 'vue-router'

type C = { id: string; title: string; type: string; project_id?: string | null; incident_id?: string | null; updated_at?: string }
type M = { id?: string; role: string; content: string }
type P = { id: string; name: string }

const route = useRoute()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const convs = ref<C[]>([]), projects = ref<P[]>([]), active = ref(''), messages = ref<M[]>([]), input = ref('')
const busy = ref(false), newProject = ref(''), search = ref(''), showArchived = ref(false), statusLine = ref('')

const activeConversation = computed(() => convs.value.find(x => x.id === active.value))
function render(text: string) { return DOMPurify.sanitize(md.render(text || '')) }
function timeAgo(iso?: string) { if (!iso) return ''; const d = new Date(iso).getTime(); const s = Math.floor((Date.now() - d) / 1000); if (s < 60) return '刚刚'; if (s < 3600) return Math.floor(s / 60) + ' 分钟前'; if (s < 86400) return Math.floor(s / 3600) + ' 小时前'; return Math.floor(s / 86400) + ' 天前' }

async function load() {
  const qs = new URLSearchParams()
  if (search.value.trim()) qs.set('q', search.value.trim())
  if (showArchived.value) qs.set('include_archived', 'true')
  convs.value = await api('/conversations' + (qs.size ? '?' + qs.toString() : ''))
  projects.value = await api('/projects')
  if (!active.value && convs.value[0]) await open(convs.value[0].id)
}
async function create() {
  const c = await api<C>('/conversations', { method: 'POST', body: JSON.stringify({ title: '新对话', project_id: newProject.value || null }) })
  newProject.value = ''; search.value = ''; showArchived.value = false
  await load(); await open(c.id)
}
async function startWith(promptText: string) {
  if (!active.value) await create()
  input.value = promptText
}
async function open(id: string) { active.value = id; messages.value = await api(`/conversations/${id}/messages`) }
async function rename() {
  const c = activeConversation.value; if (!c) return
  const title = prompt('会话名称', c.title)?.trim(); if (!title) return
  await api(`/conversations/${c.id}`, { method: 'PATCH', body: JSON.stringify({ title }) }); await load()
}
async function archive() {
  const c = activeConversation.value; if (!c) return
  await api(`/conversations/${c.id}`, { method: 'PATCH', body: JSON.stringify({ archived: true }) })
  active.value = ''; messages.value = []; await load()
}
async function remove() {
  const c = activeConversation.value; if (!c || !confirm(`删除会话「${c.title}」？`)) return
  await api(`/conversations/${c.id}`, { method: 'DELETE' })
  active.value = ''; messages.value = []; await load()
}
function onCmd(cmd: string) { if (cmd === 'rename') rename(); else if (cmd === 'archive') archive(); else if (cmd === 'remove') remove() }

async function send() {
  if (!input.value.trim() || !active.value || busy.value) return
  const text = input.value; input.value = ''
  messages.value.push({ role: 'user', content: text })
  const streamingMsg: M = { role: 'assistant', content: '' }
  messages.value.push(streamingMsg)
  busy.value = true; statusLine.value = '正在分析…'
  try {
    await streamChat(active.value, text, (t, d) => {
      if (t === 'token') streamingMsg.content += d.content
      else if (t === 'tool_started') statusLine.value = '正在调用只读工具 ' + d.tool_name + ' …'
      else if (t === 'tool_finished') statusLine.value = (d.ok ? '✓ ' : '✗ ') + d.tool_name + (d.ok ? '' : ' · ' + (d.error || '失败'))
      else if (t === 'rag_retrieved') statusLine.value = '已检索知识库 ' + d.count + ' 条，正在生成…'
      else if (t === 'diagnosis_ready') statusLine.value = '诊断完成 · 置信度 ' + Math.round((d.confidence || 0) * 100) + '%'
      else if (t === 'final') streamingMsg.content = d.content
      else if (t === 'error') streamingMsg.content = '错误：' + d.message
    })
    await load()
  } catch (e: any) {
    if (!streamingMsg.content) streamingMsg.content = '错误：' + e.message
  } finally { busy.value = false; statusLine.value = '' }
}
function autoresize(e: Event) { const t = e.target as HTMLTextAreaElement; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 200) + 'px' }

let timer: any
watch(search, () => { clearTimeout(timer); timer = setTimeout(load, 250) })
watch(showArchived, load)
onMounted(async () => { await load(); const q = String(route.query.conversation || ''); if (q) await open(q) })
</script>

<template>
  <div class="chat-layout">
    <!-- conversation list -->
    <section class="conv-list">
      <div class="conv-list-top">
        <el-select v-model="newProject" clearable placeholder="绑定项目（可选）" size="small" style="width: 100%; margin-bottom: 10px">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button type="primary" class="new-btn" @click="create">
          <span style="font-size: 15px; line-height: 1">＋</span>&nbsp; 新建对话
        </el-button>
        <el-input v-model="search" clearable placeholder="搜索会话" size="small" class="conv-list-search" />
      </div>
      <div class="conv-scroll">
        <div v-for="c in convs" :key="c.id" class="conv" :class="{ active: active === c.id }" @click="open(c.id)">
          <div class="conv-title">
            <span v-if="c.type === 'incident'" class="badge warn" style="padding: 0 6px">告警</span>
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ c.title }}</span>
          </div>
          <div class="conv-meta">{{ c.incident_id ? 'Incident 会话' : timeAgo(c.updated_at) }}</div>
        </div>
        <div v-if="!convs.length" style="color: var(--text-3); text-align: center; padding: 30px 10px; font-size: 13px">暂无会话</div>
      </div>
      <div style="padding: 8px 16px; border-top: 1px solid var(--border)">
        <el-checkbox v-model="showArchived" size="small">显示已归档</el-checkbox>
      </div>
    </section>

    <!-- chat -->
    <section class="chat">
      <div class="chat-head" v-if="activeConversation">
        <div class="t">
          <b>{{ activeConversation.title }}</b>
          <span>{{ activeConversation.incident_id ? '围绕 Incident 持续追问' : activeConversation.project_id ? '已绑定监控项目' : '通用问答' }}</span>
        </div>
        <el-dropdown trigger="click" @command="onCmd">
          <button class="menu-btn" title="更多">⋯</button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="rename">重命名</el-dropdown-item>
              <el-dropdown-item command="archive">归档</el-dropdown-item>
              <el-dropdown-item command="remove" divided>删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <div class="messages">
        <div v-if="!active" class="empty-state">
          <div class="empty-icon">◈</div>
            <div class="welcome-kicker">ONCALL AI SRE</div>
            <h2>从一个运维问题开始</h2>
            <p class="muted">绑定项目后，我会用实时采集、知识库和只读工具协助你定位问题。</p>
            <div class="prompt-grid">
              <button class="prompt-card" @click="startWith('检查当前项目的 CPU、内存和磁盘状态')"><b>检查主机状态</b><span>CPU · 内存 · 磁盘</span><i>→</i></button>
              <button class="prompt-card" @click="startWith('最近有没有异常告警？请给出优先级最高的问题')"><b>梳理当前告警</b><span>Incident · 优先级 · 影响</span><i>→</i></button>
              <button class="prompt-card" @click="startWith('根据知识库给我一份服务异常排查步骤')"><b>查找排障方案</b><span>知识库 · SOP · 引用</span><i>→</i></button>
            </div>
        </div>
        <template v-else>
          <div v-for="(m, i) in messages" :key="m.id || i" class="msg-row" :class="m.role === 'user' ? 'user' : 'assistant'">
            <div v-if="m.role === 'assistant'" class="msg-avatar ai">◈</div>
            <div class="msg-bubble">
              <div v-if="m.role === 'assistant'" class="markdown" v-html="render(m.content)"></div>
              <div v-else>{{ m.content }}</div>
            </div>
            <div v-if="m.role === 'user'" class="msg-avatar me">A</div>
          </div>
        </template>
        <div v-if="busy" class="status-line"><span class="dot"></span>{{ statusLine || 'Oncall 正在分析…' }}</div>
      </div>

      <div class="composer">
        <div class="composer-inner">
          <textarea v-model="input" placeholder="输入运维问题，Enter 发送 / Shift+Enter 换行" @input="autoresize" @keydown.enter.exact.prevent="send" @keydown.ctrl.enter.prevent="send"></textarea>
          <button class="send-btn" :disabled="busy || !input.trim()" @click="send" title="发送">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.menu-btn { background: none; border: none; font-size: 20px; color: var(--text-2); cursor: pointer; padding: 4px 10px; border-radius: 8px; line-height: 1; }
.menu-btn:hover { background: #eef0f4; }
.empty-state { text-align: center; padding: 90px 20px 40px; }
.empty-icon { width: 64px; height: 64px; margin: 0 auto 18px; border-radius: 20px; background: linear-gradient(135deg, #818cf8, #6366f1); color: #fff; font-size: 28px; display: grid; place-items: center; box-shadow: var(--shadow); }
.welcome-kicker { color: var(--accent-strong); font-size: 11px; font-weight: 700; letter-spacing: .14em; margin-bottom: 7px; }
.empty-state h2 { font-size: 20px; }
.empty-state > p { max-width: 510px; margin: 0 auto 24px; }
.prompt-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; max-width: 760px; margin: 0 auto; text-align: left; }
.prompt-card { position: relative; text-align: left; border: 1px solid var(--border); background: var(--surface); border-radius: 13px; padding: 14px; cursor: pointer; box-shadow: var(--shadow-sm); transition: .15s; }
.prompt-card:hover { border-color: var(--accent); box-shadow: var(--shadow); transform: translateY(-2px); }
.prompt-card b, .prompt-card span { display: block; }
.prompt-card b { font-size: 13px; margin-bottom: 5px; }
.prompt-card span { font-size: 11px; color: var(--text-3); }
.prompt-card i { position: absolute; right: 12px; bottom: 12px; color: var(--accent-strong); font-style: normal; }
@media (max-width: 760px) { .prompt-grid { grid-template-columns: 1fr; max-width: 360px; } .empty-state { padding-top: 55px; } }
</style>
