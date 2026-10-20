<template>
  <div :class="layout === 'dock'
    ? 'flex flex-col rounded-xl border border-slate-200 bg-slate-50 overflow-hidden'
    : 'rounded-xl border border-slate-200 bg-slate-50 overflow-hidden'">
    <div class="px-3 py-2 border-b border-slate-200 bg-white flex items-center justify-between gap-2 shrink-0">
      <div class="text-xs font-semibold text-slate-700 truncate">
        Переписка
        <span v-if="contact" class="font-normal text-slate-400">· {{ contact }}</span>
      </div>
      <span v-if="waiting" class="text-[10px] font-semibold text-orange-600">Ждёт ответа</span>
    </div>

    <div v-if="loading" class="flex-1 px-3 py-6 text-xs text-slate-400 text-center">Загружаем чат…</div>
    <div v-else-if="error" class="flex-1 px-3 py-3 text-xs text-rose-600">{{ error }}</div>
    <div
      v-else
      ref="scroller"
      class="chat-thread overscroll-contain px-3 py-2 space-y-2"
      :style="layout === 'dock'
        ? { height: '200px', overflowY: 'scroll' }
        : { maxHeight: '14rem', overflowY: 'auto' }"
    >
      <p v-if="!thread.length" class="text-xs text-slate-400 text-center py-4">Сообщений пока нет</p>
      <template v-for="(msg, idx) in thread" :key="msg.id || idx">
        <div v-if="isNewDay(idx)" class="flex justify-center">
          <span class="text-[10px] font-medium text-slate-500 bg-slate-200/80 rounded-full px-2 py-0.5">
            {{ fmtDayLabel(msg.createdAt) }}
          </span>
        </div>
        <div
          :class="['flex', isSystem(msg) ? 'justify-center' : (isSeller(msg) ? 'justify-end' : 'justify-start')]"
        >
        <div :class="['max-w-[78%] text-sm leading-snug px-3 py-2 rounded-xl break-words',
          isSeller(msg) ? 'bg-indigo-600 text-white rounded-br-md' :
          isSystem(msg) ? 'bg-slate-200 text-slate-600 text-center text-xs' :
          'bg-white border border-slate-200 text-slate-800 rounded-bl-md']">
          <div v-if="!isSystem(msg)" :class="['text-[10px] font-semibold mb-0.5', isSeller(msg) ? 'text-white/70' : 'text-slate-400']">
            {{ isSeller(msg) ? 'Мы' : (msg.fromClaim ? 'Покупатель · из заявки' : 'Покупатель') }}
          </div>
          <div v-if="msgVideos(msg).length" class="mb-1 space-y-1">
            <video v-for="(att, i) in msgVideos(msg)" :key="'v'+i"
              :src="att.url" controls playsinline preload="metadata"
              class="rounded max-w-[220px] max-h-[180px] bg-black" />
          </div>
          <div v-if="msgImages(msg).length" class="flex flex-wrap gap-1 mb-1">
            <a v-for="(att, i) in msgImages(msg)" :key="i" :href="att.url" target="_blank" rel="noopener">
              <img :src="att.url" class="w-16 h-16 object-cover rounded border border-white/30" />
            </a>
          </div>
          <div v-if="msgDocuments(msg).length" class="mb-1 space-y-1">
            <a v-for="(att, i) in msgDocuments(msg)" :key="'d'+i"
              :href="att.url" target="_blank" rel="noopener"
              class="block underline underline-offset-2 break-all text-xs">{{ att.name || 'Документ' }}</a>
          </div>
          <div v-if="msg.text" class="whitespace-pre-wrap">{{ msg.text }}</div>
          <div v-if="fmtMsgTime(msg.createdAt)"
            :class="['text-[10px] mt-1', isSeller(msg) ? 'text-white/60' : 'text-slate-400']">
            {{ fmtMsgTime(msg.createdAt) }}
          </div>
        </div>
        </div>
      </template>
    </div>

    <div class="border-t border-slate-200 bg-white p-2 space-y-1.5 shrink-0">
      <div v-if="suggest && suggest.reply_text"
        class="rounded-lg border border-indigo-100 bg-indigo-50 px-2 py-1.5 flex items-start justify-between gap-2">
        <p class="text-[10px] text-indigo-900 leading-snug">
          Предложенный ответ
          <span v-if="suggest.theme_name" class="opacity-80">· {{ suggest.theme_name }}</span>
        </p>
        <button type="button" @click="clearSuggest" class="shrink-0 text-[10px] text-indigo-700">Очистить</button>
      </div>
      <div v-else-if="suggestLoading" class="text-[10px] text-slate-400">Подбираем шаблон…</div>
      <div v-else-if="suggestMiss" class="rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5">
        <p class="text-[10px] font-semibold text-amber-900">{{ suggestMiss.need_human ? 'Нужен менеджер' : 'Ответ не подобран' }}</p>
        <p class="text-[10px] text-amber-800/80 leading-snug">{{ suggestMiss.reason || 'Нет подходящего шаблона' }}</p>
      </div>

      <div v-if="templatesLoading || templateGroups.length" class="relative">
        <div class="flex min-w-0 items-center gap-1">
          <p class="shrink-0 text-[10px] font-medium text-slate-400">Шаблоны</p>
          <div v-if="templatesLoading" class="text-[10px] text-slate-400">…</div>
          <template v-else>
            <div class="flex min-w-0 flex-1 gap-1 overflow-hidden">
              <button v-for="group in compactGroups" :key="group.theme_key"
                type="button"
                @click="onCompactTheme(group)"
                class="min-w-0 max-w-[9rem] truncate rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:border-indigo-300">
                {{ group.theme_name }}
              </button>
            </div>
            <button type="button" @click="templatesOpen = !templatesOpen"
              class="shrink-0 rounded-lg border border-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
              {{ templatesOpen ? 'Свернуть' : `Темы · ${templateGroups.length}` }}
            </button>
          </template>
        </div>
        <div v-if="templatesOpen" class="absolute inset-x-0 bottom-full z-20 mb-1 max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <div v-if="!focusTheme" class="grid grid-cols-1 gap-1">
            <button v-for="group in templateGroups" :key="group.theme_key"
              type="button" @click="onTheme(group)"
              class="rounded-lg border border-slate-200 px-2 py-1.5 text-left text-[11px] font-medium text-slate-700 hover:bg-slate-50">
              {{ group.theme_name }}
              <span class="block text-[10px] font-normal text-slate-400">{{ group.templates.length }} вар.</span>
            </button>
          </div>
          <div v-else>
            <button type="button" @click="focusTheme = ''" class="mb-1 text-[10px] text-slate-500">← Все темы</button>
            <div class="grid grid-cols-1 gap-1">
              <button v-for="tpl in (focusedGroup?.templates || [])" :key="tpl.id"
                type="button" @click="applyTemplate(tpl)"
                class="rounded-lg border border-slate-200 px-2 py-1.5 text-left text-[11px] hover:bg-slate-50">
                <span class="block font-semibold">{{ templateVariantLabel(tpl) }}</span>
                <span v-if="templatePreviewText(tpl)" class="mt-0.5 block text-[10px] text-slate-500 line-clamp-2">
                  {{ templatePreviewText(tpl) }}
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="attachments.length" class="flex flex-wrap gap-1">
        <div v-for="att in attachments" :key="att.id"
          class="flex max-w-40 items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-1.5 py-1">
          <span class="truncate text-[10px] text-slate-600">{{ att.file.name }}</span>
          <button type="button" @click="removeAttachment(att.id)" class="text-slate-400 hover:text-rose-600">×</button>
        </div>
      </div>

      <div class="flex items-end gap-1">
        <input ref="fileInput" type="file" multiple class="hidden"
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          @change="onFilesSelected" />
        <button type="button" @click="fileInput?.click()" :disabled="sending"
          title="Прикрепить JPEG, PNG или PDF"
          class="w-8 h-8 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 shrink-0">
          <svg class="mx-auto" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
          </svg>
        </button>
        <textarea
          ref="draftEl"
          v-model="draft"
          rows="1"
          placeholder="Написать здесь…"
          @keydown.enter.exact.prevent="send"
          class="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 resize-none min-h-[34px] max-h-24 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        ></textarea>
        <button type="button" @click="insertTemplate" :disabled="generating || sending"
          title="Подставить шаблон"
          class="w-8 h-8 rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700 shrink-0">
          <svg class="mx-auto" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 6h16M4 12h16M4 18h10"/>
          </svg>
        </button>
        <button type="button" @click="generateAi" :disabled="generating || sending"
          title="Сгенерировать ответ ИИ"
          class="w-8 h-8 rounded-lg border border-violet-200 bg-violet-50 text-violet-700 shrink-0">
          <svg v-if="generating" class="mx-auto animate-spin" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
          </svg>
          <svg v-else class="mx-auto" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/>
          </svg>
        </button>
        <button
          type="button"
          :disabled="!canSend"
          @click="send"
          class="shrink-0 text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-40"
        >{{ sending ? '…' : 'Отправить' }}</button>
      </div>
      <p v-if="hint" class="text-[11px] text-slate-500">{{ hint }}</p>
      <p v-if="sendError" class="text-[11px] text-rose-600">{{ sendError }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { apiFetch } from '../api'
import {
  canSendChatContent,
  chatAttachmentKey,
  validateChatAttachment,
  validateChatAttachmentAddition,
} from '../utils/chatAttachments'
import {
  compactThemeGroups,
  groupTemplatesByTheme,
  pickThemeTemplate,
  templatePreviewText,
  templateVariantLabel,
} from '../utils/templateGroups'

const props = defineProps({
  mp: { type: String, required: true },
  chatId: { type: String, required: true },
  layout: { type: String, default: 'mini' },
  claimComment: { type: String, default: '' },
})
const emit = defineEmits(['changed'])

const BOT = '/api/v1/chat-bot'
const OZON_HIDDEN = new Set([
  'nevozvratny', 'kupit_detal', 'promokody',
])

const loading = ref(false)
const sending = ref(false)
const generating = ref(false)
const error = ref('')
const sendError = ref('')
const hint = ref('')
const messages = ref([])
const draft = ref('')
const contact = ref('')
const waiting = ref(false)
const scroller = ref(null)
const draftEl = ref(null)
const fileInput = ref(null)
const attachments = ref([])
const templates = ref([])
const templatesLoading = ref(false)
const templatesOpen = ref(false)
const focusTheme = ref('')
const greeting = ref('')
const signature = ref('')
const nekomplektBranch = ref('')
const fulfillment = ref(null)
const suggest = ref(null)
const suggestMiss = ref(null)
const suggestLoading = ref(false)
let pollTimer = null

const canSend = computed(() => canSendChatContent(draft.value, attachments.value, sending.value) && !!props.chatId)

const visibleTemplates = computed(() => {
  const mp = (props.mp || 'wb').toLowerCase()
  let list = templates.value || []
  if (mp === 'ozon') list = list.filter(t => !OZON_HIDDEN.has(t.theme_key))
  else if (mp === 'ym') list = list.filter(t => t.theme_key !== 'nevozvratny' && t.theme_key !== 'promokody')
  else list = list.filter(t => t.theme_key !== 'kupit_detal')
  return list
})
const templateGroups = computed(() => groupTemplatesByTheme(visibleTemplates.value))
const compactGroups = computed(() => compactThemeGroups(templateGroups.value))
const focusedGroup = computed(() => templateGroups.value.find(g => g.theme_key === focusTheme.value) || null)

function msgTime(value) {
  if (value == null || value === '') return 0
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
  const n = Date.parse(value)
  return Number.isNaN(n) ? 0 : n
}

function fmtMsgTime(value) {
  const t = msgTime(value)
  if (!t) return ''
  return new Date(t).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

function fmtDayLabel(value) {
  const t = msgTime(value)
  if (!t) return ''
  const d = new Date(t)
  const today = new Date()
  const yest = new Date()
  yest.setDate(today.getDate() - 1)
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, today)) return 'Сегодня'
  if (sameDay(d, yest)) return 'Вчера'
  const opts = { day: 'numeric', month: 'long' }
  if (d.getFullYear() !== today.getFullYear()) opts.year = 'numeric'
  return d.toLocaleDateString('ru', opts)
}

