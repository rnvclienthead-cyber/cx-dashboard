<template>
  <div class="p-6 max-w-7xl mx-auto">
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-slate-800">Возвраты</h1>
        <p class="text-sm text-slate-500 mt-0.5">Возвраты и претензии с Wildberries</p>
      </div>
    </div>

    <!-- Вкладки активные / архив -->
    <div class="flex gap-1 mb-5 bg-slate-100 rounded-lg p-1 w-fit">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        @click="setTab(tab.value)"
        :class="[
          'px-4 py-1.5 rounded-md text-sm font-medium transition-colors',
          activeTab === tab.value
            ? 'bg-white text-slate-800 shadow-sm'
            : 'text-slate-500 hover:text-slate-700'
        ]"
      >{{ tab.label }}</button>
    </div>

    <!-- Фильтры -->
    <div class="flex flex-wrap gap-3 mb-5">
      <select v-model="filterStatus" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все статусы</option>
        <option v-for="(label, val) in STATUS_MAP" :key="val" :value="val">{{ label }}</option>
      </select>

      <select v-model="filterType" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все типы</option>
        <option v-for="(label, val) in TYPE_MAP" :key="val" :value="val">{{ label }}</option>
      </select>

      <div class="ml-auto text-sm text-slate-500 self-center" v-if="total > 0">
        Найдено: <strong>{{ total }}</strong>
      </div>
    </div>

    <!-- Загрузка -->
    <div v-if="loading" class="flex justify-center py-16">
      <div class="animate-spin rounded-full h-8 w-8 border-2 border-emerald-500 border-t-transparent"></div>
    </div>

    <!-- Пусто -->
    <div v-else-if="items.length === 0" class="text-center py-16 text-slate-400">
      <PackageX class="w-10 h-10 mx-auto mb-3 opacity-30" />
      <p>Возвратов не найдено</p>
    </div>

    <!-- Список -->
    <div v-else class="space-y-3">
      <div
        v-for="item in items"
        :key="item.srid"
        class="bg-white rounded-xl border border-slate-200 p-4 hover:border-slate-300 transition-colors"
      >
        <div class="flex items-start gap-4 flex-wrap">
          <!-- Фото товара -->
          <div v-if="item.product_photo" class="shrink-0 w-[78px] h-[105px] rounded-lg overflow-hidden bg-slate-100 border border-slate-200">
            <img :src="item.product_photo" class="w-full h-full object-cover" loading="lazy" />
          </div>

          <!-- Тип + статус -->
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2 mb-1.5">
              <span class="font-medium text-slate-800 text-sm">{{ item.claim_type_label || '—' }}</span>
              <span :class="['text-xs font-semibold px-2 py-0.5 rounded-full', statusClass(item.status)]">
                {{ item.status_label || '—' }}
              </span>
            </div>

            <!-- Артикул + nm_id -->
            <div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
              <span v-if="item.supplier_article">Артикул: <strong class="text-slate-700">{{ item.supplier_article }}</strong></span>
              <span v-if="item.nm_id">nm_id: <strong class="text-slate-700">{{ item.nm_id }}</strong></span>
              <span v-if="item.srid" class="font-mono">{{ item.srid }}</span>
            </div>

            <!-- Комментарий покупателя -->
            <p v-if="item.user_comment" class="mt-2 text-sm text-slate-600 leading-snug">
              {{ item.user_comment }}
            </p>
          </div>

          <!-- Дата справа -->
          <div class="text-right flex-shrink-0">
            <div class="text-sm text-slate-500">{{ ruDate(item.claim_date) }}</div>
            <div v-if="item.order_date" class="text-xs text-slate-400 mt-0.5">
              Заказ: {{ ruDate(item.order_date) }}
            </div>
          </div>
        </div>

        <!-- Фото -->
        <div v-if="item.photos && item.photos.length" class="flex flex-wrap gap-2 mt-3">
          <a
            v-for="(photo, idx) in item.photos.slice(0, 6)"
            :key="idx"
            :href="photo"
            target="_blank"
            rel="noopener"
            class="block w-[78px] h-[105px] rounded-lg overflow-hidden border border-slate-200 hover:border-emerald-400 transition-colors flex-shrink-0"
          >
            <img :src="photo" class="w-full h-full object-cover" loading="lazy" />
          </a>
          <span v-if="item.photos.length > 6" class="self-center text-xs text-slate-400">
            +{{ item.photos.length - 6 }} фото
          </span>
        </div>
      </div>
    </div>

    <!-- Пагинация -->
    <div v-if="totalPages > 1" class="flex justify-center gap-2 mt-6">
      <button
        v-for="p in visiblePages"
        :key="p"
        @click="loadData(p)"
        :class="[
          'w-8 h-8 rounded-lg text-sm font-medium transition-colors',
          p === page
            ? 'bg-emerald-500 text-white'
            : 'bg-white border border-slate-200 text-slate-600 hover:border-emerald-400'
        ]"
      >{{ p }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { PackageX } from 'lucide-vue-next'
import { apiFetch } from '../api'

const STATUS_MAP = { '0': 'На рассмотрении', '1': 'Одобрено', '2': 'Отклонено', '3': 'Закрыто' }
const TYPE_MAP   = { '1': 'Брак', '2': 'Не то, что заказывали', '3': 'Недостача', '4': 'Другое', '5': 'Недовложение' }

const tabs = [
  { label: 'Активные', value: true },
  { label: 'Архив',    value: false },
]

const activeTab    = ref(true)
const filterStatus = ref('')
const filterType   = ref('')
const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const totalPages   = ref(1)
const loading      = ref(false)

const visiblePages = computed(() => {
  const all = []
  for (let i = 1; i <= totalPages.value; i++) all.push(i)
  if (all.length <= 9) return all
  const cur = page.value
  const tp  = totalPages.value
  const set = new Set([1, 2, cur - 1, cur, cur + 1, tp - 1, tp].filter(p => p >= 1 && p <= tp))
  return [...set].sort((a, b) => a - b)
})

function statusClass(status) {
  const s = String(status)
  if (s === '0') return 'bg-yellow-100 text-yellow-700'
  if (s === '1') return 'bg-green-100 text-green-700'
  if (s === '2') return 'bg-red-100 text-red-700'
  return 'bg-slate-100 text-slate-600'
}

function ruDate(d) {
  if (!d) return ''
  const months = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
  try {
    const dt = new Date(d)
    return `${dt.getDate()} ${months[dt.getMonth()]} ${dt.getFullYear()}`
  } catch { return String(d).slice(0, 10) }
}

async function loadData(p = 1) {
  loading.value = true
  page.value = p
  try {
    const params = new URLSearchParams({
      active: activeTab.value,
      page: p,
      per_page: 25,
    })
    if (filterStatus.value) params.set('status', filterStatus.value)
    if (filterType.value)   params.set('type', filterType.value)
    const res  = await apiFetch(`/api/v1/cx-responses/claims?${params}`)
    const data = await res.json()
    items.value      = data.items || []
    total.value      = data.total || 0
    totalPages.value = data.total_pages || 1
  } catch (e) {
    console.error('ReturnsView load error:', e)
  } finally {
    loading.value = false
  }
}

function setTab(val) {
  activeTab.value = val
  filterStatus.value = ''
  filterType.value = ''
  loadData(1)
}

onMounted(() => loadData(1))
</script>
