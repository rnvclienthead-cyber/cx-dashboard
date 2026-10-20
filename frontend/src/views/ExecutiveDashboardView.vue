<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  DollarSign, AlertTriangle, Star, ShieldCheck, Calendar, ChevronDown,
  ArrowUpRight, TrendingUp, TrendingDown, BarChart3, ChevronLeft, ChevronRight, X
} from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import MetricNote from '../components/MetricNote.vue'

const platformStore = usePlatformStore()
const loading = ref(true)
const dashboardData = ref(null)

// ─── Даты ────────────────────────────────────────────────────────────────────
const startDate = ref('')
const endDate   = ref('')
const showCalendarPopover = ref(false)
const todayObj      = new Date()
const calendarYear  = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const monthNames = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]
const activePeriodLabel = ref('')

const currentYear = todayObj.getFullYear()
const MONTHS_PRESETS = [
  { label: 'Январь', start: `${currentYear}-01-01`, end: `${currentYear}-01-31` },
  { label: 'Февраль', start: `${currentYear}-02-01`, end: `${currentYear}-02-28` },
  { label: 'Март', start: `${currentYear}-03-01`, end: `${currentYear}-03-31` },
  { label: 'Апрель', start: `${currentYear}-04-01`, end: `${currentYear}-04-30` },
  { label: 'Май', start: `${currentYear}-05-01`, end: `${currentYear}-05-31` },
  { label: 'Июнь', start: `${currentYear}-06-01`, end: `${currentYear}-06-30` },
  { label: 'Июль', start: `${currentYear}-07-01`, end: `${currentYear}-07-31` },
  { label: 'Август', start: `${currentYear}-08-01`, end: `${currentYear}-08-31` },
  { label: 'Сентябрь', start: `${currentYear}-09-01`, end: `${currentYear}-09-30` },
  { label: 'Октябрь', start: `${currentYear}-10-01`, end: `${currentYear}-10-31` },
  { label: 'Ноябрь', start: `${currentYear}-11-01`, end: `${currentYear}-11-30` },
  { label: 'Декабрь', start: `${currentYear}-12-01`, end: `${currentYear}-12-31` },
]
const showMonthsPanel = ref(false)

const setPeriodPreset = (type) => {
  const now = new Date()
  if (type === 'current_month') {
    const y = now.getFullYear(), m = String(now.getMonth() + 1).padStart(2,'0'), d = String(now.getDate()).padStart(2,'0')
    startDate.value = `${y}-${m}-01`
    endDate.value   = `${y}-${m}-${d}`
    activePeriodLabel.value = 'current_month'
    calendarYear.value = now.getFullYear(); calendarMonth.value = now.getMonth()
  } else if (type === 'prev_month') {
    const d = new Date(); d.setMonth(d.getMonth() - 1)
    const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2,'0')
    const lastDay = new Date(y, d.getMonth() + 1, 0).getDate()
    startDate.value = `${y}-${m}-01`
    endDate.value   = `${y}-${m}-${String(lastDay).padStart(2,'0')}`
    activePeriodLabel.value = 'prev_month'
    calendarYear.value = d.getFullYear(); calendarMonth.value = d.getMonth()
  }
  showCalendarPopover.value = false
  showMonthsPanel.value = false
}

const selectSpecificMonth = (preset) => {
  startDate.value = preset.start; endDate.value = preset.end
  activePeriodLabel.value = preset.label
  showCalendarPopover.value = false; showMonthsPanel.value = false
}

const changeCalendarMonth = (dir) => {
  calendarMonth.value += dir
  if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
  else if (calendarMonth.value < 0) { calendarMonth.value = 11; calendarYear.value-- }
}

const handleCalendarDayClick = (d) => {
  if (!d.isCurrentMonth || !d.dateStr) return
  if (!startDate.value || (startDate.value && endDate.value)) {
    startDate.value = d.dateStr; endDate.value = ''
    activePeriodLabel.value = ''
  } else {
    if (d.dateStr < startDate.value) { startDate.value = d.dateStr; endDate.value = '' }
    else { endDate.value = d.dateStr; showCalendarPopover.value = false }
  }
}