function isNewDay(idx) {
  const list = thread.value
  const cur = msgTime(list[idx]?.createdAt)
  if (!cur) return false
  if (idx === 0) return true
  const prev = msgTime(list[idx - 1]?.createdAt)
  if (!prev) return true
  const a = new Date(cur)
  const b = new Date(prev)
  return a.getFullYear() !== b.getFullYear() || a.getMonth() !== b.getMonth() || a.getDate() !== b.getDate()
}

const thread = computed(() => {
  const list = (messages.value || []).slice().sort((a, b) => msgTime(a.createdAt) - msgTime(b.createdAt))
  const comment = String(props.claimComment || '').trim()
  if (!comment) return list
  const sample = comment.slice(0, 40)
  const already = list.some(m => !isSeller(m) && !isSystem(m) && String(m.text || '').includes(sample))
  if (already) return list
  return [{ text: comment, senderType: 'client', fromClaim: true, createdAt: 0 }, ...list]
})

function isSeller(msg) {
  const t = String(msg.senderType || msg.sender || '').toLowerCase()
  return ['seller', 'partner', 'merchant', 'courier', 'client_seller'].includes(t) || msg.isMine === true
}
function isSystem(msg) {
  const t = String(msg.senderType || msg.sender || '').toLowerCase()
  return ['system', 'wb', 'market', 'chatbot'].includes(t)
}
function attachmentsOf(msg) {
  if (Array.isArray(msg.attachments)) return msg.attachments.filter(a => a?.url)
  return (Array.isArray(msg.images) ? msg.images : []).map(url => ({ type: 'image', url }))
}
function msgImages(msg) {
  return attachmentsOf(msg).filter(a => a.type === 'image' || (!a.type && a.url))
}
function msgVideos(msg) {
  return attachmentsOf(msg).filter(a => a.type === 'video')
}
function msgDocuments(msg) {
  return attachmentsOf(msg).filter(a => a.type && a.type !== 'image' && a.type !== 'video')
}

