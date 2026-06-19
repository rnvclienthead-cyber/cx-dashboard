<script setup>
import { ref, onMounted, computed, nextTick, watch } from 'vue'
import {
  MessageSquare, TrendingUp, TrendingDown, Minus,
  HelpCircle, AlertTriangle, BarChart2, Package,
  ChevronDown, ChevronUp, Star
} from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()
const loading    = ref(true)
const data       = ref(null)
const days       = ref(30)
const viewMode   = ref('count')   // 'count' | 'share'

// Chart refs
const reviewTrendChart   = ref(null)
const questionTrendChart = ref(null)

// ── Константы цветов ─────────────────────────────────────────────────────────

const CAT_COLORS = {
  'Конструкция': '#e11d48',
  'Эргономика':  '#f97316',
  'Функционал':  '#eab308',
  'Сборка':      '#3b82f6',
  'Ожидания':    '#8b5cf6',
  'Прочее':      '#94a3b8',
}

const TOPIC_COLORS = {
  'Доставка':       '#06b6d4',
  'Размеры':        '#10b981',
  'Качество':       '#f97316',
  'Цвет':           '#a855f7',
  'Комплектация':   '#6366f1',
  'Сборка':         '#3b82f6',
  'Возврат/Обмен':  '#ef4444',
  'Описание/Фото':  '#84cc16',
  'Другое':         '#94a3b8',
}

// ── Загрузка данных ──────────────────────────────────────────────────────────

const loadData = async () => {
  loading.value = true
  data.value = null
  try {
    const res = await apiFetch(
      `/api/v1/voc/trends?platform=${platformStore.platform}&days=${days.value}`
    )
    data.value = await res.json()
  } catch (e) {
    console.error('VOC trends error:', e)
  } finally {
    loading.value = false
    await nextTick()
    renderCharts()
  }
}

watch(() => platformStore.platform, loadData)
watch(days, loadData)
watch(viewMode, () => nextTick(() => renderCharts()))

// ── KPI computed ─────────────────────────────────────────────────────────────

const topProblem = computed(() => data.value?.review_categories?.[0] ?? null)
const fastestGrowing = computed(() => {
  if (!data.value?.review_categories?.length) return null
  return [...data.value.review_categories]
    .filter(c => c.share_delta > 0)
    .sort((a, b) => b.share_delta - a.share_delta)[0] ?? null
})
const topQuestion = computed(() => data.value?.question_topics?.[0] ?? null)

// ── Форматирование ────────────────────────────────────────────────────────────

const pct = (v) => `${Math.round((v || 0) * 100)}%`
const delta = (v) => {
  if (!v) return null
  const sign = v > 0 ? '+' : ''
  return `${sign}${Math.round(v * 100)} п.п.`
}

// ── Построение графиков ───────────────────────────────────────────────────────

const renderCharts = () => {
  if (!data.value) return
  renderTrendChart(
    reviewTrendChart.value,
    data.value.review_categories,
    data.value.dates,
    CAT_COLORS,
    'name',
    data.value.totals_daily
  )
  renderTrendChart(
    questionTrendChart.value,
    data.value.question_topics,
    data.value.dates,
    TOPIC_COLORS,
    'topic',
    data.value.q_totals_daily
  )
}

const renderTrendChart = (el, items, dates, colorMap, nameKey, totalsDaily) => {
  if (!el || !items?.length || !dates?.length) return

  const traces = items.map(item => {
    const name  = item[nameKey]
    const color = colorMap[name] || '#94a3b8'
    let yValues

    if (viewMode.value === 'share') {
      yValues = dates.map(d => {
        const total = totalsDaily[d] || 0
        return total ? Math.round((item.daily[d] || 0) / total * 100) : 0
      })
    } else {
      yValues = dates.map(d => item.daily[d] || 0)
    }

    return {
      x: dates,
      y: yValues,
      name,
      type: 'scatter',
      mode: 'lines',
      line: { color, width: 2.5, shape: 'spline', smoothing: 0.8 },
      fill: 'tozeroy',
      fillcolor: color + '18',
      hovertemplate: `<b>${name}</b><br>%{x}<br>${viewMode.value === 'share' ? '%{y}%' : '%{y} упоминаний'}<extra></extra>`,
    }
  })

  Plotly.newPlot(el, traces, {
    margin:      { t: 10, l: 45, r: 20, b: 40 },
    paper_bgcolor: 'transparent',
    plot_bgcolor:  'transparent',
    showlegend:  true,
    legend: { orientation: 'h', y: -0.35, font: { size: 11 } },
    xaxis: {
      showgrid: false,
      tickformat: '%d.%m',
      tickfont: { size: 10, color: '#94a3b8' },
    },
    yaxis: {
      showgrid: true,
      gridcolor: '#f1f5f9',
      tickfont: { size: 10, color: '#94a3b8' },
      ticksuffix: viewMode.value === 'share' ? '%' : '',
      zeroline: false,
    },
  }, { displayModeBar: false, responsive: true })
}

