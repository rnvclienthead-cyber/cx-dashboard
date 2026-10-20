<script setup>
import { ref, computed, onMounted, onActivated, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch } from '../api'
import { getAuthToken, getAuthUsername } from '../authStorage'
import { onAppClick, sameQuery } from '../utils/nav'
import { usePermissionsStore } from '../stores/permissions'
import {
  Store, RefreshCw, Loader2, Search, Phone, Mail, MapPin,
  CheckSquare, CheckCircle2, Printer, Settings2, X
} from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const route = useRoute()
const router = useRouter()
const username = computed(() => getAuthUsername() || '')
const permissionsStore = usePermissionsStore()
const isAdmin = computed(() => permissionsStore.isAdmin)

const orders = ref([])
const stats = ref({ by_status: {}, new: 0, open: 0 })
const loading = ref(false)
const selected = ref(null)
const filterStatus = ref('open')
const searchQuery = ref('')
const actionLoading = ref(false)
const actionError = ref('')
const statusHistory = ref([])
const statusPanel = ref(false)
const setStatusForm = ref({ status: '', comment: '' })

const statusMeta = {
  new: { label: 'Заказ оформлен', color: 'bg-sky-100 text-sky-800' },
  paid: { label: 'Оплачен', color: 'bg-emerald-100 text-emerald-800' },
  incomplete: { label: 'Незавершённый заказ', color: 'bg-stone-100 text-stone-700' },
  packing: { label: 'Собираем', color: 'bg-amber-100 text-amber-800' },
  packed: { label: 'Собран', color: 'bg-teal-100 text-teal-800' },
  shipped: { label: 'Отправлен', color: 'bg-emerald-100 text-emerald-800' },
  in_transit: { label: 'В пути', color: 'bg-indigo-100 text-indigo-800' },
  ready_for_pickup: { label: 'Готов к выдаче', color: 'bg-violet-100 text-violet-800' },
  delivered: { label: 'Выдан', color: 'bg-slate-100 text-slate-700' },
  returned: { label: 'Возвращён', color: 'bg-rose-100 text-rose-800' },
  cancelled: { label: 'Отменён', color: 'bg-slate-100 text-slate-500' },
  cancelled_seller: { label: 'Отменён продавцом', color: 'bg-rose-100 text-rose-800' },
  cancelled_buyer: { label: 'Отменён покупателем', color: 'bg-rose-100 text-rose-700' },
}

const filters = [
  { value: 'all', label: 'Все' },
  { value: 'open', label: 'В работе' },
  { value: 'new', label: 'Заказ оформлен' },
  { value: 'paid', label: 'Оплачен' },
  { value: 'packing', label: 'Собираем' },
  { value: 'packed', label: 'Собран' },
  { value: 'shipped', label: 'Отправлен' },
  { value: 'in_transit', label: 'В пути' },
  { value: 'ready_for_pickup', label: 'Готов к выдаче' },
  { value: 'delivered', label: 'Выдан' },
  { value: 'returned', label: 'Возвращён' },
  { value: 'cancelled_seller', label: 'Отменён продавцом' },
  { value: 'cancelled_buyer', label: 'Отменён покупателем' },
  { value: 'incomplete', label: 'Незавершённый заказ' },
]

const manualStatuses = filters.filter((row) => !['all', 'open'].includes(row.value))

const carrierClass = (carrier) => {
  if (carrier === 'ozon') return 'bg-blue-100 text-blue-800'
  if (carrier === 'cdek') return 'bg-emerald-100 text-emerald-800'
  if (carrier === 'yandex') return 'bg-amber-100 text-amber-800'
  return 'bg-slate-100 text-slate-500'
}

