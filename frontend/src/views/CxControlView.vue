<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { apiFetch } from '../api'
import {
  RefreshCw, AlertTriangle, Clock, Inbox, CheckCircle2,
  ChevronDown, Plug, TrendingUp, TrendingDown, Minus,
  BarChart2, ListChecks, Award, Bell, Users, Circle, MessageSquare
} from 'lucide-vue-next'

const tab = ref('queues')

// ── Очереди ──────────────────────────────────────────────────────────────────
const qData    = ref(null)
const qLoading = ref(true)
const qError   = ref('')

// ── Нагрузка ─────────────────────────────────────────────────────────────────
const wData    = ref(null)
const wLoading = ref(true)
const wError   = ref('')
const wDays    = ref(30)

// ── Сотрудники ───────────────────────────────────────────────────────────────
const staffData    = ref(null)
const staffLoading = ref(true)
let staffTimer     = null

async function loadStaff () {
  try {
    const r = await apiFetch('/api/v1/cx-control/staff')
    staffData.value = await r.json()
  } catch { staffData.value = null }
  finally { staffLoading.value = false }
}

// ── Качество ─────────────────────────────────────────────────────────────────
const qualData    = ref(null)
const qualLoading = ref(true)
const qualError   = ref('')
const qualDays    = ref(30)

// ── Алерты ───────────────────────────────────────────────────────────────────
const alertData   = ref(null)
const testSending = ref(false)
const testResult  = ref('')

async function sendTestAlert () {
  testSending.value = true
  testResult.value = ''
  try {
    const r = await apiFetch('/api/v1/cx-control/alerts/test', { method: 'POST' })
    const d = await r.json()
    testResult.value = d.sent ? '✅ Тест-сообщение отправлено в Telegram' : '⚠️ Не отправлено — проверьте TG_ALERT_BOT_TOKEN и TG_ALERT_CHAT_ID в .env'
  } catch {
    testResult.value = '❌ Ошибка запроса'
  } finally {
    testSending.value = false
  }
}

const updatedAt = ref(null)
const tick      = ref(0)
const expanded  = ref({})
const qDays     = ref(1)
let pollTimer = null
let tickTimer = null

const platformImg = { wb: '/icons/wb.png', ym: '/icons/ym.png', ozon: '/icons/ozon.png' }

async function loadQueues () {
  try { qError.value = ''
    const param = qDays.value ? `?days=${qDays.value}` : ''
    const r = await apiFetch(`/api/v1/cx-control/queues${param}`)
    qData.value = await r.json()
    updatedAt.value = Date.now()
  } catch (e) { qError.value = e.message || 'Ошибка загрузки' }
  finally { qLoading.value = false }
}
async function loadWorkload () {
  try { wError.value = ''
    const r = await apiFetch(`/api/v1/cx-control/workload?days=${wDays.value}`)
    wData.value = await r.json()
  } catch (e) { wError.value = e.message || 'Ошибка загрузки' }
  finally { wLoading.value = false }
}
async function loadQuality () {
  try { qualError.value = ''
    const r = await apiFetch(`/api/v1/cx-control/quality?days=${qualDays.value}`)
    qualData.value = await r.json()
  } catch (e) { qualError.value = e.message || 'Ошибка загрузки' }
  finally { qualLoading.value = false }
}

const repliesData = ref(null)
const repliesLoading = ref(true)
const repliesError = ref('')
const repliesDays = ref(30)

async function loadReplies () {
  try {
    repliesError.value = ''
    const r = await apiFetch(`/api/v1/cx-control/replies?days=${repliesDays.value}`)
    repliesData.value = await r.json()
  } catch (e) { repliesError.value = e.message || 'Ошибка загрузки' }
  finally { repliesLoading.value = false }
}
async function loadAlerts () {
  try {
    const r = await apiFetch('/api/v1/cx-control/alerts/status')
    alertData.value = await r.json()
  } catch { alertData.value = null }
}

async function reload () {
  qLoading.value = true; wLoading.value = true; qualLoading.value = true; staffLoading.value = true; repliesLoading.value = true
  await Promise.all([loadQueues(), loadWorkload(), loadQuality(), loadAlerts(), loadStaff(), loadReplies()])
}

onMounted(() => {
  loadQueues(); loadWorkload(); loadQuality(); loadAlerts(); loadStaff(); loadReplies(); loadSlaSettings()
  pollTimer = setInterval(reload, 60000)
  staffTimer = setInterval(loadStaff, 2000)
  tickTimer = setInterval(() => tick.value++, 1000)
})
onUnmounted(() => {
  clearInterval(pollTimer)
  clearInterval(staffTimer)
  clearInterval(tickTimer)
})

// ── Helpers очередей ─────────────────────────────────────────────────────────
const agoText = computed(() => {
  tick.value
  if (!updatedAt.value) return ''
  const s = Math.floor((Date.now() - updatedAt.value) / 1000)
  if (s < 5)  return 'обновлено только что'
  if (s < 60) return `обновлено ${s} сек назад`
  return `обновлено ${Math.floor(s / 60)} мин назад`
})

const summary  = computed(() => qData.value?.summary ?? {})
const queues   = computed(() => qData.value?.queues ?? [])
const noText   = computed(() => qData.value?.no_text ?? [])
const intake   = computed(() => qData.value?.intake ?? [])
const gaps     = computed(() => qData.value?.gaps ?? [])
const moderation        = computed(() => qData.value?.moderation ?? [])
const moderationSummary = computed(() => qData.value?.moderation_summary ?? {})

// ── SLA настройки ─────────────────────────────────────────────────────────────
const slaSettings  = ref([])
const slaEditing   = ref(false)
const slaExpanded  = ref(false)
const slaSaving    = ref(false)
const slaUnit      = ref({})   // {id: 'min'|'h'|'d'}
const slaVal       = ref({})   // {id: число в выбранной единице}
const UNIT_MIN = { min: 1, h: 60, d: 1440 }
const UNIT_LABEL = { min: 'мин', h: 'ч', d: 'дн' }

function bestUnit (min) {
  if (min % 1440 === 0) return 'd'
  if (min % 60 === 0) return 'h'
  return 'min'
}

async function loadSlaSettings () {
  try {
    const r = await apiFetch('/api/v1/cx-control/sla-settings')
    const d = await r.json()
    slaSettings.value = d.settings || []
    for (const s of d.settings) {
      const u = bestUnit(s.sla_min)
      slaUnit.value[s.id] = u
      slaVal.value[s.id]  = s.sla_min / UNIT_MIN[u]
    }
  } catch {}
}

function slaMinFor (id) {
  return Math.round((slaVal.value[id] || 0) * UNIT_MIN[slaUnit.value[id] || 'min'])
}

async function saveSlaSettings () {
  slaSaving.value = true
  try {
    const payload = Object.fromEntries(slaSettings.value.map(s => [s.id, slaMinFor(s.id)]))
    await apiFetch('/api/v1/cx-control/sla-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    slaEditing.value = false
    await Promise.all([loadSlaSettings(), loadQueues()])
  } finally { slaSaving.value = false }
}

function fmtSlaEdit (min) {
  if (min < 60) return `${min} мин`
  if (min % 1440 === 0) return `${min/1440} дн`
  if (min % 60 === 0) return `${min/60} ч`
  return `${min} мин`
}

// TG привязка в карточках сотрудников
const editingTg = ref({})
const savingTg  = ref({})
async function saveTg (userId) {
  savingTg.value[userId] = true
  try {
    await apiFetch(`/api/v1/cx-control/staff/${userId}/tg`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tg_chat_id: editingTg.value[userId] }),
    })
    await loadStaff()
    editingTg.value[userId] = undefined
  } finally { savingTg.value[userId] = false }
}
const activeAlerts = computed(() => alertData.value?.alerts ?? [])

