<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ChevronDown, ChevronUp, ChevronsUpDown } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()
const loading = ref(true)
const error = ref('')
const report = ref(null)
const expanded = ref(new Set())
const sortCol = ref('reliable_rating')
const sortDir = ref('desc')

const groupLabel = {
  all: 'Все площадки',
  wb: 'Wildberries',
  ym: 'Яндекс Маркет',
  ozon: 'Ozon',
}

const confidenceLabel = {
  top: 'Очень много',
  high: 'Много',
  mid: 'Средне',
  low: 'Мало',
}
const confidenceClass = {
  top: 'bg-blue-50 text-blue-800',
  high: 'bg-emerald-50 text-emerald-800',
  mid: 'bg-slate-100 text-slate-700',
  low: 'bg-amber-50 text-amber-800',
}

const fetchReport = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch(`/api/ratings/factories?platform=${platformStore.platform}`)
    if (!res.ok) {
      error.value = 'Не удалось загрузить рейтинг заводов'
      report.value = null
      return
    }
    report.value = await res.json()
  } catch (e) {
    error.value = 'Не удалось загрузить рейтинг заводов'
    report.value = null
  } finally {
    loading.value = false
  }
}

onMounted(fetchReport)
watch(() => platformStore.platform, fetchReport)

const setSort = (col) => {
  if (sortCol.value === col) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortCol.value = col; sortDir.value = 'desc' }
}

const sortRows = (rows, col, dir) => [...rows].sort((a, b) => {
  const v = a[col] ?? 0
  const w = b[col] ?? 0
  if (typeof v === 'string') return dir === 'asc' ? v.localeCompare(w, 'ru') : w.localeCompare(v, 'ru')
  return dir === 'asc' ? v - w : w - v
})

const groups = computed(() => report.value?.groups || [])

const sortedFactories = (group) => sortRows(group?.factories || [], sortCol.value, sortDir.value)

const rowKey = (platform, name) => `${platform}|${name}`

