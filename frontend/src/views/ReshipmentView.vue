<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../api'
import {
  PackageCheck, RefreshCw, X, CheckCircle2, XCircle,
  Truck, Copy, ExternalLink, Star, Loader2, Search,
  Info, AlertTriangle, Phone, Mail, MapPin, CheckSquare, Square, Link,
  MessageCircle, FileDown, Printer
} from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const userRole = ref(localStorage.getItem('role') || '')
const username = ref(localStorage.getItem('username') || '')

const requests      = ref([])
const stats         = ref([])
const loading       = ref(false)
const selected      = ref(null)
const filterStatus  = ref('all')
const searchQuery   = ref('')

// ── Роль-зависимые флаги ─────────────────────────────────────────────────────
const isCS        = computed(() => ['cs_manager', 'admin', 'moderator'].includes(userRole.value))
const isWarehouse = computed(() => ['warehouse_manager', 'admin', 'moderator'].includes(userRole.value))

const defaultStatus = computed(() => {
  if (userRole.value === 'cs_manager')        return 'new'
  if (userRole.value === 'warehouse_manager') return 'approved'
  return 'all'
})

const visibleFilters = computed(() => {
  if (userRole.value === 'cs_manager') return [
    { value: 'all',      label: 'Все' },
    { value: 'new',      label: 'Новые' },
    { value: 'rejected', label: 'Отклонённые' },
    { value: 'shipped',  label: 'Отправленные' },
    { value: 'delivered',label: 'Полученные' },
  ]
  if (userRole.value === 'warehouse_manager') return [
    { value: 'all',      label: 'Все' },
    { value: 'approved', label: 'К отправке' },
    { value: 'shipped',  label: 'Отправленные' },
    { value: 'delivered',label: 'Полученные' },
  ]
  return [
    { value: 'all',      label: 'Все' },
    { value: 'new',      label: 'Новые' },
    { value: 'approved', label: 'Одобрены' },
    { value: 'rejected', label: 'Отклонены' },
    { value: 'shipped',  label: 'Отправлены' },
    { value: 'delivered',label: 'Получены' },
  ]
})

const statusMeta = {
  new:       { label: 'Новая',        color: 'bg-blue-100 text-blue-700' },
  matched:   { label: 'Сопоставлена', color: 'bg-amber-100 text-amber-700' },
  approved:  { label: 'Одобрена',     color: 'bg-emerald-100 text-emerald-700' },
  rejected:  { label: 'Отклонена',    color: 'bg-rose-100 text-rose-700' },
  shipped:   { label: 'Отправлена',   color: 'bg-violet-100 text-violet-700' },
  delivered: { label: 'Получена',     color: 'bg-slate-100 text-slate-600' },
}

// ── Состояние панели ──────────────────────────────────────────────────────────
const panel        = ref(null)
const panelLoading = ref(false)
const confirmUrl   = ref('')
const copied       = ref(false)
const copiedForm   = ref(false)
const costLoading  = ref(false)
const estimatedCost = ref(null)
const lastShipResult = ref(null)

const FORM_URL = 'http://box.vidovito.com'

const copyFormUrl = async () => {
  await navigator.clipboard.writeText(FORM_URL)
  copiedForm.value = true
  setTimeout(() => { copiedForm.value = false }, 2000)
}

// ── Формы ─────────────────────────────────────────────────────────────────────
const approveForm         = ref({ matched_srid: '', match_notes: '', moderator_comment: '' })
const rejectForm          = ref({ moderator_comment: '' })
const warehouseRejectForm = ref({ rejection_reason: '' })
const yandexForm          = ref({ track_number: '', shipping_cost: '' })

// ── Массовый выбор (склад) ───────────────────────────────────────────────────
const bulkSelected = ref(new Set())

const toggleBulk = (id) => {
  const s = new Set(bulkSelected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  bulkSelected.value = s
}

const toggleAllApproved = () => {
  const approvedIds = filteredRequests.value.filter(r => r.status === 'approved').map(r => r.id)
  if (approvedIds.every(id => bulkSelected.value.has(id))) {
    bulkSelected.value = new Set()
  } else {
    bulkSelected.value = new Set(approvedIds)
  }
}

const approvedCount = computed(() =>
  filteredRequests.value.filter(r => r.status === 'approved').length
)
const allApprovedSelected = computed(() =>
  approvedCount.value > 0 &&
  filteredRequests.value.filter(r => r.status === 'approved').every(r => bulkSelected.value.has(r.id))
)

// ── Вычисляемые ──────────────────────────────────────────────────────────────
const filteredRequests = computed(() => {
  let list = requests.value
  if (userRole.value === 'cs_manager') {
    list = list.filter(r => ['new', 'matched', 'rejected', 'shipped', 'delivered'].includes(r.status))
  } else if (userRole.value === 'warehouse_manager') {
    list = list.filter(r => ['approved', 'shipped', 'delivered', 'rejected'].includes(r.status))
  }
  if (filterStatus.value !== 'all') list = list.filter(r => r.status === filterStatus.value)
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(r =>
      (r.customer_name  || '').toLowerCase().includes(q) ||
      (r.order_number   || '').toLowerCase().includes(q) ||
      (r.items_to_send  || '').toLowerCase().includes(q) ||
      (r.customer_phone || '').includes(q)
    )
  }
  return list
})

