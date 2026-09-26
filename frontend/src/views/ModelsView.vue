<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })

type Service = 'llm' | 'embedding' | 'rerank'
interface ModelConfig {
  kind: Service
  name: string
  provider: string
  base_url: string
  model: string
  api_key_configured: boolean
  embedding_dimension?: number | null
}
interface ModelProfile extends ModelConfig {
  id: string
  active: boolean
  created_at: string
  reindex_queued?: number
}
interface ProfilesResponse {
  profiles: ModelProfile[]
  current: Record<Service, ModelConfig>
}
interface ModelDraft {
  kind: Service
  name: string
  provider: 'openai-compatible' | 'mock'
  base_url: string
  model: string
  api_key: string
  embedding_dimension: number | null
}
interface ProviderTemplate {
  id: string
  name: string
  mark: string
  tone: string
  logo?: string
  endpoint: string
  kinds: Service[]
}

const kinds: { id: Service; label: string }[] = [
  { id: 'llm', label: '大语言模型' },
  { id: 'embedding', label: 'Embedding' },
  { id: 'rerank', label: 'Rerank' },
]
const providerTemplates: ProviderTemplate[] = [
  { id: 'custom', name: '自定义模型', mark: '✦', tone: 'mint', endpoint: '', kinds: ['llm', 'embedding', 'rerank'] },
  { id: 'deepseek', name: 'DeepSeek', mark: 'DS', tone: 'cyan', logo: 'https://www.deepseek.com/favicon.ico', endpoint: 'https://api.deepseek.com', kinds: ['llm'] },
  { id: 'volcengine', name: '火山引擎', mark: '火', tone: 'blue', logo: 'https://portal.volccdn.com/obj/volcfe/misc/favicon.png', endpoint: 'https://ark.cn-beijing.volces.com/api/v3', kinds: ['llm', 'embedding'] },
  { id: 'minimax-cn', name: 'MiniMax CN', mark: 'M', tone: 'coral', logo: 'https://www.minimaxi.com/favicon.ico', endpoint: 'https://api.minimaxi.com/v1', kinds: ['llm'] },
  { id: 'minimax', name: 'MiniMax Global', mark: 'M', tone: 'coral', logo: 'https://www.minimax.io/favicon.ico', endpoint: 'https://api.minimax.io/v1', kinds: ['llm'] },
  { id: 'glm-cn', name: 'Bigmodel', mark: 'Z', tone: 'slate', logo: 'https://open.bigmodel.cn/static/images/favicon.png', endpoint: 'https://open.bigmodel.cn/api/paas/v4', kinds: ['llm', 'embedding'] },
  { id: 'qwen-cn', name: '通义千问（中国区）', mark: '阿', tone: 'orange', logo: 'https://assets.alicdn.com/g/qwenweb/qwen-chat-fe/0.2.91/static/images/qwen-logo.svg', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', kinds: ['llm', 'embedding'] },
  { id: 'qwen', name: '通义千问（国际区）', mark: '阿', tone: 'orange', logo: 'https://assets.alicdn.com/g/qwenweb/qwen-chat-fe/0.2.91/static/images/qwen-logo.svg', endpoint: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', kinds: ['llm', 'embedding'] },
  { id: 'xiaomi-mimo', name: 'Xiaomi MIMO', mark: 'mi', tone: 'xiaomi', logo: 'https://www.mi.com/favicon.ico', endpoint: 'https://api.xiaomimimo.com/v1', kinds: ['llm'] },
  { id: 'siliconflow', name: '硅基流动', mark: 'SF', tone: 'indigo', logo: 'https://www.siliconflow.cn/favicon.ico', endpoint: 'https://api.siliconflow.cn/v1', kinds: ['llm', 'embedding', 'rerank'] },
  { id: 'z-ai', name: 'Z.ai', mark: 'Z', tone: 'violet', logo: 'https://z-cdn.chatglm.cn/z-ai/static/logo.svg', endpoint: 'https://api.z.ai/api/paas/v4', kinds: ['llm'] },
  { id: 'openrouter', name: 'OpenRouter', mark: '◈', tone: 'violet', logo: 'https://openrouter.ai/favicon.ico', endpoint: 'https://openrouter.ai/api/v1', kinds: ['llm'] },
  { id: 'kimi-cn', name: 'Kimi CN', mark: 'K', tone: 'slate', logo: 'https://www.moonshot.cn/favicon.ico', endpoint: 'https://api.moonshot.cn/v1', kinds: ['llm'] },
  { id: 'kimi', name: 'Kimi Global', mark: 'K', tone: 'slate', logo: 'https://www.moonshot.ai/favicon.ico', endpoint: 'https://api.moonshot.ai/v1', kinds: ['llm'] },
  { id: 'byteplus', name: 'BytePlus', mark: 'BP', tone: 'blue', logo: 'https://sf-bpcms.bytepluscdn.com/obj/byteplus-public-aiso/portal/assets/favicon.png', endpoint: 'https://ark.ap-southeast.bytepluses.com/api/v3', kinds: ['llm'] },
  { id: 'aws-bedrock', name: 'AWS', mark: 'aws', tone: 'amber', logo: 'https://aws.amazon.com/favicon.ico', endpoint: 'https://bedrock-mantle.us-east-1.api.aws/v1', kinds: ['llm'] },
  { id: 'tencent-hunyuan', name: '腾讯云', mark: '云', tone: 'cyan', logo: 'https://cloud.tencent.com/favicon.ico', endpoint: 'https://api.hunyuan.cloud.tencent.com/v1', kinds: ['llm'] },
  { id: 'moark', name: '模力方舟', mark: 'MO', tone: 'green', logo: 'https://www.moark.com/favicon.ico', endpoint: 'https://api.moark.com/v1', kinds: ['llm'] },
  { id: 'ppio', name: 'PPIO', mark: 'P', tone: 'coral', logo: 'https://ppio.com/favicon.ico', endpoint: 'https://api.ppio.com/openai/v1', kinds: ['llm'] },
  { id: 'openai', name: 'OpenAI', mark: 'AI', tone: 'dark', logo: 'https://openai.com/favicon.svg', endpoint: 'https://api.openai.com/v1', kinds: ['llm', 'embedding'] },
  { id: 'groq', name: 'Groq', mark: 'G', tone: 'amber', logo: 'https://groq.com/favicon.ico', endpoint: 'https://api.groq.com/openai/v1', kinds: ['llm'] },
  { id: 'xai-grok', name: 'xAI / Grok', mark: 'X', tone: 'dark', logo: 'https://x.ai/favicon.ico', endpoint: 'https://api.x.ai/v1', kinds: ['llm'] },
  { id: 'opencode-zen', name: 'OpenCode Zen', mark: 'OC', tone: 'dark', logo: 'https://opencode.ai/favicon.ico', endpoint: 'https://opencode.ai/zen/v1', kinds: ['llm'] },
  { id: 'jina', name: 'Jina AI', mark: 'J', tone: 'blue', logo: 'https://jina.ai/favicon.ico', endpoint: 'https://api.jina.ai/v1/rerank', kinds: ['rerank'] },
  { id: 'cohere', name: 'Cohere', mark: 'C', tone: 'green', logo: 'https://cohere.com/favicon.ico', endpoint: 'https://api.cohere.com/v2/rerank', kinds: ['rerank'] },
]
const data = ref<ProfilesResponse | null>(null)
const pageError = ref('')
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const discovering = ref(false)
const editorOpen = ref(false)
const editingId = ref<string | null>(null)
const discoveredModels = ref<string[]>([])
const probeMessage = ref('')
const probeOk = ref(false)
const providerTemplate = ref('custom')
const draft = reactive<ModelDraft>({ kind: 'llm', name: '', provider: 'openai-compatible', base_url: '', model: '', api_key: '', embedding_dimension: null })

const savedProfiles = computed(() => data.value?.profiles || [])
const activeProfile = (kind: Service) => savedProfiles.value.find(profile => profile.kind === kind && profile.active)
const profilesFor = (kind: Service) => savedProfiles.value.filter(profile => profile.kind === kind)
const selectedProfile = (kind: Service) => activeProfile(kind)?.id || '__environment__'
const availableProviders = computed(() => providerTemplates.filter(item => item.kinds.includes(draft.kind)))
const selectedProvider = computed<ProviderTemplate>(() => providerTemplates.find(item => item.id === providerTemplate.value) || providerTemplates[0]!)
const displayModelName = (model: string) => {
  const parts = model.split('/').filter(Boolean)
  return parts[parts.length - 1] || model
}

async function load() {
  loading.value = true
  pageError.value = ''
  try { data.value = await api<ProfilesResponse>('/model-profiles') }
  catch (e) { pageError.value = e instanceof Error ? e.message : String(e) }
  finally { loading.value = false }
}

function resetEditor() {
  editingId.value = null
  discoveredModels.value = []
  probeMessage.value = ''
  providerTemplate.value = 'custom'
  Object.assign(draft, { kind: 'llm', name: '', provider: 'openai-compatible', base_url: '', model: '', api_key: '', embedding_dimension: null })
}

function addProfile(kind: Service = 'llm') {
  resetEditor()
  draft.kind = kind
  editorOpen.value = true
}

function useTemplate(value: unknown) {
  providerTemplate.value = String(value)
  const template = providerTemplates.find(item => item.id === providerTemplate.value)
  if (!template || template.id === 'custom') return
  draft.model = ''
  draft.embedding_dimension = null
  discoveredModels.value = []
  draft.base_url = template.id === 'siliconflow' && draft.kind === 'rerank'
    ? 'https://api.siliconflow.cn/v1/rerank'
    : template.endpoint
}

function onEndpointInput() {
  providerTemplate.value = 'custom'
  draft.embedding_dimension = null
  discoveredModels.value = []
}

function hideBrokenIcon(event: Event) {
  const image = event.currentTarget as HTMLImageElement | null
  if (image) image.hidden = true
}

async function discover() {
  discovering.value = true
  probeMessage.value = ''
  try {
    const result = await api<{ ok: boolean; message: string; models: string[] }>('/settings/models/discover', {
      method: 'POST',
      body: JSON.stringify({
        service: draft.kind,
        base_url: draft.base_url,
        model: draft.model,
        api_key: draft.api_key,
        profile_id: editingId.value,
      }),
    })
    discoveredModels.value = result.models || []
    probeOk.value = result.ok
    probeMessage.value = result.message
  } catch (e) {
    probeOk.value = false
    probeMessage.value = e instanceof Error ? e.message : String(e)
  } finally { discovering.value = false }
}

async function testConnection() {
  testing.value = true
  probeMessage.value = ''
  try {
    const result = await api<{ ok: boolean; message: string; embedding_dimension?: number | null }>('/settings/models/test', {
      method: 'POST',
      body: JSON.stringify({
        service: draft.kind,
        base_url: draft.base_url,
        model: draft.model,
        api_key: draft.api_key,
        profile_id: editingId.value,
      }),
    })
    probeOk.value = result.ok
    probeMessage.value = result.message
    if (result.ok && draft.kind === 'embedding' && result.embedding_dimension) {
      draft.embedding_dimension = result.embedding_dimension
    }
  } catch (e) {
    probeOk.value = false
    probeMessage.value = e instanceof Error ? e.message : String(e)
  } finally { testing.value = false }
}

async function saveProfile(activate: boolean) {
  saving.value = true
  pageError.value = ''
  try {
    const body = { ...draft, api_key: draft.api_key || null, activate }
    let saved: ModelProfile
    if (editingId.value) {
      saved = await api<ModelProfile>(`/model-profiles/${editingId.value}`, { method: 'PUT', body: JSON.stringify(body) })
    } else {
      saved = await api<ModelProfile>('/model-profiles', { method: 'POST', body: JSON.stringify(body) })
    }
    editorOpen.value = false
    ElMessage.success(saved.reindex_queued
      ? `模型已启用，${saved.reindex_queued} 个知识文档正在重新索引`
      : saved.active ? '模型已保存并立即生效' : '模型已保存到历史列表')
    await load()
  } catch (e) { pageError.value = e instanceof Error ? e.message : String(e) }
  finally { saving.value = false }
}

async function activateProfile(profile: ModelProfile) {
  try {
    const result = await api<{ message: string }>(`/model-profiles/${profile.id}/activate`, { method: 'POST' })
    ElMessage.success(result.message)
    await load()
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : String(e)) }
}

async function chooseProfile(kind: Service, profileId: string) {
  if (!profileId || profileId === '__environment__') return
  const profile = profilesFor(kind).find(item => item.id === profileId)
  if (profile) await activateProfile(profile)
}

function handleProfileChoice(kind: Service, value: unknown) {
  void chooseProfile(kind, String(value || ''))
}

watch(() => draft.model, () => {
  if (draft.kind === 'embedding') draft.embedding_dimension = null
})

onMounted(load)
</script>

<template>
  <div class="page models-page" :class="{ embedded }">
    <div v-if="!embedded" class="page-head">
      <div><h1>模型接入</h1><p class="sub">为项目选择大语言模型、Embedding 和 Rerank；需要更换时直接使用下拉框。</p></div>
    </div>
    <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" show-icon />

    <section class="section-block">
      <div v-if="data" class="current-grid">
        <article v-for="kind in kinds" :key="kind.id" class="card current-card">
          <h2>{{ kind.label }}</h2>
          <label class="select-label">选择模型</label>
          <el-select
            :model-value="selectedProfile(kind.id)"
            placeholder="选择模型"
            style="width:100%"
            @change="handleProfileChoice(kind.id, $event)"
          >
            <el-option
              v-if="!activeProfile(kind.id)"
              :label="`${displayModelName(data.current[kind.id].model)}（当前环境）`"
              value="__environment__"
            />
            <el-option
              v-for="profile in profilesFor(kind.id)"
              :key="profile.id"
              :label="`${profile.name} · ${displayModelName(profile.model)}`"
              :value="profile.id"
            />
          </el-select>
          <el-button plain class="add-model-button" @click="addProfile(kind.id)">＋ 添加模型</el-button>
        </article>
      </div>
      <el-skeleton v-else-if="loading" :rows="3" animated />
    </section>

    <el-dialog v-model="editorOpen" title="添加模型" width="min(880px, 94vw)" destroy-on-close>
      <div class="model-editor">
        <div class="editor-grid">
          <label>模型用途
            <el-select v-model="draft.kind" style="width:100%" disabled>
              <el-option v-for="kind in kinds" :key="kind.id" :label="kind.label" :value="kind.id" />
            </el-select>
          </label>
          <label>配置名称
            <el-input v-model="draft.name" placeholder="例如：生产环境 DeepSeek" />
          </label>
          <label class="full-field">服务商模板
            <el-select :model-value="providerTemplate" class="provider-select" popper-class="provider-popper" style="width:100%" @change="useTemplate">
              <template #prefix>
                <span class="provider-mark provider-mark-compact" :class="`provider-mark-${selectedProvider.tone}`">
                  <span>{{ selectedProvider.mark }}</span>
                  <img v-if="selectedProvider.logo" :src="selectedProvider.logo" alt="" referrerpolicy="no-referrer" @error="hideBrokenIcon" />
                </span>
              </template>
              <el-option v-for="provider in availableProviders" :key="provider.id" :label="provider.name" :value="provider.id">
                <div class="provider-option">
                  <span class="provider-mark" :class="`provider-mark-${provider.tone}`">
                    <span>{{ provider.mark }}</span>
                    <img v-if="provider.logo" :src="provider.logo" alt="" loading="lazy" referrerpolicy="no-referrer" @error="hideBrokenIcon" />
                  </span>
                  <span class="provider-copy"><b>{{ provider.name }}</b><small>{{ provider.endpoint || '手动填写 OpenAI 兼容地址' }}</small></span>
                </div>
              </el-option>
            </el-select>
          </label>
          <label class="full-field">Endpoint URL
            <el-input v-model="draft.base_url" placeholder="https://api.example.com/v1" @input="onEndpointInput" />
          </label>
          <label class="full-field">添加 API Key
            <div class="api-key-actions">
              <el-input v-model="draft.api_key" type="password" show-password :placeholder="editingId ? '留空保留已保存密钥；输入新密钥可替换' : '输入服务商 API Key'" />
              <el-button :loading="testing" @click.prevent="testConnection">测试连接</el-button>
              <el-button :loading="discovering" @click.prevent="discover">发现模型</el-button>
            </div>
          </label>
          <label class="full-field">模型
            <el-select v-model="draft.model" filterable allow-create default-first-option style="width:100%" placeholder="点击发现模型后，从结果中选择适合本项目的模型">
              <el-option v-for="model in discoveredModels" :key="model" :label="displayModelName(model)" :value="model" />
            </el-select>
          </label>
        </div>
        <div v-if="probeMessage" class="probe-result" :class="probeOk ? 'success' : 'failure'">{{ probeMessage }}</div>
      </div>
      <template #footer>
        <el-button @click="saveProfile(false)" :loading="saving">仅保存</el-button>
        <el-button type="primary" @click="saveProfile(true)" :loading="saving">保存并启用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.models-page{max-width:1160px}.models-page.embedded{max-width:none;margin:0;padding:0}.section-block{margin-top:24px}.embedded .section-block{margin-top:0}.current-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.current-card{padding:24px;display:flex;flex-direction:column;gap:14px;min-height:190px}.current-card h2{margin:0 0 10px;font-size:18px}.select-label{font-size:13px;color:var(--text-2)}.add-model-button{align-self:flex-start;margin-top:4px}.editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px}.editor-grid label{display:grid;gap:8px;font-size:13px;color:var(--text-2)}.editor-grid .full-field{grid-column:1/-1}.editor-grid small{font-weight:400}.api-key-actions{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;align-items:center}.provider-option{height:54px;display:flex;align-items:center;gap:12px}.provider-mark{position:relative;width:34px;height:34px;border-radius:10px;display:grid;place-items:center;color:#fff;font-size:10px;font-weight:800;flex:none;overflow:hidden;box-shadow:inset 0 1px rgba(255,255,255,.35),0 4px 10px rgba(39,75,55,.12)}.provider-mark img{position:absolute;inset:4px;width:26px;height:26px;border-radius:6px;padding:2px;background:#fff;object-fit:contain}.provider-mark-compact{width:28px;height:28px;border-radius:8px}.provider-mark-compact img{inset:3px;width:22px;height:22px}.provider-mark-mint{background:linear-gradient(135deg,#58bd8b,#22765b)}.provider-mark-cyan{background:linear-gradient(135deg,#68c8e4,#277f9d)}.provider-mark-blue{background:linear-gradient(135deg,#6e9dff,#3c57b9)}.provider-mark-coral{background:linear-gradient(135deg,#ff9aab,#db536b)}.provider-mark-slate{background:linear-gradient(135deg,#708090,#37434d)}.provider-mark-orange{background:linear-gradient(135deg,#ffbb5d,#e27725)}.provider-mark-indigo{background:linear-gradient(135deg,#8186ff,#4d4cb5)}.provider-mark-violet{background:linear-gradient(135deg,#ad80ff,#6542bf)}.provider-mark-amber{background:linear-gradient(135deg,#f3b84e,#a86518)}.provider-mark-green{background:linear-gradient(135deg,#74d19d,#26835d)}.provider-mark-dark{background:linear-gradient(135deg,#53616a,#192329)}.provider-mark-xiaomi{background:#ff6900}.provider-copy{display:grid;line-height:1.25;min-width:0}.provider-copy b{font-size:14px;color:var(--text-1)}.provider-copy small{font-size:11px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis}.probe-result{padding:12px 14px;margin-top:16px;border-radius:10px}.probe-result.success{background:#ecf8f1;color:#137a4a}.probe-result.failure{background:#fff0f0;color:#b4232e}.editor-hint{margin:14px 0 0;font-size:12px}:global(.provider-popper .el-select-dropdown__item){height:auto;min-height:56px;padding:4px 12px}@media(max-width:900px){.current-grid{grid-template-columns:1fr 1fr}}@media(max-width:620px){.current-grid,.editor-grid{grid-template-columns:1fr}.editor-grid .full-field{grid-column:auto}.api-key-actions{grid-template-columns:1fr}.api-key-actions .el-button{margin-left:0}}
</style>
