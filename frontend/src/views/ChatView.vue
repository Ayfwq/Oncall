<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { ElMessage } from 'element-plus'
import { api, streamChat } from '../api'
import { useRoute } from 'vue-router'
import { formatBeijingTime } from '../formatTime'
import type { ChatMessage, Conversation, KnowledgeCitation, KnowledgeCitationDetail, ProjectSummary } from '../types'
import WelcomePanel from '../components/WelcomePanel.vue'

const route = useRoute()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const convs = ref<Conversation[]>([]), projects = ref<ProjectSummary[]>([]), active = ref(''), messages = ref<ChatMessage[]>([]), input = ref('')
const busy = ref(false), newProject = ref(''), search = ref(''), showArchived = ref(false), statusLine = ref(''), error = ref('')
const mobileListOpen = ref(false)
const initialLoading = ref(true)
const composerEl = ref<HTMLTextAreaElement | null>(null)
async function choosePrompt(text: string) {
  input.value = text
  await nextTick()
  composerEl.value?.focus()
  if (composerEl.value) {
    composerEl.value.style.height = 'auto'
    composerEl.value.style.height = Math.min(composerEl.value.scrollHeight, 200) + 'px'
  }
}
const messagesEl = ref<HTMLElement | null>(null)
const expandedCitation = ref<string | null>(null)
const incidentFocus = ref<string | null>(String(route.query.incident || '') || null)
const citationDetails = ref<Record<string, KnowledgeCitationDetail>>({})
const citationLoading = ref<Record<string, boolean>>({})
const citationErrors = ref<Record<string, string>>({})
type ContextUsage = {
  estimated_tokens: number
  context_window_tokens: number
  compact_at_tokens: number
  can_compact: boolean
  scope: string
  compaction: { status: string; error: string | null } | null
}
const contextUsage = ref<ContextUsage | null>(null)
const compacting = ref(false)
const contextMenuOpen = ref(false)
const contextPercent = computed(() => Math.min(100, Math.round((contextUsage.value?.estimated_tokens || 0) / (contextUsage.value?.context_window_tokens || 64000) * 100)))
function formatTokenCount(value?: number) { return new Intl.NumberFormat('zh-CN').format(value || 0) }
const contextTitle = computed(() => contextUsage.value
  ? `上下文约 ${formatTokenCount(contextUsage.value.estimated_tokens)} / ${formatTokenCount(contextUsage.value.context_window_tokens)} token（${contextPercent.value}%）；自动压缩阈值 ${formatTokenCount(contextUsage.value.compact_at_tokens)} token`
  : '正在读取上下文用量')
function closeContextMenuOnOutside(event: PointerEvent) {
  if (!(event.target as HTMLElement | null)?.closest('.context-control')) contextMenuOpen.value = false
}
function compressFromContextMenu() {
  contextMenuOpen.value = false
  if (!active.value) {
    ElMessage.info('请先打开一个会话')
    return
  }
  void compactContext()
}
async function refreshContextUsage(id: string) {
  try {
    const usage = await api<ContextUsage>(`/conversations/${id}/context-usage`)
    if (active.value !== id) return
    contextUsage.value = usage
    if (compacting.value && usage.compaction?.status === 'done') {
      compacting.value = false
      ElMessage.success('上下文压缩完成')
    } else if (compacting.value && usage.compaction?.status === 'dead') {
      compacting.value = false
      ElMessage.error(usage.compaction.error || '上下文压缩失败')
    } else if (usage.compaction?.status === 'pending' || usage.compaction?.status === 'running') {
      compacting.value = true
    }
  } catch { /* A later refresh can retry. The submit action reports its own errors. */ }
}
async function compactContext() {
  const id = active.value
  if (!id || compacting.value || busy.value) return
  if (contextUsage.value && !contextUsage.value.can_compact) {
    ElMessage.info('对话太短，暂时没有可压缩内容')
    return
  }
  compacting.value = true
  try {
    await api(`/conversations/${id}/compact`, { method: 'POST' })
    await refreshContextUsage(id)
  } catch (e) {
    compacting.value = false
    ElMessage.error(e instanceof Error ? e.message : '压缩提交失败')
  }
}
let scrollQueued = false