const toggle = (platform, name) => {
  const id = rowKey(platform, name)
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

const ratingColor = (r) => r >= 4.5 ? 'text-green-600' : r >= 4.0 ? 'text-lime-600' : r >= 3.5 ? 'text-yellow-600' : 'text-red-600'
const fmt = (n) => (Number(n) || 0).toLocaleString('ru-RU')
const sortIcon = (col) => sortCol.value === col ? (sortDir.value === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
</script>

<template>
  <div class="space-y-6">
    <div class="bg-white rounded-3xl border border-slate-200 shadow-sm p-6">
      <h2 class="text-base font-black text-slate-800 flex items-center gap-3">
        <span class="w-2.5 h-6 bg-blue-600 rounded-full"></span>
        Средняя оценка по заводу
      </h2>
      <p class="text-sm text-slate-600 leading-relaxed mt-3 max-w-3xl">
        Средняя — это все отзывы товаров завода вместе: товар с 1000 оценок влияет сильнее, чем товар с 50.
        Надёжная оценка чуть ниже средней, если отзывов мало. Поэтому 4,50 при 1000 оценках
        надёжнее, чем 4,70 при 50: вторая цифра легко оказалась случайной.
        Пометка рядом: до 1 000 — мало, до 10 000 — средне, до 100 000 — много, дальше — очень много.
        <template v-if="platformStore.platform === 'all'">
          Сейчас это общая цифра: отзывы Wildberries, Яндекс Маркета и Ozon складываются.
          У Ozon их больше всего, поэтому он сильнее влияет на оценку.
        </template>
        <template v-if="platformStore.platform === 'wb' || platformStore.platform === 'all'">
          На Wildberries сюда также входят отзывы товаров с двух заводов, если отзыв удалось отнести к поставке.
        </template>
      </p>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-400 font-bold animate-pulse">Считаем заводы...</div>
    <div v-else-if="error" class="bg-white rounded-3xl border border-slate-200 p-10 text-center text-sm text-slate-500">{{ error }}</div>

    <template v-else-if="report">
      <div v-for="group in groups" :key="group.platform" class="space-y-6">
      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div v-if="groups.length > 1 || group.as_of" class="px-6 py-4 border-b border-slate-100 flex items-center justify-between gap-3">
          <h3 class="text-base font-black text-slate-800">{{ groupLabel[group.platform] || group.platform }}</h3>
          <span v-if="group.as_of" class="text-sm text-slate-500">на {{ group.as_of.split('-').reverse().join('.') }}</span>
        </div>
        <div v-if="!sortedFactories(group).length" class="py-10 text-center text-sm text-slate-400">
          Нет товаров, которые можно отнести к одному заводу.
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wider select-none">
                <th class="px-5 py-3 text-left font-bold">
                  <button type="button" class="inline-flex items-center gap-1 min-h-[44px] hover:text-slate-800" @click="setSort('factory_name')">
                    Завод <component :is="sortIcon('factory_name')" class="w-3.5 h-3.5 opacity-50" />
                  </button>
                </th>
                <th class="px-4 py-3 text-center font-bold">
                  <button type="button" class="inline-flex items-center justify-center gap-1 min-h-[44px] hover:text-slate-800" @click="setSort('average_rating')">
                    Средняя <component :is="sortIcon('average_rating')" class="w-3.5 h-3.5 opacity-50" />
                  </button>
                </th>
                <th class="px-4 py-3 text-center font-bold">
                  <button type="button" class="inline-flex items-center justify-center gap-1 min-h-[44px] hover:text-slate-800" @click="setSort('reliable_rating')">
                    Надёжная <component :is="sortIcon('reliable_rating')" class="w-3.5 h-3.5 opacity-50" />
                  </button>
                  <div class="text-xs font-normal normal-case tracking-normal text-slate-400">с учётом числа оценок</div>
                </th>
                <th class="px-4 py-3 text-center font-bold">
                  <button type="button" class="inline-flex items-center justify-center gap-1 min-h-[44px] hover:text-slate-800" @click="setSort('review_count')">
                    Оценок <component :is="sortIcon('review_count')" class="w-3.5 h-3.5 opacity-50" />
                  </button>
                </th>
                <th class="px-4 py-3 text-center font-bold">
                  <button type="button" class="inline-flex items-center justify-center gap-1 min-h-[44px] hover:text-slate-800" @click="setSort('sku_count')">
                    Товаров <component :is="sortIcon('sku_count')" class="w-3.5 h-3.5 opacity-50" />
                  </button>
                </th>
                <th class="px-4 py-3 text-center font-bold">Надёжность</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in sortedFactories(group)" :key="row.factory_name">
                <tr class="border-t border-slate-100 hover:bg-slate-50">
                  <td class="px-5 py-1 font-bold text-slate-800">
                    <button type="button" class="inline-flex items-center gap-2 min-h-[44px] text-left" :aria-expanded="expanded.has(rowKey(group.platform, row.factory_name))" @click="toggle(group.platform, row.factory_name)">
                      <ChevronDown class="w-4 h-4 text-slate-400 transition-transform" :class="expanded.has(rowKey(group.platform, row.factory_name)) ? 'rotate-0' : '-rotate-90'" />
                      {{ row.factory_name }}
                    </button>
                  </td>
                  <td class="px-4 py-3 text-center tabular-nums">
                    <span :class="['text-lg font-black', ratingColor(row.average_rating)]">{{ row.average_rating.toFixed(2) }}</span>
                  </td>
                  <td class="px-4 py-3 text-center tabular-nums font-bold text-slate-700">{{ row.reliable_rating.toFixed(2) }}</td>
                  <td class="px-4 py-3 text-center tabular-nums font-bold text-slate-700">{{ fmt(row.review_count) }}</td>
                  <td class="px-4 py-3 text-center tabular-nums text-slate-600">{{ fmt(row.sku_count) }}</td>
                  <td class="px-4 py-3 text-center">
                    <span :class="['inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold', confidenceClass[row.confidence]]">{{ confidenceLabel[row.confidence] }}</span>
                  </td>
                </tr>
                <tr v-if="expanded.has(rowKey(group.platform, row.factory_name))" class="bg-slate-50">
                  <td colspan="6" class="px-5 pb-4">
                    <table class="w-full text-sm">
                      <thead>
                        <tr class="text-xs text-slate-400">
                          <th class="py-2 text-left font-bold">Артикул</th>
                          <th class="py-2 text-center font-bold">Оценка</th>
                          <th class="py-2 text-center font-bold">Оценок</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="product in row.products" :key="product.supplier_article + (product.partial ? '-part' : '')" class="border-t border-slate-200">
                          <td class="py-2 font-semibold text-slate-700">
                            {{ product.supplier_article }}
                            <span v-if="product.partial" class="ml-2 text-xs font-bold text-slate-500">по привязанным отзывам</span>
                          </td>
                          <td class="py-2 text-center tabular-nums font-bold" :class="ratingColor(product.average_rating)">{{ product.average_rating.toFixed(2) }}</td>
                          <td class="py-2 text-center tabular-nums">{{ fmt(product.review_count) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-5 border-b border-slate-100">
          <h3 class="text-base font-black text-slate-800">Не вошли в рейтинг: товар делают на двух заводах</h3>
          <p class="text-sm text-slate-600 leading-relaxed mt-2 max-w-3xl">
            У этих товаров одна оценка на карточке, а заводов два, поэтому карточка целиком в среднюю не входит.
            <template v-if="group.platform === 'wb'">
              В среднюю завода попала только та часть отзывов, которую удалось отнести к поставке.
            </template>
          </p>
        </div>
        <div v-if="!group.shared.length" class="py-8 text-center text-sm text-slate-400">Таких товаров нет.</div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wider">
                <th class="px-5 py-3 text-left font-bold">Артикул</th>
                <th class="px-4 py-3 text-center font-bold">Оценка</th>
                <th class="px-4 py-3 text-center font-bold">Оценок</th>
                <th class="px-4 py-3 text-left font-bold">Заводы</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50">
              <tr v-for="row in group.shared" :key="row.supplier_article" class="hover:bg-slate-50">
                <td class="px-5 py-3 font-bold text-slate-800">{{ row.supplier_article }}</td>
                <td class="px-4 py-3 text-center tabular-nums font-black" :class="ratingColor(row.average_rating)">{{ row.average_rating.toFixed(2) }}</td>
                <td class="px-4 py-3 text-center tabular-nums font-bold text-slate-700">{{ fmt(row.review_count) }}</td>
                <td class="px-4 py-3 text-slate-600">{{ row.factories.join(', ') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p v-if="group.shared_only_factories?.length" class="text-sm text-slate-600">
        Своей строки нет у {{ group.shared_only_factories.join(', ') }}: все их товары делают ещё и на другом заводе.
      </p>
      <p v-if="group.unassigned_sku_count" class="text-sm text-slate-500">
        Ещё {{ fmt(group.unassigned_sku_count) }} товаров без завода
        ({{ fmt(group.unassigned_review_count) }} оценок) в таблицу не попали.
      </p>
      </div>
    </template>
  </div>
</template>
