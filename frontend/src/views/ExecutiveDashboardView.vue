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

    <!-- Воронка: Отправка деталей -->
    <div class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
      <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-5">
        <span class="w-2 h-4 bg-sky-500 rounded-md block"></span>
        Доотправка деталей
      </h3>

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
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        <div class="bg-blue-50 rounded-2xl p-4 text-center">
          <div class="text-2xl font-black text-blue-700">{{ dashboardData?.reshipment_funnel?.approved_wb ?? '—' }}</div>
          <div class="text-xs font-bold text-blue-600 mt-1">Одобрено ВБ</div>
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
            <div class="text-[10px] text-slate-400">СДЭК + Яндекс, по всем одобренным</div>
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
              {{ dashboardData?.reshipment_funnel?.approved > 0 && dashboardData?.reshipment_funnel?.reviews_left > 0
                ? Math.round(dashboardData.reshipment_funnel.reviews_left / dashboardData.reshipment_funnel.approved * 100) + '% от одобренных'
                : '—' }}
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- Экономика доотправок -->
    <div v-if="dashboardData?.reshipment_economics?.requests_with_data > 0"
         class="mt-6 bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">

      <!-- Заголовок -->
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-2 mb-2">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2">
          <span class="w-2 h-4 bg-emerald-500 rounded-md block"></span>
          Экономика доотправок — выгода инструмента
        </h3>
        <span class="text-[11px] text-slate-400 sm:text-right">
          По {{ dashboardData.reshipment_economics.requests_with_data }} заявкам с данными финотчёта WB<br>
          <span class="text-slate-300">(из {{ dashboardData.reshipment_funnel.approved }} одобренных · данные динамические, обновляются с отчётами WB)</span>
        </span>
      </div>

      <!-- Глоссарий -->
      <div class="bg-slate-50 rounded-2xl p-4 mb-5 text-[11px] text-slate-500 space-y-1.5">
        <div><span class="font-bold text-slate-700">Выплата WB (ppvz)</span> — сумма, которую WB перечисляет нам после вычета своих комиссий за продажу. Это наша реальная выручка.</div>
        <div><span class="font-bold text-slate-700">Себестоимость (СС)</span> — затраты на производство/закупку товара. При доотправке детали товар остаётся у клиента (дефектный) — СС потеряна в обоих сценариях.</div>
        <div><span class="font-bold text-slate-700">WB-расходы при возврате</span> — эквайринг (~4% от суммы покупки при возврате средств клиенту) + штрафы + логистика. Возникают при любом варианте возврата.</div>
        <div><span class="font-bold text-slate-700">Обратная логистика</span> — стоимость доставки товара от клиента к нам на ПВЗ, которую берёт WB (в среднем {{ formatMoney(dashboardData.reshipment_economics.avg_return_delivery) }}/шт по финотчёту).</div>
      </div>

      <!-- Блок 1: Потери при возврате (альтернативные сценарии) -->
      <div class="mb-5">
        <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">
          Если бы согласовывали возврат — потери по тем же {{ dashboardData.reshipment_economics.requests_with_data }} заявкам
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <!-- V1 -->
          <div class="bg-red-50 border border-red-100 rounded-2xl p-4">
            <div class="flex items-start justify-between gap-2 mb-3">
              <div>
                <div class="text-[10px] font-bold text-red-400 uppercase tracking-wider mb-1">Вариант 1 — деньги клиенту, товар у него</div>
                <div class="text-3xl font-black text-red-700">{{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v1) }}</div>
                <div class="text-[11px] text-red-400 mt-0.5">≈ {{ formatMoney(Math.round(dashboardData.reshipment_economics.loss_if_return_v1 / dashboardData.reshipment_economics.requests_with_data)) }}/заявку</div>
              </div>
              <TrendingDown class="w-7 h-7 text-red-300 flex-shrink-0 mt-1" />
            </div>
            <div class="space-y-1.5 text-[12px]">
              <div class="flex justify-between items-center py-1 border-b border-red-100">
                <span class="text-red-600">Себестоимость товара (дефектный, не реализуем)</span>
                <span class="font-bold text-red-700">{{ formatMoney(dashboardData.reshipment_economics.total_cog) }}</span>
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-red-600">WB-расходы: эквайринг + штрафы при возврате</span>
                <span class="font-bold text-red-700">{{ formatMoney(dashboardData.reshipment_economics.total_wb_return_costs) }}</span>
              </div>
            </div>
          </div>
          <!-- V2 -->
          <div class="bg-orange-50 border border-orange-100 rounded-2xl p-4">
            <div class="flex items-start justify-between gap-2 mb-3">
              <div>
                <div class="text-[10px] font-bold text-orange-400 uppercase tracking-wider mb-1">Вариант 2 — деньги клиенту + товар к нам (дефектный)</div>
                <div class="text-3xl font-black text-orange-700">{{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v2) }}</div>
                <div class="text-[11px] text-orange-400 mt-0.5">≈ {{ formatMoney(Math.round(dashboardData.reshipment_economics.loss_if_return_v2 / dashboardData.reshipment_economics.requests_with_data)) }}/заявку · товар приходит, но не реализуем</div>
              </div>
              <TrendingDown class="w-7 h-7 text-orange-300 flex-shrink-0 mt-1" />
            </div>
            <div class="space-y-1.5 text-[12px]">
              <div class="flex justify-between items-center py-1 border-b border-orange-100">
                <span class="text-orange-600">Себестоимость товара (дефектный, не реализуем)</span>
                <span class="font-bold text-orange-700">{{ formatMoney(dashboardData.reshipment_economics.total_cog) }}</span>
              </div>
              <div class="flex justify-between items-center py-1 border-b border-orange-100">
                <span class="text-orange-600">WB-расходы: эквайринг + штрафы</span>
                <span class="font-bold text-orange-700">{{ formatMoney(dashboardData.reshipment_economics.total_wb_return_costs) }}</span>
              </div>
              <div class="flex justify-between items-center py-1">
                <span class="text-orange-600">+ Обратная логистика WB → ПВЗ (≈ {{ formatMoney(dashboardData.reshipment_economics.avg_return_delivery) }}/шт)</span>
                <span class="font-bold text-orange-700">{{ formatMoney(dashboardData.reshipment_economics.ret_logistics) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Блок 2: Наш реальный результат (V3) -->
      <div class="mb-5">
        <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">Наш результат с инструментом доотправок</div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div class="bg-slate-50 rounded-2xl p-4">
            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Выплата WB сохранена (ppvz)</div>
            <div class="text-2xl font-black text-slate-800">{{ formatMoney(dashboardData.reshipment_economics.total_ppvz) }}</div>
            <div class="text-[11px] text-slate-400 mt-1">Выручка после всех комиссий WB, не аннулирована возвратом</div>
          </div>
          <div class="bg-slate-50 rounded-2xl p-4">
            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Заплатили за доставку деталей</div>
            <div class="text-2xl font-black text-slate-800">{{ formatMoney(dashboardData.reshipment_economics.total_delivery_cost) }}</div>
            <div class="text-[11px] text-slate-400 mt-1">СДЭК + Яндекс, по {{ dashboardData.reshipment_economics.requests_with_data }} заявкам с финданными (все одобренные — {{ formatMoney(dashboardData.reshipment_funnel.total_cost) }})</div>
          </div>
          <div :class="dashboardData.reshipment_economics.net_v3 >= 0 ? 'bg-emerald-50' : 'bg-rose-50'" class="rounded-2xl p-4">
            <div :class="dashboardData.reshipment_economics.net_v3 >= 0 ? 'text-emerald-400' : 'text-rose-400'" class="text-[10px] font-bold uppercase tracking-wider mb-1">Чистый результат (ppvz − СС − доставка)</div>
            <div :class="dashboardData.reshipment_economics.net_v3 >= 0 ? 'text-emerald-800' : 'text-rose-700'" class="text-2xl font-black">
              {{ dashboardData.reshipment_economics.net_v3 >= 0 ? '+' : '' }}{{ formatMoney(dashboardData.reshipment_economics.net_v3) }}
            </div>
            <div :class="dashboardData.reshipment_economics.net_v3 >= 0 ? 'text-emerald-400' : 'text-rose-400'" class="text-[11px] mt-1">
              Убыток меньше, чем В1 ({{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v1) }}) и В2 ({{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v2) }}). Разница — наша выгода.
            </div>
          </div>
        </div>
      </div>

      <!-- Блок 3: Выгода (разница между V3 и вариантами возврата) -->
      <div class="mb-5">
        <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">Чистая выгода = разница между нашим результатом и потерями при возврате</div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
            <div class="flex items-start justify-between gap-2 mb-3">
              <div>
                <div class="text-[10px] font-bold text-emerald-600 uppercase tracking-wider mb-1">Выгода vs Вариант 1 (деньги + товар у клиента)</div>
                <div class="text-3xl font-black text-emerald-700">+{{ formatMoney(dashboardData.reshipment_economics.benefit_vs_v1) }}</div>
                <div class="text-[12px] text-emerald-500 mt-0.5">В среднем +{{ formatMoney(dashboardData.reshipment_economics.avg_benefit_vs_v1) }} на каждую заявку</div>
              </div>
              <TrendingUp class="w-7 h-7 text-emerald-400 flex-shrink-0 mt-1" />
            </div>
            <div class="bg-emerald-100 rounded-xl p-3 text-[11px] text-emerald-800 space-y-1">
              <div class="font-bold mb-1">Формула: наш результат − потери В1</div>
              <div class="flex justify-between">
                <span>Наш результат (ppvz − СС − доставка)</span>
                <span class="font-semibold">{{ formatMoney(dashboardData.reshipment_economics.net_v3) }}</span>
              </div>
              <div class="flex justify-between border-t border-emerald-200 pt-1">
                <span>Минус потери В1 (−СС − WB-расходы)</span>
                <span class="font-semibold">−({{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v1) }})</span>
              </div>
              <div class="flex justify-between font-bold border-t border-emerald-300 pt-1 text-emerald-900">
                <span>= Выгода (СС взаимно отменяется)</span>
                <span>+{{ formatMoney(dashboardData.reshipment_economics.benefit_vs_v1) }}</span>
              </div>
              <div class="text-[10px] text-emerald-600 pt-1">
                Упрощённо: ppvz ({{ formatMoney(dashboardData.reshipment_economics.total_ppvz) }}) + WB-расходы ({{ formatMoney(dashboardData.reshipment_economics.total_wb_return_costs) }}) − доставка ({{ formatMoney(dashboardData.reshipment_economics.total_delivery_cost) }})
              </div>
            </div>
          </div>
          <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
            <div class="flex items-start justify-between gap-2 mb-3">
              <div>
                <div class="text-[10px] font-bold text-emerald-600 uppercase tracking-wider mb-1">Выгода vs Вариант 2 (деньги + товар к нам)</div>
                <div class="text-3xl font-black text-emerald-700">+{{ formatMoney(dashboardData.reshipment_economics.benefit_vs_v2) }}</div>
                <div class="text-[12px] text-emerald-500 mt-0.5">В среднем +{{ formatMoney(dashboardData.reshipment_economics.avg_benefit_vs_v2) }} на каждую заявку</div>
              </div>
              <TrendingUp class="w-7 h-7 text-emerald-400 flex-shrink-0 mt-1" />
            </div>
            <div class="bg-emerald-100 rounded-xl p-3 text-[11px] text-emerald-800 space-y-1">
              <div class="font-bold mb-1">Формула: наш результат − потери В2</div>
              <div class="flex justify-between">
                <span>Наш результат (ppvz − СС − доставка)</span>
                <span class="font-semibold">{{ formatMoney(dashboardData.reshipment_economics.net_v3) }}</span>
              </div>
              <div class="flex justify-between border-t border-emerald-200 pt-1">
                <span>Минус потери В2 (−СС − WB − логистика)</span>
                <span class="font-semibold">−({{ formatMoney(dashboardData.reshipment_economics.loss_if_return_v2) }})</span>
              </div>
              <div class="flex justify-between font-bold border-t border-emerald-300 pt-1 text-emerald-900">
                <span>= Выгода (СС взаимно отменяется)</span>
                <span>+{{ formatMoney(dashboardData.reshipment_economics.benefit_vs_v2) }}</span>
              </div>
              <div class="text-[10px] text-emerald-600 pt-1">
                ppvz ({{ formatMoney(dashboardData.reshipment_economics.total_ppvz) }}) + WB-расходы ({{ formatMoney(dashboardData.reshipment_economics.total_wb_return_costs) }}) + логистика обр. ({{ formatMoney(dashboardData.reshipment_economics.ret_logistics) }}) − доставка ({{ formatMoney(dashboardData.reshipment_economics.total_delivery_cost) }})
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Блок 4: Прошлый месяц — все возвраты WB -->
      <div v-if="dashboardData.reshipment_economics.prev_month_all_returns" class="pt-5 border-t border-slate-100">
        <div class="text-[10px] font-black text-slate-400 uppercase tracking-wider mb-3">
          Прошлый месяц — все возвраты WB (до / с инструментом)
        </div>

        <!-- Все возвраты из финотчёта -->
        <div class="bg-red-50 border border-red-100 rounded-2xl p-4 mb-3">
          <div class="flex items-start justify-between gap-2 mb-3">
            <div>
              <div class="text-[10px] font-bold text-red-400 uppercase tracking-wider mb-1">Все возвраты за прошлый месяц (финотчёт WB)</div>
              <div class="text-2xl font-black text-red-700">{{ dashboardData.reshipment_economics.prev_month_all_returns.returns_count }} возвратов</div>
              <div class="text-[11px] text-red-400 mt-0.5">Возврат клиентам: {{ formatMoney(dashboardData.reshipment_economics.prev_month_all_returns.total_retail_reversed) }}</div>
            </div>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[12px]">
            <div class="bg-white rounded-xl p-3">
              <div class="text-[10px] font-bold text-red-400 uppercase mb-1">Эквайринг при возвратах</div>
              <div class="text-lg font-black text-red-700">{{ formatMoney(dashboardData.reshipment_economics.prev_month_all_returns.total_acquiring) }}</div>
              <div class="text-[10px] text-slate-400 mt-0.5">прямые потери, из финотчёта</div>
            </div>
            <div class="bg-white rounded-xl p-3">
              <div class="text-[10px] font-bold text-red-400 uppercase mb-1">Обратная логистика (оценка)</div>
              <div class="text-lg font-black text-red-700">{{ formatMoney(dashboardData.reshipment_economics.prev_month_all_returns.ret_logistics_estimated) }}</div>
              <div class="text-[10px] text-slate-400 mt-0.5">≈ {{ formatMoney(dashboardData.reshipment_economics.prev_month_all_returns.avg_return_delivery) }}/шт × {{ dashboardData.reshipment_economics.prev_month_all_returns.returns_count }} возвратов</div>
            </div>
            <div class="bg-white rounded-xl p-3">
              <div class="text-[10px] font-bold text-red-400 uppercase mb-1">Итого прямые потери (без СС)</div>
              <div class="text-lg font-black text-red-700">{{ formatMoney(dashboardData.reshipment_economics.prev_month_all_returns.total_loss_estimated) }}</div>
              <div class="text-[10px] text-slate-400 mt-0.5">эквайринг + оценка логистики</div>
            </div>
          </div>
        </div>

        <!-- Гипотетический расчёт: 50% возвратов через инструмент -->
        <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
          <div class="text-[10px] font-bold text-emerald-600 uppercase tracking-wider mb-3">
            Гипотеза: если бы отправляли детали по 50% всех возвратов
          </div>

          <!-- Формула -->
          <div class="flex flex-wrap items-center gap-2 mb-4 text-sm font-bold">
            <div class="bg-white rounded-xl px-3 py-2 text-center shadow-sm">
              <div class="text-[10px] text-slate-400 font-normal mb-0.5">Всего возвратов</div>
              <div class="text-slate-800">{{ dashboardData.reshipment_economics.prev_month_all_returns.returns_count }}</div>
            </div>
            <div class="text-slate-400 text-lg">×</div>
            <div class="bg-white rounded-xl px-3 py-2 text-center shadow-sm">
              <div class="text-[10px] text-slate-400 font-normal mb-0.5">Охват</div>
              <div class="text-slate-800">50%</div>
            </div>
            <div class="text-slate-400 text-lg">=</div>
            <div class="bg-white rounded-xl px-3 py-2 text-center shadow-sm">
              <div class="text-[10px] text-slate-400 font-normal mb-0.5">Заявок</div>
              <div class="text-slate-800">{{ Math.round(dashboardData.reshipment_economics.prev_month_all_returns.returns_count * 0.5) }}</div>
            </div>
            <div class="text-slate-400 text-lg">×</div>
            <div class="bg-white rounded-xl px-3 py-2 text-center shadow-sm">
              <div class="text-[10px] text-slate-400 font-normal mb-0.5">Выгода / заявку</div>
              <div class="text-slate-800">{{ formatMoney(dashboardData.reshipment_economics.avg_benefit_vs_v2) }}</div>
            </div>
            <div class="text-emerald-600 text-lg">=</div>
            <div class="bg-emerald-100 border-2 border-emerald-300 rounded-xl px-4 py-2 text-center shadow-sm">
              <div class="text-[10px] text-emerald-600 font-normal mb-0.5">Итого выгода</div>
              <div class="text-emerald-700 text-base">+{{ formatMoney(Math.round(dashboardData.reshipment_economics.prev_month_all_returns.returns_count * 0.5 * dashboardData.reshipment_economics.avg_benefit_vs_v2)) }}</div>
            </div>
          </div>

          <!-- Пояснение откуда взята выгода/заявку -->
          <div class="bg-white/70 rounded-xl px-3 py-2 text-[11px] text-slate-500 leading-relaxed">
            <span class="font-bold text-slate-600">{{ formatMoney(dashboardData.reshipment_economics.avg_benefit_vs_v2) }}/заявку</span> —
            средняя выгода vs В2 по {{ dashboardData.reshipment_economics.requests_with_data }} текущим заявкам с фин. отчётом WB
            (выплата WB + расходы WB на возврат − стоимость доставки детали).
            Оценочные затраты на доставку:
            {{ formatMoney(Math.round(dashboardData.reshipment_economics.prev_month_all_returns.returns_count * 0.5 * (dashboardData.reshipment_economics.total_delivery_cost / dashboardData.reshipment_economics.requests_with_data))) }}
            (≈ {{ formatMoney(Math.round(dashboardData.reshipment_economics.total_delivery_cost / dashboardData.reshipment_economics.requests_with_data)) }}/заявку).
          </div>
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
        % считается от числа отправленных актов в выбранном периоде. <button @click="router.push('/production')" class="text-amber-600 font-bold hover:underline">Перейти в PPM →</button>
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