const statsByStatus = computed(() => {
  const map = {}
  stats.value.forEach(s => { map[s.status] = { cnt: Number(s.cnt), cost: parseFloat(s.total_cost || 0) } })
  return map
})

const totalShippingCost = computed(() =>
  ['shipped', 'delivered'].reduce((acc, s) => acc + (statsByStatus.value[s]?.cost || 0), 0)
)

// ── Данные ────────────────────────────────────────────────────────────────────
const load = async () => {
  loading.value = true
  try {
    const [r1, r2] = await Promise.all([
      apiFetch(`${API_BASE}/api/v1/reshipment/requests`),
      apiFetch(`${API_BASE}/api/v1/reshipment/stats`),
    ])
    const d1 = await r1.json()
    const d2 = await r2.json()
    requests.value = d1.data || []
    stats.value    = d2.data || []
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

const select = (req) => {
  selected.value = req
  panel.value    = null
  confirmUrl.value = ''
  estimatedCost.value = null
  lastShipResult.value = null
  approveForm.value         = { matched_srid: req.matched_srid || '', match_notes: req.match_notes || '', moderator_comment: '' }
  rejectForm.value          = { moderator_comment: '' }
  warehouseRejectForm.value = { rejection_reason: '' }
  yandexForm.value          = { track_number: '', shipping_cost: '' }
}

const reload = async () => {
  await load()
  if (selected.value) {
    selected.value = requests.value.find(r => r.id === selected.value.id) || null
  }
}

// ── Загрузка стоимости (для КС при одобрении) ─────────────────────────────
const loadCost = async () => {
  if (!selected.value) return
  costLoading.value = true
  estimatedCost.value = null
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/cost`)
    const data = await res.json()
    if (data.cost !== null && data.cost !== undefined) {
      estimatedCost.value = data.cost
    }
  } catch {}
  finally { costLoading.value = false }
}

// ── КС: одобрить ─────────────────────────────────────────────────────────────
const openApprove = async () => {
  panel.value = 'approve'
  await loadCost()
}

const doApprove = async () => {
  panelLoading.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/approve`, {
      method: 'POST',
      body: JSON.stringify({
        matched_srid:      approveForm.value.matched_srid || null,
        match_notes:       approveForm.value.match_notes || null,
        moderator_comment: approveForm.value.moderator_comment || null,
        processed_by:      username.value,
      }),
    })
    panel.value = null
    selected.value = null
    await reload()
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── КС: отклонить ────────────────────────────────────────────────────────────
const doReject = async () => {
  if (!rejectForm.value.moderator_comment.trim()) { alert('Укажите причину'); return }
  panelLoading.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        moderator_comment: rejectForm.value.moderator_comment,
        processed_by:      username.value,
      }),
    })
    panel.value = null
    await reload()
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: отклонить с причиной ───────────────────────────────────────────────
const doWarehouseReject = async () => {
  if (!warehouseRejectForm.value.rejection_reason.trim()) { alert('Укажите причину'); return }
  panelLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/warehouse-reject`, {
      method: 'POST',
      body: JSON.stringify({
        rejection_reason: warehouseRejectForm.value.rejection_reason,
        processed_by:     username.value,
      }),
    })
    const data = await res.json()
    panel.value = 'warehouse_rejected'
    lastShipResult.value = data
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: отметить как сдано на ПВЗ ────────────────────────────────────────
const doMarkShipped = async (id) => {
  panelLoading.value = true
  try {
    await apiFetch(
      `${API_BASE}/api/v1/reshipment/requests/${id}/mark-shipped?processed_by=${encodeURIComponent(username.value)}`,
      { method: 'POST' }
    )
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: массовые действия ─────────────────────────────────────────────────
const bulkLoading = ref(false)
const bulkResults = ref([])

const doBulkMarkShipped = async () => {
  if (!bulkSelected.value.size) return
  bulkLoading.value = true
  try {
    await Promise.all([...bulkSelected.value].map(id =>
      apiFetch(`${API_BASE}/api/v1/reshipment/requests/${id}/mark-shipped?processed_by=${encodeURIComponent(username.value)}`, { method: 'POST' }).catch(() => {})
    ))
    bulkSelected.value = new Set()
    await reload()
  } catch (e) { alert(e.message) }
  finally { bulkLoading.value = false }
}

// ── Склад: скачать список к упаковке ─────────────────────────────────────────
const downloadPacklist = () => {
  const token = localStorage.getItem('auth_token')
  const url = `${API_BASE}/api/v1/reshipment/warehouse/packlist`
  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.click()
}

// ── Склад: печать этикеток PDF ────────────────────────────────────────────────
const downloadLabels = (ids = []) => {
  const idsParam = ids.length ? `?ids=${ids.join(',')}` : ''
  window.open(`${API_BASE}/api/v1/reshipment/warehouse/labels${idsParam}`, '_blank')
}

// ── Утилиты ────────────────────────────────────────────────────────────────
const doRequestReview = async () => {
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/request-review`, { method: 'POST' })
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
  } catch (e) { alert(e.message) }
}