const fmt = (value) => {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const money = (value) => `${Number(value || 0).toLocaleString('ru-RU')} ₽`

const listUrl = computed(() => {
  const params = new URLSearchParams()
  if (filterStatus.value && filterStatus.value !== 'all') params.set('status', filterStatus.value)
  const qs = params.toString()
  return `${API_BASE}/api/v1/shop/orders${qs ? `?${qs}` : ''}`
})

const filteredOrders = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return orders.value
  return orders.value.filter((row) => [
    row.customer_name, row.customer_phone, row.customer_email,
    String(row.cscart_order_id), row.pvz_address, row.pvz_name, row.shipping_city,
  ].join(' ').toLowerCase().includes(q))
})

async function load () {
  loading.value = true
  try {
    const [listRes, statsRes] = await Promise.all([
      apiFetch(listUrl.value),
      apiFetch(`${API_BASE}/api/v1/shop/orders/stats`),
    ])
    const listData = await listRes.json()
    const statsData = await statsRes.json()
    orders.value = listData.data || []
    stats.value = statsData.data || { by_status: {}, new: 0, open: 0 }
    if (selected.value) {
      const fresh = orders.value.find((row) => row.id === selected.value.id)
      if (fresh && !selected.value.items?.length) selected.value = { ...selected.value, ...fresh }
    }
  } finally {
    loading.value = false
  }
}

function listQueryFromState (extra = {}) {
  const q = { status: extra.status ?? filterStatus.value }
  const id = extra.id ?? selected.value?.id
  if (id) q.id = String(id)
  return q
}

function syncListQuery (extra = {}) {
  const next = listQueryFromState(extra)
  if (sameQuery(next, route.query, ['id', 'status']) && !route.query.q && !route.query.carrier && !route.query.req) return
  router.replace({ query: next })
}

async function loadStatusHistory (orderId) {
  statusHistory.value = []
  if (!orderId) return
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${orderId}/status-history`)
    const data = await res.json()
    statusHistory.value = data.data || []
  } catch { /* история не должна ломать карточку */ }
}

async function select (row) {
  const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${row.id}`)
  const data = await res.json()
  selected.value = data.data || row
  actionError.value = ''
  statusPanel.value = false
  syncListQuery({ id: row.id })
  loadStatusHistory(selected.value?.id)
}

function orderTo (row) {
  return { path: '/shop-orders', query: listQueryFromState({ id: row.id }) }
}

function orderHref (row) {
  return router.resolve(orderTo(row)).href
}

function applyListQuery () {
  const st = String(route.query.status || '')
  if (filters.some(f => f.value === st)) filterStatus.value = st
}

async function openFromQuery () {
  const id = Number(route.query.id)
  if (!id) return
  const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${id}`)
  const data = await res.json()
  if (data.data) {
    selected.value = data.data
    loadStatusHistory(data.data.id)
  }
}

async function runAction (fn) {
  actionLoading.value = true
  actionError.value = ''
  try {
    const order = await fn()
    if (order) selected.value = order
    await load()
  } catch (err) {
    actionError.value = err.message || 'Не получилось'
  } finally {
    actionLoading.value = false
  }
}

async function readOrder (res) {
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || data.message || 'Ошибка')
  return data.data
}

function packItem (item) {
  return runAction(async () => {
    const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${selected.value.id}/items/${item.id}/pack`, { method: 'POST' })
    return readOrder(res)
  })
}