const activeConversation = computed(() => convs.value.find(x => x.id === active.value))
function render(text: string) { return DOMPurify.sanitize(md.render(text || '')) }
function renderMessage(message: ChatMessage) {
  if (message.metadata?.intent !== 'active_alerts') return render(message.content)
  const labels: Record<string, string> = {
    critical: '严重', warning: '警告', info: '提示', open: '待调查',
    investigating: '调查中', diagnosed: '已诊断', resolved: '已恢复',
    failed: '调查失败', firing: '触发中', pending: '待处理',
  }
  return render(message.content.replace(/\b(critical|warning|info|open|investigating|diagnosed|resolved|failed|firing|pending)\b/gi, word => labels[word.toLowerCase()] || word))
}
function citationsOf(message: ChatMessage): KnowledgeCitation[] {
  const value = message.metadata?.citations
  return Array.isArray(value) ? value as KnowledgeCitation[] : []
}
function citationKey(citation: KnowledgeCitation) { return String(citation.chunk_id || citation.citation_id || citation.document_id) }
function citationSummary(message: ChatMessage) {
  const refs = citationsOf(message)
  const used = refs.filter(x => x.used_in_answer).length
  return used ? `已引用 ${used}/${refs.length}` : `已检索 ${refs.length} 条，回答未明确引用`
}
function citationTitle(citation: KnowledgeCitation) { return citation.title || citation.document_id || '知识库分块' }
function citationDetail(citation: KnowledgeCitation) { return citationDetails.value[citationKey(citation)] }
async function toggleCitation(citation: KnowledgeCitation) {
  const key = citationKey(citation)
  if (expandedCitation.value === key) { expandedCitation.value = null; return }
  expandedCitation.value = key
  if (!citation.chunk_id || citationDetails.value[key]) return
  citationLoading.value = { ...citationLoading.value, [key]: true }
  citationErrors.value = { ...citationErrors.value, [key]: '' }
  try {
    citationDetails.value = {
      ...citationDetails.value,
      [key]: await api<KnowledgeCitationDetail>(`/knowledge/citations/${encodeURIComponent(citation.chunk_id)}`),
    }
  } catch (e) {
    citationErrors.value = { ...citationErrors.value, [key]: e instanceof Error ? e.message : '原文加载失败' }
  } finally {
    citationLoading.value = { ...citationLoading.value, [key]: false }
  }
}
function timeAgo(iso?: string) { if (!iso) return ''; const d = new Date(iso).getTime(); const s = Math.floor((Date.now() - d) / 1000); if (s < 60) return '刚刚'; if (s < 3600) return Math.floor(s / 60) + ' 分钟前'; if (s < 86400) return Math.floor(s / 3600) + ' 小时前'; return Math.floor(s / 86400) + ' 天前' }
function scrollToBottom() { if (messagesEl.value) messagesEl.value.scrollTop = messages.value.length ? messagesEl.value.scrollHeight : 0 }
function queueScroll() {
  if (scrollQueued) return
  scrollQueued = true
  requestAnimationFrame(() => { scrollQueued = false; scrollToBottom() })
}

