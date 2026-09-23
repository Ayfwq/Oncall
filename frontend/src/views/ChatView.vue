<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { api, streamChat } from '../api'
import { useRoute } from 'vue-router'
import type { ChatMessage, Conversation, KnowledgeCitation, KnowledgeCitationDetail, ProjectSummary } from '../types'

const route = useRoute()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const convs = ref<Conversation[]>([]), projects = ref<ProjectSummary[]>([]), active = ref(''), messages = ref<ChatMessage[]>([]), input = ref('')
const busy = ref(false), newProject = ref(''), search = ref(''), showArchived = ref(false), statusLine = ref(''), error = ref('')
const mobileListOpen = ref(false)
const messagesEl = ref<HTMLElement | null>(null)
const expandedCitation = ref<string | null>(null)
const citationDetails = ref<Record<string, KnowledgeCitationDetail>>({})
const citationLoading = ref<Record<string, boolean>>({})
const citationErrors = ref<Record<string, string>>({})
let scrollQueued = false

const activeConversation = computed(() => convs.value.find(x => x.id === active.value))
function render(text: string) { return DOMPurify.sanitize(md.render(text || '')) }
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
function scrollToBottom() { if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight }
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
  if (!active.value && convs.value[0]) await open(convs.value[0].id)
}
async function createConversation() {
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
async function open(id: string) {
  active.value = id; expandedCitation.value = null; messages.value = await api<ChatMessage[]>(`/conversations/${id}/messages`); mobileListOpen.value = false
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
  if (nextArchived && active.value === c.id) { active.value = ''; messages.value = [] }
  await load()
}
async function removeConversation(c: Conversation) {
  if (busy.value) return
  if (!confirm(`删除会话「${c.title}」？删除后消息和上下文将无法恢复。`)) return
  await api(`/conversations/${c.id}`, { method: 'DELETE' })
  if (active.value === c.id) { active.value = ''; messages.value = [] }
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
    })
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
onMounted(async () => { await load(); const q = String(route.query.conversation || ''); if (q) await open(q) })
</script>