function createShipment (item) {
  return runAction(async () => {
    const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${selected.value.id}/items/${item.id}/create-shipment`, { method: 'POST' })
    return readOrder(res)
  })
}

function openStatusPanel () {
  setStatusForm.value = { status: selected.value?.status || '', comment: '' }
  statusPanel.value = true
}

function applyManualStatus () {
  const status = setStatusForm.value.status
  if (!isAdmin.value || !status) return
  return runAction(async () => {
    const res = await apiFetch(`${API_BASE}/api/v1/shop/orders/${selected.value.id}/status`, {
      method: 'POST',
      body: JSON.stringify({
        status,
        comment: setStatusForm.value.comment || '',
        processed_by: username.value,
      }),
    })
    const order = await readOrder(res)
    statusPanel.value = false
    setStatusForm.value = { status: '', comment: '' }
    loadStatusHistory(order?.id || selected.value?.id)
    return order
  })
}

async function openPdf (path) {
  const token = getAuthToken()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    let msg = 'Не удалось открыть этикетку'
    try {
      const data = await res.json()
      if (data?.detail) msg = String(data.detail)
    } catch { /* blob */ }
    throw new Error(msg)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

function printItemLabel (item) {
  return runAction(() => openPdf(`/api/v1/shop/orders/${selected.value.id}/items/${item.id}/labels`))
}

function printOrderLabels () {
  return runAction(() => openPdf(`/api/v1/shop/orders/${selected.value.id}/labels`))
}

const canPrintOrder = computed(() => (selected.value?.items || []).some((item) => item.has_label || item.track_number || item.ozon_posting_number))

const countFor = (status) => {
  if (status === 'all') return Object.values(stats.value.by_status || {}).reduce((sum, n) => sum + Number(n || 0), 0)
  if (status === 'open') return Number(stats.value.open || 0)
  if (status === 'on_way') {
    const by = stats.value.by_status || {}
    return Number(by.shipped || 0) + Number(by.in_transit || 0) + Number(by.ready_for_pickup || 0)
  }
  return Number(stats.value.by_status?.[status] || 0)
}

watch(filterStatus, () => {
  syncListQuery()
  load()
})
watch(() => route.query.id, () => { openFromQuery() })

onMounted(async () => {
  if (!permissionsStore.loaded) await permissionsStore.load()
  applyListQuery()
  syncListQuery()
  await load()
  await openFromQuery()
})
onActivated(() => {
  applyListQuery()
  syncListQuery()
})
</script>

<template>
  <div class="flex flex-col md:flex-row h-full bg-slate-50">
    <div :class="['flex flex-col min-h-0 md:w-80 xl:w-96 md:flex-shrink-0 border-r border-slate-200 bg-white', selected ? 'hidden md:flex' : 'flex w-full']">
      <div class="p-4 border-b border-slate-100">
        <div class="flex items-start justify-between gap-2 mb-3">
          <div>
            <h1 class="flex items-center gap-2 text-base font-bold text-slate-800">
              <Store class="w-4 h-4 text-violet-600" /> Заказы сайта
            </h1>
            <p class="mt-0.5 text-[10px] text-slate-400">Продажи с lk.vidovito.com · каждая единица — отдельная посылка</p>
          </div>
          <button @click="load" :class="['flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100', loading ? 'animate-spin' : '']">
            <RefreshCw class="w-3.5 h-3.5" />
          </button>
        </div>
        <div class="relative mb-3">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input v-model="searchQuery" type="search" name="shop-orders-search" autocomplete="off"
            placeholder="Номер, имя, телефон, артикул, ПВЗ..."
            class="w-full pl-8 pr-8 py-2 text-xs rounded-lg border border-slate-200 focus:border-violet-400 focus:outline-none" />
          <button v-if="searchQuery" type="button" @click="searchQuery = ''"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <div class="flex flex-wrap gap-1">
          <button v-for="f in filters" :key="f.value" @click="filterStatus = f.value"
            :class="['px-2.5 py-1 text-xs rounded-full font-medium transition-colors',
              filterStatus === f.value ? 'bg-violet-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200']">
            {{ f.label }}
            <span v-if="countFor(f.value)" class="ml-1 opacity-70">{{ countFor(f.value) }}</span>
          </button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto">
        <div v-if="loading && !orders.length" class="flex items-center justify-center h-32 text-slate-400">
          <Loader2 class="w-5 h-5 animate-spin" />
        </div>
        <div v-else-if="!filteredOrders.length" class="text-center text-sm text-slate-400 py-12 px-4">
          Пока нет заказов с сайта
        </div>
        <a v-for="row in filteredOrders" :key="row.id"
          :href="orderHref(row)"
          @click="onAppClick($event, () => select(row))"
          :class="['w-full text-left px-4 py-3 border-b border-slate-100 no-underline text-inherit block', selected?.id === row.id ? 'bg-violet-50' : 'hover:bg-slate-50']">
          <div class="flex items-start justify-between gap-2 mb-1">
            <span class="text-sm font-medium text-slate-800 truncate">{{ row.customer_name }}</span>
            <span :class="['text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0', statusMeta[row.status]?.color]">
              {{ statusMeta[row.status]?.label || row.status }}
            </span>
          </div>
          <p class="text-xs text-slate-500 truncate mb-1">
            Заказ #{{ row.cscart_order_id }} · {{ row.item_count }} пос. · {{ money(row.total) }}
          </p>
          <div class="flex items-center gap-2 text-[10px] text-slate-400">
            <span :class="['text-[9px] font-bold px-1.5 py-0.5 rounded-full', carrierClass(row.carrier)]">
              {{ row.carrier_label }}
            </span>
            <span class="truncate">{{ row.pvz_address || row.shipping_city || 'без ПВЗ' }}</span>
            <span class="ml-auto shrink-0">{{ fmt(row.placed_at || row.created_at) }}</span>
          </div>
        </a>
      </div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto">
      <div v-if="!selected" class="h-full flex items-center justify-center text-slate-400">
        <div class="text-center px-6">
          <Store class="w-12 h-12 mx-auto mb-3 opacity-20" />
          <p class="text-sm">Выберите заказ — здесь будут покупатель, ПВЗ и посылки для отправки</p>
        </div>
      </div>
      <div v-else class="p-5 max-w-3xl">
        <button class="md:hidden mb-3 text-xs text-slate-500" @click="selected = null">← К списку</button>
        <div class="flex items-start justify-between gap-3 mb-4">
          <div>
            <h2 class="text-lg font-bold text-slate-800">Заказ #{{ selected.cscart_order_id }}</h2>
            <p class="text-xs text-slate-400 mt-0.5">
              {{ selected.cscart_status_label }} · {{ selected.paid ? 'оплачен / тест' : 'ещё не оплачен' }}
              · {{ money(selected.total) }}
            </p>
          </div>
          <div class="relative group">
            <div class="flex items-center gap-1.5">
              <span :class="['text-xs font-semibold px-2.5 py-1 rounded-full', statusMeta[selected.status]?.color]">
                {{ statusMeta[selected.status]?.label }}
              </span>
              <span v-if="statusHistory.length" class="text-xs text-slate-400">
                {{ fmt(statusHistory[statusHistory.length - 1]?.changed_at) }}
              </span>
            </div>
            <div v-if="statusHistory.length"
              class="absolute right-0 top-full mt-1.5 z-50 hidden group-hover:block bg-white border border-slate-200 rounded-xl shadow-xl p-3 min-w-[220px]">
              <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">История статусов</p>
              <ol class="relative border-l border-slate-200 ml-1.5 space-y-2">
                <li v-if="statusHistory[0]?.new_status !== 'new'" class="pl-3 relative">
                  <span class="absolute -left-[4px] top-1 w-2 h-2 rounded-full border-2 border-white bg-slate-300" />
                  <span :class="['inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded-full', statusMeta.new?.color]">Заказ оформлен</span>
                  <div class="text-[9px] text-slate-400 leading-tight">{{ fmt(selected.placed_at || selected.created_at) }}</div>
                </li>
                <li v-for="(entry, i) in statusHistory" :key="i" class="pl-3 relative">
                  <span :class="['absolute -left-[4px] top-1 w-2 h-2 rounded-full border-2 border-white',
                    i === statusHistory.length - 1 ? 'bg-slate-700' : 'bg-slate-300']" />
                  <span :class="['inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded-full', statusMeta[entry.new_status]?.color || 'bg-slate-100 text-slate-600']">
                    {{ statusMeta[entry.new_status]?.label || entry.new_status }}
                  </span>
                  <div class="text-[9px] text-slate-400 leading-tight">
                    {{ fmt(entry.changed_at) }}
                    <span v-if="entry.changed_by" class="ml-1 text-slate-500">· {{ entry.changed_by }}</span>
                  </div>
                  <p v-if="entry.comment" class="text-[9px] text-slate-500 leading-tight mt-0.5">{{ entry.comment }}</p>
                </li>
              </ol>
            </div>
          </div>
        </div>

        <div class="grid sm:grid-cols-2 gap-3 mb-4">
          <div class="rounded-xl border border-slate-200 bg-white p-4">
            <p class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">Покупатель</p>
            <p class="text-sm font-semibold text-slate-800">{{ selected.customer_name }}</p>
            <p v-if="selected.customer_company" class="text-xs text-slate-500">{{ selected.customer_company }}</p>
            <p v-if="selected.customer_phone" class="mt-2 flex items-center gap-1.5 text-sm text-slate-700">
              <Phone class="w-3.5 h-3.5 text-slate-400" />
              <a :href="'tel:' + selected.customer_phone" class="hover:text-violet-700">{{ selected.customer_phone }}</a>
            </p>
            <p v-if="selected.customer_email" class="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
              <Mail class="w-3.5 h-3.5" /> {{ selected.customer_email }}
            </p>
          </div>
          <div class="rounded-xl border border-slate-200 bg-white p-4">
            <p class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">Куда везём</p>
            <span :class="['text-[10px] font-bold px-1.5 py-0.5 rounded-full', carrierClass(selected.carrier)]">
              {{ selected.carrier_label }}
            </span>
            <p class="mt-2 flex items-start gap-1.5 text-sm text-slate-800">
              <MapPin class="w-3.5 h-3.5 mt-0.5 text-slate-400 shrink-0" />
              <span>{{ selected.pvz_address || selected.pvz_name || [selected.shipping_city, selected.shipping_address].filter(Boolean).join(', ') || 'Пункт выдачи не выбран' }}</span>
            </p>
            <p v-if="selected.delivery_time" class="text-xs text-slate-500 mt-1">{{ selected.delivery_time }}</p>
            <p v-if="selected.shipping_method" class="text-[11px] text-slate-400 mt-2">В CS-Cart: {{ selected.shipping_method }}</p>
            <p v-if="!selected.has_pvz" class="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5">
              Нет ПВЗ Ozon / СДЭК / Яндекс — отправление из CX создать нельзя, только собрать заказ.
            </p>
          </div>
        </div>

        <div v-if="selected.notes" class="mb-4 text-xs bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-600">
          Примечание покупателя: {{ selected.notes }}
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-4 mb-4">
          <p class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-3">Посылки</p>
          <div v-for="item in selected.items" :key="item.id" class="border border-slate-100 rounded-xl p-3 mb-2 last:mb-0">
            <div class="flex items-start justify-between gap-2">
              <div>
                <p class="text-sm font-medium text-slate-800">{{ item.name }}</p>
                <p class="text-xs text-slate-500 font-mono mt-0.5">
                  {{ item.product_code || 'без артикула' }}
                  · {{ item.length_cm }}×{{ item.width_cm }}×{{ item.height_cm }} см
                  · {{ item.weight_g }} г
                  · {{ money(item.price) }}
                </p>
                <p v-if="item.track_number" class="text-xs font-mono text-emerald-700 mt-1">Трек: {{ item.track_number }}</p>
                <p v-if="item.ozon_posting_number" class="text-xs font-mono text-slate-600 mt-0.5">Отправление Ozon: {{ item.ozon_posting_number }}</p>
                <p v-if="item.ozon_order_number" class="text-xs font-mono text-slate-600 mt-0.5">Заказ Ozon: {{ item.ozon_order_number }}</p>
                <a v-if="item.tracking_url" :href="item.tracking_url" target="_blank" rel="noopener" class="inline-block text-xs text-violet-700 hover:underline mt-0.5">Отследить</a>
                <p v-if="item.error" class="text-xs text-rose-600 mt-1">{{ item.error }}</p>
              </div>
              <span :class="['text-[10px] font-semibold px-2 py-0.5 rounded-full', statusMeta[item.status]?.color]">
                {{ item.status_label }}
              </span>
            </div>
            <div class="flex flex-wrap gap-2 mt-3">
              <button v-if="selected.has_pvz && item.status !== 'shipped' && item.status !== 'in_transit' && item.status !== 'ready_for_pickup' && item.status !== 'delivered' && !item.track_number" @click="createShipment(item)" :disabled="actionLoading"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 text-white text-xs font-semibold hover:bg-violet-700 disabled:opacity-50">
                <CheckCircle2 class="w-3.5 h-3.5" /> Одобрить {{ selected.carrier_label }}
              </button>
              <button v-if="item.status === 'new' || item.status === 'packing'" @click="packItem(item)" :disabled="actionLoading"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-600 text-white text-xs font-semibold hover:bg-teal-700 disabled:opacity-50">
                <CheckSquare class="w-3.5 h-3.5" /> Собрана
              </button>
              <button v-if="item.has_label || item.track_number || item.ozon_posting_number" @click="printItemLabel(item)" :disabled="actionLoading"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                <Printer class="w-3.5 h-3.5" /> Этикетка
              </button>
            </div>
          </div>
        </div>

        <p v-if="actionError" class="text-xs text-rose-600 mb-3">{{ actionError }}</p>
        <div class="flex flex-wrap gap-2">
          <button v-if="canPrintOrder" @click="printOrderLabels" :disabled="actionLoading"
            class="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl border border-slate-200 bg-white text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <Printer class="w-4 h-4" /> Этикетки заказа
          </button>
        </div>
        <p class="mt-3 text-[11px] text-slate-400">
          Сначала одобрить отправку в Ozon, СДЭК или Яндекс, потом отметить посылку собранной. Дальше статус ставит служба доставки, трек уходит на сайт.
        </p>

        <div v-if="isAdmin" class="mt-6 pt-4 border-t border-slate-200">
          <button v-if="!statusPanel" @click="openStatusPanel"
            class="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-1.5">
            <Settings2 class="w-3.5 h-3.5" /> Изменить статус вручную
          </button>
          <div v-else class="bg-slate-50 rounded-xl border border-slate-200 p-4">
            <div class="flex items-center justify-between mb-3">
              <p class="text-xs font-bold text-slate-500 uppercase tracking-wider">Изменить статус</p>
              <button @click="statusPanel = false" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
            </div>
            <div class="space-y-3">
              <div class="flex flex-wrap gap-1.5">
                <button v-for="opt in manualStatuses" :key="opt.value" type="button"
                  @click="setStatusForm.status = opt.value"
                  :class="['px-3 py-1.5 text-xs rounded-lg font-medium transition-colors border',
                    setStatusForm.status === opt.value
                      ? 'bg-slate-700 text-white border-slate-700'
                      : 'bg-white text-slate-500 border-slate-200 hover:border-slate-400']">
                  {{ opt.label }}
                </button>
              </div>
              <textarea v-model="setStatusForm.comment" rows="2"
                placeholder="Комментарий (необязательно)"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:border-slate-400 focus:outline-none resize-none" />
              <button @click="applyManualStatus" :disabled="!setStatusForm.status || actionLoading"
                class="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-800 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors">
                <Loader2 v-if="actionLoading" class="w-3.5 h-3.5 animate-spin" />
                Применить
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