async function load() {
  const qs = new URLSearchParams()
  if (search.value.trim()) qs.set('q', search.value.trim())
  if (showArchived.value) qs.set('include_archived', 'true')
  convs.value = await api('/conversations' + (qs.size ? '?' + qs.toString() : ''))
  projects.value = await api('/projects')
  if (!active.value && convs.value[0]) await open(String(route.query.conversation || convs.value[0].id))
}
async function createConversation() {
  incidentFocus.value = null
  const c = await api<Conversation>('/conversations', { method: 'POST', body: JSON.stringify({ title: '新对话', project_id: newProject.value || null }) })
  newProject.value = ''; search.value = ''; showArchived.value = false
  await load()
  await open(c.id)
  return c
}
async function create() {
  error.value = ''
  try {
    await createConversation()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function open(id: string, clearFocus = false) {
  if (clearFocus) incidentFocus.value = null
  active.value = id; expandedCitation.value = null; contextUsage.value = null; compacting.value = false
  void refreshContextUsage(id)
  messages.value = await api<ChatMessage[]>(`/conversations/${id}/messages`); mobileListOpen.value = false
  await nextTick(); scrollToBottom()
}
async function rename() {
  const c = activeConversation.value; if (!c) return
  const title = prompt('会话名称', c.title)?.trim(); if (!title) return
  await api(`/conversations/${c.id}`, { method: 'PATCH', body: JSON.stringify({ title }) }); await load()
}
async function archive() {
  const c = activeConversation.value; if (!c) return
  await archiveConversation(c)
}
async function archiveConversation(c: Conversation) {
  const nextArchived = !c.archived
  await api(`/conversations/${c.id}`, { method: 'PATCH', body: JSON.stringify({ archived: nextArchived }) })
  if (nextArchived && active.value === c.id) { active.value = ''; messages.value = []; contextUsage.value = null }
  await load()
}
async function removeConversation(c: Conversation) {
  if (busy.value) return
  if (!confirm(`删除会话「${c.title}」？删除后消息和上下文将无法恢复。`)) return
  await api(`/conversations/${c.id}`, { method: 'DELETE' })
  if (active.value === c.id) { active.value = ''; messages.value = []; contextUsage.value = null }
  await load()
}
async function remove() {
  const c = activeConversation.value
  if (c) await removeConversation(c)
}
function onCmd(cmd: string) { if (cmd === 'rename') rename(); else if (cmd === 'archive') archive(); else if (cmd === 'remove') remove() }

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  error.value = ''
  if (!active.value) {
    try {
      await createConversation()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return
    }
  }
  if (!active.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  const streamingMsg: ChatMessage = { role: 'assistant', content: '' }
  messages.value.push(streamingMsg)
  busy.value = true; statusLine.value = '正在分析…'
  await nextTick(); scrollToBottom()
  try {
    await streamChat(active.value, text, (e) => {
      if (e.type === 'token') streamingMsg.content += e.data.content
      else if (e.type === 'tool_started') statusLine.value = '正在调用只读工具 ' + e.data.tool_name + ' …'
      else if (e.type === 'tool_finished') statusLine.value = (e.data.ok ? '✓ ' : '✗ ') + e.data.tool_name + (e.data.ok ? '' : ' · ' + (e.data.error || '失败'))
      else if (e.type === 'intent_routed') { const label = ({ casual_chat: '普通聊天', ops_qa: '运维问答', project_query: '项目实时查询', incident_followup: '告警追问', incident_investigation: '告警调查', clarification: '需要补充上下文' } as Record<string, string>)[e.data.intent]; statusLine.value = '已识别为 ' + (label || e.data.intent) }
      else if (e.type === 'knowledge_started') statusLine.value = '正在检索运维知识库…'
      else if (e.type === 'knowledge_finished') statusLine.value = e.data.ok ? '知识库检索完成，正在生成…' : '知识库暂不可用，使用通用知识继续回答…'
      else if (e.type === 'rag_retrieved') statusLine.value = '已检索知识库 ' + e.data.count + ' 条，正在生成…'
      else if (e.type === 'diagnosis_ready') statusLine.value = '诊断完成 · 置信度 ' + Math.round((e.data.confidence || 0) * 100) + '%'
      else if (e.type === 'final') streamingMsg.content = e.data.content
      else if (e.type === 'error') streamingMsg.content = '错误：' + e.data.message
      queueScroll()
    }, incidentFocus.value)
    await load()
    // The streaming placeholder is local-only and does not contain the
    // persisted citation metadata. Reload the active conversation so the
    // knowledge-base source panel appears immediately after completion.
    if (active.value) await open(active.value)
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (!streamingMsg.content) streamingMsg.content = '错误：' + msg
  } finally { busy.value = false; statusLine.value = '' }
}
function autoresize(e: Event) { const t = e.target as HTMLTextAreaElement; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 200) + 'px' }
function onEnterKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  send()
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(search, () => { clearTimeout(timer); timer = setTimeout(load, 250) })
watch(showArchived, load)
let opsTimer: ReturnType<typeof setInterval> | undefined
let contextTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  document.addEventListener('pointerdown', closeContextMenuOnOutside)
  load().catch(e => { error.value = e instanceof Error ? e.message : '会话加载失败，请稍后重试' }).finally(() => { initialLoading.value = false })
  contextTimer = setInterval(() => {
    if (active.value && compacting.value) void refreshContextUsage(active.value)
  }, 3000)
  opsTimer = setInterval(async () => {
    if (busy.value) return
    try {
      const current = activeConversation.value
      if (!current || current.type !== 'ops') return
      const rows = await api<ChatMessage[]>(`/conversations/${current.id}/messages`)
      if (active.value !== current.id || busy.value) return
      const previousLast = messages.value[messages.value.length - 1]?.id
      if (rows[rows.length - 1]?.id === previousLast && rows.length === messages.value.length) return
      const el = messagesEl.value
      const nearBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
      messages.value = rows
      convs.value = await api<Conversation[]>('/conversations')
      if (nearBottom) await nextTick().then(scrollToBottom)
    } catch { /* The next refresh will retry. */ }
  }, 5000)
})
onUnmounted(() => { document.removeEventListener('pointerdown', closeContextMenuOnOutside); if (opsTimer) clearInterval(opsTimer); if (contextTimer) clearInterval(contextTimer); clearTimeout(timer) })
</script>

