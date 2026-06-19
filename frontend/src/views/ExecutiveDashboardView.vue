<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  DollarSign, AlertTriangle, Star, Cpu, Calendar, ChevronDown,
  ArrowUpRight, TrendingUp, TrendingDown, BarChart3, ChevronLeft, ChevronRight, X
} from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()
const router = useRouter()
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
const loadDashboardData = async () => {
  if (!startDate.value || !endDate.value) return
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/dashboard/executive?start_date=${startDate.value}&end_date=${endDate.value}&platform=${platformStore.platform}`)
    if (res.ok) dashboardData.value = await res.json()
  } catch (err) { console.error("Ошибка загрузки данных дашборда:", err) }
  finally { loading.value = false }
}

const formatMoney = (v) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v || 0)

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
    desc: '% дефектных товаров от числа заказов',
    invertColors: false,
  },
  csat: {
    title: 'Индекс CSAT (VOC)',
    route: '/voc', routeLabel: 'Перейти в VOC',
    getValue: (d) => d?.metrics?.csat ?? 100,
    getPrev:  (d) => d?.metrics?.prev_csat ?? null,
    getDelta: (d) => d?.metrics?.csat_delta ?? null,
    format:   (v) => `${v}%`,
    desc: 'Доля положительных отзывов (оценки 4 и 5)',
    invertColors: true,
  },
  ai: {
    title: 'Объёмы ИИ воркера',
    route: '/ai-tagging', routeLabel: 'Перейти в ИИ',
    getValue: (d) => d?.metrics?.ai_processed ?? 0,
    getPrev:  (d) => d?.metrics?.prev_ai_processed ?? null,
    getDelta: (d) => d?.metrics?.ai_processed_delta ?? null,
    format:   (v) => `${v} обр.`,
    desc: 'Количество отзывов и рекламаций, проанализированных ИИ',
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
          <h1 class="text-2xl font-black text-slate-800 tracking-tight">Панель Управления Компанией</h1>
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

      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-violet-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-violet-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-violet-50 text-violet-600 rounded-2xl"><TrendingDown class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Недополученный доход</div>
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
        <p class="text-[11px] text-slate-400">Сумма розничных цен одобренных возвратов и рекламаций</p>
      </div>

      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-red-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
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
        <p class="text-[11px] text-slate-400">Стоимость подтвержденного фабричного брака в себестоимости</p>
      </div>

      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-purple-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
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
        <p class="text-[11px] text-slate-400">Разница между ценой продажи и себестоимостью по потерям</p>
      </div>

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
        <p class="text-[11px] text-slate-400">Средний процент дефектных товаров от общего числа чистых заказов</p>
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
        <p class="text-[11px] text-slate-400">Доля положительных отзывов (оценки 4 и 5) от общего числа</p>
      </div>

      <div @click="openModal('ai')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-indigo-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-indigo-400 transition-all"><BarChart3 class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-indigo-50 text-indigo-600 rounded-2xl"><Cpu class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Объёмы ИИ воркера</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ dashboardData?.metrics?.ai_processed || 0 }} <span class="text-sm font-bold text-slate-400">обр.</span></div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.ai_processed_delta != null">
            <span :class="dashboardData.metrics.ai_processed_delta > 0 ? 'text-emerald-500' : 'text-rose-500'" class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.ai_processed_delta > 0" class="w-3.5 h-3.5"/><TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.ai_processed_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <p class="text-[11px] text-slate-400">Количество отзывов и рекламаций, проанализированных ИИ</p>
      </div>

    </div>

    <!-- Нижние блоки -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm md:col-span-2">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-6">
          <span class="w-2 h-4 bg-red-500 rounded-md block"></span>
          Красная зона: Топ-5 SKU по финансовым потерям
        </h3>
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
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-4">
          <span class="w-2 h-4 bg-slate-800 rounded-md block"></span>
          Воронка данных в периоде
        </h3>
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
              <div :class="{ 'bg-amber-50 text-amber-600': activeModal==='ppm', 'bg-emerald-50 text-emerald-600': activeModal==='csat', 'bg-indigo-50 text-indigo-600': activeModal==='ai' }" class="p-3 rounded-2xl">
                <AlertTriangle v-if="activeModal==='ppm'" class="w-5 h-5"/>
                <Star v-if="activeModal==='csat'" class="w-5 h-5"/>
                <Cpu v-if="activeModal==='ai'" class="w-5 h-5"/>
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
                  <div :class="{ 'bg-amber-500': activeModal==='ppm', 'bg-emerald-500': activeModal==='csat', 'bg-indigo-500': activeModal==='ai' }" class="h-full rounded-full transition-all duration-500" :style="{ width: `${modalBarData.currPct}%` }"></div>
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
            <button @click="closeModal(); router.push(modalConfig[activeModal].route)"
              class="w-full flex items-center justify-center gap-2 py-3 px-4 rounded-2xl font-bold text-sm transition-all"
              :class="{ 'bg-amber-50 text-amber-700 hover:bg-amber-100': activeModal==='ppm', 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100': activeModal==='csat', 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100': activeModal==='ai' }">
              {{ modalConfig[activeModal].routeLabel }} <ArrowUpRight class="w-4 h-4"/>
            </button>
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