const copyUrl = async (url) => {
  await navigator.clipboard.writeText(url)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

const cdekTrackUrl = (num) => num ? `https://www.cdek.ru/track.html?order_id=${num}` : null

const fmt = (d) => d ? new Date(d).toLocaleString('ru-RU', {
  day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'
}) : '—'

const fmtCost = (v) => v != null ? `${Number(v).toLocaleString('ru-RU')} ₽` : '—'

onMounted(() => {
  filterStatus.value = defaultStatus.value
  load()
})
</script>

<template>
  <div class="flex h-full bg-slate-50">

    <!-- ── Список ──────────────────────────────────────────────────────────── -->
    <div class="flex flex-col w-80 xl:w-96 flex-shrink-0 border-r border-slate-200 bg-white">

      <div class="p-4 border-b border-slate-100">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h1 class="text-base font-bold text-slate-800 flex items-center gap-2">
              <PackageCheck class="w-4 h-4 text-emerald-600" /> Отправки
            </h1>
            <p class="text-[10px] text-slate-400 mt-0.5">
              {{ userRole === 'cs_manager' ? 'Менеджер КС' : userRole === 'warehouse_manager' ? 'Менеджер склада' : 'Все заявки' }}
            </p>
          </div>
          <div class="flex items-center gap-1">
            <button @click="copyFormUrl" :title="copiedForm ? 'Скопировано!' : 'Скопировать ссылку на форму клиента'"
              :class="['p-1.5 rounded-lg transition-colors flex items-center gap-1 text-xs font-medium',
                copiedForm ? 'bg-emerald-100 text-emerald-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600']">
              <Link class="w-3.5 h-3.5" />
              <span class="hidden xl:inline">{{ copiedForm ? 'Скопировано' : 'Форма' }}</span>
            </button>
            <button @click="reload" :class="['p-1.5 rounded-lg text-slate-400 hover:bg-slate-100 transition-colors', loading ? 'animate-spin' : '']">
              <RefreshCw class="w-4 h-4" />
            </button>
          </div>
        </div>

        <div class="relative mb-3">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input v-model="searchQuery" type="text" placeholder="Имя, номер, телефон..."
            class="w-full pl-8 pr-3 py-2 text-xs rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none" />
        </div>

        <div class="flex flex-wrap gap-1">
          <button v-for="f in visibleFilters" :key="f.value" @click="filterStatus = f.value"
            :class="['px-2.5 py-1 text-xs rounded-full font-medium transition-colors',
              filterStatus === f.value ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200']">
            {{ f.label }}
            <span v-if="f.value !== 'all' && statsByStatus[f.value]" class="ml-1 opacity-70">{{ statsByStatus[f.value].cnt }}</span>
          </button>
        </div>
      </div>

      <!-- Массовое действие (склад) -->
      <div v-if="isWarehouse && approvedCount > 0 && (filterStatus === 'approved' || filterStatus === 'all')"
        class="px-3 py-2 border-b border-slate-100 bg-violet-50 flex items-center gap-2 flex-wrap">
        <button @click="toggleAllApproved" class="text-violet-600 hover:text-violet-800 flex-shrink-0">
          <CheckSquare v-if="allApprovedSelected" class="w-4 h-4" />
          <Square v-else class="w-4 h-4" />
        </button>
        <span class="text-xs text-violet-700 flex-1">
          {{ bulkSelected.size ? `Выбрано: ${bulkSelected.size}` : `К отправке: ${approvedCount}` }}
        </span>
        <button @click="downloadPacklist"
          class="text-xs px-2 py-1 bg-white hover:bg-slate-50 text-slate-600 rounded-lg font-medium transition-colors flex items-center gap-1 border border-slate-200">
          <FileDown class="w-3 h-3" /> Excel
        </button>
        <button @click="downloadLabels()"
          class="text-xs px-2 py-1 bg-white hover:bg-slate-50 text-slate-600 rounded-lg font-medium transition-colors flex items-center gap-1 border border-slate-200">
          <Printer class="w-3 h-3" /> Этикетки
        </button>
        <button v-if="bulkSelected.size" @click="doBulkMarkShipped" :disabled="bulkLoading"
          class="text-xs px-2.5 py-1 bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white rounded-lg font-semibold transition-colors flex items-center gap-1">
          <Loader2 v-if="bulkLoading" class="w-3 h-3 animate-spin" />
          <CheckCircle2 v-else class="w-3 h-3" />
          Сдано на ПВЗ ({{ bulkSelected.size }})
        </button>
      </div>

      <!-- Сводка -->
      <div class="px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs text-slate-500 flex justify-between">
        <span>{{ filteredRequests.length }} заявок</span>
        <span v-if="totalShippingCost > 0">Расходы: <b class="text-slate-700">{{ fmtCost(totalShippingCost) }}</b></span>
      </div>

      <!-- Bulk результаты -->
      <div v-if="panel === 'bulk_done' && bulkResults.length" class="px-3 py-2 border-b border-slate-100 bg-white">
        <p class="text-xs font-bold text-slate-600 mb-1.5">Результаты создания заказов:</p>
        <div v-for="r in bulkResults" :key="r.id"
          :class="['text-xs flex items-center gap-1.5 mb-1', r.ok ? 'text-emerald-700' : 'text-rose-600']">
          <CheckCircle2 v-if="r.ok" class="w-3 h-3" />
          <XCircle v-else class="w-3 h-3" />
          <span>#{{ r.id }}</span>
          <span v-if="r.ok && r.cdek_number" class="font-mono">{{ r.cdek_number }}</span>
          <span v-else-if="!r.ok" class="truncate">{{ r.error }}</span>
        </div>
        <button @click="panel = null; bulkResults = []" class="text-[10px] text-slate-400 hover:text-slate-600 mt-1">Закрыть</button>
      </div>

      <!-- Список заявок -->
      <div class="flex-1 overflow-y-auto">
        <div v-if="loading && !requests.length" class="flex items-center justify-center h-32 text-slate-400">
          <Loader2 class="w-5 h-5 animate-spin" />
        </div>
        <div v-else-if="!filteredRequests.length" class="text-center text-sm text-slate-400 py-12 px-4">
          {{ filterStatus === 'new' ? 'Новых заявок нет' : filterStatus === 'approved' ? 'Нет заявок к отправке' : 'Нет заявок' }}
        </div>

        <div v-for="req in filteredRequests" :key="req.id"
          :class="['flex items-start border-b border-slate-100', selected?.id === req.id ? 'bg-emerald-50 border-l-2 border-l-emerald-500' : 'hover:bg-slate-50']">

          <!-- Чекбокс (только для склада, только approved) -->
          <div v-if="isWarehouse && req.status === 'approved'" class="pl-3 pt-3.5 flex-shrink-0">
            <button @click.stop="toggleBulk(req.id)" class="text-violet-400 hover:text-violet-600">
              <CheckSquare v-if="bulkSelected.has(req.id)" class="w-4 h-4 text-violet-600" />
              <Square v-else class="w-4 h-4" />
            </button>
          </div>

          <button class="flex-1 text-left px-4 py-3 min-w-0" @click="select(req)">
            <div class="flex items-start justify-between gap-2 mb-1">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="text-sm font-medium text-slate-800 truncate">{{ req.customer_name }}</span>
                <span v-if="req.wb_chat_id" title="Заявка из WB чата"
                  class="flex-shrink-0 w-4 h-4 rounded-full bg-violet-100 text-violet-600 flex items-center justify-center">
                  <MessageCircle class="w-2.5 h-2.5" />
                </span>
              </div>
              <span :class="['text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0', statusMeta[req.status]?.color]">
                {{ statusMeta[req.status]?.label || req.status }}
              </span>
            </div>
            <div class="text-xs text-slate-500 truncate mb-1">{{ req.problem_type }} · {{ req.items_to_send }}</div>
            <div class="flex items-center gap-2 text-[10px] text-slate-400 flex-wrap">
              <span>#{{ req.id }}</span>
              <span v-if="req.order_number">· {{ req.order_number }}</span>
              <span v-if="req.cdek_cost" class="text-emerald-600 font-medium">· {{ fmtCost(req.cdek_cost) }}</span>
              <span class="ml-auto">{{ fmt(req.created_at) }}</span>
            </div>
          </button>
        </div>
      </div>
    </div>

    <!-- ── Детали ──────────────────────────────────────────────────────────── -->
    <div class="flex-1 overflow-y-auto">
      <div v-if="!selected" class="flex flex-col items-center justify-center h-full text-slate-400 gap-3">
        <PackageCheck class="w-12 h-12 opacity-30" />
        <p class="text-sm">Выберите заявку из списка</p>
      </div>

      <div v-else class="p-6 max-w-3xl">

        <!-- Заголовок -->
        <div class="flex items-start justify-between mb-5 gap-4">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <h2 class="text-xl font-bold text-slate-800">{{ selected.customer_name }}</h2>
              <span :class="['text-xs font-semibold px-2.5 py-1 rounded-full', statusMeta[selected.status]?.color]">
                {{ statusMeta[selected.status]?.label }}
              </span>
            </div>
            <p class="text-sm text-slate-400">Заявка #{{ selected.id }} · {{ fmt(selected.created_at) }}</p>
          </div>
          <button @click="selected = null" class="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100">
            <X class="w-5 h-5" />
          </button>
        </div>

        <!-- Карточки: контакты + заказ -->
        <div class="grid grid-cols-2 gap-3 mb-3">
          <div class="bg-white rounded-xl border border-slate-200 p-4">
            <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Контакты</p>
            <div class="space-y-1 text-sm text-slate-700">
              <div class="font-medium flex items-center gap-1.5">
                <Phone class="w-3.5 h-3.5 text-slate-400" />
                {{ selected.customer_phone }}
              </div>
              <div v-if="selected.customer_email" class="text-slate-500 text-xs flex items-center gap-1.5">
                <Mail class="w-3.5 h-3.5 text-slate-400" />
                {{ selected.customer_email }}
              </div>
            </div>
          </div>
          <div class="bg-white rounded-xl border border-slate-200 p-4">
            <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Заказ</p>
            <div class="space-y-1 text-sm text-slate-700">
              <div v-if="selected.order_number">№ {{ selected.order_number }}</div>
              <div v-if="selected.matched_srid" class="text-emerald-700 text-xs font-medium">✓ SRID: {{ selected.matched_srid }}</div>
              <div v-if="!selected.order_number && !selected.matched_srid" class="text-slate-400 text-xs">Номер не указан</div>
            </div>
          </div>
        </div>

        <!-- WB чат (если заявка пришла через чат продавца) -->
        <div v-if="selected.wb_chat_id" class="bg-violet-50 rounded-xl border border-violet-200 p-4 mb-3">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs font-bold text-violet-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                <MessageCircle class="w-3.5 h-3.5" /> WB чат продавца
              </p>
              <div class="space-y-0.5 text-xs text-violet-700">
                <div v-if="selected.wb_nm_id">Артикул WB: <span class="font-mono font-bold">{{ selected.wb_nm_id }}</span>
                  <a :href="`https://www.wildberries.ru/catalog/${selected.wb_nm_id}/detail.aspx`" target="_blank"
                    class="ml-1.5 text-violet-500 hover:text-violet-700 underline">открыть товар</a>
                </div>
                <div class="text-violet-400 font-mono text-[10px]">chatID: {{ selected.wb_chat_id }}</div>
              </div>
            </div>
            <a href="https://seller.wildberries.ru/messages" target="_blank"
              class="flex items-center gap-1.5 text-xs px-3 py-2 bg-violet-600 hover:bg-violet-700 text-white font-semibold rounded-lg transition-colors flex-shrink-0">
              <ExternalLink class="w-3.5 h-3.5" /> Открыть чат
            </a>
          </div>
        </div>

        <!-- Стоимость доставки -->
        <div v-if="selected.cdek_cost" class="bg-emerald-50 rounded-xl border border-emerald-200 p-3 mb-3 flex items-center gap-3">
          <div class="w-8 h-8 bg-emerald-100 rounded-lg flex items-center justify-center flex-shrink-0">
            <Truck class="w-4 h-4 text-emerald-600" />
          </div>
          <div>
            <p class="text-xs text-emerald-600 font-semibold uppercase tracking-wider">Стоимость доставки СДЭК</p>
            <p class="text-lg font-bold text-emerald-800">{{ fmtCost(selected.cdek_cost) }}</p>
          </div>
        </div>

        <!-- Проблема -->
        <div class="bg-white rounded-xl border border-slate-200 p-4 mb-3">
          <div class="flex items-center gap-2 mb-3">
            <p class="text-xs font-bold text-slate-400 uppercase tracking-wider">Проблема</p>
            <span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-700">{{ selected.problem_type }}</span>
            <span v-if="selected.product_category" class="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-100">{{ selected.product_category }}</span>
          </div>
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1.5">Что нужно отправить</p>
          <p class="text-sm font-medium text-slate-800">{{ selected.items_to_send }}</p>
        </div>

        <!-- ПВЗ клиента -->
        <div class="bg-white rounded-xl border border-slate-200 p-4 mb-3">
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Пункт выдачи клиента (СДЭК)</p>
          <div v-if="selected.client_pvz_code" class="flex items-start gap-2 text-sm text-slate-700">
            <MapPin class="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5" />
            <div>
              <p class="font-medium">{{ selected.client_pvz_city }}</p>
              <p class="text-xs text-slate-500 mt-0.5">{{ selected.client_pvz_address }}</p>
              <p class="text-xs text-slate-400 mt-0.5 font-mono">Код: {{ selected.client_pvz_code }}</p>
            </div>
          </div>
          <p v-else class="text-sm text-slate-400 italic">ПВЗ не выбран (старая заявка)</p>
        </div>

        <!-- Фото -->
        <div v-if="selected.photo_files" class="bg-white rounded-xl border border-slate-200 p-4 mb-3">
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Фото</p>
          <div class="flex flex-wrap gap-2">
            <a v-for="(url, i) in JSON.parse(selected.photo_files || '[]')" :key="i"
              :href="url" target="_blank"
              class="flex items-center gap-1.5 text-xs text-emerald-600 hover:text-emerald-700 bg-emerald-50 px-2.5 py-1.5 rounded-lg border border-emerald-100">
              <ExternalLink class="w-3 h-3" /> Фото {{ i + 1 }}
            </a>
          </div>
        </div>

        <!-- Трек (если отправлено) -->
        <div v-if="selected.status === 'shipped' || selected.track_number" class="bg-violet-50 rounded-xl border border-violet-200 p-4 mb-3">
          <div class="flex items-center gap-2 mb-2">
            <p class="text-xs font-bold text-violet-400 uppercase tracking-wider">Отправление</p>
            <span v-if="selected.delivery_method === 'cdek'" class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-violet-200 text-violet-800">СДЭК</span>
            <span v-else-if="selected.delivery_method === 'yandex'" class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">Яндекс</span>
          </div>
          <div class="space-y-1 text-sm">
            <div v-if="selected.cdek_number || selected.track_number" class="flex items-center gap-2">
              <span class="text-violet-500 text-xs">Трек:</span>
              <a v-if="selected.delivery_method === 'cdek' && selected.cdek_number"
                :href="cdekTrackUrl(selected.cdek_number)" target="_blank"
                class="font-mono font-bold text-violet-800 hover:text-violet-600 flex items-center gap-1">
                {{ selected.cdek_number }} <ExternalLink class="w-3 h-3" />
              </a>
              <span v-else class="font-mono font-bold text-violet-800">{{ selected.track_number }}</span>
            </div>
            <div v-if="selected.cdek_cost || selected.shipping_cost" class="flex items-center gap-2">
              <span class="text-violet-500 text-xs">Стоимость:</span>
              <span class="font-bold text-violet-800">{{ fmtCost(selected.cdek_cost || selected.shipping_cost) }}</span>
            </div>
            <div v-if="selected.shipped_at" class="text-violet-400 text-xs">{{ fmt(selected.shipped_at) }}</div>
          </div>
        </div>

        <!-- Подтверждение получения -->
        <div v-if="selected.confirmed_at" class="bg-emerald-50 rounded-xl border border-emerald-200 p-4 mb-4 flex items-center justify-between">
          <div>
            <p class="text-xs font-bold text-emerald-600 uppercase tracking-wider mb-1">Получено клиентом</p>
            <p class="text-sm text-emerald-700">{{ fmt(selected.confirmed_at) }}</p>
          </div>
          <button v-if="!selected.review_requested && isCS" @click="doRequestReview"
            class="flex items-center gap-1.5 text-xs px-3 py-2 bg-amber-500 hover:bg-amber-600 text-white font-semibold rounded-lg transition-colors">
            <Star class="w-3.5 h-3.5" /> Попросить исправить отзыв
          </button>
          <span v-else-if="selected.review_requested" class="text-xs text-emerald-600 flex items-center gap-1.5">
            <CheckCircle2 class="w-4 h-4" /> Отзыв запрошен
          </span>
        </div>

        <!-- Причина отклонения склада -->
        <div v-if="selected.rejected_by === 'warehouse' && selected.rejection_reason"
          class="bg-rose-50 rounded-xl border border-rose-200 p-4 mb-4">
          <p class="text-xs font-bold text-rose-500 uppercase tracking-wider mb-1">Причина отклонения (склад)</p>
          <p class="text-sm text-rose-700">{{ selected.rejection_reason }}</p>
          <p v-if="selected.processed_by" class="text-xs text-rose-400 mt-1">— {{ selected.processed_by }}, {{ fmt(selected.processed_at) }}</p>
        </div>

        <!-- Комментарий КС -->
        <div v-if="selected.moderator_comment" class="bg-slate-50 rounded-xl border border-slate-200 p-4 mb-4">
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Комментарий КС</p>
          <p class="text-sm text-slate-600">{{ selected.moderator_comment }}</p>
          <p v-if="selected.processed_by" class="text-xs text-slate-400 mt-1">— {{ selected.processed_by }}, {{ fmt(selected.processed_at) }}</p>
        </div>

        <!-- Ссылка подтверждения (после создания заказа) -->
        <div v-if="confirmUrl" class="bg-violet-50 rounded-xl border border-violet-200 p-4 mb-4">
          <p class="text-xs font-bold text-violet-500 uppercase tracking-wider mb-1">Ссылка для клиента</p>
          <p class="text-xs text-violet-600 mb-3">Отправьте клиенту — по ней он может подтвердить получение</p>
          <div class="flex items-center gap-2">
            <code class="flex-1 text-xs bg-white border border-violet-200 rounded-lg px-3 py-2 text-violet-700 truncate">{{ confirmUrl }}</code>
            <button @click="copyUrl(confirmUrl)"
              class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-2 bg-violet-600 hover:bg-violet-700 text-white font-semibold rounded-lg transition-colors">
              <Copy class="w-3.5 h-3.5" />{{ copied ? 'Скопировано!' : 'Копировать' }}
            </button>
          </div>
        </div>

        <!-- Результат отклонения складом -->
        <div v-if="panel === 'warehouse_rejected' && lastShipResult" class="bg-amber-50 rounded-xl border border-amber-200 p-4 mb-4">
          <p class="text-xs font-bold text-amber-600 uppercase tracking-wider mb-2">Заявка отклонена</p>
          <div v-if="lastShipResult.email_sent" class="text-xs text-emerald-700 flex items-center gap-1.5 mb-2">
            <CheckCircle2 class="w-3.5 h-3.5" /> Email уведомление отправлено клиенту
          </div>
          <div v-if="!lastShipResult.email_sent" class="bg-amber-100 rounded-lg p-3">
            <p class="text-xs text-amber-700 font-semibold mb-1">Email не отправлен — свяжитесь вручную:</p>
            <p class="text-sm font-bold text-amber-800">{{ lastShipResult.customer_name }}</p>
            <p class="text-xs text-amber-700 flex items-center gap-1 mt-1">
              <Phone class="w-3 h-3" /> {{ lastShipResult.customer_phone }}
            </p>
            <div class="mt-2 p-2 bg-white rounded border border-amber-200 text-xs text-slate-600 leading-relaxed">
              «К сожалению, в данный момент нет возможности отправить деталь. Предлагаем оформить возврат.»
            </div>
          </div>
        </div>

        <!-- ── Действия КС ────────────────────────────────────────────────── -->
        <template v-if="isCS && ['new', 'matched'].includes(selected.status) && !panel">
          <div class="flex gap-3">
            <button @click="openApprove"
              class="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-xl transition-colors">
              <CheckCircle2 class="w-4 h-4" /> Одобрить заявку
            </button>
            <button @click="panel = 'reject'"
              class="flex items-center gap-2 px-4 py-2.5 bg-rose-50 hover:bg-rose-100 text-rose-600 text-sm font-semibold rounded-xl border border-rose-200 transition-colors">
              <XCircle class="w-4 h-4" /> Отклонить
            </button>
          </div>
        </template>

        <!-- Панель одобрения (КС) -->
        <div v-if="panel === 'approve'" class="bg-emerald-50 rounded-xl border border-emerald-200 p-5 mt-4">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold text-emerald-800 flex items-center gap-2">
              <CheckCircle2 class="w-4 h-4" /> Одобрение
            </h3>
            <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
          </div>

          <!-- Стоимость СДЭК -->
          <div class="mb-4 p-3 rounded-lg border" :class="costLoading ? 'border-slate-200 bg-slate-50' : estimatedCost ? 'border-emerald-300 bg-emerald-100' : 'border-slate-200 bg-white'">
            <p class="text-xs text-slate-500 mb-1">Расчётная стоимость доставки СДЭК</p>
            <div v-if="costLoading" class="flex items-center gap-2 text-slate-500 text-sm">
              <Loader2 class="w-3.5 h-3.5 animate-spin" /> Запрашиваем у СДЭК...
            </div>
            <p v-else-if="estimatedCost" class="text-xl font-bold text-emerald-800">{{ fmtCost(estimatedCost) }}</p>
            <p v-else class="text-sm text-slate-400 italic">Не удалось рассчитать</p>
          </div>

          <div class="space-y-3">
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">SRID возврата (если найден)</label>
              <input v-model="approveForm.matched_srid" type="text" placeholder="WB-12345..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none" />
            </div>
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">Комментарий для склада</label>
              <textarea v-model="approveForm.moderator_comment" rows="2"
                placeholder="Проверено, отправить срочно / стандартно..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none resize-none" />
            </div>
            <div class="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
              <Info class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              После одобрения заявка появится у менеджера склада для создания заказа СДЭК
            </div>
            <button @click="doApprove" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <CheckCircle2 v-else class="w-3.5 h-3.5" /> Одобрить и передать на склад
            </button>
          </div>
        </div>

        <!-- Панель отклонения (КС) -->
        <div v-if="panel === 'reject'" class="bg-rose-50 rounded-xl border border-rose-200 p-5 mt-4">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold text-rose-700 flex items-center gap-2"><XCircle class="w-4 h-4" /> Отклонение</h3>
            <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">Причина <span class="text-rose-500">*</span></label>
              <textarea v-model="rejectForm.moderator_comment" rows="3" placeholder="Возврат не подтверждён / дубль заявки..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-rose-400 focus:outline-none resize-none" />
            </div>
            <button @click="doReject" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <XCircle v-else class="w-3.5 h-3.5" /> Отклонить
            </button>
          </div>
        </div>

        <!-- ── Панель склада (только warehouse_manager) ─────────────────────── -->
        <template v-if="userRole === 'warehouse_manager' && selected.status === 'approved'">
          <div class="bg-violet-50 rounded-xl border border-violet-200 p-4">
            <p class="text-xs font-bold text-violet-500 uppercase tracking-wider mb-2">К отправке</p>
            <p v-if="selected.moderator_comment" class="text-sm text-violet-700 mb-3 italic">«{{ selected.moderator_comment }}»</p>
            <div v-if="selected.cdek_uuid" class="mb-3 text-xs bg-violet-100 rounded-lg px-3 py-2 text-violet-700 font-mono break-all">
              СДЭК заказ создан ✓
            </div>
            <div class="flex flex-wrap gap-2">
              <button @click="downloadLabels([selected.id])"
                class="flex items-center gap-2 px-3 py-2.5 bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold rounded-xl transition-colors">
                <Printer class="w-4 h-4" /> Этикетка PDF
              </button>
              <button @click="doMarkShipped(selected.id)" :disabled="panelLoading"
                class="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
                <Loader2 v-if="panelLoading" class="w-4 h-4 animate-spin" />
                <CheckCircle2 v-else class="w-4 h-4" /> Сдано на ПВЗ
              </button>
            </div>
          </div>
        </template>

        <!-- Панель отклонения склада (скрыта — решения принимает менеджер КС) -->
        <div v-if="false && panel === 'warehouse_reject'" class="bg-rose-50 rounded-xl border border-rose-200 p-5 mt-4">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold text-rose-700 flex items-center gap-2">
              <AlertTriangle class="w-4 h-4" /> Отклонение (склад)
            </h3>
            <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">
                Причина <span class="text-rose-500">*</span>
                <span class="text-slate-400 font-normal">(видна только нам, клиенту придёт общее уведомление)</span>
              </label>
              <textarea v-model="warehouseRejectForm.rejection_reason" rows="3"
                placeholder="Например: Отсутствует деталь на складе / Деталь снята с производства..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-rose-400 focus:outline-none resize-none" />
            </div>
            <div class="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
              <Info class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              Клиенту {{ selected.customer_email ? 'автоматически придёт email' : 'нет email — потребуется позвонить' }} с предложением оформить возврат
            </div>
            <button @click="doWarehouseReject" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <XCircle v-else class="w-3.5 h-3.5" /> Отклонить и уведомить клиента
            </button>
          </div>
        </div>

      </div>
    </div>
  </div>
</template>