<template>
  <div class="chat-layout">
    <!-- conversation list -->
    <div v-if="mobileListOpen" class="conv-scrim" @click="mobileListOpen = false"></div>
    <section class="conv-list" :class="{ open: mobileListOpen }">
      <div class="conv-list-top">
        <div class="conv-section-heading"><b>会话记录</b><span>{{ convs.length.toString().padStart(2, '0') }}</span></div>
        <el-select v-model="newProject" clearable placeholder="绑定项目（可选）" size="small" style="width: 100%; margin-bottom: 10px">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button type="primary" class="new-btn" @click="create">
          <span style="font-size: 15px; line-height: 1">＋</span>&nbsp; 新建对话
        </el-button>
        <el-input v-model="search" clearable placeholder="搜索会话…" size="small" class="conv-list-search"><template #prefix><svg class="search-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg></template></el-input>
      </div>
      <div class="conv-scroll">
        <div v-for="c in convs" :key="c.id" class="conv" :class="{ active: active === c.id }" @click="open(c.id, true)">
          <div class="conv-title">
            <span v-if="c.type === 'ops'" class="badge warn" style="padding: 0 6px">飞书同步</span>
            <span class="conv-name">{{ c.title }}</span>
          </div>
          <div class="conv-meta">{{ c.incident_id ? 'Incident 会话' : timeAgo(c.updated_at) }}</div>
          <div v-if="c.type !== 'ops'" class="conv-actions" @click.stop>
            <button class="conv-icon archive" :title="c.archived ? '恢复会话' : '归档会话'" :aria-label="c.archived ? '恢复会话' : '归档会话'" @click="archiveConversation(c)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4zM3 4h18v3H3zM9 11h6" /></svg>
            </button>
            <button class="conv-icon danger" title="删除会话" aria-label="删除会话" @click="removeConversation(c)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M10 4h4l1 3H9l1-3M8 10v7M12 10v7M16 10v7M7 7l1 14h8l1-14" /></svg>
            </button>
          </div>
        </div>
        <div v-if="!convs.length" class="conversation-empty"><span>⌁</span><b>{{ initialLoading ? '正在加载会话…' : search ? '没有匹配的会话' : '新的灵感，从这里开始' }}</b><p>{{ search ? '试试其他搜索关键词' : '你的排查思路与对话，将保存在这里' }}</p></div>
      </div>
      <div style="padding: 8px 16px; border-top: 1px solid var(--border)">
        <el-checkbox v-model="showArchived" size="small">显示已归档</el-checkbox>
      </div>
    </section>

    <!-- chat -->
    <section class="chat">
      <div class="chat-head">
        <button class="menu-btn mobile-conv-toggle" @click="mobileListOpen = true" title="会话列表" aria-label="打开会话列表">☰</button>
        <div v-if="!activeConversation" class="t chat-default-title"><b>巡脉助手 <span class="assistant-badge">AI</span></b><span>让每一个运维问题，都有迹可循</span></div>
        <template v-if="activeConversation">
          <div class="t">
            <b>{{ activeConversation.title }}</b>
            <span>{{ activeConversation.type === 'ops' ? 'Web 与飞书共用 · 所有告警在此显示' : activeConversation.incident_id ? '围绕 Incident 持续追问' : activeConversation.project_id ? '已绑定监控项目' : '通用问答' }}</span>
          </div>
          <el-dropdown trigger="click" @command="onCmd">
            <button class="menu-btn" title="更多">⋯</button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">重命名</el-dropdown-item>
                <el-dropdown-item v-if="activeConversation.type !== 'ops'" command="archive">{{ activeConversation.archived ? '恢复' : '归档' }}</el-dropdown-item>
                <el-dropdown-item v-if="activeConversation.type !== 'ops'" command="remove" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </div>

      <div v-if="incidentFocus && activeConversation?.type === 'ops'" class="incident-focus">
        正在追问事件 {{ incidentFocus.slice(0, 8) }}
        <button type="button" @click="incidentFocus = null">退出事件追问</button>
      </div>

      <div ref="messagesEl" class="messages" :class="{ 'welcome-messages': !messages.length }" role="region" aria-label="对话内容" tabindex="0">
        <div v-if="initialLoading" class="chat-loading" role="status"><span class="dot"></span>正在载入工作空间…</div>
        <WelcomePanel v-else-if="!messages.length" @prompt="choosePrompt" />
        <template v-else>
          <div v-for="(m, i) in messages" :key="m.id || i" class="msg-row" :class="[m.role === 'event' ? 'event' : m.role === 'user' ? 'user' : 'assistant', m.metadata?.intent === 'active_alerts' ? 'active-alerts' : '']">
            <img v-if="m.role === 'assistant'" class="msg-avatar ai" src="/pulseops-icon.png" alt="巡脉图标" />
            <div class="msg-bubble">
              <div v-if="m.role === 'event'" class="event-content">
                <div class="markdown" v-html="render(m.content)"></div>
                <div v-if="m.created_at" class="event-time">通知时间 · {{ formatBeijingTime(m.created_at) }}（北京时间）</div>
                <div v-if="m.metadata?.incident_id" class="event-actions">
                  <a :href="'/incidents/' + m.metadata.incident_id">查看告警详情</a>
                  <button v-if="activeConversation?.type === 'ops'" type="button" @click="incidentFocus = String(m.metadata.incident_id)">追问此告警</button>
                </div>
              </div>
              <div v-else-if="m.role === 'assistant'" class="markdown" v-html="renderMessage(m)"></div>
              <div v-else>{{ m.content }}</div>
              <div v-if="m.role === 'assistant' && citationsOf(m).length" class="citation-panel">
                <div class="citation-panel-head">
                  <span>知识库依据</span>
                  <span class="citation-panel-status">{{ citationSummary(m) }}</span>
                </div>
                <div class="citation-list">
                  <template v-for="citation in citationsOf(m)" :key="citationKey(citation)">
                    <button
                      type="button"
                      class="citation-chip"
                      :class="{ used: citation.used_in_answer, unavailable: !citation.chunk_id }"
                      :disabled="!citation.chunk_id"
                      :aria-expanded="expandedCitation === citationKey(citation)"
                      :title="citation.chunk_id ? '展开知识库原文' : '该历史引用没有关联知识库分块'"
                      @click="toggleCitation(citation)"
                    >
                      <span class="citation-id">{{ citation.citation_id || 'KB' }}</span>
                      <span class="citation-name">{{ citationTitle(citation) }}</span>
                      <span v-if="citation.page_range" class="citation-page">第 {{ citation.page_range }} 页</span>
                      <span class="citation-used">{{ citation.used_in_answer ? '已引用' : '检索命中' }}</span>
                      <span class="citation-chevron">{{ expandedCitation === citationKey(citation) ? '⌃' : '⌄' }}</span>
                    </button>
                    <div v-if="expandedCitation === citationKey(citation)" class="citation-detail">
                      <div v-if="citationLoading[citationKey(citation)]" class="citation-loading">正在读取知识库原文…</div>
                      <div v-else-if="citationErrors[citationKey(citation)]" class="citation-error">{{ citationErrors[citationKey(citation)] }}</div>
                      <div v-else-if="!citation.chunk_id" class="citation-error">该历史引用没有关联可展开的知识库分块。</div>
                      <template v-else-if="citationDetail(citation)">
                        <div class="citation-detail-head">
                          <b>{{ citationDetail(citation)?.title }}</b>
                          <span>{{ citationDetail(citation)?.original_filename }}</span>
                        </div>
                        <div class="citation-detail-meta">
                          <span v-if="citationDetail(citation)?.page_range">页码：{{ citationDetail(citation)?.page_range }}</span>
                          <span v-if="citationDetail(citation)?.heading_path.length">章节：{{ citationDetail(citation)?.heading_path.join(' / ') }}</span>
                          <span>分块：{{ (citationDetail(citation)?.chunk_index || 0) + 1 }}</span>
                        </div>
                        <div class="citation-source-label">命中分块 · 第 {{ (citationDetail(citation)?.chunk_index || 0) + 1 }} 块</div>
                        <div class="citation-source-text">{{ citationDetail(citation)?.content }}</div>
                        <div v-if="citationDetail(citation)?.truncated" class="citation-loading">原文较长，当前仅展示前 24000 个字符。</div>
                        <details v-if="citationDetail(citation)?.neighbors.length" class="citation-neighbors">
                          <summary>相邻分块 · {{ citationDetail(citation)?.neighbors.length }} 块</summary>
                          <div class="citation-neighbor-list">
                          <article v-for="neighbor in citationDetail(citation)?.neighbors || []" :key="neighbor.chunk_id" class="citation-neighbor">
                            <div class="citation-neighbor-head">
                              <span class="citation-neighbor-position">{{ neighbor.chunk_index < (citationDetail(citation)?.chunk_index || 0) ? '上一块' : '下一块' }} · 第 {{ neighbor.chunk_index + 1 }} 块</span>
                              <b>{{ neighbor.heading_path.join(' / ') || '无章节标题' }}</b>
                              <span v-if="neighbor.page_range" class="citation-neighbor-page">第 {{ neighbor.page_range }} 页</span>
                            </div>
                            <p>{{ neighbor.content }}</p>
                          </article>
                          </div>
                        </details>
                      </template>
                    </div>
                  </template>
                </div>
              </div>
            </div>
            <div v-if="m.role === 'user'" class="msg-avatar me">A</div>
          </div>
        </template>
        <div v-if="busy" class="status-line"><span class="dot"></span>{{ statusLine || 'PulseOps 正在分析…' }}</div>
      </div>

      <div class="composer">
        <div class="composer-label"><span><i></i> 和巡脉一起探索</span><span>Enter 发送 <kbd>↵</kbd></span></div>
        <div class="composer-inner">
          <textarea ref="composerEl" v-model="input" aria-label="运维问题" placeholder="描述你遇到的问题，剩下的我们一起解决…" @input="autoresize" @keydown.enter.exact.prevent="onEnterKey" @keydown.ctrl.enter.prevent="onEnterKey"></textarea>
          <div class="context-control">
            <button class="context-circle" type="button" :title="contextTitle" :aria-label="contextTitle + '；压缩上文'" :aria-expanded="contextMenuOpen" @click="contextMenuOpen = !contextMenuOpen">
              <svg class="context-ring" viewBox="0 0 20 20" aria-hidden="true"><circle class="context-ring-track" cx="10" cy="10" r="7.5"/><circle class="context-ring-used" cx="10" cy="10" r="7.5" :stroke-dashoffset="47.124 * (1 - contextPercent / 100)"/></svg>
            </button>
            <div v-if="contextMenuOpen" class="context-menu" role="menu">
              <button class="context-option" type="button" role="menuitem" :disabled="compacting || busy || (contextUsage !== null && !contextUsage.can_compact)" @click="compressFromContextMenu">
                {{ compacting ? '正在压缩…' : '压缩上文' }}
              </button>
            </div>
          </div>
          <button class="send-btn" :disabled="busy || !input.trim()" @click="send" title="发送" aria-label="发送消息">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>
          </button>
        </div>
        <p v-if="error" class="chat-error">{{ error }}</p>
        <p class="composer-hint">PulseOps 会结合监控数据与知识库回答，请在执行变更前核实建议。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.menu-btn { background: none; border: none; font-size: 20px; color: var(--text-2); cursor: pointer; padding: 4px 10px; border-radius: 8px; line-height: 1; }