<template>
  <div class="chat-layout">
    <!-- conversation list -->
    <div v-if="mobileListOpen" class="conv-scrim" @click="mobileListOpen = false"></div>
    <section class="conv-list" :class="{ open: mobileListOpen }">
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
            <span class="conv-name">{{ c.title }}</span>
          </div>
          <div class="conv-meta">{{ c.incident_id ? 'Incident 会话' : timeAgo(c.updated_at) }}</div>
          <div class="conv-actions" @click.stop>
            <button class="conv-icon archive" :title="c.archived ? '恢复会话' : '归档会话'" :aria-label="c.archived ? '恢复会话' : '归档会话'" @click="archiveConversation(c)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4zM3 4h18v3H3zM9 11h6" /></svg>
            </button>
            <button class="conv-icon danger" title="删除会话" aria-label="删除会话" @click="removeConversation(c)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M10 4h4l1 3H9l1-3M8 10v7M12 10v7M16 10v7M7 7l1 14h8l1-14" /></svg>
            </button>
          </div>
        </div>
        <div v-if="!convs.length" style="color: var(--text-3); text-align: center; padding: 30px 10px; font-size: 13px">暂无会话</div>
      </div>
      <div style="padding: 8px 16px; border-top: 1px solid var(--border)">
        <el-checkbox v-model="showArchived" size="small">显示已归档</el-checkbox>
      </div>
    </section>

    <!-- chat -->
    <section class="chat">
      <div class="chat-head">
        <button class="menu-btn mobile-conv-toggle" @click="mobileListOpen = true" title="会话列表">☰</button>
        <template v-if="activeConversation">
          <div class="t">
            <b>{{ activeConversation.title }}</b>
            <span>{{ activeConversation.incident_id ? '围绕 Incident 持续追问' : activeConversation.project_id ? '已绑定监控项目' : '通用问答' }}</span>
          </div>
          <el-dropdown trigger="click" @command="onCmd">
            <button class="menu-btn" title="更多">⋯</button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">重命名</el-dropdown-item>
                <el-dropdown-item command="archive">{{ activeConversation.archived ? '恢复' : '归档' }}</el-dropdown-item>
                <el-dropdown-item command="remove" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </div>

      <div ref="messagesEl" class="messages">
        <div v-if="!active" class="empty-state">
          <img class="empty-icon" src="/pulseops-icon.png" alt="巡脉图标" />
            <div class="welcome-kicker">PULSEOPS · 巡脉智能运维</div>
            <h2>从一个运维问题开始</h2>
            <p class="muted">普通运维问题可直接咨询；绑定项目后可进一步查询实时日志、指标和服务状态。</p>
        </div>
        <template v-else>
          <div v-for="(m, i) in messages" :key="m.id || i" class="msg-row" :class="m.role === 'user' ? 'user' : 'assistant'">
            <img v-if="m.role === 'assistant'" class="msg-avatar ai" src="/pulseops-icon.png" alt="巡脉图标" />
            <div class="msg-bubble">
              <div v-if="m.role === 'assistant'" class="markdown" v-html="render(m.content)"></div>
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
                        <div class="citation-source-label">命中的原文</div>
                        <div class="citation-source-text">{{ citationDetail(citation)?.content }}</div>
                        <div v-if="citationDetail(citation)?.truncated" class="citation-loading">原文较长，当前仅展示前 24000 个字符。</div>
                        <details v-if="citationDetail(citation)?.neighbors.length" class="citation-neighbors">
                          <summary>查看相邻知识分块</summary>
                          <div v-for="neighbor in citationDetail(citation)?.neighbors || []" :key="neighbor.chunk_id" class="citation-neighbor">
                            <div><b>{{ neighbor.heading_path.join(' / ') || `分块 ${neighbor.chunk_index + 1}` }}</b><span v-if="neighbor.page_range"> · 第 {{ neighbor.page_range }} 页</span></div>
                            <p>{{ neighbor.content }}</p>
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
        <div class="composer-inner">
          <textarea v-model="input" placeholder="输入运维问题，Enter 发送 / Shift+Enter 换行" @input="autoresize" @keydown.enter.exact.prevent="onEnterKey" @keydown.ctrl.enter.prevent="onEnterKey"></textarea>
          <button class="send-btn" :disabled="busy || !input.trim()" @click="send" title="发送">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
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
.empty-state { min-height: 100%; display:flex; flex-direction:column; justify-content:center; text-align: center; padding: 36px 20px 155px; }
.empty-icon { width: 58px; height: 58px; margin: 0 auto 17px; border-radius: 18px; object-fit: contain; display: block; box-shadow: 0 15px 36px -18px rgba(25,132,99,.7); }
.welcome-kicker { color: var(--accent-strong); font-size: 11px; font-weight: 700; letter-spacing: .14em; margin-bottom: 7px; }
.empty-state h2 { font-size: 20px; }
.empty-state > p { max-width: 510px; margin: 0 auto 24px; }
.chat-error{margin:7px auto 0;color:#c0393f;font-size:12px;text-align:center;max-width:680px}
.composer-hint{margin:7px auto 0;color:#9aa8a3;font-size:10px;text-align:center}
@media (max-width: 760px) { .empty-state { padding-top: 55px; } }

.mobile-conv-toggle { display: none; }
.conv-scrim { display: none; }
@media (max-width: 680px) {
  .mobile-conv-toggle { display: inline-flex; margin-left: -6px; flex-shrink: 0; }
  .conv-list { display: flex; position: fixed; z-index: 45; inset: 0 auto 0 0; width: 280px; max-width: 86vw; transform: translateX(-102%); transition: transform .2s ease; box-shadow: var(--shadow-lg); background: #fbfbfc; }
  .conv-list.open { transform: translateX(0); }
  .conv-scrim { display: block; position: fixed; inset: 0; z-index: 40; background: rgba(15, 23, 42, .35); }
}
</style>