const calendarDays = computed(() => {
  const firstDay = new Date(calendarYear.value, calendarMonth.value, 1).getDay()
  const padding  = firstDay === 0 ? 6 : firstDay - 1
  const totalDays = new Date(calendarYear.value, calendarMonth.value + 1, 0).getDate()
  const days = []
  const prevTotal = new Date(calendarYear.value, calendarMonth.value, 0).getDate()
  for (let i = padding - 1; i >= 0; i--) days.push({ day: prevTotal - i, isCurrentMonth: false, dateStr: null })
  for (let i = 1; i <= totalDays; i++) {
    const mStr = String(calendarMonth.value + 1).padStart(2,'0'); const dStr = String(i).padStart(2,'0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})

const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}` : dateStr
}
const calendarDisplayLabel = computed(() => {
  if (activePeriodLabel.value === 'current_month') return 'Текущий месяц'
  if (activePeriodLabel.value === 'prev_month') return 'Прошлый месяц'
  if (activePeriodLabel.value && MONTHS_PRESETS.find(m => m.label === activePeriodLabel.value)) return activePeriodLabel.value
  if (startDate.value && endDate.value) return `${formatDateDisplay(startDate.value)} — ${formatDateDisplay(endDate.value)}`
  if (startDate.value) return `С ${formatDateDisplay(startDate.value)}...`
  return 'Выбрать период'
})

// ─── Данные ──────────────────────────────────────────────────────────────────
let _dashLoadSeq = 0
const loadDashboardData = async () => {
  if (!startDate.value || !endDate.value) return
  const seq = ++_dashLoadSeq
  loading.value = true
  dashboardData.value = null // сразу убираем старые цифры при смене периода
  skuPayload.value = null
  ecoPayload.value = null
  if (skuOpen.value) loadSkuProblems()
  if (ecoOpen.value) loadEco()
  try {
    const res = await apiFetch(`/api/v1/dashboard/executive?start_date=${startDate.value}&end_date=${endDate.value}&platform=${platformStore.platform}`)
    if (seq !== _dashLoadSeq) return // устаревший ответ после смены периода
    if (res.ok) dashboardData.value = await res.json()
  } catch (err) {
    if (seq !== _dashLoadSeq) return
    console.error("Ошибка загрузки данных дашборда:", err)
  }
  finally {
    if (seq === _dashLoadSeq) loading.value = false
  }
}

const formatMoney = (v) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v || 0)

// Тяжёлые блоки грузятся только после раскрытия.
const skuOpen = ref(false)
const ecoOpen = ref(false)
const skuPayload = ref(null)
const ecoPayload = ref(null)
const skuLoading = ref(false)
const ecoLoading = ref(false)
const skuError = ref('')
const ecoError = ref('')
let _skuSeq = 0
let _ecoSeq = 0

const dashQuery = () =>
  `start_date=${startDate.value}&end_date=${endDate.value}&platform=${platformStore.platform}`

const loadSkuProblems = async () => {
  if (!startDate.value || !endDate.value) return
  const seq = ++_skuSeq
  skuLoading.value = true
  skuError.value = ''
  try {
    const res = await apiFetch(`/api/v1/dashboard/executive/sku-problems?${dashQuery()}`)
    if (seq !== _skuSeq) return
    if (!res.ok) throw new Error('sku')
    skuPayload.value = await res.json()
  } catch (err) {
    if (seq !== _skuSeq) return
    skuError.value = 'Не удалось загрузить'
    console.error(err)
  } finally {
    if (seq === _skuSeq) skuLoading.value = false
  }
}

const loadEco = async () => {
  if (!startDate.value || !endDate.value) return
  const seq = ++_ecoSeq
  ecoLoading.value = true
  ecoError.value = ''
  try {
    const res = await apiFetch(`/api/v1/dashboard/executive/reshipment-economics?${dashQuery()}`)
    if (seq !== _ecoSeq) return
    if (!res.ok) throw new Error('eco')
    ecoPayload.value = await res.json()
  } catch (err) {
    if (seq !== _ecoSeq) return
    ecoError.value = 'Не удалось загрузить'
    console.error(err)
  } finally {
    if (seq === _ecoSeq) ecoLoading.value = false
  }
}

const toggleSku = () => {
  skuOpen.value = !skuOpen.value
  if (skuOpen.value && !skuPayload.value) loadSkuProblems()
}

const toggleEco = () => {
  ecoOpen.value = !ecoOpen.value
  if (ecoOpen.value && !ecoPayload.value) loadEco()
}

// ─── Топ SKU по типам проблем (для решения о доотправке) ─────────────────────
const selectedProblemCat = ref(1)
const problemCatTabs = computed(() => skuPayload.value?.top_sku_by_problem || [])
const activeProblemBlock = computed(() => {
  const tabs = problemCatTabs.value
  if (!tabs.length) return null
  return tabs.find(t => t.cat_id === selectedProblemCat.value) || tabs[0]
})
watch(problemCatTabs, (tabs) => {
  if (!tabs.length) return
  // По умолчанию — некомплект (1 или 2), иначе первый доступный тип
  const preferred = tabs.find(t => t.cat_id === 1) || tabs.find(t => t.cat_id === 2) || tabs[0]
  if (!tabs.find(t => t.cat_id === selectedProblemCat.value)) {
    selectedProblemCat.value = preferred.cat_id
  }
})

const skuProblemIndex = computed(() => skuPayload.value?.sku_problem_index || [])
const eco = computed(() => ecoPayload.value?.reshipment_economics || null)
const problemZoneClass = (zone) => {
  if (zone === 'high') return 'bg-rose-100 text-rose-700'
  if (zone === 'mid') return 'bg-amber-100 text-amber-700'
  return 'bg-emerald-100 text-emerald-700'
}

// ─── PPM коэффициент — переключатель периода ─────────────────────────────────
const ppmPeriod = ref('current_month')

// ─── Модальное окно ───────────────────────────────────────────────────────────
const activeModal = ref(null)

const modalConfig = {
  ppm: {
    title: 'Уровень брака (PPM)',
    route: '/production', routeLabel: 'Перейти в PPM',
    getValue: (d) => d?.metrics?.ppm ?? 0,
    getPrev:  (d) => d?.metrics?.prev_ppm ?? null,
    getDelta: (d) => d?.metrics?.ppm_delta ?? null,
    format:   (v) => `${v}%`,
    desc: 'Доля рекламаций с дефектом от заказов. Нужна, чтобы видеть, растёт ли брак.',
    invertColors: false,
  },
  csat: {
    title: 'Индекс CSAT (VOC)',
    route: '/voc', routeLabel: 'Перейти в VOC',
    getValue: (d) => d?.metrics?.csat ?? 100,
    getPrev:  (d) => d?.metrics?.prev_csat ?? null,
    getDelta: (d) => d?.metrics?.csat_delta ?? null,
    format:   (v) => `${v}%`,
    desc: 'Доля отзывов на 4 и 5 звёзд. Нужна, чтобы видеть, довольны ли покупатели.',
    invertColors: true,
  },
}

const openModal = (key) => { activeModal.value = key }
const closeModal = () => { activeModal.value = null }

const deltaClass = (delta, invert) => {
  if (delta == null) return ''
  const isGood = invert ? delta > 0 : delta < 0
  return isGood ? 'text-emerald-500' : 'text-rose-500'
}

const modalBarData = computed(() => {
  if (!activeModal.value || !dashboardData.value) return null
  const cfg = modalConfig[activeModal.value]
  const curr = cfg.getValue(dashboardData.value)
  const prev = cfg.getPrev(dashboardData.value)
  if (prev === null) return null
  const max = Math.max(curr, prev, 0.001)
  return { curr, prev, currPct: (curr / max) * 100, prevPct: (prev / max) * 100 }
})

onMounted(() => setPeriodPreset('current_month'))
watch([startDate, endDate], loadDashboardData)
watch(() => platformStore.platform, loadDashboardData)
</script>

<template>
  <div class="p-4 md:p-6 max-w-7xl mx-auto bg-slate-50/50 min-h-screen" @click="showCalendarPopover = false; showMonthsPanel = false">

    <!-- Заглушка Ozon -->
    <div v-if="platformStore.platform === 'ozon'" class="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div class="w-16 h-16 rounded-2xl bg-blue-100 flex items-center justify-center mb-5">
        <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg"><rect width="36" height="36" rx="8" fill="#005BFF"/><text x="18" y="24" text-anchor="middle" fill="white" font-weight="900" font-size="13" font-family="Arial,sans-serif">OZON</text></svg>
      </div>
      <h2 class="text-xl font-black text-slate-800 mb-2">Аналитика Ozon — в разработке</h2>
      <p class="text-sm text-slate-500 max-w-sm">Сводная аналитика по Ozon находится в разработке. Переключитесь на WB или Яндекс Маркет.</p>
    </div>

    <template v-else>
    <!-- Плашка WB -->
    <div v-if="platformStore.platform !== 'ym'" class="mb-4 flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl px-4 py-3 text-sm text-amber-800">
      <span class="text-amber-500 text-base flex-shrink-0">⚠️</span>
      <span><strong>WB:</strong> точные данные доступны только с <strong>апреля 2026 года</strong>. Более ранние периоды могут быть неполными.</span>
    </div>

    <!-- Шапка -->
    <div class="bg-white border border-slate-200 rounded-3xl p-4 md:p-6 shadow-sm mb-6 md:mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <div class="p-3 bg-slate-800 text-white rounded-2xl shadow-sm"><BarChart3 class="w-6 h-6" /></div>
        <div>
          <h1 class="text-2xl font-black text-slate-800 tracking-tight">Главный дашборд CX</h1>
          <p class="text-xs text-slate-400 mt-0.5">Сводные операционные показатели и финансовые риски на маркетплейсах</p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2" @click.stop>
        <button @click="setPeriodPreset('current_month')"
          :class="activePeriodLabel === 'current_month' ? 'bg-slate-800 text-white shadow' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'"
          class="px-3 py-2 rounded-xl text-xs font-bold transition-all">Текущий месяц</button>
        <button @click="setPeriodPreset('prev_month')"
          :class="activePeriodLabel === 'prev_month' ? 'bg-slate-800 text-white shadow' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'"
          class="px-3 py-2 rounded-xl text-xs font-bold transition-all">Прошлый месяц</button>

        <div class="relative">
          <button @click="showMonthsPanel = !showMonthsPanel; showCalendarPopover = false"
            :class="MONTHS_PRESETS.find(m => m.label === activePeriodLabel) ? 'bg-slate-800 text-white shadow' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'"
            class="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold transition-all">
            <span>{{ MONTHS_PRESETS.find(m => m.label === activePeriodLabel)?.label || 'Месяц' }}</span>
            <ChevronDown class="w-3.5 h-3.5 transition-transform" :class="{'rotate-180': showMonthsPanel}" />
          </button>
          <div v-if="showMonthsPanel" class="absolute right-0 mt-2 w-44 bg-white border border-slate-200 rounded-2xl shadow-xl z-50 p-2 grid grid-cols-2 gap-1">
            <button v-for="m in MONTHS_PRESETS" :key="m.label" @click="selectSpecificMonth(m)"
              :class="activePeriodLabel === m.label ? 'bg-slate-800 text-white' : 'hover:bg-slate-50 text-slate-700'"
              class="text-center px-2 py-1.5 rounded-xl text-xs font-semibold transition-all">{{ m.label }}</button>
          </div>
        </div>

        <div class="relative">
          <button @click="showCalendarPopover = !showCalendarPopover; showMonthsPanel = false"
            :class="!activePeriodLabel && startDate ? 'bg-slate-800 text-white shadow' : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-50 shadow-sm'"
            class="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-bold transition-all">
            <Calendar class="w-4 h-4 opacity-70" />
            <span>{{ calendarDisplayLabel }}</span>
            <ChevronDown class="w-3.5 h-3.5 opacity-60 transition-transform" :class="{'rotate-180': showCalendarPopover}" />
          </button>
          <div v-if="showCalendarPopover" class="absolute right-0 mt-2 bg-white border border-slate-100 rounded-3xl shadow-2xl z-50 p-5 min-w-[300px]">
            <div class="flex items-center justify-between mb-4">
              <button @click="changeCalendarMonth(-1)" class="p-1.5 hover:bg-slate-100 rounded-xl transition-colors"><ChevronLeft class="w-4 h-4 text-slate-500"/></button>
              <span class="text-sm font-black text-slate-800">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
              <button @click="changeCalendarMonth(1)" class="p-1.5 hover:bg-slate-100 rounded-xl transition-colors"><ChevronRight class="w-4 h-4 text-slate-500"/></button>
            </div>
            <div class="grid grid-cols-7 mb-1">
              <div v-for="d in ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']" :key="d" class="text-center text-[10px] font-bold text-slate-400 py-1">{{ d }}</div>
            </div>
            <div class="grid grid-cols-7 gap-0.5">
              <div v-for="(d, idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)"
                :class="[
                  d.isCurrentMonth ? 'cursor-pointer' : 'opacity-25 pointer-events-none',
                  d.dateStr === startDate || d.dateStr === endDate ? 'bg-slate-800 text-white shadow-md rounded-xl' : '',
                  d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-slate-100 text-slate-700 rounded-none' : '',
                  d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate && endDate) ? 'hover:bg-slate-100 text-slate-700 rounded-xl' : '',
                ]"
                class="text-center text-xs py-1.5 font-semibold transition-colors select-none">{{ d.day }}</div>
            </div>
            <p class="text-[10px] text-slate-400 text-center mt-3">
              {{ !startDate || (startDate && endDate) ? 'Кликните на начальную дату' : 'Теперь выберите конечную дату' }}
            </p>
          </div>
        </div>
      </div>
    </div>

    <!-- Карточки метрик -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">

      <router-link to="/finances" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-violet-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden no-underline text-inherit block">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-violet-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-violet-50 text-violet-600 rounded-2xl"><TrendingDown class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Выручка по отказам</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.lost_revenue) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.lost_revenue_delta != null">
            <span :class="dashboardData.metrics.lost_revenue_delta > 0 ? 'text-rose-500' : 'text-emerald-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.lost_revenue_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.lost_revenue_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <MetricNote
          how="Розничные цены товаров, по которым возврат одобрили."
          why="Видно, сколько продаж потеряли, когда согласились на возврат."
        />
      </router-link>

      <router-link to="/finances" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-emerald-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden no-underline text-inherit block">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-emerald-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-emerald-50 text-emerald-600 rounded-2xl"><ShieldCheck class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider leading-tight">Сохранённая выручка после отказов</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.saved_revenue) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.saved_revenue_delta != null">
            <span :class="dashboardData.metrics.saved_revenue_delta > 0 ? 'text-emerald-500' : 'text-rose-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.saved_revenue_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.saved_revenue_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <div class="mt-1 space-y-1.5 border-t border-slate-100 pt-3">
          <div class="flex items-baseline justify-between gap-3 text-[11px]">
            <span class="text-slate-500">Доотправка деталей <span class="text-slate-400">· {{ dashboardData?.metrics?.saved_reship_count || 0 }} заяв.</span></span>
            <span class="font-black text-slate-700 tabular-nums">{{ formatMoney(dashboardData?.metrics?.saved_reship_revenue) }}</span>
          </div>
          <div class="flex items-baseline justify-between gap-3 text-[11px]">
            <span class="text-slate-500">Прочие отказы <span class="text-slate-400">· {{ dashboardData?.metrics?.saved_other_count || 0 }} заяв.</span></span>
            <span class="font-black text-slate-700 tabular-nums">{{ formatMoney(dashboardData?.metrics?.saved_other_revenue) }}</span>
          </div>
        </div>
        <div class="mt-3">
          <MetricNote
            how="Розничные цены заявок, в которых в возврате отказали. Строки ниже — доотправка деталей и остальные отказы."
            why="Видно, сколько продаж удержали, не возвращая товар."
          />
        </div>
      </router-link>

      <router-link to="/finances" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-red-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden no-underline text-inherit block">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-red-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-red-50 text-red-600 rounded-2xl"><DollarSign class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Потери по себестоимости</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.total_loss) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.total_loss_delta != null">
            <span :class="dashboardData.metrics.total_loss_delta > 0 ? 'text-rose-500' : 'text-emerald-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.total_loss_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.total_loss_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <MetricNote
          how="Себестоимость товаров с подтверждённым заводским браком."
          why="Это прямые потери на брак — что реально «сгорело»."
        />
      </router-link>

      <router-link to="/finances" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-purple-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden no-underline text-inherit block">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-purple-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-purple-50 text-purple-600 rounded-2xl"><DollarSign class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Недополученная прибыль</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.lost_profit) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.lost_profit_delta != null">
            <span :class="dashboardData.metrics.lost_profit_delta > 0 ? 'text-rose-500' : 'text-emerald-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.lost_profit_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.lost_profit_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <MetricNote
          how="Цена продажи минус себестоимость по одобренным возвратам."
          why="Сколько маржи не заработали на этих возвратах."
        />
      </router-link>

      <div @click="openModal('ppm')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-amber-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-amber-400 transition-all"><BarChart3 class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-amber-50 text-amber-600 rounded-2xl"><AlertTriangle class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Уровень брака (PPM)</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ dashboardData?.metrics?.ppm || 0 }}% <span class="text-sm font-bold text-slate-400">от заказов</span></div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.ppm_delta != null">
            <span :class="dashboardData.metrics.ppm_delta > 0 ? 'text-rose-500' : 'text-emerald-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.ppm_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.ppm_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <MetricNote
          how="Доля рекламаций с дефектом от числа чистых заказов за период."
          why="Показывает, растёт ли брак относительно продаж."
        />
      </div>

      <div @click="openModal('csat')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-emerald-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-emerald-400 transition-all"><BarChart3 class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-emerald-50 text-emerald-600 rounded-2xl"><Star class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Индекс CSAT (VOC)</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ dashboardData?.metrics?.csat || 100 }}%</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.csat_delta != null">
            <span :class="dashboardData.metrics.csat_delta > 0 ? 'text-emerald-500' : 'text-rose-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.csat_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.csat_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <MetricNote
          how="Доля отзывов с оценкой 4 и 5 от всех отзывов за период."
          why="Насколько покупатели довольны — сигнал по товару и сервису."
        />
      </div>

    </div>

    <!-- Нижние блоки -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm md:col-span-2">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-2">
          <span class="w-2 h-4 bg-red-500 rounded-md block"></span>
          Красная зона: Топ-5 SKU по финансовым потерям
        </h3>
        <div class="mb-4">
          <MetricNote
            how="Артикулы с самой большой суммой потерь по себестоимости за период."
            why="С чего начинать разбор — какие товары съедают больше всего денег."
          />
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs border-collapse">
            <thead>
              <tr class="text-slate-400 font-bold border-b border-slate-100">
                <th class="pb-3">Артикул продавца</th>
                <th class="pb-3 text-center">Выявлено брака</th>
                <th class="pb-3 text-right">Суммарный убыток</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50 font-medium">
              <tr v-for="sku in dashboardData?.top_problem_sku" :key="sku.sku" class="hover:bg-slate-50/50">
                <td class="py-3.5 font-bold text-slate-700">{{ sku.sku }}</td>
                <td class="py-3.5 text-center text-slate-600 font-semibold">{{ sku.defects }} шт.</td>
                <td class="py-3.5 text-right text-red-600 font-black">{{ formatMoney(sku.loss) }}</td>
              </tr>
              <tr v-if="!loading && (!dashboardData?.top_problem_sku || dashboardData?.top_problem_sku.length === 0)">
                <td colspan="3" class="py-6 text-center text-slate-400">За выбранный период критических отклонений не зафиксировано.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm flex flex-col justify-between">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-2">
          <span class="w-2 h-4 bg-slate-800 rounded-md block"></span>
          Воронка данных в периоде
        </h3>
        <div class="mb-4">
          <MetricNote
            how="Чистые заказы, затем отзывы, затем рекламации с дефектом за тот же период."
            why="Видно масштаб: сколько продали, сколько оценили, сколько брака всплыло."
          />
        </div>
        <div class="space-y-4 flex-1 flex flex-col justify-center">
          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Заказы клиентов (Чистые)</span>
              <span class="text-slate-800 font-black">{{ dashboardData?.metrics?.orders_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden"><div class="bg-slate-800 h-full w-full"></div></div>
          </div>
          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Получено отзывов и оценок</span>
              <span class="text-slate-800 font-black">{{ dashboardData?.metrics?.feedbacks_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
              <div class="bg-emerald-500 h-full" :style="{ width: `${Math.min((dashboardData?.metrics?.feedbacks_count / (dashboardData?.metrics?.orders_count || 1)) * 100, 100)}%` }"></div>
            </div>
          </div>
          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Подтвержденный брак по рекламациям</span>
              <span class="text-red-600 font-black">{{ dashboardData?.metrics?.defects_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
              <div class="bg-red-500 h-full" :style="{ width: `${Math.min((dashboardData?.metrics?.defects_count / (dashboardData?.metrics?.orders_count || 1)) * 100 * 10, 100)}%` }"></div>
            </div>
          </div>
        </div>
        <p class="text-[10px] text-slate-400 border-t border-slate-100 pt-3 mt-4">Данные обновляются в реальном времени на основе API маркетплейсов и ИИ-анализа.</p>
      </div>
    </div>

    <!-- Аналитика SKU — грузится только после раскрытия -->
    <div class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
      <button type="button" class="w-full flex items-center justify-between gap-3 text-left" @click="toggleSku">
        <div>
          <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
            <span class="w-2 h-4 bg-amber-500 rounded-md block"></span>
            Аналитика проблем по SKU
          </h3>
          <p class="text-[11px] text-slate-400 mt-1">Топ по типам жалоб и индекс проблемы. Считается только после открытия.</p>
        </div>
        <span class="flex items-center gap-1.5 text-xs font-bold text-slate-500 shrink-0">
          {{ skuOpen ? 'Скрыть' : 'Показать' }}
          <ChevronDown class="w-4 h-4 transition-transform" :class="skuOpen ? 'rotate-180' : ''" />
        </span>
      </button>
      <div v-if="skuOpen" class="mt-5">
        <div v-if="skuLoading" class="py-8 text-center text-slate-400 text-sm animate-pulse">Загрузка…</div>
        <div v-else-if="skuError" class="py-8 text-center text-rose-500 text-sm">{{ skuError }}</div>
        <template v-else>
    <div class="border border-slate-100 rounded-2xl p-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div>
          <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
            <span class="w-2 h-4 bg-amber-500 rounded-md block"></span>
            Топ SKU по типам проблем
          </h3>
          <div class="mt-2">
            <MetricNote
              how="Топ артикулов внутри каждого типа жалобы за выбранный период."
              why="Помогает решить, на какие товары запускать доотправку деталей."
            />
          </div>
        </div>
        <router-link
          to="/production"
          class="text-xs font-bold text-amber-700 bg-amber-50 hover:bg-amber-100 px-3 py-2 rounded-xl transition-colors whitespace-nowrap no-underline"
        >Открыть в карте проблем →</router-link>
      </div>

      <div v-if="problemCatTabs.length" class="flex flex-wrap gap-1.5 mb-4">
        <button
          v-for="tab in problemCatTabs"
          :key="tab.cat_id"
          @click="selectedProblemCat = tab.cat_id"
          :class="selectedProblemCat === tab.cat_id
            ? ([1,2].includes(tab.cat_id) ? 'bg-amber-600 text-white shadow' : 'bg-slate-800 text-white shadow')
            : ([1,2].includes(tab.cat_id) ? 'bg-amber-50 text-amber-800 hover:bg-amber-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200')"
          class="px-2.5 py-1.5 rounded-xl text-[11px] font-bold transition-all"
        >
          {{ tab.cat_id }}. {{ tab.cat_name }}
          <span class="opacity-70 ml-1">{{ tab.total }}</span>
        </button>
      </div>

      <div v-if="activeProblemBlock" class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="text-slate-400 font-bold border-b border-slate-100">
              <th class="pb-3 pr-2">Артикул</th>
              <th class="pb-3 text-center">Проблем</th>
              <th class="pb-3 text-center">% типа</th>
              <th class="pb-3 text-center">Рейтинг</th>
              <th class="pb-3 text-center">Заказы</th>
              <th class="pb-3 text-right">Себестоимость</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-50 font-medium">
            <tr v-for="(row, idx) in activeProblemBlock.skus" :key="row.sku" class="hover:bg-slate-50/50">
              <td class="py-3.5 font-bold text-slate-700">
                <span class="text-slate-300 mr-1.5">{{ idx + 1 }}.</span>{{ row.sku }}
              </td>
              <td class="py-3.5 text-center text-slate-600 font-semibold">{{ row.defects }}</td>
              <td class="py-3.5 text-center text-slate-500">{{ row.share_pct }}%</td>
              <td class="py-3.5 text-center font-black" :class="row.rating == null ? 'text-slate-300' : (row.rating >= 4.5 ? 'text-emerald-600' : row.rating >= 4.0 ? 'text-amber-600' : 'text-rose-600')">
                {{ row.rating != null ? row.rating + ' ★' : '—' }}
              </td>
              <td class="py-3.5 text-center text-slate-600">{{ row.orders || '—' }}</td>
              <td class="py-3.5 text-right text-slate-700 font-semibold">
                {{ row.cost != null ? formatMoney(row.cost) : '—' }}
              </td>
            </tr>
          </tbody>
        </table>
        <p class="text-[10px] text-slate-400 mt-3">
          Всего по типу «{{ activeProblemBlock.cat_name }}»: <strong class="text-slate-600">{{ activeProblemBlock.total }}</strong> случаев в выбранном периоде.
          Типы 1–2 (некомплект) выделены — по ним обычно нужна доотправка.
        </p>
      </div>
      <div v-else class="py-8 text-center text-slate-400 text-sm">
        За выбранный период размеченных проблем по типам нет.
      </div>
    </div>

    <div class="mt-6 border border-slate-100 rounded-2xl p-4">
      <div class="mb-5">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
          <span class="w-2 h-4 bg-rose-500 rounded-md block"></span>
          Индекс проблемы по SKU
        </h3>
        <div class="mt-2">
          <MetricNote
            how="Сводный балл по частоте жалоб, объёму, деньгам и рейтингу артикула."
            why="Какие товары самые «горячие» по всем типам проблем сразу."
          />
        </div>
      </div>

      <!-- Памятка -->
      <div class="mb-5 grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="rounded-2xl border border-slate-100 bg-slate-50/80 p-4">
          <div class="text-[11px] font-black text-slate-700 uppercase tracking-wide mb-2">Что такое Частота</div>
          <p class="text-[12px] text-slate-600 leading-relaxed">
            Сколько жалоб приходится на <strong class="text-slate-800">1000 заказов</strong> этого товара.
            Так можно сравнивать ходовые и редкие артикулы честно: у кого жалоб больше <em>относительно продаж</em>.
          </p>
          <p class="text-[11px] text-slate-500 mt-2">
            Пример: частота 20 ≈ 2 жалобы на каждые 100 заказов.
          </p>
        </div>
        <div class="rounded-2xl border border-slate-100 bg-slate-50/80 p-4">
          <div class="text-[11px] font-black text-slate-700 uppercase tracking-wide mb-2">Что такое Индекс</div>
          <p class="text-[12px] text-slate-600 leading-relaxed">
            Оценка приоритета от <strong class="text-slate-800">0 до 100</strong>: чем выше — тем скорее товар надо разбирать
            (доотправка деталей, акт на завод, контроль качества).
          </p>
          <div class="flex flex-wrap gap-2 mt-3 text-[11px] font-bold">
            <span class="px-2 py-1 rounded-lg bg-emerald-100 text-emerald-700">&lt;30 спокойно</span>
            <span class="px-2 py-1 rounded-lg bg-amber-100 text-amber-700">30–60 смотреть</span>
            <span class="px-2 py-1 rounded-lg bg-rose-100 text-rose-700">&gt;60 срочно</span>
          </div>
        </div>
      </div>
      <div class="mb-5 rounded-2xl border border-amber-100 bg-amber-50/50 px-4 py-3 text-[11px] text-amber-900/80 leading-relaxed">
        <strong class="text-amber-900">Из чего складывается индекс:</strong>
        насколько часто жалуются (40%) + сколько жалоб в штуках (25%) + сколько это примерно стоит по себестоимости (25%) + насколько просел рейтинг (10%).
        Таблица отсортирована по индексу — сверху самые приоритетные. Показаны топ-20.
      </div>

      <div v-if="skuProblemIndex.length" class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="text-slate-400 font-bold border-b border-slate-100">
              <th class="pb-3 pr-2">Артикул</th>
              <th class="pb-3 text-center">Индекс</th>
              <th class="pb-3 text-center">Частота</th>
              <th class="pb-3 text-center">Проблем</th>
              <th class="pb-3 text-left">Ведущий тип</th>
              <th class="pb-3 text-center">Рейтинг</th>
              <th class="pb-3 text-center">Заказы</th>
              <th class="pb-3 text-right">Себестоимость</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-50 font-medium">
            <tr v-for="(row, idx) in skuProblemIndex" :key="row.sku" class="hover:bg-slate-50/50">
              <td class="py-3.5 font-bold text-slate-700">
                <span class="text-slate-300 mr-1.5">{{ idx + 1 }}.</span>{{ row.sku }}
              </td>
              <td class="py-3.5 text-center">
                <span
                  class="inline-flex items-center justify-center min-w-[2.25rem] px-2 py-0.5 rounded-lg text-[11px] font-black"
                  :class="problemZoneClass(row.problem_zone)"
                >{{ row.problem_index ?? '—' }}</span>
              </td>
              <td class="py-3.5 text-center text-slate-700 font-semibold">
                {{ row.rate_per_1000 != null ? row.rate_per_1000 : '—' }}
              </td>
              <td class="py-3.5 text-center text-slate-600 font-semibold">{{ row.defects }}</td>
              <td class="py-3.5 text-slate-500 truncate max-w-[180px]" :title="row.top_cat_name || ''">
                <span v-if="row.top_cat_id" class="text-slate-400 mr-1">{{ row.top_cat_id }}.</span>{{ row.top_cat_name || '—' }}
              </td>
              <td class="py-3.5 text-center font-black" :class="row.rating == null ? 'text-slate-300' : (row.rating >= 4.5 ? 'text-emerald-600' : row.rating >= 4.0 ? 'text-amber-600' : 'text-rose-600')">
                {{ row.rating != null ? row.rating + ' ★' : '—' }}
              </td>
              <td class="py-3.5 text-center text-slate-600">{{ row.orders || '—' }}</td>
              <td class="py-3.5 text-right text-slate-700 font-semibold">
                {{ row.cost != null ? formatMoney(row.cost) : '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-8 text-center text-slate-400 text-sm">
        За выбранный период размеченных проблем нет.
      </div>
    </div>
        </template>
      </div>
    </div>

    <!-- Воронка: Отправка деталей -->
    <div class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
      <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-1">
        <span class="w-2 h-4 bg-sky-500 rounded-md block"></span>
        Доотправка деталей
      </h3>
      <div class="mb-5">
        <MetricNote
          how="Шаги инструмента: ссылка в чат → форма → одобрение → отправка → доставка. Период — по дате создания заявки."
          why="Где клиенты отваливаются и сколько заявок реально доезжает."
        />
      </div>

      <!-- Воронка: верхний ряд — 3 основных шага -->
      <div class="grid grid-cols-3 gap-3 mb-3">
        <div class="bg-sky-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-sky-700">{{ dashboardData?.reshipment_funnel?.sent ?? '—' }}</div>
          <div class="text-xs font-bold text-sky-600 mt-1">Ссылок отправлено</div>
          <div class="text-[10px] text-slate-400 mt-0.5">Токен в WB-чат</div>
        </div>
        <div class="bg-amber-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-amber-700">{{ dashboardData?.reshipment_funnel?.form_submitted ?? '—' }}</div>
          <div class="text-xs font-bold text-amber-600 mt-1">Форм заполнено</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.sent > 0
              ? Math.round(dashboardData.reshipment_funnel.form_submitted / dashboardData.reshipment_funnel.sent * 100) + '% от ссылок'
              : '—' }}
          </div>
        </div>
        <div class="bg-violet-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-violet-700">{{ dashboardData?.reshipment_funnel?.approved ?? '—' }}</div>
          <div class="text-xs font-bold text-violet-600 mt-1">Одобрено</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.form_submitted > 0
              ? Math.round(dashboardData.reshipment_funnel.approved / dashboardData.reshipment_funnel.form_submitted * 100) + '% от заявок'
              : '—' }}
          </div>
        </div>
      </div>

      <!-- Нижний ряд — разбивка по перевозчикам + отправлено + доставлено -->
      <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-5">
        <div class="rounded-2xl p-4 text-center" style="background:#EEF3FF">
          <div class="text-2xl font-black tabular-nums" style="color:#005BFF">{{ dashboardData?.reshipment_funnel?.approved_ozon ?? '—' }}</div>
          <div class="text-xs font-bold mt-1" style="color:#005BFF">Одобрено Ozon</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.approved > 0
              ? Math.round(dashboardData.reshipment_funnel.approved_ozon / dashboardData.reshipment_funnel.approved * 100) + '% от одобренных'
              : '—' }}
          </div>
          <div class="text-[10px] font-semibold mt-1" style="color:#005BFF">
            {{ dashboardData?.reshipment_funnel?.approved_ozon_cost > 0
              ? formatMoney(dashboardData.reshipment_funnel.approved_ozon_cost)
              : '—' }}
          </div>
        </div>
        <div class="bg-blue-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-blue-700">{{ dashboardData?.reshipment_funnel?.approved_wb ?? '—' }}</div>
          <div class="text-xs font-bold text-blue-600 mt-1">Одобрено СДЭК</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.approved > 0
              ? Math.round(dashboardData.reshipment_funnel.approved_wb / dashboardData.reshipment_funnel.approved * 100) + '% от одобренных'
              : '—' }}
          </div>
          <div class="text-[10px] text-blue-500 font-semibold mt-1">
            {{ dashboardData?.reshipment_funnel?.approved_wb_cost > 0
              ? formatMoney(dashboardData.reshipment_funnel.approved_wb_cost)
              : '—' }}
          </div>
        </div>
        <div class="bg-indigo-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-indigo-700">{{ dashboardData?.reshipment_funnel?.approved_yandex ?? '—' }}</div>
          <div class="text-xs font-bold text-indigo-600 mt-1">Одобрено Яндекс</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.approved > 0
              ? Math.round(dashboardData.reshipment_funnel.approved_yandex / dashboardData.reshipment_funnel.approved * 100) + '% от одобренных'
              : '—' }}
          </div>
          <div class="text-[10px] text-indigo-500 font-semibold mt-1">
            {{ dashboardData?.reshipment_funnel?.approved_yandex_cost > 0
              ? formatMoney(dashboardData.reshipment_funnel.approved_yandex_cost)
              : '—' }}
          </div>
        </div>
        <div class="bg-orange-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-orange-700">{{ dashboardData?.reshipment_funnel?.shipped ?? '—' }}</div>
          <div class="text-xs font-bold text-orange-600 mt-1">Отправлено</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.approved > 0
              ? Math.round(dashboardData.reshipment_funnel.shipped / dashboardData.reshipment_funnel.approved * 100) + '% от одобренных'
              : '—' }}
          </div>
        </div>
        <div class="bg-emerald-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-emerald-700">{{ dashboardData?.reshipment_funnel?.delivered ?? '—' }}</div>
          <div class="text-xs font-bold text-emerald-600 mt-1">Доставлено</div>
          <div class="text-[10px] text-slate-400 mt-0.5">
            {{ dashboardData?.reshipment_funnel?.shipped > 0
              ? Math.round(dashboardData.reshipment_funnel.delivered / dashboardData.reshipment_funnel.shipped * 100) + '% от отправленных'
              : '—' }}
          </div>
        </div>
      </div>

      <!-- Затраты + Отзывы -->
      <div class="pt-4 border-t border-slate-100 grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="flex items-center gap-4 bg-slate-50 rounded-2xl p-4">
          <div class="p-3 bg-white rounded-xl shadow-sm text-slate-600 flex-shrink-0">
            <DollarSign class="w-5 h-5" />
          </div>
          <div>
            <div class="text-lg font-black text-slate-800">{{ formatMoney(dashboardData?.reshipment_funnel?.total_cost) }}</div>
            <div class="text-xs font-bold text-slate-500">Общие затраты на доставку</div>
            <div class="mt-2">
              <MetricNote
                how="Сумма доставки СДЭК + Яндекс + Ozon по одобренным заявкам за период."
                why="Сколько инструмент стоит сам по себе, без учёта сохранённых продаж."
              />
            </div>
          </div>
        </div>
        <div class="flex items-center gap-4 bg-green-50 rounded-2xl p-4">
          <div class="p-3 bg-white rounded-xl shadow-sm text-green-600 flex-shrink-0">
            <Star class="w-5 h-5" />
          </div>
          <div>
            <div class="text-lg font-black text-green-800">{{ dashboardData?.reshipment_funnel?.reviews_left ?? '—' }}</div>
            <div class="text-xs font-bold text-green-700">Отзывов обновлено / оставлено</div>
            <div class="text-[10px] text-slate-400">
              {{ dashboardData?.reshipment_funnel?.delivered > 0 && dashboardData?.reshipment_funnel?.reviews_left > 0
                ? Math.round(dashboardData.reshipment_funnel.reviews_left / dashboardData.reshipment_funnel.delivered * 100) + '% от доставленных'
                : '—' }}
            </div>
            <div class="mt-2">
              <MetricNote
                how="Отзывы, которые клиент обновил или оставил после доставки детали."
                why="Видно, отрабатывает ли доотправка на оценке товара."
              />
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- Экономика доотправок — грузится только после раскрытия -->
    <div class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
      <button type="button" class="w-full flex items-center justify-between gap-3 text-left" @click="toggleEco">
        <div>
          <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
            <span class="w-2 h-4 bg-emerald-500 rounded-md block"></span>
            Итоговая экономика доотправок
          </h3>
          <p class="text-[11px] text-slate-400 mt-1">Считается только после открытия.</p>
        </div>
        <span class="flex items-center gap-1.5 text-xs font-bold text-slate-500 shrink-0">
          {{ ecoOpen ? 'Скрыть' : 'Показать' }}
          <ChevronDown class="w-4 h-4 transition-transform" :class="ecoOpen ? 'rotate-180' : ''" />
        </span>
      </button>
      <div v-if="ecoOpen" class="mt-5">
        <div v-if="ecoLoading" class="py-8 text-center text-slate-400 text-sm animate-pulse">Загрузка…</div>
        <div v-else-if="ecoError" class="py-8 text-center text-rose-500 text-sm">{{ ecoError }}</div>
        <template v-else-if="eco?.requests_with_data > 0">
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-2 mb-5">
        <span class="text-[11px] text-slate-400 sm:ml-auto sm:text-right">
          {{ eco.requests_with_data }} фактически отправленных заявок с финданными<br>
          <span class="text-slate-300">(из {{ dashboardData.reshipment_funnel.shipped }} отправленных за выбранный период)</span>
        </span>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5">
        <div class="bg-violet-50 border border-violet-100 rounded-2xl p-4 flex items-center gap-4">
          <div class="p-3 bg-white rounded-xl shadow-sm text-violet-600 flex-shrink-0">
            <TrendingDown class="w-5 h-5" />
          </div>
          <div>
            <div class="text-2xl font-black text-violet-900 tabular-nums">{{ dashboardData.reshipment_funnel.returns_prevented }}</div>
            <div class="text-xs font-bold text-violet-700">Возвратов отменено</div>
            <div class="text-[10px] text-violet-500 mt-0.5">Справочный показатель · в расчёте выгоды не используется</div>
            <div class="mt-2">
              <MetricNote
                how="Заявки на возврат, которые закрыли доотправкой детали."
                why="Сколько возвратов инструмент снял с потока. В деньгах выгоды не участвует."
              />
            </div>
          </div>
        </div>
        <div class="bg-emerald-50 border border-emerald-100 rounded-2xl p-4 flex items-center gap-4">
          <div class="p-3 bg-white rounded-xl shadow-sm text-emerald-600 flex-shrink-0">
            <DollarSign class="w-5 h-5" />
          </div>
          <div>
            <div class="text-2xl font-black text-emerald-900 tabular-nums">{{ formatMoney(eco.saved_sales_amount) }}</div>
            <div class="text-xs font-bold text-emerald-700">Сумма продаж сохранена</div>
            <div class="text-[10px] text-emerald-500 mt-0.5">По всем фактически отправленным заявкам, найденным в финотчёте</div>
            <div class="mt-2">
              <MetricNote
                how="Розничные суммы продаж по заявкам, где деталь реально отправили."
                why="Какой объём продаж удержали доотправкой, а не возвратом."
              />
            </div>
          </div>
        </div>
      </div>

      <div :class="eco.real_benefit >= 0 ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'"
           class="border rounded-2xl p-5 mb-5">
        <div class="flex items-start justify-between gap-4">
          <div>
            <div :class="eco.real_benefit >= 0 ? 'text-emerald-600' : 'text-rose-600'"
                 class="text-[10px] font-black uppercase tracking-wider mb-1">Реальная итоговая выгода</div>
            <div :class="eco.real_benefit >= 0 ? 'text-emerald-800' : 'text-rose-800'"
                 class="text-4xl font-black tabular-nums">
              {{ eco.real_benefit >= 0 ? '+' : '' }}{{ formatMoney(eco.real_benefit) }}
            </div>
            <div :class="eco.real_benefit >= 0 ? 'text-emerald-600' : 'text-rose-600'"
                 class="text-xs mt-1">
              В среднем {{ eco.avg_real_benefit >= 0 ? '+' : '' }}{{ formatMoney(eco.avg_real_benefit) }} на доотправку
            </div>
            <div class="mt-3 max-w-md">
              <MetricNote
                how="Что получили с доотправкой минус то, что потеряли бы при возврате (себестоимость, расходы WB, обратная логистика)."
                why="Итог инструмента: выгоднее ли чинить деталью, чем принимать возврат."
              />
            </div>
          </div>
          <TrendingUp v-if="eco.real_benefit >= 0" class="w-8 h-8 text-emerald-500 flex-shrink-0" />
          <TrendingDown v-else class="w-8 h-8 text-rose-500 flex-shrink-0" />
        </div>
        <div :class="eco.real_benefit >= 0 ? 'border-emerald-200 text-emerald-800' : 'border-rose-200 text-rose-800'"
             class="mt-4 pt-3 border-t text-[11px] space-y-2">
          <details class="group">
            <summary class="flex justify-between items-center gap-4 cursor-pointer list-none rounded-lg hover:bg-white/40 py-1 transition-colors">
              <span class="font-medium">Могли потерять при возврате</span>
              <span class="flex items-center gap-2">
                <span class="font-bold tabular-nums">{{ formatMoney(-eco.counterfactual_return_loss) }}</span>
                <ChevronDown class="w-4 h-4 opacity-60 transition-transform duration-200 group-open:rotate-180" />
              </span>
            </summary>
            <div class="mt-2 rounded-xl bg-white/60 p-3 space-y-1.5"
                 :class="eco.real_benefit >= 0 ? 'border border-emerald-100' : 'border border-rose-100'">
              <div class="flex justify-between gap-4">
                <span>СС товаров, которые остались бы у клиента (2-СБ, кроме Full Black)</span>
                <span class="font-bold tabular-nums">-{{ formatMoney(eco.stay_with_client_cog) }}</span>
              </div>
              <div class="flex justify-between gap-4">
                <span>WB-расходы при возврате</span>
                <span class="font-bold tabular-nums">-{{ formatMoney(eco.total_wb_return_costs) }}</span>
              </div>
              <div class="flex justify-between gap-4">
                <span>Обратная логистика товаров, которые вернулись бы на склад</span>
                <span class="font-bold tabular-nums">-{{ formatMoney(eco.return_logistics) }}</span>
              </div>
              <div class="flex justify-between gap-4 border-t pt-2 font-black"
                   :class="eco.real_benefit >= 0 ? 'border-emerald-200' : 'border-rose-200'">
                <span>Итого возможная потеря</span>
                <span class="tabular-nums">{{ formatMoney(-eco.counterfactual_return_loss) }}</span>
              </div>
            </div>
          </details>
          <details class="group border-t pt-2"
               :class="eco.real_benefit >= 0 ? 'border-emerald-200' : 'border-rose-200'">
            <summary class="flex justify-between items-center gap-4 cursor-pointer list-none rounded-lg hover:bg-white/40 py-1 transition-colors">
              <span class="font-medium">Факт после доотправок</span>
              <span class="flex items-center gap-2">
                <span class="font-bold tabular-nums">{{ eco.net_v3 >= 0 ? '+' : '' }}{{ formatMoney(eco.net_v3) }}</span>
                <ChevronDown class="w-4 h-4 opacity-60 transition-transform duration-200 group-open:rotate-180" />
              </span>
            </summary>
            <div class="mt-2 rounded-xl bg-white/60 p-3 space-y-1.5"
                 :class="eco.real_benefit >= 0 ? 'border border-emerald-100' : 'border border-rose-100'">
              <div class="flex justify-between gap-4">
                <span>Сохранённая выплата WB (ppvz)</span>
                <span class="font-bold tabular-nums">+{{ formatMoney(eco.total_ppvz) }}</span>
              </div>
              <div class="flex justify-between gap-4">
                <span>Себестоимость товара</span>
                <span class="font-bold tabular-nums">-{{ formatMoney(eco.total_cog) }}</span>
              </div>
              <div class="flex justify-between gap-4">
                <span>Доставка деталей</span>
                <span class="font-bold tabular-nums">-{{ formatMoney(eco.total_delivery_cost) }}</span>
              </div>
              <div class="flex justify-between gap-4 border-t pt-2 font-black"
                   :class="eco.real_benefit >= 0 ? 'border-emerald-200' : 'border-rose-200'">
                <span>Итого факт после доотправок</span>
                <span class="tabular-nums">{{ eco.net_v3 >= 0 ? '+' : '' }}{{ formatMoney(eco.net_v3) }}</span>
              </div>
            </div>
          </details>
          <div class="flex justify-between gap-4 border-t pt-2 font-black"
               :class="eco.real_benefit >= 0 ? 'border-emerald-300' : 'border-rose-300'">
            <span>Выгода = разница</span>
            <span class="tabular-nums">{{ eco.real_benefit >= 0 ? '+' : '' }}{{ formatMoney(eco.real_benefit) }}</span>
          </div>
          <div class="pt-1 opacity-80">
            По всем фактически отправленным заявкам с финданными. Количество отменённых возвратов на итог не влияет.
          </div>
        </div>
      </div>

      <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">Как учтён фактический возврат</div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5">
        <div class="bg-amber-50 border border-amber-100 rounded-2xl p-4">
          <div class="text-[10px] font-bold text-amber-700 uppercase tracking-wider">2-СБ, кроме Full Black</div>
          <div class="text-2xl font-black text-amber-900 mt-1 tabular-nums">{{ eco.stay_with_client_count }} шт.</div>
          <div class="text-[11px] text-amber-700 mt-1">Остались бы у клиента · потеря себестоимости {{ formatMoney(eco.stay_with_client_cog) }}</div>
          <div class="mt-2">
            <MetricNote
              how="Товары 2-СБ (кроме Full Black): при возврате деньги клиенту, товар ему."
              why="Эту себестоимость мы бы потеряли — её учитываем в выгоде доотправки."
            />
          </div>
        </div>
        <div class="bg-sky-50 border border-sky-100 rounded-2xl p-4">
          <div class="text-[10px] font-bold text-sky-700 uppercase tracking-wider">Full Black, BO, BS и другие SKU</div>
          <div class="text-2xl font-black text-sky-900 mt-1 tabular-nums">{{ eco.return_to_stock_count }} шт.</div>
          <div class="text-[11px] text-sky-700 mt-1">Вернулись бы на склад · СС {{ formatMoney(eco.return_to_stock_cog) }} не теряем · обратная логистика {{ formatMoney(eco.return_logistics) }}</div>
          <div class="mt-2">
            <MetricNote
              how="Full Black, BO, BS и прочие: товар вернулся бы на склад."
              why="Себестоимость не теряем, но платили бы обратную логистику."
            />
          </div>
        </div>
      </div>

      <details class="group border border-slate-200 rounded-2xl mb-5 bg-slate-50/60">
        <summary class="min-h-12 px-4 py-3 flex items-center justify-between gap-3 cursor-pointer list-none rounded-2xl text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 transition-colors">
          <span>
            <span class="block text-xs font-black uppercase tracking-wider">Памятка: варианты расчёта</span>
            <span class="block text-[11px] text-slate-500 mt-0.5">Два прежних условных сценария — не участвуют в итоговой выгоде</span>
          </span>
          <ChevronDown class="w-5 h-5 text-slate-400 flex-shrink-0 transition-transform duration-200 group-open:rotate-180" />
        </summary>
        <div class="border-t border-slate-200 p-4 sm:p-5">
        <div class="bg-slate-50 rounded-2xl p-4 mb-5 text-[11px] text-slate-500 space-y-1.5">
          <div><span class="font-bold text-slate-700">Выплата WB (ppvz)</span> — сумма, которую WB перечисляет после своих комиссий за продажу.</div>
          <div><span class="font-bold text-slate-700">Себестоимость (СС)</span> — затраты на производство или закупку товара.</div>
          <div><span class="font-bold text-slate-700">WB-расходы при возврате</span> — эквайринг, штрафы и логистика. В обоих справочных вариантах это расход и он вычитается.</div>
          <div><span class="font-bold text-slate-700">Обратная логистика</span> — среднее по последним 10 возвратам этого артикула из финотчёта WB (сейчас {{ formatMoney(eco.avg_return_delivery) }}/шт по заявкам, где товар вернулся бы на склад).</div>
        </div>

        <div class="mb-5">
          <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">
            Если бы согласовывали возврат — два прежних крайних сценария по тем же {{ eco.requests_with_data }} заявкам
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div class="bg-red-50 border border-red-100 rounded-2xl p-4">
              <div class="flex items-start justify-between gap-2 mb-3">
                <div>
                  <div class="text-[10px] font-bold text-red-500 uppercase tracking-wider mb-1">Вариант 1 — деньги клиенту, товар у него</div>
                  <div class="text-3xl font-black text-red-700 tabular-nums">{{ formatMoney(-eco.loss_if_return_v1) }}</div>
                  <div class="text-[11px] text-red-500 mt-0.5">≈ {{ formatMoney(-Math.round(eco.loss_if_return_v1 / eco.requests_with_data)) }}/заявку</div>
                </div>
                <TrendingDown class="w-7 h-7 text-red-300 flex-shrink-0 mt-1" />
              </div>
              <div class="space-y-1.5 text-[12px]">
                <div class="flex justify-between items-center gap-3 py-1 border-b border-red-100">
                  <span class="text-red-600">Себестоимость товара</span>
                  <span class="font-bold text-red-700 tabular-nums">{{ formatMoney(-eco.total_cog) }}</span>
                </div>
                <div class="flex justify-between items-center gap-3 py-1">
                  <span class="text-red-600">WB-расходы при возврате</span>
                  <span class="font-bold text-red-700 tabular-nums">{{ formatMoney(-eco.total_wb_return_costs) }}</span>
                </div>
              </div>
            </div>

            <div class="bg-orange-50 border border-orange-100 rounded-2xl p-4">
              <div class="flex items-start justify-between gap-2 mb-3">
                <div>
                  <div class="text-[10px] font-bold text-orange-500 uppercase tracking-wider mb-1">Вариант 2 — деньги клиенту + товар к нам</div>
                  <div class="text-3xl font-black text-orange-700 tabular-nums">{{ formatMoney(-eco.loss_if_return_v2) }}</div>
                  <div class="text-[11px] text-orange-500 mt-0.5">≈ {{ formatMoney(-Math.round(eco.loss_if_return_v2 / eco.requests_with_data)) }}/заявку</div>
                </div>
                <TrendingDown class="w-7 h-7 text-orange-300 flex-shrink-0 mt-1" />
              </div>
              <div class="space-y-1.5 text-[12px]">
                <div class="flex justify-between items-center gap-3 py-1 border-b border-orange-100">
                  <span class="text-orange-600">Себестоимость товара</span>
                  <span class="font-bold text-orange-700 tabular-nums">{{ formatMoney(-eco.total_cog) }}</span>
                </div>
                <div class="flex justify-between items-center gap-3 py-1 border-b border-orange-100">
                  <span class="text-orange-600">WB-расходы при возврате</span>
                  <span class="font-bold text-orange-700 tabular-nums">{{ formatMoney(-eco.total_wb_return_costs) }}</span>
                </div>
                <div class="flex justify-between items-center gap-3 py-1">
                  <span class="text-orange-600">Обратная логистика всех товаров</span>
                  <span class="font-bold text-orange-700 tabular-nums">{{ formatMoney(-eco.ret_logistics) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="mb-5">
          <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">Наш результат с инструментом доотправок</div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div class="bg-slate-50 rounded-2xl p-4">
              <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Выплата WB сохранена (ppvz)</div>
              <div class="text-2xl font-black text-slate-800 tabular-nums">{{ formatMoney(eco.total_ppvz) }}</div>
              <div class="text-[11px] text-slate-400 mt-1">Выручка после комиссий WB не аннулирована возвратом</div>
            </div>
            <div class="bg-slate-50 rounded-2xl p-4">
              <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Доставка деталей</div>
              <div class="text-2xl font-black text-slate-800 tabular-nums">−{{ formatMoney(eco.total_delivery_cost) }}</div>
              <div class="text-[11px] text-slate-400 mt-1">СДЭК + Яндекс по заявкам с финданными</div>
            </div>
            <div :class="eco.net_v3 >= 0 ? 'bg-emerald-50' : 'bg-rose-50'" class="rounded-2xl p-4">
              <div :class="eco.net_v3 >= 0 ? 'text-emerald-500' : 'text-rose-500'" class="text-[10px] font-bold uppercase tracking-wider mb-1">Чистый результат (ppvz − СС − доставка)</div>
              <div :class="eco.net_v3 >= 0 ? 'text-emerald-800' : 'text-rose-700'" class="text-2xl font-black tabular-nums">
                {{ eco.net_v3 >= 0 ? '+' : '' }}{{ formatMoney(eco.net_v3) }}
              </div>
              <div class="text-[11px] text-slate-500 mt-1">СС товара и доставка вычтены из сохранённой выплаты</div>
            </div>
          </div>
        </div>

        <div class="mb-5">
          <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">Справочный результат по каждому варианту</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
              <div class="flex items-start justify-between gap-2 mb-3">
                <div>
                  <div class="text-[10px] font-bold text-emerald-600 uppercase tracking-wider mb-1">Выгода против варианта 1</div>
                  <div class="text-3xl font-black text-emerald-700 tabular-nums">{{ eco.benefit_vs_v1 >= 0 ? '+' : '' }}{{ formatMoney(eco.benefit_vs_v1) }}</div>
                  <div class="text-[12px] text-emerald-600 mt-0.5">В среднем {{ eco.avg_benefit_vs_v1 >= 0 ? '+' : '' }}{{ formatMoney(eco.avg_benefit_vs_v1) }}/заявку</div>
                </div>
                <TrendingUp class="w-7 h-7 text-emerald-400 flex-shrink-0 mt-1" />
              </div>
              <div class="bg-emerald-100 rounded-xl p-3 text-[11px] text-emerald-800 space-y-1">
                <div class="font-bold">Наш результат − результат варианта 1</div>
                <div class="border-t border-emerald-200 pt-1 tabular-nums">{{ formatMoney(eco.net_v3) }} − ({{ formatMoney(-eco.loss_if_return_v1) }})</div>
              </div>
            </div>
            <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
              <div class="flex items-start justify-between gap-2 mb-3">
                <div>
                  <div class="text-[10px] font-bold text-emerald-600 uppercase tracking-wider mb-1">Выгода против варианта 2</div>
                  <div class="text-3xl font-black text-emerald-700 tabular-nums">{{ eco.benefit_vs_v2 >= 0 ? '+' : '' }}{{ formatMoney(eco.benefit_vs_v2) }}</div>
                  <div class="text-[12px] text-emerald-600 mt-0.5">В среднем {{ eco.avg_benefit_vs_v2 >= 0 ? '+' : '' }}{{ formatMoney(eco.avg_benefit_vs_v2) }}/заявку</div>
                </div>
                <TrendingUp class="w-7 h-7 text-emerald-400 flex-shrink-0 mt-1" />
              </div>
              <div class="bg-emerald-100 rounded-xl p-3 text-[11px] text-emerald-800 space-y-1">
                <div class="font-bold">Наш результат − результат варианта 2</div>
                <div class="border-t border-emerald-200 pt-1 tabular-nums">{{ formatMoney(eco.net_v3) }} − ({{ formatMoney(-eco.loss_if_return_v2) }})</div>
              </div>
            </div>
          </div>
        </div>
        </div>
      </details>

      <div class="bg-slate-50 rounded-2xl p-4 text-[11px] text-slate-600 space-y-2">
        <div class="font-bold text-slate-700">Расчёт итога</div>
        <div class="flex justify-between gap-4">
          <span>Результат доотправок (ppvz − СС − доставка)</span>
          <span class="font-bold tabular-nums">{{ formatMoney(eco.net_v3) }}</span>
        </div>
        <div class="flex justify-between gap-4 border-t border-slate-200 pt-2">
          <span>+ Предотвращённые потери при возврате (СС оставшихся 2-СБ + WB-расходы + обратная логистика)</span>
          <span class="font-bold text-emerald-700 tabular-nums">+{{ formatMoney(eco.counterfactual_return_loss) }}</span>
        </div>
        <div class="flex justify-between gap-4 border-t border-slate-300 pt-2 font-black text-slate-900">
          <span>= Реальная выгода</span>
          <span class="tabular-nums">{{ eco.real_benefit >= 0 ? '+' : '' }}{{ formatMoney(eco.real_benefit) }}</span>
        </div>
      </div>
        </template>
        <div v-else class="py-8 text-center text-slate-400 text-sm">
          За выбранный период нет данных по отправленным заявкам.
        </div>
      </div>
    </div>

    <!-- Коэффициент реализации PPM -->
    <div class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm" @click.stop>
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
          <span class="w-2 h-4 bg-amber-500 rounded-md block"></span>
          Коэффициент реализации — Рекламационные акты
        </h3>
        <!-- Переключатель периода -->
        <div class="flex bg-slate-100 rounded-xl p-1 gap-1 self-start sm:self-auto">
          <button v-for="p in [{k:'current_month',l:'Текущий месяц'},{k:'prev_month',l:'Прошлый месяц'},{k:'all',l:'Всё время'}]" :key="p.k"
            @click="ppmPeriod = p.k"
            :class="ppmPeriod === p.k ? 'bg-white shadow text-slate-800' : 'text-slate-500 hover:text-slate-700'"
            class="px-3 py-1.5 rounded-lg text-xs font-bold transition-all">
            {{ p.l }}
          </button>
        </div>
      </div>
      <div class="mb-5">
        <MetricNote
          how="Сколько рекламационных актов ушло на завод и какая доля получила ответ, дату исправления и инвойс."
          why="Показывает, отрабатывает ли завод претензии, а не только что мы их отправили."
        />
      </div>

      <template v-if="dashboardData?.ppm_realization">
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <!-- Отправлено -->
          <div class="bg-slate-50 rounded-2xl p-4 text-center">
            <div class="text-2xl font-black text-slate-800">{{ dashboardData.ppm_realization[ppmPeriod]?.total_sent ?? '—' }}</div>
            <div class="text-xs font-bold text-slate-600 mt-1">Актов отправлено</div>
            <div class="text-[10px] text-slate-400 mt-0.5">базовый показатель</div>
          </div>
          <!-- Ответ завода -->
          <div class="rounded-2xl p-4 text-center" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_reply || 0) > 0 ? 'bg-emerald-50' : 'bg-slate-50'">
            <div class="text-2xl font-black" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_reply || 0) > 0 ? 'text-emerald-700' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_reply ?? '—' }}
            </div>
            <div class="text-[11px] font-black mt-1" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_reply || 0) > 0 ? 'text-emerald-500' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_reply_pct ?? 0 }}%
            </div>
            <div class="text-xs font-bold mt-0.5" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_reply || 0) > 0 ? 'text-emerald-600' : 'text-slate-500'">Получен ответ</div>
          </div>
          <!-- Дата исправления -->
          <div class="rounded-2xl p-4 text-center" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date || 0) > 0 ? 'bg-blue-50' : 'bg-slate-50'">
            <div class="text-2xl font-black" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date || 0) > 0 ? 'text-blue-700' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date ?? '—' }}
            </div>
            <div class="text-[11px] font-black mt-1" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date || 0) > 0 ? 'text-blue-500' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date_pct ?? 0 }}%
            </div>
            <div class="text-xs font-bold mt-0.5" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_improvement_date || 0) > 0 ? 'text-blue-600' : 'text-slate-500'">Дата исправления</div>
          </div>
          <!-- Инвойс исправления -->
          <div class="rounded-2xl p-4 text-center" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice || 0) > 0 ? 'bg-violet-50' : 'bg-slate-50'">
            <div class="text-2xl font-black" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice || 0) > 0 ? 'text-violet-700' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice ?? '—' }}
            </div>
            <div class="text-[11px] font-black mt-1" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice || 0) > 0 ? 'text-violet-500' : 'text-slate-400'">
              {{ dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice_pct ?? 0 }}%
            </div>
            <div class="text-xs font-bold mt-0.5" :class="(dashboardData.ppm_realization[ppmPeriod]?.got_correction_invoice || 0) > 0 ? 'text-violet-600' : 'text-slate-500'">Инвойс исправления</div>
          </div>
        </div>
      </template>

      <p class="text-[10px] text-slate-400 mt-4 border-t border-slate-100 pt-3">
        % считается от числа отправленных актов в выбранном периоде. <router-link to="/production" class="text-amber-600 font-bold hover:underline">Перейти в PPM →</router-link>
      </p>
    </div>
    </template>

    <!-- Модальное окно -->
    <Transition name="modal-fade">
      <div v-if="activeModal" class="fixed inset-0 z-[200] flex items-center justify-center p-4" @click.self="closeModal">
        <div class="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" @click="closeModal"></div>
        <div class="relative bg-white rounded-3xl shadow-2xl w-full max-w-md p-6 z-10">
          <button @click="closeModal" class="absolute right-4 top-4 p-2 hover:bg-slate-100 rounded-xl transition-colors">
            <X class="w-4 h-4 text-slate-400"/>
          </button>
          <template v-if="activeModal && modalConfig[activeModal]">
            <div class="flex items-center gap-3 mb-6">
              <div :class="{ 'bg-amber-50 text-amber-600': activeModal==='ppm', 'bg-emerald-50 text-emerald-600': activeModal==='csat' }" class="p-3 rounded-2xl">
                <AlertTriangle v-if="activeModal==='ppm'" class="w-5 h-5"/>
                <Star v-if="activeModal==='csat'" class="w-5 h-5"/>
              </div>
              <div>
                <h2 class="text-base font-black text-slate-800">{{ modalConfig[activeModal].title }}</h2>
                <p class="text-xs text-slate-400">{{ modalConfig[activeModal].desc }}</p>
              </div>
            </div>
            <div class="flex items-end gap-3 mb-6">
              <span class="text-4xl font-black text-slate-800">{{ modalConfig[activeModal].format(modalConfig[activeModal].getValue(dashboardData)) }}</span>
              <template v-if="modalConfig[activeModal].getDelta(dashboardData) != null">
                <span :class="deltaClass(modalConfig[activeModal].getDelta(dashboardData), modalConfig[activeModal].invertColors)" class="flex items-center gap-1 text-sm font-black mb-1">
                  <TrendingUp v-if="modalConfig[activeModal].getDelta(dashboardData) > 0" class="w-4 h-4"/>
                  <TrendingDown v-else class="w-4 h-4"/>
                  {{ Math.abs(modalConfig[activeModal].getDelta(dashboardData)) }}%
                </span>
              </template>
            </div>
            <div v-if="modalBarData" class="bg-slate-50 rounded-2xl p-4 mb-6">
              <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-4">Сравнение периодов</p>
              <div class="mb-3">
                <div class="flex justify-between text-xs font-bold text-slate-600 mb-1.5">
                  <span>Текущий период</span>
                  <span class="font-black text-slate-800">{{ modalConfig[activeModal].format(modalBarData.curr) }}</span>
                </div>
                <div class="w-full bg-slate-200 h-3 rounded-full overflow-hidden">
                  <div :class="{ 'bg-amber-500': activeModal==='ppm', 'bg-emerald-500': activeModal==='csat' }" class="h-full rounded-full transition-all duration-500" :style="{ width: `${modalBarData.currPct}%` }"></div>
                </div>
              </div>
              <div>
                <div class="flex justify-between text-xs font-bold text-slate-400 mb-1.5">
                  <span>{{ dashboardData?.prev_period_label }}</span>
                  <span class="font-black">{{ modalConfig[activeModal].format(modalBarData.prev) }}</span>
                </div>
                <div class="w-full bg-slate-200 h-3 rounded-full overflow-hidden">
                  <div class="bg-slate-300 h-full rounded-full transition-all duration-500" :style="{ width: `${modalBarData.prevPct}%` }"></div>
                </div>
              </div>
              <div v-if="modalConfig[activeModal].getDelta(dashboardData) != null" class="mt-4 pt-3 border-t border-slate-200">
                <p :class="deltaClass(modalConfig[activeModal].getDelta(dashboardData), modalConfig[activeModal].invertColors)" class="text-xs font-black text-center">
                  {{ modalConfig[activeModal].getDelta(dashboardData) > 0 ? '▲' : '▼' }} {{ Math.abs(modalConfig[activeModal].getDelta(dashboardData)) }}%
                  {{ modalConfig[activeModal].getDelta(dashboardData) > 0 ? 'выше' : 'ниже' }} предыдущего периода
                </p>
              </div>
            </div>
            <div v-else class="bg-slate-50 rounded-2xl p-4 mb-6 text-center text-xs text-slate-400">Нет данных за предыдущий период для сравнения</div>
            <router-link :to="modalConfig[activeModal].route" @click="closeModal"
              class="w-full flex items-center justify-center gap-2 py-3 px-4 rounded-2xl font-bold text-sm transition-all no-underline"
              :class="{ 'bg-amber-50 text-amber-700 hover:bg-amber-100': activeModal==='ppm', 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100': activeModal==='csat' }">
              {{ modalConfig[activeModal].routeLabel }} <ArrowUpRight class="w-4 h-4"/>
            </router-link>
          </template>
        </div>
      </div>
    </Transition>

  </div>
</template>

<style scoped>
.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 0.2s ease; }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }
</style>
