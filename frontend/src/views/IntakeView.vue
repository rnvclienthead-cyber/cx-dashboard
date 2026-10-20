<template>
  <div class="flex flex-col h-full bg-slate-50">
    <div class="border-b border-slate-200 bg-white px-4 py-3 md:px-6">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="text-base font-bold text-slate-800">Приёмка</h1>
          <p class="mt-0.5 text-xs text-slate-500">Товары, пробитые сканером на складе</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button
            v-for="opt in dayOptions"
            :key="opt.value"
            type="button"
            class="rounded-full px-2.5 py-1 text-xs font-medium"
            :class="days === opt.value ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'"
            @click="days = opt.value"
          >{{ opt.label }}</button>
          <button
            type="button"
            class="ml-1 rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
            :class="{ 'animate-spin': loading }"
            @click="load"
          >
            <RefreshCw class="h-4 w-4" />
          </button>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-3 text-sm">
        <div class="rounded-xl bg-slate-50 px-3 py-2">
          <div class="text-[10px] uppercase tracking-wide text-slate-400">Принято</div>
          <div class="font-bold text-slate-800">{{ total }} шт</div>
        </div>
        <div class="rounded-xl bg-slate-50 px-3 py-2">
          <div class="text-[10px] uppercase tracking-wide text-slate-400">Артикулов</div>
          <div class="font-bold text-slate-800">{{ articles.length }}</div>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
      <p v-if="error" class="mb-4 text-sm text-rose-600">{{ error }}</p>
      <p v-else-if="loading && !items.length" class="text-sm text-slate-400">Загрузка…</p>
      <p v-else-if="!items.length" class="text-sm text-slate-400">Пока ничего не принимали.</p>

      <div v-if="articles.length" class="mb-6 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div class="border-b border-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">По артикулам</div>
        <div v-for="row in articles" :key="row.supplier_article" class="flex items-center justify-between gap-3 border-b border-slate-50 px-4 py-2 last:border-0">
          <div class="min-w-0">
            <div class="truncate text-sm font-medium text-slate-800">{{ row.supplier_article }}</div>
            <div v-if="row.product_name" class="truncate text-xs text-slate-500">{{ row.product_name }}</div>
          </div>
          <div class="shrink-0 text-sm font-bold text-emerald-700">{{ row.qty }}</div>
        </div>
      </div>

      <div v-if="items.length" class="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div class="border-b border-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Журнал</div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-sm">
            <thead class="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-400">
              <tr>
                <th class="px-4 py-2 font-medium">Когда</th>
                <th class="px-4 py-2 font-medium">Артикул</th>
                <th class="px-4 py-2 font-medium">Товар</th>
                <th class="px-4 py-2 font-medium">Штрихкод</th>
                <th class="px-4 py-2 font-medium">Площадка</th>
                <th class="px-4 py-2 font-medium">Связи</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in items" :key="row.id" class="border-t border-slate-50">
                <td class="whitespace-nowrap px-4 py-2 text-slate-500">
                  {{ formatReceivedAt(row.received_at) }}
                  <span v-if="row.scan_count > 1" class="ml-1 text-amber-600">повтор</span>
                </td>
                <td class="px-4 py-2 font-medium text-slate-800">{{ row.supplier_article || '—' }}</td>
                <td class="px-4 py-2 text-slate-600">{{ row.product_name || '—' }}</td>
                <td class="px-4 py-2 font-mono text-xs text-slate-500">{{ row.barcode || row.scan_code }}</td>
                <td class="px-4 py-2 text-slate-500">{{ mpLabel(row.marketplace) }}</td>
                <td class="px-4 py-2">
                  <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <RouterLink
                      v-if="returnTo(row)"
                      :to="returnTo(row)"
                      class="text-sky-600 hover:underline"
                    >Возврат</RouterLink>
                    <RouterLink
                      v-if="orderTo(row)"
                      :to="orderTo(row)"
                      class="text-sky-600 hover:underline"
                    >Заказ</RouterLink>
                    <span v-if="!returnTo(row) && !orderTo(row)" class="text-slate-400">нет в базе</span>
                    <span v-else-if="!returnTo(row)" class="text-slate-400">без претензии</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import { formatReceivedAt } from '../utils/returnScan.js'

const API_BASE = import.meta.env.VITE_API_URL || ''
const MP = { wb: 'WB', ym: 'Яндекс', ozon: 'Ozon' }

const dayOptions = [
  { value: 1, label: 'Сегодня' },
  { value: 7, label: '7 дней' },
  { value: 31, label: 'Месяц' },
]

const days = ref(7)
const loading = ref(false)
const error = ref('')
const total = ref(0)
const articles = ref([])
const items = ref([])

function mpLabel(mp) {
  return MP[mp] || mp || '—'
}

function returnTo(row) {
  if (!row?.has_claim || !row?.claim_id) return null
  const q = { claim: String(row.claim_id) }
  if (row.marketplace) q.mp = String(row.marketplace)
  if (row.srid) q.srid = String(row.srid)
  return { path: '/returns', query: q }
}

function orderTo(row) {
  const code = row?.srid || row?.barcode || row?.scan_code
  if (!row?.found || !code) return null
  return `/return/${encodeURIComponent(code)}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API_BASE}/api/return/intake?days=${days.value}`)
    if (!res.ok) throw new Error('load')
    const data = await res.json()
    total.value = Number(data.total || 0)
    articles.value = Array.isArray(data.articles) ? data.articles : []
    items.value = Array.isArray(data.items) ? data.items : []
  } catch {
    error.value = 'Не удалось загрузить приёмку. Проверьте интернет.'
  } finally {
    loading.value = false
  }
}

watch(days, () => { load() })
onMounted(load)
</script>