function scrollBottom() {
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function autoGrow() {
  const el = draftEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 96) + 'px'
}

function stripGreeting(value) {
  return String(value || '')
    .replace(/^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*/i, '')
    .trim()
}

function polish(body) {
  const text = stripGreeting(body)
  if (!text) return ''
  const parts = []
  const greet = String(greeting.value || '').trim()
  const recentlyGreeted = (messages.value || []).some(msg => {
    if (!isSeller(msg)) return false
    if (!/^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)/i.test(String(msg.text || ''))) return false
    const t = new Date(msg.createdAt).getTime()
    return !Number.isNaN(t) && (Date.now() - t) < 12 * 60 * 60 * 1000
  })
  if (greet && !recentlyGreeted) parts.push(greet)
  parts.push(text)
  let result = parts.join('\n\n').trim()
  const sign = String(signature.value || '').trim()
  if (sign && text.length > 40 && !result.includes(sign)) result = `${result}\n\n${sign}`
  return result
}

function onTheme(group) {
  if ((group?.templates || []).length === 1) {
    applyTemplate(group.templates[0])
    return
  }
  focusTheme.value = group.theme_key
  templatesOpen.value = true
}

function onCompactTheme(group) {
  const picked = pickThemeTemplate(group, String(fulfillment.value?.code || '').toUpperCase(), {
    nekomplektBranch: nekomplektBranch.value,
  })
  if (picked) {
    applyTemplate(picked)
    return
  }
  onTheme(group)
}