.menu-btn:hover { background: #eef0f4; }
.conv { position: relative; }
.conv-actions { position: absolute; right: 7px; top: 10px; display: flex; gap: 3px; opacity: 0; transition: opacity .15s; }
.conv:hover .conv-actions, .conv.active .conv-actions, .conv-actions:focus-within { opacity: 1; }
.conv-icon { width: 28px; height: 28px; display: grid; place-items: center; border: 1px solid #dce8e3; border-radius: 7px; background: rgba(255,255,255,.96); color: var(--text-2); cursor: pointer; padding: 0; }
.conv-icon svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.conv-icon:hover { background: #eaf6f1; border-color: #a9d9c7; color: var(--accent-strong); }
.conv-icon.danger:hover { background: #fff1f0; border-color: #f2c5c2; color: #c0393f; }
.chat-error{margin:7px auto 0;color:#c0393f;font-size:12px;text-align:center;max-width:980px}
.incident-focus{padding:8px 18px;background:#fff6e8;color:#8a5513;font-size:12px;display:flex;justify-content:space-between;align-items:center;gap:12px}.incident-focus button{border:0;background:none;color:#a86413;cursor:pointer;font-weight:600}
.msg-row.event{justify-content:center;box-sizing:border-box;width:100%}
.msg-row.event .msg-bubble{box-sizing:border-box;width:100%;max-width:700px;min-width:0;padding:18px 20px;background:#fff8ed;border:1px solid #edd7b5;border-radius:20px;color:#6d4a1f;box-shadow:0 5px 18px rgba(123,79,27,.035);overflow-wrap:anywhere}
.msg-row.assistant.active-alerts .msg-bubble{box-sizing:border-box;max-width:700px;padding:18px 20px;border:1px solid #cce8db;border-radius:20px;background:#f5fbf7;box-shadow:0 5px 18px rgba(24,84,66,.045);overflow-wrap:anywhere}
.event-content{min-width:0}.event-content a{display:inline-flex;margin-top:12px;color:#a86413;font-weight:600;text-decoration:none}.event-content a:hover{text-decoration:underline}
.event-time{margin-top:10px;color:#947a59;font-size:12px;line-height:1.5}
.event-actions{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.event-actions button{margin-top:12px;padding:0;border:0;background:none;color:#a86413;font:inherit;font-weight:600;cursor:pointer}
.event-actions button:hover{text-decoration:underline}
.composer-hint{width:100%;max-width:980px;margin:9px auto 0;color:#83948d;font-size:11px;line-height:1.5;text-align:center}
.context-control{position:relative;flex:0 0 auto}
.context-circle{position:relative;box-sizing:border-box;display:grid;place-items:center;width:36px;height:36px;flex:0 0 auto;padding:0;border:0;border-radius:50%;background:transparent;cursor:pointer;transition:background .16s,transform .16s}
.context-circle:hover{background:#f3f6f4;transform:scale(1.03)}
.context-circle:focus-visible{outline:2px solid #2daa7a;outline-offset:1px}
.context-ring{width:22px;height:22px;overflow:visible}
.context-ring circle{fill:none;stroke-width:2}
.context-ring-track{stroke:#c9d2ce}
.context-ring-used{stroke:#17191a;stroke-dasharray:47.124;stroke-linecap:round;transform:rotate(-90deg);transform-origin:10px 10px;transition:stroke-dashoffset .3s ease}
.context-menu{position:absolute;right:-4px;bottom:calc(100% + 12px);z-index:8;box-sizing:border-box;width:max-content;min-width:136px;padding:6px;border:1px solid #deebe5;border-radius:14px;background:#fff;box-shadow:0 12px 32px rgba(28,76,59,.15);animation:context-menu-in .14s ease-out}
.context-menu::after{position:absolute;right:17px;bottom:-6px;width:10px;height:10px;border-right:1px solid #deebe5;border-bottom:1px solid #deebe5;background:#fff;content:"";transform:rotate(45deg)}
.context-option{position:relative;z-index:1;display:flex;align-items:center;justify-content:flex-start;gap:20px;width:100%;min-height:40px;padding:0 9px;border:0;border-radius:9px;background:transparent;color:#33453d;font:inherit;font-size:13px;text-align:left;cursor:pointer}
.context-option:hover{background:#f5faf7}
.context-option:disabled{color:#9ba8a2;cursor:not-allowed}
.context-option:disabled:hover{background:transparent}
@keyframes context-menu-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.composer-inner{transition:border-color .18s,box-shadow .18s}
.composer-inner:focus-within{border-color:#a5d6bf;box-shadow:0 12px 38px -20px rgba(24,84,66,.38),0 0 0 3px rgba(49,169,116,.08)}

.mobile-conv-toggle { display: none; }
.conv-scrim { display: none; }
@media (max-width: 680px) {
  .context-circle{width:34px;height:34px}
  .context-menu{right:-3px;min-width:204px}
  .composer-hint{margin-top:8px;font-size:10px}
  .mobile-conv-toggle { display: inline-flex; margin-left: -6px; flex-shrink: 0; }
  .conv-list { display: flex; position: fixed; z-index: 45; inset: 0 auto 0 0; width: 280px; max-width: 86vw; transform: translateX(-102%); transition: transform .2s ease; box-shadow: var(--shadow-lg); background: #fbfbfc; }
  .conv-list.open { transform: translateX(0); }
  .conv-scrim { display: block; position: fixed; inset: 0; z-index: 40; background: rgba(15, 23, 42, .35); }
}
</style>