function fmtAge (min) {
  if (!min || min <= 0) return '—'
  if (min < 60) return `${min} мин`
  const h = Math.floor(min / 60), m = min % 60
  if (h < 24) return m ? `${h} ч ${m} мин` : `${h} ч`
  const d = Math.floor(h / 24), hh = h % 24
  return hh ? `${d} дн ${hh} ч` : `${d} дн`
}
function fmtSla (min) {
  if (min < 60) return `${min} мин`
  const h = min / 60
  return h >= 24 ? `${h / 24} дн` : `${h} ч`
}

const STATUS = {
  red:   { ring: 'ring-rose-200',    badge: 'bg-rose-50 text-rose-700' },
  amber: { ring: 'ring-amber-200',   badge: 'bg-amber-50 text-amber-700' },
  green: { ring: 'ring-emerald-200', badge: 'bg-emerald-50 text-emerald-700' },
}
const statusLabel = { red: 'Есть просрочка', amber: 'На грани', green: 'В норме' }
function pct (q, key) { const t = q.green+q.amber+q.red; return t ? Math.round(q[key]/t*100) : 0 }
function toggle (id) { expanded.value[id] = !expanded.value[id] }
const complianceColor = computed(() => { const c=summary.value.compliance??100; return c>=95?'text-emerald-600':c>=80?'text-amber-600':'text-rose-600' })

// ── Helpers нагрузки ─────────────────────────────────────────────────────────
const channels      = computed(() => wData.value?.channels ?? [])
const planTotal     = computed(() => wData.value?.plan_total_min ?? 310)
const channelsByMp  = computed(() => {
  const g = {}
  for (const ch of channels.value) {
    if (!g[ch.mp]) g[ch.mp] = { mp: ch.mp, mp_label: ch.mp_label, items: [] }
    g[ch.mp].items.push(ch)
  }
  return Object.values(g)
})
function sparkline (daily) {
  if (!daily) return ''
  const vals = Object.values(daily).slice(-14)
  const max = Math.max(...vals, 1)
  const W=70, H=24, gap=2, bw=(W-gap*(vals.length-1))/vals.length
  const bars = vals.map((v,i)=>{const bh=Math.max(2,Math.round(v/max*H)); return `<rect x="${Math.round(i*(bw+gap))}" y="${H-bh}" width="${Math.floor(bw)}" height="${bh}" rx="1"/>`}).join('')
  return `<svg viewBox="0 0 ${W} ${H}" class="inline-block text-emerald-500 fill-current">${bars}</svg>`
}
function loadColor (idx) { if(idx===null)return'text-slate-400'; if(idx>1.15)return'text-rose-600'; if(idx>1.05)return'text-amber-600'; if(idx<0.85)return'text-blue-500'; return'text-emerald-600' }
function loadDir (idx) { if(idx===null)return'flat'; if(idx>1.05)return'up'; if(idx<0.95)return'down'; return'flat' }
function budgetBarW (min) { return Math.round(Math.min(min/planTotal.value*100,100))+'%' }
const MP_COLOR = { wb:'text-violet-700', ym:'text-amber-700', ozon:'text-sky-700', mm:'text-slate-600' }

// ── Helpers качества ─────────────────────────────────────────────────────────
const sentiment  = computed(() => qualData.value?.sentiment ?? {})
const tagging    = computed(() => qualData.value?.tagging ?? {})
const reshipment = computed(() => qualData.value?.reshipment ?? {})

function ratingColor (v) { return v >= 4.5 ? 'text-emerald-600' : v >= 4.0 ? 'text-amber-600' : 'text-rose-600' }
function tagColor (pct) { return pct >= 70 ? 'text-emerald-600' : pct >= 40 ? 'text-amber-600' : 'text-rose-600' }

function ratingBars (daily) {
  if (!daily) return []
  return Object.entries(daily).slice(-30).map(([d, v]) => ({
    date: d, avg: v.avg_r || 0, pos: v.positive || 0, neg: v.negative || 0, total: v.total || 0
  }))
}

function tagBars (daily) {
  if (!daily) return []
  return Object.entries(daily).slice(-30).map(([d, v]) => ({
    date: d, pct: v.pct || 0, tagged: v.tagged || 0, total: v.total || 0
  }))
}

const tagPeriods = computed(() => [
  { key: '7d', label: '7 дней' },
  { key: '30d', label: '30 дней' },
  { key: `${qualDays.value}d`, label: `${qualDays.value} дней` },
])

const reshipFunnel = computed(() => {
  const d = reshipment.value
  if (!d.total) return []
  return [
    { label: 'Подано заявок',     value: d.total,            color: 'bg-slate-200' },
    { label: 'Прогресс (отпр.)',  value: d.progressed,       color: 'bg-blue-400' },
    { label: 'Завершено',         value: d.completed,        color: 'bg-emerald-500' },
    { label: 'Отказано',          value: d.rejected,         color: 'bg-rose-400' },
    { label: 'Запрошен отзыв',    value: d.review_requested, color: 'bg-amber-400' },
  ]
})
</script>