async function applyTemplate(template) {
  templatesOpen.value = false
  focusTheme.value = ''
  const raw = String(template?.body || '')
  if (template?.needs_form_url || raw.includes('{form_url}')) {
    try {
      const r = await apiFetch(`${BOT}/form-link`, {
        method: 'POST',
        body: JSON.stringify({ chat_id: props.chatId, mp: props.mp, body: raw }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || d.error || 'Не удалось создать ссылку')
      draft.value = polish(d.reply_text || '')
      hint.value = 'Ссылка на форму вставлена'
    } catch (e) {
      sendError.value = e.message || 'Ошибка ссылки'
    }
    nextTick(autoGrow)
    return
  }
  draft.value = polish(raw)
  hint.value = 'Шаблон в поле — можно править и отправить'
  nextTick(autoGrow)
}

function insertTemplate() {
  const suggested = String(suggest.value?.reply_text || '').replace(/^\s+/, '')
  if (suggested) {
    draft.value = suggested
    hint.value = 'Шаблон подставлен'
    nextTick(autoGrow)
    return
  }
  const theme = suggest.value?.theme || suggestMiss.value?.theme || ''
  const group = theme ? templateGroups.value.find(g => g.theme_key === theme) : null
  if (group) {
    onCompactTheme(group)
    return
  }
  if (!templateGroups.value.length) {
    hint.value = 'Нет шаблона — нажмите звезду, чтобы сгенерировать'
    return
  }
  templatesOpen.value = true
  focusTheme.value = ''
}

function clearSuggest() {
  suggest.value = null
  suggestMiss.value = null
  draft.value = ''
  nextTick(autoGrow)
}

function onFilesSelected(event) {
  const selected = Array.from(event?.target?.files || [])
  const existing = new Set(attachments.value.map(item => item.id))
  for (const file of selected) {
    if (validateChatAttachment(file)) continue
    const id = chatAttachmentKey(file)
    if (existing.has(id)) continue
    if (validateChatAttachmentAddition(attachments.value.map(a => a.file), file)) continue
    attachments.value.push({ id, file })
  }
  if (fileInput.value) fileInput.value.value = ''
}

function removeAttachment(id) {
  attachments.value = attachments.value.filter(a => a.id !== id)
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    const query = new URLSearchParams({ mp: props.mp || 'wb', chat_id: props.chatId })
    const r = await apiFetch(`${BOT}/chat-templates?${query}`)
    const d = await r.json().catch(() => ({}))
    templates.value = d.templates || []
    greeting.value = String(d.greeting_text || '').trim()
    signature.value = String(d.signature || '').trim()
    nekomplektBranch.value = String(d.nekomplekt_branch || '').trim()
  } catch {
    templates.value = []
  } finally {
    templatesLoading.value = false
  }
}

async function loadSuggest() {
  suggestLoading.value = true
  try {
    const r = await apiFetch(`${BOT}/suggest/${encodeURIComponent(props.chatId)}?mp=${encodeURIComponent(props.mp || 'wb')}`)
    const d = await r.json().catch(() => ({}))
    if (d?.skip) {
      suggest.value = null
      suggestMiss.value = null
      return
    }
    if (d?.ok && d.reply_text) {
      suggest.value = d
      suggestMiss.value = null
      if (!draft.value.trim()) {
        draft.value = String(d.reply_text || '').replace(/^\s+/, '')
        nextTick(autoGrow)
      }
    } else {
      suggest.value = null
      suggestMiss.value = { reason: d?.reason || '', need_human: !!d?.need_human, theme: d?.theme }
    }
  } catch {
    suggestMiss.value = { reason: 'Ошибка подбора шаблона' }
  } finally {
    suggestLoading.value = false
  }
}

async function generateAi() {
  if (generating.value || !props.chatId) return
  generating.value = true
  sendError.value = ''
  try {
    const r = await apiFetch(
      `${BOT}/suggest/${encodeURIComponent(props.chatId)}/generate?mp=${encodeURIComponent(props.mp || 'wb')}`,
      { method: 'POST' },
    )
    const d = await r.json().catch(() => ({}))
    if (d?.ok && d.reply_text) {
      suggest.value = d
      suggestMiss.value = null
      draft.value = String(d.reply_text || '').replace(/^\s+/, '')
      hint.value = 'ИИ предложил ответ'
      nextTick(autoGrow)
    } else {
      sendError.value = d?.reason || 'ИИ не смог предложить текст'
    }
  } catch {
    sendError.value = 'Ошибка генерации'
  } finally {
    generating.value = false
  }
}

async function load(silent = false) {
  if (!props.chatId) return
  if (!silent) loading.value = true
  error.value = ''
  try {
    const [msgRes, cardRes] = await Promise.all([
      apiFetch(`/api/v1/cx-responses/chat-messages/${props.mp}/${encodeURIComponent(props.chatId)}`),
      apiFetch(`/api/v1/cx-responses/chat-card/${props.mp}/${encodeURIComponent(props.chatId)}`).catch(() => null),
    ])
    const data = await msgRes.json().catch(() => ({}))
    if (!msgRes.ok) throw new Error(data.detail || 'Не удалось загрузить чат')
    messages.value = Array.isArray(data.messages) ? data.messages : []
    if (data.fulfillment) fulfillment.value = data.fulfillment
    if (cardRes && cardRes.ok) {
      const card = await cardRes.json().catch(() => ({}))
      contact.value = card.contact || ''
      waiting.value = !!card.needs_answer
    }
    if (!silent) scrollBottom()
  } catch (e) {
    if (!silent) error.value = e.message || 'Не удалось загрузить чат'
  } finally {
    loading.value = false
  }
}

async function send() {
  const txt = draft.value.trim()
  if (!canSend.value) return
  sending.value = true
  sendError.value = ''
  hint.value = ''
  try {
    let r
    if (attachments.value.length) {
      const body = new FormData()
      body.append('text', txt)
      attachments.value.forEach(entry => body.append('files', entry.file, entry.file.name))
      r = await apiFetch(
        `/api/v1/cx-responses/${props.mp}/chats/${encodeURIComponent(props.chatId)}/message-with-files`,
        { method: 'POST', body },
      )
    } else {
      r = await apiFetch(
        `/api/v1/cx-responses/${props.mp}/chats/${encodeURIComponent(props.chatId)}/message`,
        { method: 'POST', body: JSON.stringify({ text: txt }) },
      )
    }
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.detail || d.message || 'Не отправилось')
    draft.value = ''
    attachments.value = []
    suggest.value = null
    suggestMiss.value = null
    messages.value = [...messages.value, { text: txt, senderType: 'seller', createdAt: Date.now() }]
    waiting.value = false
    scrollBottom()
    emit('changed')
    setTimeout(() => load(true), 2000)
  } catch (e) {
    sendError.value = e.message || 'Не отправилось'
  } finally {
    sending.value = false
  }
}

async function boot() {
  clearInterval(pollTimer)
  await load()
  await Promise.all([loadTemplates(), loadSuggest()])
  pollTimer = setInterval(() => load(true), 8000)
}

watch(() => [props.mp, props.chatId], boot)
onMounted(boot)
onUnmounted(() => {
  clearInterval(pollTimer)
})
</script>