onMounted(loadData)
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen font-sans max-w-[1600px] text-slate-800">

    <!-- ── Заголовок ── -->
    <div class="flex items-center justify-between mb-6 border-b border-slate-200 pb-5">
      <div class="flex items-center gap-4">
        <div class="p-3 bg-violet-600 text-white rounded-xl shadow-lg shadow-violet-200/50">
          <MessageSquare class="w-6 h-6" />
        </div>
        <div>
          <h1 class="text-xl font-black tracking-tight text-slate-900">Voice of Customer (VOC)</h1>
          <p class="text-sm text-slate-500 font-medium">Проблемы, вопросы и динамика обратной связи</p>
        </div>
      </div>

      <!-- Период + режим -->
      <div class="flex items-center gap-3">
        <div class="flex bg-white border border-slate-200 rounded-xl p-1 shadow-sm">
          <button v-for="d in [7,14,30,90]" :key="d"
            @click="days = d"
            :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all',
              days === d ? 'bg-violet-600 text-white shadow-sm' : 'text-slate-500 hover:text-slate-700']">
            {{ d }}д
          </button>
        </div>
        <div class="flex bg-white border border-slate-200 rounded-xl p-1 shadow-sm">
          <button @click="viewMode='count'"
            :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all',
              viewMode==='count' ? 'bg-slate-800 text-white shadow-sm' : 'text-slate-500 hover:text-slate-700']">
            Штуки
          </button>
          <button @click="viewMode='share'"
            :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all',
              viewMode==='share' ? 'bg-slate-800 text-white shadow-sm' : 'text-slate-500 hover:text-slate-700']">
            Доля %
          </button>
        </div>
      </div>
    </div>

    <!-- ── Загрузка ── -->
    <div v-if="loading" class="text-center py-24 text-slate-400 font-bold animate-pulse">
      Анализируем обратную связь...
    </div>

    <div v-else-if="!data" class="text-center py-24 text-slate-400 font-bold">
      Нет данных — проверьте подключение
    </div>

    <div v-else class="space-y-6">

      <!-- ── KPI полоса ── -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">

        <!-- Отзывов за период -->
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-5 flex items-center gap-4">
          <div class="p-3 bg-violet-50 rounded-xl text-violet-600"><BarChart2 class="w-5 h-5"/></div>
          <div>
            <div class="text-[10px] uppercase font-bold tracking-wider text-slate-400 mb-0.5">Отзывов (с тегами)</div>
            <div class="text-2xl font-black text-slate-900">{{ data.total_tagged.toLocaleString('ru') }}</div>
            <div class="text-[11px] text-slate-400 mt-0.5">за {{ days }} дней</div>
          </div>
        </div>

        <!-- Вопросов -->
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-5 flex items-center gap-4">
          <div class="p-3 bg-cyan-50 rounded-xl text-cyan-600"><HelpCircle class="w-5 h-5"/></div>
          <div>
            <div class="text-[10px] uppercase font-bold tracking-wider text-slate-400 mb-0.5">Вопросов</div>
            <div class="text-2xl font-black text-slate-900">{{ data.total_questions.toLocaleString('ru') }}</div>
            <div class="text-[11px] text-slate-400 mt-0.5">за {{ days }} дней</div>
          </div>
        </div>

        <!-- Топ-проблема -->
        <div class="bg-white rounded-2xl border border-rose-200 shadow-sm p-5 flex items-center gap-4">
          <div class="p-3 bg-rose-50 rounded-xl text-rose-500"><AlertTriangle class="w-5 h-5"/></div>
          <div class="min-w-0">
            <div class="text-[10px] uppercase font-bold tracking-wider text-rose-400 mb-0.5">Топ-проблема</div>
            <div class="text-base font-black text-slate-900 truncate">{{ topProblem?.name ?? '—' }}</div>
            <div v-if="topProblem" class="text-[11px] text-slate-400 mt-0.5">
              {{ topProblem.total }} упом. · {{ pct(topProblem.share) }} отзывов
            </div>
          </div>
        </div>

        <!-- Растущая проблема -->
        <div class="bg-white rounded-2xl border border-amber-200 shadow-sm p-5 flex items-center gap-4">
          <div class="p-3 bg-amber-50 rounded-xl text-amber-500"><TrendingUp class="w-5 h-5"/></div>
          <div class="min-w-0">
            <div class="text-[10px] uppercase font-bold tracking-wider text-amber-500 mb-0.5">Растёт быстрее всего</div>
            <div class="text-base font-black text-slate-900 truncate">{{ fastestGrowing?.name ?? '—' }}</div>
            <div v-if="fastestGrowing" class="text-[11px] text-amber-600 font-bold mt-0.5">
              {{ delta(fastestGrowing.share_delta) }} vs прошлый период
            </div>
          </div>
        </div>

      </div>

      <!-- ══ БЛОК ПРОБЛЕМ В ОТЗЫВАХ ══ -->
      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between">
          <h2 class="font-black text-slate-800 flex items-center gap-2">
            <span class="w-2.5 h-5 bg-rose-500 rounded-full"></span>
            Проблемы в отзывах
          </h2>
          <span class="text-xs text-slate-400">По категориям AI-тегов · {{ days }} дней</span>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-0 divide-y lg:divide-y-0 lg:divide-x divide-slate-100">

          <!-- Рейтинг проблем -->
          <div class="p-5">
            <div class="space-y-3">
              <div v-for="cat in data.review_categories" :key="cat.name" class="group">
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full flex-shrink-0"
                         :style="{ background: CAT_COLORS[cat.name] || '#94a3b8' }"></div>
                    <span class="font-bold text-sm text-slate-700">{{ cat.name }}</span>
                  </div>
                  <div class="flex items-center gap-3">
                    <!-- Дельта доли -->
                    <span v-if="cat.share_delta > 0.005"
                          class="flex items-center gap-0.5 text-[11px] font-black text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">
                      <TrendingUp class="w-3 h-3"/> {{ delta(cat.share_delta) }}
                    </span>
                    <span v-else-if="cat.share_delta < -0.005"
                          class="flex items-center gap-0.5 text-[11px] font-black text-green-600 bg-green-50 px-2 py-0.5 rounded-full">
                      <TrendingDown class="w-3 h-3"/> {{ delta(cat.share_delta) }}
                    </span>
                    <span v-else class="text-[11px] text-slate-300 px-2 py-0.5">
                      <Minus class="w-3 h-3 inline"/>
                    </span>
                    <span class="text-sm font-black text-slate-600 w-10 text-right">{{ pct(cat.share) }}</span>
                    <span class="text-xs text-slate-400 w-16 text-right">{{ cat.total.toLocaleString('ru') }} упом.</span>
                  </div>
                </div>
                <!-- Прогресс-бар -->
                <div class="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div class="h-full rounded-full transition-all duration-500"
                       :style="{ width: pct(cat.share), background: CAT_COLORS[cat.name] || '#94a3b8' }"></div>
                </div>
              </div>
            </div>

            <!-- Топ подтегов -->
            <div v-if="data.top_subtags?.length" class="mt-5 pt-4 border-t border-slate-100">
              <div class="text-[10px] uppercase font-bold text-slate-400 tracking-wider mb-3">Топ конкретных проблем</div>
              <div class="space-y-1.5">
                <div v-for="st in data.top_subtags.slice(0,10)" :key="st.tag"
                     class="flex items-center justify-between text-xs">
                  <div class="flex items-center gap-2 min-w-0">
                    <div class="w-2 h-2 rounded-full flex-shrink-0"
                         :style="{ background: CAT_COLORS[st.category] || '#94a3b8' }"></div>
                    <span class="text-slate-600 truncate font-medium">{{ st.tag }}</span>
                  </div>
                  <div class="flex items-center gap-2 flex-shrink-0 ml-2">
                    <span class="text-slate-400">{{ st.total }}</span>
                    <span class="text-slate-300 text-[10px]">{{ pct(st.share) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Динамика по дням -->
          <div class="p-5">
            <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">
              Динамика день за днём
            </div>
            <div ref="reviewTrendChart" class="w-full h-[320px]"></div>
          </div>
        </div>
      </div>

      <!-- ══ БЛОК ВОПРОСОВ ПОКУПАТЕЛЕЙ ══ -->
      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between">
          <h2 class="font-black text-slate-800 flex items-center gap-2">
            <span class="w-2.5 h-5 bg-cyan-500 rounded-full"></span>
            Вопросы покупателей
          </h2>
          <span class="text-xs text-slate-400">Авто-классификация по ключевым словам · {{ days }} дней</span>
        </div>

        <div v-if="!data.question_topics?.length" class="p-8 text-center text-sm text-slate-400">
          Нет вопросов за выбранный период
        </div>

        <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-0 divide-y lg:divide-y-0 lg:divide-x divide-slate-100">

          <!-- Рейтинг тем -->
          <div class="p-5">
            <div class="space-y-3">
              <div v-for="tp in data.question_topics" :key="tp.topic">
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full flex-shrink-0"
                         :style="{ background: TOPIC_COLORS[tp.topic] || '#94a3b8' }"></div>
                    <span class="font-bold text-sm text-slate-700">{{ tp.topic }}</span>
                  </div>
                  <div class="flex items-center gap-3">
                    <span v-if="tp.share_delta > 0.005"
                          class="flex items-center gap-0.5 text-[11px] font-black text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">
                      <TrendingUp class="w-3 h-3"/> {{ delta(tp.share_delta) }}
                    </span>
                    <span v-else-if="tp.share_delta < -0.005"
                          class="flex items-center gap-0.5 text-[11px] font-black text-green-600 bg-green-50 px-2 py-0.5 rounded-full">
                      <TrendingDown class="w-3 h-3"/> {{ delta(tp.share_delta) }}
                    </span>
                    <span v-else class="text-[11px] text-slate-300 px-2 py-0.5">
                      <Minus class="w-3 h-3 inline"/>
                    </span>
                    <span class="text-sm font-black text-slate-600 w-10 text-right">{{ pct(tp.share) }}</span>
                    <span class="text-xs text-slate-400 w-14 text-right">{{ tp.total }} вопр.</span>
                  </div>
                </div>
                <div class="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div class="h-full rounded-full transition-all duration-500"
                       :style="{ width: pct(tp.share), background: TOPIC_COLORS[tp.topic] || '#94a3b8' }"></div>
                </div>
              </div>
            </div>
          </div>

          <!-- Динамика вопросов -->
          <div class="p-5">
            <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">
              Динамика день за днём
            </div>
            <div ref="questionTrendChart" class="w-full h-[320px]"></div>
          </div>
        </div>
      </div>

      <!-- ══ РАЗБИВКА ПО ТОВАРАМ ══ -->
      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-100 bg-slate-50/60">
          <h2 class="font-black text-slate-800 flex items-center gap-2">
            <span class="w-2.5 h-5 bg-blue-500 rounded-full"></span>
            Проблемы по товарам
          </h2>
        </div>

        <div v-if="!data.sku_problems?.length" class="p-8 text-center text-sm text-slate-400">
          Нет данных по товарам за выбранный период
        </div>

        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-400 font-bold">
                <th class="px-5 py-3 text-left">Артикул</th>
                <th class="px-4 py-3 text-center">Отзывов</th>
                <th class="px-4 py-3 text-center text-rose-400">Негатив (1-3★)</th>
                <th class="px-4 py-3 text-center">% негатива</th>
                <th class="px-4 py-3 text-center">Ср. оценка</th>
                <th class="px-4 py-3">Топ-3 проблемы</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50">
              <tr v-for="sku in data.sku_problems" :key="sku.sku" class="hover:bg-slate-50 transition-colors">
                <td class="px-5 py-3 font-bold text-slate-800">{{ sku.sku }}</td>
                <td class="px-4 py-3 text-center text-slate-500 font-medium">{{ sku.total }}</td>
                <td class="px-4 py-3 text-center">
                  <span v-if="sku.negative > 0" class="font-black text-rose-500">{{ sku.negative }}</span>
                  <span v-else class="text-slate-300">—</span>
                </td>
                <td class="px-4 py-3 text-center">
                  <div class="flex items-center justify-center gap-2">
                    <div class="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div class="h-full bg-rose-400 rounded-full"
                           :style="{ width: Math.round(sku.negative / sku.total * 100) + '%' }"></div>
                    </div>
                    <span class="text-xs text-slate-500 font-medium">
                      {{ Math.round(sku.negative / sku.total * 100) }}%
                    </span>
                  </div>
                </td>
                <td class="px-4 py-3 text-center">
                  <span :class="[
                    'font-black text-sm',
                    sku.avg_rating >= 4.5 ? 'text-green-600' :
                    sku.avg_rating >= 4.0 ? 'text-lime-600' :
                    sku.avg_rating >= 3.5 ? 'text-yellow-600' : 'text-red-500'
                  ]">{{ sku.avg_rating }} ★</span>
                </td>
                <td class="px-4 py-3">
                  <div class="flex flex-wrap gap-1">
                    <span v-for="(p, i) in sku.top_problems" :key="i"
                          class="text-[11px] font-semibold px-2 py-0.5 rounded-md"
                          :style="{
                            background: (CAT_COLORS[p.tag?.split(':')[0]?.trim()] || '#94a3b8') + '20',
                            color: CAT_COLORS[p.tag?.split(':')[0]?.trim()] || '#64748b'
                          }">
                      {{ p.tag }} <span class="opacity-60">({{ p.count }})</span>
                    </span>
                    <span v-if="!sku.top_problems?.length" class="text-slate-300 text-xs">—</span>
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