<template>
  <div class="p-4 sm:p-6 max-w-7xl mx-auto">
    <!-- Шапка -->
    <div class="flex flex-wrap items-start justify-between gap-3 mb-5">
      <div>
        <h1 class="text-xl font-bold text-slate-800">Контроль отдела</h1>
        <p class="text-sm text-slate-500 mt-0.5">Только для администратора. Мониторинг работы менеджеров КС.</p>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs text-slate-400">{{ agoText }}</span>
        <button @click="reload"
          class="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors">
          <RefreshCw class="w-4 h-4" :class="(qLoading||wLoading||qualLoading)?'animate-spin':''" /> Обновить
        </button>
      </div>
    </div>

    <!-- Алерты-баннеры -->
    <div v-if="activeAlerts.length" class="mb-4 space-y-2">
      <div v-for="a in activeAlerts" :key="a.key"
        class="flex items-start gap-3 p-3 rounded-xl border text-sm"
        :class="a.severity==='high' ? 'bg-rose-50 border-rose-200 text-rose-800' : 'bg-amber-50 border-amber-200 text-amber-800'">
        <AlertTriangle class="w-4 h-4 mt-0.5 flex-shrink-0"/>
        <div>
          <span class="font-semibold">{{ a.label }}: </span>{{ a.message }}
        </div>
      </div>
    </div>

    <!-- Таб-переключатель -->
    <div class="flex gap-1 mb-6 p-1 bg-slate-100 rounded-xl w-fit">
      <button @click="tab='queues'"
        class="relative flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        :class="tab==='queues'?'bg-white text-slate-800 shadow-sm':'text-slate-500 hover:text-slate-700'">
        <ListChecks class="w-4 h-4"/> Очереди / SLA
        <span v-if="activeAlerts.length"
          class="absolute -top-1 -right-1 w-4 h-4 bg-rose-500 rounded-full text-white text-[10px] flex items-center justify-center">
          {{ activeAlerts.length }}
        </span>
      </button>
      <button @click="tab='workload'"
        class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        :class="tab==='workload'?'bg-white text-slate-800 shadow-sm':'text-slate-500 hover:text-slate-700'">
        <BarChart2 class="w-4 h-4"/> Нагрузка
      </button>
      <button @click="tab='quality'"
        class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        :class="tab==='quality'?'bg-white text-slate-800 shadow-sm':'text-slate-500 hover:text-slate-700'">
        <Award class="w-4 h-4"/> Качество
      </button>
      <button @click="tab='replies'"
        class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        :class="tab==='replies'?'bg-white text-slate-800 shadow-sm':'text-slate-500 hover:text-slate-700'">
        <MessageSquare class="w-4 h-4"/> Автоответы
      </button>
      <button @click="tab='staff'"
        class="relative flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        :class="tab==='staff'?'bg-white text-slate-800 shadow-sm':'text-slate-500 hover:text-slate-700'">
        <Users class="w-4 h-4"/> Сотрудники
        <span v-if="staffData?.staff?.some(s=>s.status==='online')"
          class="absolute -top-1 -right-1 w-2.5 h-2.5 bg-emerald-500 rounded-full"></span>
      </button>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА 1: Очереди                                                   -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <template v-if="tab==='queues'">
      <div v-if="qError" class="mb-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-sm border border-rose-100">{{ qError }}</div>
      <div v-if="qLoading && !qData" class="text-slate-400 text-sm py-20 text-center">Загрузка…</div>

      <template v-else-if="qData">
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="flex items-center gap-2 text-slate-400 text-xs font-medium uppercase tracking-wide"><Inbox class="w-4 h-4"/>Ждут ответа</div>
            <div class="text-3xl font-bold text-slate-800 mt-1">{{ summary.total_waiting }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="flex items-center gap-2 text-slate-400 text-xs font-medium uppercase tracking-wide"><AlertTriangle class="w-4 h-4"/>Просрочено</div>
            <div class="text-3xl font-bold mt-1" :class="summary.total_breached?'text-rose-600':'text-slate-800'">{{ summary.total_breached }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="flex items-center gap-2 text-slate-400 text-xs font-medium uppercase tracking-wide"><CheckCircle2 class="w-4 h-4"/>Соблюдение SLA</div>
            <div class="text-3xl font-bold mt-1" :class="complianceColor">{{ summary.compliance }}%</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="flex items-center gap-2 text-slate-400 text-xs font-medium uppercase tracking-wide"><Clock class="w-4 h-4"/>Самое старое</div>
            <div class="text-3xl font-bold text-slate-800 mt-1">{{ fmtAge(summary.oldest_min) }}</div>
            <div v-if="summary.worst" class="text-xs text-slate-400 mt-1 truncate">{{ summary.worst }}</div>
          </div>
        </div>

        <!-- Фильтр по периоду -->
        <div class="flex items-center gap-2 mb-5">
          <span class="text-sm text-slate-500">Обращения за:</span>
          <div class="flex gap-1">
            <button v-for="d in [1,7,14,30,60]" :key="d" @click="qDays=d;loadQueues()"
              class="px-3 py-1 rounded-lg text-sm transition-colors"
              :class="qDays===d?'bg-emerald-600 text-white':'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'">{{ d }} дн</button>
            <button @click="qDays=null;loadQueues()"
              class="px-3 py-1 rounded-lg text-sm transition-colors"
              :class="!qDays?'bg-emerald-600 text-white':'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'">Все</button>
          </div>
        </div>

        <!-- SLA настройки (сворачиваемая панель) -->
        <div class="bg-white rounded-xl border border-slate-100 shadow-sm mb-6 overflow-hidden">
          <button @click="slaExpanded = !slaExpanded"
            class="w-full flex items-center justify-between p-4 hover:bg-slate-50 transition-colors">
            <div class="flex items-center gap-2">
              <ChevronDown class="w-4 h-4 text-slate-400 transition-transform" :class="slaExpanded?'rotate-180':''"/>
              <span class="text-sm font-semibold text-slate-700">Лимиты SLA</span>
              <span class="text-xs text-slate-400">— максимальное время ответа клиенту</span>
            </div>
            <span class="text-xs text-slate-400">{{ slaSettings.length }} параметров</span>
          </button>

          <div v-if="slaExpanded" class="p-4 pt-0">
            <div class="flex items-center justify-end mb-3">
              <button v-if="!slaEditing" @click="slaEditing=true"
                class="text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors">
                Изменить
              </button>
              <div v-else class="flex gap-2">
                <button @click="saveSlaSettings" :disabled="slaSaving"
                  class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors">
                  {{ slaSaving ? 'Сохраняю…' : 'Сохранить' }}
                </button>
                <button @click="slaEditing=false"
                  class="text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50">
                  Отмена
                </button>
              </div>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
              <div v-for="s in slaSettings" :key="s.id"
                class="p-2 bg-slate-50 rounded-lg flex items-center justify-between gap-2">
                <span class="text-xs text-slate-500 leading-tight truncate" :title="s.label">{{ s.label }}</span>
                <div v-if="!slaEditing" class="text-sm font-semibold text-slate-800 whitespace-nowrap">{{ fmtSlaEdit(s.sla_min) }}</div>
                <div v-else class="flex items-center gap-1 flex-shrink-0">
                  <input type="number" v-model.number="slaVal[s.id]" min="1" max="999"
                    class="w-12 text-sm border border-slate-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-emerald-400"/>
                  <select v-model="slaUnit[s.id]"
                    class="text-xs border border-slate-200 rounded px-1 py-0.5 bg-white focus:outline-none focus:ring-1 focus:ring-emerald-400">
                    <option value="min">мин</option>
                    <option value="h">ч</option>
                    <option value="d">дн</option>
                  </select>
                </div>
              </div>
            </div>
            <p class="text-xs text-slate-400 mt-3">Жёлтый — 75% от лимита. Красный — превышение.</p>
          </div>
        </div>

        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Очереди под контролем SLA <span class="normal-case font-normal text-slate-400">(только с текстом)</span></h2>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 mb-8">
          <div v-for="q in queues" :key="q.id"
            class="bg-white rounded-xl border border-slate-100 shadow-sm ring-1 p-4 flex flex-col" :class="STATUS[q.status].ring">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 min-w-0">
                <img :src="platformImg[q.mp]" class="w-5 h-5 rounded object-cover flex-shrink-0"/>
                <div class="min-w-0">
                  <div class="text-sm font-semibold text-slate-800 truncate">{{ q.title }}</div>
                  <div class="text-xs text-slate-400">{{ q.mp_label }}</div>
                </div>
              </div>
              <span class="text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap" :class="STATUS[q.status].badge">{{ statusLabel[q.status] }}</span>
            </div>
            <div class="flex items-end justify-between mt-3">
              <div><div class="text-3xl font-bold text-slate-800 leading-none">{{ q.waiting }}</div><div class="text-xs text-slate-400 mt-1">ждут ответа</div></div>
              <div class="text-right"><div class="text-lg font-bold" :class="q.red?'text-rose-600':'text-slate-300'">{{ q.red }}</div><div class="text-xs text-slate-400">просрочено</div></div>
            </div>
            <div v-if="q.closing_count" class="mt-2 text-xs text-slate-400 flex items-center gap-1">
              <CheckCircle2 class="w-3.5 h-3.5 text-emerald-400"/>
              + {{ q.closing_count }} с благодарностью — ответ не требуется
            </div>
            <div v-if="q.waiting" class="mt-3 h-2 rounded-full overflow-hidden bg-slate-100 flex">
              <div class="bg-emerald-500 h-full" :style="{width:pct(q,'green')+'%'}"></div>
              <div class="bg-amber-400 h-full" :style="{width:pct(q,'amber')+'%'}"></div>
              <div class="bg-rose-500 h-full" :style="{width:pct(q,'red')+'%'}"></div>
            </div>
            <div class="grid grid-cols-4 gap-2 mt-3 text-center">
              <div><div class="text-xs text-slate-400">лимит</div><div class="text-sm font-medium text-slate-700">{{ fmtSla(q.sla_min) }}</div></div>
              <div><div class="text-xs text-slate-400">старое</div><div class="text-sm font-medium" :class="q.oldest_min>q.sla_min?'text-rose-600':'text-slate-700'">{{ fmtAge(q.oldest_min) }}</div></div>
              <div><div class="text-xs text-slate-400">ср. 30 дн</div><div class="text-sm font-medium" :class="q.avg_age_30d>q.sla_min?'text-rose-600':q.avg_age_30d>q.sla_min*0.75?'text-amber-600':'text-emerald-600'">{{ fmtAge(q.avg_age_30d) }}</div></div>
              <div><div class="text-xs text-slate-400">за сутки</div><div class="text-sm font-medium text-slate-700">+{{ q.intake_24h }}</div></div>
            </div>
            <button v-if="q.samples&&q.samples.length" @click="toggle(q.id)"
              class="mt-3 flex items-center justify-center gap-1 text-xs text-slate-500 hover:text-slate-700 pt-2 border-t border-slate-100">
              {{ expanded[q.id]?'Скрыть':'Показать старейшие' }}
              <ChevronDown class="w-3.5 h-3.5 transition-transform" :class="expanded[q.id]?'rotate-180':''"/>
            </button>
            <div v-if="expanded[q.id]" class="mt-2 space-y-2">
              <div v-for="s in q.samples" :key="s.id" class="text-xs bg-slate-50 rounded-lg p-2">
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium text-slate-600 truncate">{{ s.sku }}</span>
                  <span class="flex items-center gap-2 flex-shrink-0">
                    <span v-if="q.type==='review'&&s.valuation" class="text-amber-500">{{ s.valuation }}★</span>
                    <span :class="s.age_min>q.sla_min?'text-rose-600 font-medium':'text-slate-400'">{{ fmtAge(s.age_min) }}</span>
                  </span>
                </div>
                <div v-if="s.snippet" class="text-slate-500 mt-1 line-clamp-2">{{ s.snippet }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Отзывы без текста -->
        <div v-if="noText.length" class="mb-8">
          <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">
            Отзывы без текста
            <span class="normal-case font-normal text-slate-400 ml-1">— SLA не применяется, ответ необязателен</span>
          </h2>
          <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="n in noText" :key="n.id"
              class="bg-white rounded-xl border border-slate-100 shadow-sm p-4 border-l-4 border-l-slate-200">
              <div class="flex items-center gap-2 mb-3">
                <img :src="platformImg[n.mp]" class="w-5 h-5 rounded object-cover"/>
                <span class="text-sm font-semibold text-slate-700">{{ n.title }}</span>
              </div>
              <div class="flex gap-6 text-center">
                <div><div class="text-2xl font-bold text-slate-700">{{ n.total }}</div><div class="text-xs text-slate-400 mt-1">всего</div></div>
                <div><div class="text-2xl font-bold text-rose-500">{{ n.negative }}</div><div class="text-xs text-slate-400 mt-1">негатив</div></div>
                <div><div class="text-2xl font-bold text-emerald-600">{{ n.positive }}</div><div class="text-xs text-slate-400 mt-1">позитив</div></div>
              </div>
            </div>
          </div>
        </div>

        <!-- Модерация тегов (ИИ-разметка, требует проверки менеджером) -->
        <div v-if="moderation.length" class="mb-8">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider">
              Модерация тегов
              <span class="normal-case font-normal text-slate-400 ml-1">— ИИ-разметка ждёт проверки/коррекции</span>
            </h2>
            <span v-if="moderationSummary.total_waiting" class="text-xs text-slate-500">
              всего <span class="font-semibold text-slate-700">{{ moderationSummary.total_waiting }}</span>,
              просрочено <span class="font-semibold" :class="moderationSummary.total_breached?'text-rose-600':'text-slate-700'">{{ moderationSummary.total_breached }}</span>
            </span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="m in moderation" :key="m.id"
              class="bg-white rounded-xl border border-slate-100 shadow-sm ring-1 p-4 flex flex-col" :class="STATUS[m.status].ring">
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                  <img :src="platformImg[m.mp]" class="w-5 h-5 rounded object-cover flex-shrink-0"/>
                  <div class="min-w-0">
                    <div class="text-sm font-semibold text-slate-800 truncate">{{ m.title }}</div>
                    <div class="text-xs text-slate-400">{{ m.mp_label }}</div>
                  </div>
                </div>
                <span class="text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap" :class="STATUS[m.status].badge">{{ statusLabel[m.status] }}</span>
              </div>
              <div class="flex items-end justify-between mt-3">
                <div><div class="text-3xl font-bold text-slate-800 leading-none">{{ m.waiting }}</div><div class="text-xs text-slate-400 mt-1">в очереди</div></div>
                <div class="text-right"><div class="text-lg font-bold" :class="m.red?'text-rose-600':'text-slate-300'">{{ m.red }}</div><div class="text-xs text-slate-400">просрочено</div></div>
              </div>
              <div v-if="m.waiting" class="mt-3 h-2 rounded-full overflow-hidden bg-slate-100 flex">
                <div class="bg-emerald-500 h-full" :style="{width:pct(m,'green')+'%'}"></div>
                <div class="bg-amber-400 h-full" :style="{width:pct(m,'amber')+'%'}"></div>
                <div class="bg-rose-500 h-full" :style="{width:pct(m,'red')+'%'}"></div>
              </div>
              <div class="grid grid-cols-4 gap-2 mt-3 text-center">
                <div><div class="text-xs text-slate-400">лимит</div><div class="text-sm font-medium text-slate-700">{{ fmtSla(m.sla_min) }}</div></div>
                <div><div class="text-xs text-slate-400">старое</div><div class="text-sm font-medium" :class="m.oldest_min>m.sla_min?'text-rose-600':'text-slate-700'">{{ fmtAge(m.oldest_min) }}</div></div>
                <div><div class="text-xs text-slate-400">ср. 30 дн</div><div class="text-sm font-medium" :class="m.avg_age_30d>m.sla_min?'text-rose-600':m.avg_age_30d>m.sla_min*0.75?'text-amber-600':'text-emerald-600'">{{ fmtAge(m.avg_age_30d) }}</div></div>
                <div><div class="text-xs text-slate-400">за сутки</div><div class="text-sm font-medium text-slate-700">+{{ m.intake_24h }}</div></div>
              </div>
              <button v-if="m.samples&&m.samples.length" @click="toggle(m.id)"
                class="mt-3 flex items-center justify-center gap-1 text-xs text-slate-500 hover:text-slate-700 pt-2 border-t border-slate-100">
                {{ expanded[m.id]?'Скрыть':'Показать старейшие' }}
                <ChevronDown class="w-3.5 h-3.5 transition-transform" :class="expanded[m.id]?'rotate-180':''"/>
              </button>
              <div v-if="expanded[m.id]" class="mt-2 space-y-2">
                <div v-for="s in m.samples" :key="s.id" class="text-xs bg-slate-50 rounded-lg p-2">
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-medium text-slate-600 truncate">{{ s.sku }}</span>
                    <span class="flex items-center gap-2 flex-shrink-0">
                      <span v-if="m.type==='review'&&s.valuation" class="text-amber-500">{{ s.valuation }}★</span>
                      <span :class="s.age_min>m.sla_min?'text-rose-600 font-medium':'text-slate-400'">{{ fmtAge(s.age_min) }}</span>
                    </span>
                  </div>
                  <div v-if="s.snippet" class="text-slate-500 mt-1 line-clamp-2">{{ s.snippet }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="intake.length" class="mb-8">
          <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Поток без статуса ответа в БД</h2>
          <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="i in intake" :key="i.id" class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
              <div class="flex items-center gap-2"><img :src="platformImg[i.mp]" class="w-5 h-5 rounded object-cover"/><div class="text-sm font-semibold text-slate-800">{{ i.title }}</div></div>
              <div class="flex items-baseline gap-4 mt-3">
                <div><span class="text-2xl font-bold text-slate-800">+{{ i.intake_24h }}</span><span class="text-xs text-slate-400 ml-1">за сутки</span></div>
                <div><span class="text-lg font-semibold text-slate-500">+{{ i.intake_7d }}</span><span class="text-xs text-slate-400 ml-1">за 7 дней</span></div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="gaps.length">
          <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2"><Plug class="w-4 h-4"/>Нет данных — нужна интеграция</h2>
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm divide-y divide-slate-100">
            <div v-for="(g,i) in gaps" :key="i" class="flex items-start gap-3 p-3">
              <img :src="platformImg[g.mp]" class="w-5 h-5 rounded object-cover mt-0.5 flex-shrink-0"/>
              <div><div class="text-sm font-medium text-slate-700">{{ g.mp_label }} · {{ g.title_ru }}</div><div class="text-xs text-slate-400">{{ g.note_ru }}</div></div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА 2: Нагрузка                                                  -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <template v-else-if="tab==='workload'">
      <div v-if="wError" class="mb-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-sm border border-rose-100">{{ wError }}</div>
      <div v-if="wLoading && !wData" class="text-slate-400 text-sm py-20 text-center">Загрузка…</div>
      <template v-else-if="wData">
        <div class="flex items-center gap-2 mb-6">
          <span class="text-sm text-slate-500">Период:</span>
          <div class="flex gap-1">
            <button v-for="d in [7,14,30,60]" :key="d" @click="wDays=d;loadWorkload()"
              class="px-3 py-1 rounded-lg text-sm transition-colors"
              :class="wDays===d?'bg-emerald-600 text-white':'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'">{{ d }} дн</button>
          </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="text-xs font-bold text-slate-400 uppercase tracking-wide mb-3">Плановый бюджет рабочего дня</div>
            <div class="text-4xl font-bold text-slate-800">{{ planTotal }} мин</div>
            <div class="text-sm text-slate-500 mt-1">= {{ Math.round(planTotal/60*10)/10 }} ч · из «Время работы Лилии»</div>
            <div class="mt-4 space-y-2">
              <div v-for="g in channelsByMp.filter(g=>g.items.some(i=>i.budget_min>0))" :key="g.mp">
                <div class="flex justify-between text-xs text-slate-500 mb-1">
                  <span :class="MP_COLOR[g.mp]||'text-slate-600'" class="font-medium">{{ g.mp_label||g.mp.toUpperCase() }}</span>
                  <span>{{ g.items.reduce((s,i)=>s+i.budget_min,0) }} мин</span>
                </div>
                <div class="h-2 bg-slate-100 rounded-full overflow-hidden"><div class="h-full rounded-full bg-emerald-500" :style="{width:budgetBarW(g.items.reduce((s,i)=>s+i.budget_min,0))}"></div></div>
              </div>
            </div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="text-xs font-bold text-slate-400 uppercase tracking-wide mb-3">Индекс нагрузки</div>
            <p class="text-sm text-slate-600 leading-relaxed">Ср. 7 дн ÷ ср. {{ wDays }} дн. Показывает изменение потока относительно базы.</p>
            <div class="mt-3 space-y-1.5 text-xs">
              <div class="flex items-center gap-2"><TrendingUp class="w-4 h-4 text-rose-500"/><span>&gt; 1.15 — значительно выше нормы</span></div>
              <div class="flex items-center gap-2"><TrendingUp class="w-4 h-4 text-amber-500"/><span>1.05–1.15 — немного выше</span></div>
              <div class="flex items-center gap-2"><Minus class="w-4 h-4 text-emerald-500"/><span>0.95–1.05 — норма</span></div>
              <div class="flex items-center gap-2"><TrendingDown class="w-4 h-4 text-blue-500"/><span>&lt; 0.95 — ниже нормы, спокойно</span></div>
            </div>
          </div>
        </div>
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Каналы: план vs факт</h2>
        <div class="bg-white rounded-xl border border-slate-100 shadow-sm overflow-hidden mb-6">
          <table class="w-full text-sm">
            <thead><tr class="border-b border-slate-100 text-xs text-slate-400 font-medium uppercase tracking-wide">
              <th class="text-left px-4 py-2.5">Канал</th>
              <th class="text-center px-3 py-2.5 hidden sm:table-cell">Бюджет</th>
              <th class="text-center px-3 py-2.5">Ср. 7 дн</th>
              <th class="text-center px-3 py-2.5 hidden md:table-cell">Ср. {{ wDays }} дн</th>
              <th class="text-center px-3 py-2.5">Индекс</th>
              <th class="text-center px-3 py-2.5 hidden lg:table-cell">Тренд</th>
            </tr></thead>
            <tbody class="divide-y divide-slate-50">
              <template v-for="g in channelsByMp" :key="g.mp">
                <tr class="bg-slate-50"><td colspan="6" class="px-4 py-1.5">
                  <div class="flex items-center gap-2">
                    <img v-if="platformImg[g.mp]" :src="platformImg[g.mp]" class="w-4 h-4 rounded object-cover"/>
                    <span :class="MP_COLOR[g.mp]||'text-slate-600'" class="text-xs font-bold uppercase tracking-wide">{{ g.mp_label||g.mp.toUpperCase() }}</span>
                  </div>
                </td></tr>
                <tr v-for="ch in g.items" :key="ch.id" class="hover:bg-slate-50 transition-colors">
                  <td class="px-4 py-2.5"><span class="text-slate-700">{{ ch.label }}</span><span v-if="!ch.has_data" class="ml-2 text-xs text-slate-400 italic">нет данных</span></td>
                  <td class="text-center px-3 py-2.5 text-slate-500 hidden sm:table-cell">{{ ch.budget_min>0?ch.budget_min+' мин':'—' }}</td>
                  <td class="text-center px-3 py-2.5 font-semibold text-slate-800">{{ ch.has_data?ch.avg_7d:'—' }}</td>
                  <td class="text-center px-3 py-2.5 text-slate-500 hidden md:table-cell">{{ ch.has_data?ch.avg_30d:'—' }}</td>
                  <td class="text-center px-3 py-2.5">
                    <div v-if="ch.load_index!==null" class="flex items-center justify-center gap-1 font-bold" :class="loadColor(ch.load_index)">
                      <TrendingUp v-if="loadDir(ch.load_index)==='up'" class="w-3.5 h-3.5"/>
                      <TrendingDown v-else-if="loadDir(ch.load_index)==='down'" class="w-3.5 h-3.5"/>
                      <Minus v-else class="w-3.5 h-3.5"/>
                      {{ ch.load_index }}×
                    </div>
                    <span v-else class="text-slate-300">—</span>
                  </td>
                  <td class="text-center px-3 py-2.5 hidden lg:table-cell">
                    <span v-if="ch.has_data" v-html="sparkline(ch.daily)"></span>
                    <span v-else class="text-slate-300">—</span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Суточный поток</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 mb-6">
          <div v-for="ch in channels.filter(c=>c.has_data)" :key="ch.id" class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="flex items-center gap-2 mb-1">
              <img v-if="platformImg[ch.mp]" :src="platformImg[ch.mp]" class="w-4 h-4 rounded object-cover"/>
              <span class="text-sm font-semibold text-slate-700">{{ ch.label }}</span>
            </div>
            <div class="mt-3 flex items-end gap-px h-12" v-if="ch.daily">
              <div v-for="(val,day) in Object.fromEntries(Object.entries(ch.daily).slice(-30))" :key="day"
                class="flex-1 rounded-t min-w-0 cursor-default transition-opacity hover:opacity-80"
                :class="val>0?'bg-emerald-500':'bg-slate-100'"
                :style="{height:Math.max(2,Math.round(val/Math.max(...Object.values(ch.daily||{}),1)*48))+'px'}"
                :title="`${day}: ${val}`"></div>
            </div>
            <div class="flex justify-between mt-2 text-xs text-slate-400">
              <span>{{ wData?.dates?.[0]||'' }}</span>
              <span class="font-medium text-slate-700">avg {{ ch.avg_7d }}/день (7 дн)</span>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА 3: Качество                                                  -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <template v-else-if="tab==='quality'">
      <div v-if="qualError" class="mb-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-sm border border-rose-100">{{ qualError }}</div>
      <div v-if="qualLoading && !qualData" class="text-slate-400 text-sm py-20 text-center">Загрузка…</div>

      <template v-else-if="qualData">
        <!-- Переключатель периода -->
        <div class="flex items-center gap-2 mb-6">
          <span class="text-sm text-slate-500">Период:</span>
          <div class="flex gap-1">
            <button v-for="d in [7,14,30,60]" :key="d" @click="qualDays=d;loadQuality()"
              class="px-3 py-1 rounded-lg text-sm transition-colors"
              :class="qualDays===d?'bg-emerald-600 text-white':'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'">{{ d }} дн</button>
          </div>
        </div>

        <!-- ── Тональность ──────────────────────────────────────────────────── -->
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Тональность отзывов</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <!-- WB -->
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="flex items-center gap-2 mb-3">
              <img src="/icons/wb.png" class="w-5 h-5 rounded object-cover"/>
              <span class="text-sm font-semibold text-slate-700">Wildberries</span>
            </div>
            <div class="flex items-end justify-between">
              <div>
                <div class="text-4xl font-bold" :class="ratingColor(sentiment.wb?.avg_rating||0)">
                  {{ sentiment.wb?.avg_rating || '—' }}★
                </div>
                <div class="text-xs text-slate-400 mt-1">средний рейтинг ({{ qualDays }} дн)</div>
              </div>
              <div class="text-right">
                <div class="text-sm font-medium text-emerald-600">{{ sentiment.wb?.pct_positive || 0 }}% позитив</div>
                <div class="text-xs text-slate-400">{{ sentiment.wb?.positive || 0 }} отзывов</div>
                <div class="text-sm font-medium text-rose-600 mt-1">{{ (100-(sentiment.wb?.pct_positive||0)).toFixed(1) }}% негатив</div>
                <div class="text-xs text-slate-400">{{ sentiment.wb?.negative || 0 }} отзывов</div>
              </div>
            </div>
            <!-- Стек-бар позитив/негатив -->
            <div class="mt-3 h-2 rounded-full overflow-hidden bg-slate-100 flex">
              <div class="bg-emerald-500 h-full" :style="{width:(sentiment.wb?.pct_positive||0)+'%'}"></div>
              <div class="bg-rose-400 h-full flex-1"></div>
            </div>
            <!-- Мини-тренд avg_rating за период -->
            <div class="mt-3 flex items-end gap-px h-10">
              <div v-for="b in ratingBars(sentiment.wb?.daily)" :key="b.date"
                class="flex-1 rounded-t min-w-0 cursor-default"
                :class="b.avg>=4.5?'bg-emerald-500':b.avg>=4?'bg-amber-400':'bg-rose-400'"
                :style="{height:Math.max(2,Math.round(b.avg/5*40))+'px'}"
                :title="`${b.date}: ${b.avg}★ (${b.total} отз.)`"></div>
            </div>
            <div class="text-xs text-slate-400 mt-1">Тренд рейтинга ({{ qualDays }} дн)</div>
          </div>

          <!-- ЯМ -->
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="flex items-center gap-2 mb-3">
              <img src="/icons/ym.png" class="w-5 h-5 rounded object-cover"/>
              <span class="text-sm font-semibold text-slate-700">Яндекс Маркет</span>
            </div>
            <div class="flex items-end justify-between">
              <div>
                <div class="text-4xl font-bold" :class="ratingColor(sentiment.ym?.avg_rating||0)">
                  {{ sentiment.ym?.avg_rating || '—' }}★
                </div>
                <div class="text-xs text-slate-400 mt-1">средний рейтинг ({{ qualDays }} дн)</div>
              </div>
              <div class="text-right">
                <div class="text-sm font-medium text-emerald-600">{{ sentiment.ym?.pct_positive || 0 }}% позитив</div>
                <div class="text-xs text-slate-400">{{ sentiment.ym?.positive || 0 }} отзывов</div>
                <div class="text-sm font-medium text-rose-600 mt-1">{{ (100-(sentiment.ym?.pct_positive||0)).toFixed(1) }}% негатив</div>
                <div class="text-xs text-slate-400">{{ sentiment.ym?.negative || 0 }} отзывов</div>
              </div>
            </div>
            <div class="mt-3 h-2 rounded-full overflow-hidden bg-slate-100 flex">
              <div class="bg-emerald-500 h-full" :style="{width:(sentiment.ym?.pct_positive||0)+'%'}"></div>
              <div class="bg-rose-400 h-full flex-1"></div>
            </div>
            <div class="mt-3 flex items-end gap-px h-10">
              <div v-for="b in ratingBars(sentiment.ym?.daily)" :key="b.date"
                class="flex-1 rounded-t min-w-0 cursor-default"
                :class="b.avg>=4.5?'bg-emerald-500':b.avg>=4?'bg-amber-400':'bg-rose-400'"
                :style="{height:Math.max(2,Math.round(b.avg/5*40))+'px'}"
                :title="`${b.date}: ${b.avg}★`"></div>
            </div>
            <div class="text-xs text-slate-400 mt-1">Тренд рейтинга ({{ qualDays }} дн)</div>
          </div>
        </div>

        <!-- ── ИИ-разметка ─────────────────────────────────────────────────── -->
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Дисциплина ИИ-разметки (WB)</h2>
        <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4 mb-6">
          <p class="text-xs text-slate-500 mb-4">% отзывов WB, обработанных ИИ-тегированием. Если падает — ИИ не успевает за потоком. Цель: &gt; 70%.</p>
          <div class="grid grid-cols-3 gap-4 mb-4">
            <div v-for="p in tagPeriods" :key="p.key">
              <div class="text-xs text-slate-400">{{ p.label }}</div>
              <div class="text-2xl font-bold mt-1" :class="tagColor(tagging[p.key]?.pct||0)">
                {{ tagging[p.key]?.pct || 0 }}%
              </div>
              <div class="text-xs text-slate-400">{{ tagging[p.key]?.tagged||0 }} / {{ tagging[p.key]?.total||0 }}</div>
            </div>
          </div>
          <!-- Тренд % разметки -->
          <div class="flex items-end gap-px h-12">
            <div v-for="b in tagBars(tagging.daily)" :key="b.date"
              class="flex-1 rounded-t min-w-0 cursor-default"
              :class="b.pct>=70?'bg-emerald-500':b.pct>=40?'bg-amber-400':b.total>0?'bg-rose-400':'bg-slate-100'"
              :style="{height:Math.max(2,Math.round(b.pct/100*48))+'px'}"
              :title="`${b.date}: ${b.pct}% (${b.tagged}/${b.total})`"></div>
          </div>
          <div class="text-xs text-slate-400 mt-1">Тренд % разметки (зелёный ≥70%, жёлтый ≥40%, красный &lt;40%)</div>
        </div>

        <!-- ── Воронка доотправок ───────────────────────────────────────────── -->
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Воронка доотправок</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <!-- Сводные метрики -->
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="grid grid-cols-2 gap-3">
              <div class="text-center p-3 bg-slate-50 rounded-lg">
                <div class="text-2xl font-bold text-slate-800">{{ reshipment.total || 0 }}</div>
                <div class="text-xs text-slate-400 mt-1">Всего заявок</div>
              </div>
              <div class="text-center p-3 bg-emerald-50 rounded-lg">
                <div class="text-2xl font-bold text-emerald-700">{{ reshipment.completed || 0 }}</div>
                <div class="text-xs text-slate-500 mt-1">Завершено</div>
              </div>
              <div class="text-center p-3 bg-blue-50 rounded-lg">
                <div class="text-2xl font-bold text-blue-700">{{ reshipment.progressed || 0 }}</div>
                <div class="text-xs text-slate-500 mt-1">В процессе</div>
              </div>
              <div class="text-center p-3 bg-rose-50 rounded-lg">
                <div class="text-2xl font-bold text-rose-700">{{ reshipment.rejected || 0 }}</div>
                <div class="text-xs text-slate-500 mt-1">Отказов</div>
              </div>
              <div class="text-center p-3 bg-amber-50 rounded-lg col-span-2">
                <div class="text-xl font-bold text-amber-700">{{ reshipment.review_requested || 0 }}</div>
                <div class="text-xs text-slate-500 mt-1">Запрошено смены отзыва</div>
              </div>
            </div>
          </div>

          <!-- Воронка -->
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4">
            <div class="text-xs font-medium text-slate-500 mb-3">Воронка статусов ({{ qualDays }} дн)</div>
            <div class="space-y-2">
              <div v-for="step in reshipFunnel" :key="step.label">
                <div class="flex justify-between text-xs text-slate-600 mb-1">
                  <span>{{ step.label }}</span><span class="font-medium">{{ step.value }}</span>
                </div>
                <div class="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div :class="step.color" class="h-full rounded-full transition-all"
                    :style="{width: reshipment.total ? Math.round(step.value/reshipment.total*100)+'%' : '0%'}"></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Недельная динамика доотправок -->
        <div v-if="reshipment.weekly && reshipment.weekly.length" class="bg-white rounded-xl border border-slate-100 shadow-sm p-4 mb-6">
          <div class="text-xs font-medium text-slate-500 mb-3">Недельная динамика доотправок</div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-slate-400 border-b border-slate-100">
                <th class="text-left py-2">Неделя</th>
                <th class="text-center py-2">Создано</th>
                <th class="text-center py-2">Завершено</th>
                <th class="text-center py-2">% завершения</th>
              </tr></thead>
              <tbody class="divide-y divide-slate-50">
                <tr v-for="w in [...reshipment.weekly].reverse()" :key="w.week" class="hover:bg-slate-50">
                  <td class="py-2 text-slate-600">{{ w.week }}</td>
                  <td class="py-2 text-center font-medium text-slate-800">{{ w.created }}</td>
                  <td class="py-2 text-center font-medium text-emerald-700">{{ w.completed }}</td>
                  <td class="py-2 text-center">
                    <span class="text-xs font-medium" :class="w.created ? (w.completed/w.created>=0.5?'text-emerald-600':'text-amber-600') : 'text-slate-400'">
                      {{ w.created ? Math.round(w.completed/w.created*100)+'%' : '—' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Настройка Telegram алертов -->
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Bell class="w-4 h-4"/> Telegram-алерты
        </h2>
        <div class="bg-white rounded-xl border border-slate-100 shadow-sm p-4 mb-6">
          <div v-if="activeAlerts.length" class="mb-3 space-y-2">
            <div v-for="a in activeAlerts" :key="a.key"
              class="flex items-center gap-2 text-sm p-2 rounded-lg"
              :class="a.severity==='high'?'bg-rose-50 text-rose-700':'bg-amber-50 text-amber-700'">
              <AlertTriangle class="w-4 h-4 flex-shrink-0"/>{{ a.message }}
            </div>
          </div>
          <div v-else class="flex items-center gap-2 text-sm text-emerald-700 p-2 bg-emerald-50 rounded-lg mb-3">
            <CheckCircle2 class="w-4 h-4"/>Активных алертов нет — всё в норме.
          </div>
          <div class="flex items-center gap-3">
            <button @click="sendTestAlert" :disabled="testSending"
              class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors">
              <Bell class="w-4 h-4"/> {{ testSending ? 'Отправка…' : 'Тест: отправить сообщение' }}
            </button>
            <span v-if="testResult" class="text-sm">{{ testResult }}</span>
          </div>
        </div>
      </template>
    </template>

    <!-- ВКЛАДКА: Автоответы и менеджеры -->
    <template v-else-if="tab==='replies'">
      <div v-if="repliesError" class="mb-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-sm border border-rose-100">{{ repliesError }}</div>
      <div v-if="repliesLoading && !repliesData" class="text-slate-400 text-sm py-20 text-center">Загрузка…</div>
      <template v-else-if="repliesData">
        <div class="flex items-center gap-2 mb-6">
          <span class="text-sm text-slate-500">Период:</span>
          <div class="flex gap-1">
            <button v-for="d in [7,14,30,60]" :key="d" @click="repliesDays=d;loadReplies()"
              class="px-3 py-1 rounded-lg text-sm transition-colors"
              :class="repliesDays===d?'bg-emerald-600 text-white':'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'">{{ d }} дн</button>
          </div>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Автоответы в чатах</div>
            <div class="text-3xl font-bold text-slate-800 mt-1">{{ repliesData.chats?.auto_sent || 0 }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">На проверку</div>
            <div class="text-3xl font-bold text-amber-700 mt-1">{{ repliesData.chats?.need_check || 0 }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Передано менеджеру</div>
            <div class="text-3xl font-bold text-rose-600 mt-1">{{ repliesData.chats?.handoff || 0 }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Ответы менеджеров</div>
            <div class="text-3xl font-bold text-slate-800 mt-1">{{ repliesData.chats?.manager_sent || 0 }}</div>
          </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-6">
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Автоответы на отзывы</div>
            <div class="text-2xl font-bold text-slate-800 mt-1">{{ repliesData.reviews?.auto_sent || 0 }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Отзывы менеджеров</div>
            <div class="text-2xl font-bold text-slate-800 mt-1">{{ repliesData.reviews?.manager_sent || 0 }}</div>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 p-4 shadow-sm">
            <div class="text-slate-400 text-xs font-medium uppercase tracking-wide">Обучение по правкам</div>
            <div class="text-2xl font-bold text-slate-800 mt-1">{{ repliesData.corrections || 0 }}</div>
            <div class="text-xs text-slate-500 mt-1">
              примеров за период · сработало на похожих: {{ repliesData.learned_hits || 0 }}
            </div>
          </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm overflow-hidden">
            <div class="px-4 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">По площадкам</div>
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-slate-400 uppercase">
                <th class="text-left px-4 py-2">Площадка</th>
                <th class="text-center px-3 py-2">Авто</th>
                <th class="text-center px-3 py-2">Проверка</th>
                <th class="text-center px-3 py-2">Менеджеру</th>
              </tr></thead>
              <tbody>
                <tr v-for="row in (repliesData.chats?.by_mp || [])" :key="row.mp" class="border-t border-slate-50">
                  <td class="px-4 py-2 font-medium">{{ (row.mp || '').toUpperCase() }}</td>
                  <td class="text-center px-3 py-2">{{ row.auto_sent }}</td>
                  <td class="text-center px-3 py-2">{{ row.need_check }}</td>
                  <td class="text-center px-3 py-2">{{ row.handoff }}</td>
                </tr>
                <tr v-if="!(repliesData.chats?.by_mp || []).length">
                  <td colspan="4" class="px-4 py-6 text-center text-slate-400">Пока нет данных</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="bg-white rounded-xl border border-slate-100 shadow-sm overflow-hidden">
            <div class="px-4 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">Кто отвечал в чатах</div>
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-slate-400 uppercase">
                <th class="text-left px-4 py-2">Менеджер</th>
                <th class="text-center px-3 py-2">Ответов</th>
              </tr></thead>
              <tbody>
                <tr v-for="row in (repliesData.chats?.managers || [])" :key="row.manager" class="border-t border-slate-50">
                  <td class="px-4 py-2">{{ row.manager }}</td>
                  <td class="text-center px-3 py-2 font-semibold">{{ row.n }}</td>
                </tr>
                <tr v-if="!(repliesData.chats?.managers || []).length">
                  <td colspan="2" class="px-4 py-6 text-center text-slate-400">Пока нет данных</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </template>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА 4: Сотрудники                                                -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <template v-else-if="tab==='staff'">
      <div v-if="staffLoading && !staffData" class="text-slate-400 text-sm py-20 text-center">Загрузка…</div>

      <template v-else-if="staffData">
        <h2 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4">Кто на сайте</h2>

        <div v-if="!staffData.staff?.length" class="text-slate-400 text-sm py-10 text-center">
          Нет пользователей
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          <div v-for="s in staffData.staff" :key="s.id"
            class="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
            <!-- Шапка карточки -->
            <div class="flex items-start gap-3">
              <div class="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold text-lg flex-shrink-0">
                {{ s.name.charAt(0) }}
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="font-semibold text-slate-800 truncate">{{ s.name }}</span>
                  <!-- Индикатор онлайн -->
                  <span class="flex-shrink-0 w-2.5 h-2.5 rounded-full"
                    :class="s.status==='online'?'bg-emerald-500':s.status==='recent'?'bg-amber-400':'bg-slate-300'"
                    :title="s.status==='online'?'Онлайн':s.status==='recent'?'Недавно был':'Не в сети'"></span>
                </div>
                <div class="text-xs text-slate-400 truncate mt-0.5">{{ s.username }} · {{ s.role }}</div>
              </div>
            </div>

            <!-- Статус -->
            <div class="mt-4 space-y-2">
              <div class="flex items-center justify-between text-sm">
                <span class="text-slate-500">Статус</span>
                <span class="font-medium"
                  :class="s.status==='online'?'text-emerald-600':s.status==='recent'?'text-amber-600':'text-slate-400'">
                  {{ s.status==='online' ? 'Онлайн' : s.status==='recent' ? 'Недавно' : 'Не в сети' }}
                </span>
              </div>

              <div class="flex items-center justify-between text-sm">
                <span class="text-slate-500">Последняя активность</span>
                <span class="font-medium text-slate-700">
                  <template v-if="s.last_seen_sec != null && s.last_seen_sec < 60">
                    {{ s.status === 'online' ? 'сейчас' : `${s.last_seen_sec} сек назад` }}
                  </template>
                  <template v-else-if="s.last_seen_min !== null">
                    <template v-if="s.last_seen_min < 1">только что</template>
                    <template v-else-if="s.last_seen_min < 60">{{ s.last_seen_min }} мин назад</template>
                    <template v-else-if="s.last_seen_min < 1440">{{ Math.floor(s.last_seen_min/60) }} ч назад</template>
                    <template v-else>{{ Math.floor(s.last_seen_min/1440) }} дн назад</template>
                  </template>
                  <template v-else>—</template>
                </span>
              </div>

              <div class="flex items-center justify-between text-sm">
                <span class="text-slate-500">Текущая страница</span>
                <span class="font-mono text-xs text-slate-600 bg-slate-50 px-2 py-0.5 rounded truncate max-w-[140px]">
                  {{ s.last_path || '—' }}
                </span>
              </div>

              <div v-if="s.session_start" class="flex items-center justify-between text-sm">
                <span class="text-slate-500">Сессия с</span>
                <span class="text-slate-600 text-xs">
                  {{ new Date(s.session_start).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}) }}
                </span>
              </div>

              <!-- TG привязка -->
              <div class="pt-3 mt-1 border-t border-slate-100">
                <div v-if="editingTg[s.id] === undefined" class="flex items-center justify-between">
                  <div class="flex items-center gap-1.5 text-xs">
                    <Bell class="w-3.5 h-3.5" :class="s.tg_linked?'text-emerald-500':'text-slate-300'"/>
                    <span :class="s.tg_linked?'text-emerald-600 font-medium':'text-slate-400'">
                      {{ s.tg_linked ? 'Telegram привязан' : 'Telegram не привязан' }}
                    </span>
                  </div>
                  <button @click="editingTg[s.id] = s.tg_chat_id || ''"
                    class="text-xs text-slate-400 hover:text-slate-600 underline">
                    {{ s.tg_linked ? 'изменить' : 'привязать' }}
                  </button>
                </div>
                <div v-else class="flex gap-2">
                  <input v-model="editingTg[s.id]" placeholder="chat_id (число)"
                    class="flex-1 text-xs border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-emerald-400"/>
                  <button @click="saveTg(s.id)" :disabled="savingTg[s.id]"
                    class="text-xs px-2 py-1 bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:opacity-50">
                    {{ savingTg[s.id] ? '…' : 'Сохранить' }}
                  </button>
                  <button @click="editingTg[s.id] = undefined"
                    class="text-xs px-2 py-1 border border-slate-200 rounded hover:bg-slate-50">
                    ✕
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <p class="text-xs text-slate-400 mt-6">
          Онлайн — клик, набор или прокрутка в последние 12 сек. Открытая вкладка без действий не считается. Обновление каждые 2 сек.
        </p>
      </template>
    </template>
  </div>
</template>
