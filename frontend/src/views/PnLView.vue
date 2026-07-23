<script setup>
import { ref, computed, onMounted } from 'vue'
import { TrendingUp, Search, ChevronUp, ChevronDown, BarChart3, Calendar, ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { apiFetch } from '../api'

const rawData  = ref([])
const loading  = ref(true)
const search   = ref('')
const sortKey  = ref('total_revenue')
const sortAsc  = ref(false)

// --- КАЛЕНДАРЬ (идентично FinancialLossView) ---
const todayObj = new Date()
const startDate = ref('')
const endDate   = ref('')
const showCalendarPopover = ref(false)
const calendarYear  = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const activePeriodLabel = ref('all')
const monthNames = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]

const _cy = todayObj.getFullYear()
const MONTHS_PRESETS = [
  { label: 'Янв', start: `${_cy}-01-01`, end: `${_cy}-01-31` },
  { label: 'Фев', start: `${_cy}-02-01`, end: `${_cy}-02-28` },
  { label: 'Мар', start: `${_cy}-03-01`, end: `${_cy}-03-31` },
  { label: 'Апр', start: `${_cy}-04-01`, end: `${_cy}-04-30` },
  { label: 'Май', start: `${_cy}-05-01`, end: `${_cy}-05-31` },
  { label: 'Июн', start: `${_cy}-06-01`, end: `${_cy}-06-30` },
  { label: 'Июл', start: `${_cy}-07-01`, end: `${_cy}-07-31` },
  { label: 'Авг', start: `${_cy}-08-01`, end: `${_cy}-08-31` },
  { label: 'Сен', start: `${_cy}-09-01`, end: `${_cy}-09-30` },
  { label: 'Окт', start: `${_cy}-10-01`, end: `${_cy}-10-31` },
  { label: 'Ноя', start: `${_cy}-11-01`, end: `${_cy}-11-30` },
  { label: 'Дек', start: `${_cy}-12-01`, end: `${_cy}-12-31` },
]

const selectSpecificMonth = (preset) => {
  startDate.value = preset.start; endDate.value = preset.end
  activePeriodLabel.value = preset.label; showCalendarPopover.value = false
  load()
}
const setCurrentMonth = () => {
  const now = new Date()
  const y = now.getFullYear(), m = String(now.getMonth()+1).padStart(2,'0'), d2 = String(now.getDate()).padStart(2,'0')
  startDate.value = `${y}-${m}-01`; endDate.value = `${y}-${m}-${d2}`
  activePeriodLabel.value = 'current_month'; calendarYear.value = y; calendarMonth.value = now.getMonth()
  showCalendarPopover.value = false; load()
}
const setPreviousMonth = () => {
  const d = new Date(); d.setMonth(d.getMonth() - 1)
  const year = d.getFullYear(), month = String(d.getMonth() + 1).padStart(2,'0')
  const lastDay = new Date(year, d.getMonth() + 1, 0).getDate()
  startDate.value = `${year}-${month}-01`; endDate.value = `${year}-${month}-${lastDay}`
  calendarYear.value = year; calendarMonth.value = d.getMonth()
  activePeriodLabel.value = 'prev_month'; showCalendarPopover.value = false; load()
}
const changeCalendarMonth = (offset) => {
  calendarMonth.value += offset
  if (calendarMonth.value < 0)  { calendarMonth.value = 11; calendarYear.value-- }
  else if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
}
const handleCalendarDayClick = (d) => {
  if (!d.isCurrentMonth || !d.dateStr) return
  if (!startDate.value || (startDate.value && endDate.value)) { startDate.value = d.dateStr; endDate.value = '' }
  else {
    if (d.dateStr < startDate.value) { startDate.value = d.dateStr; endDate.value = '' }
    else { endDate.value = d.dateStr; activePeriodLabel.value = ''; showCalendarPopover.value = false; load() }
  }
}
const calendarDays = computed(() => {
  const firstDay = new Date(calendarYear.value, calendarMonth.value, 1).getDay()
  const padding = firstDay === 0 ? 6 : firstDay - 1
  const totalDays = new Date(calendarYear.value, calendarMonth.value + 1, 0).getDate()
  const days = []
  const prevTotal = new Date(calendarYear.value, calendarMonth.value, 0).getDate()
  for (let i = padding - 1; i >= 0; i--) days.push({ day: prevTotal - i, isCurrentMonth: false, dateStr: null })
  for (let i = 1; i <= totalDays; i++) {
    const mStr = String(calendarMonth.value + 1).padStart(2,'0'), dStr = String(i).padStart(2,'0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})
const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

const periodLabel = computed(() => {
  if (activePeriodLabel.value === 'current_month') return 'Текущий месяц'
  if (activePeriodLabel.value === 'prev_month')    return 'Прошлый месяц'
  if (activePeriodLabel.value === 'all')           return 'Все данные'
  if (activePeriodLabel.value)                     return activePeriodLabel.value
  return `${formatDateDisplay(startDate.value)} — ${formatDateDisplay(endDate.value)}`
})

// --- ЗАГРУЗКА ---
const load = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (startDate.value) params.set('date_from', startDate.value)
    if (endDate.value)   params.set('date_to',   endDate.value)
    const res  = await apiFetch(`/api/v1/finances/pnl-by-article?${params}`)
    const json = await res.json()
    rawData.value = json.data || []
  } catch (e) {
    console.error('P&L load error:', e)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const fmt  = (v) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v || 0)
const fmtN = (v, dec = 1) => (v || 0).toFixed(dec)

const setSort = (key) => {
  if (sortKey.value === key) sortAsc.value = !sortAsc.value
  else { sortKey.value = key; sortAsc.value = false }
}

const filtered = computed(() => {
  let d = rawData.value
  if (search.value) {
    const q = search.value.toLowerCase()
    d = d.filter(r => r.sku.toLowerCase().includes(q))
  }
  return [...d].sort((a, b) => {
    const va = a[sortKey.value] ?? 0, vb = b[sortKey.value] ?? 0
    return sortAsc.value ? va - vb : vb - va
  })
})

const totals = computed(() => {
  const d = filtered.value
  const sum = (key) => d.reduce((acc, r) => acc + (r[key] || 0), 0)
  return {
    revenue: sum('total_revenue'), payout: sum('total_payout'),
    commission: sum('total_commission'), delivery: sum('total_delivery'),
    storage: sum('total_storage'), penalty: sum('total_penalty'),
    cogs: sum('total_cogs'), gross: sum('gross_profit'), net: sum('net_profit'),
    sales: sum('sales_count'), returns: sum('returns_count'),
  }
})

const profitColor = (v) => v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-500'
const profitBg    = (v) => v > 0 ? 'bg-emerald-50' : v < 0 ? 'bg-red-50' : ''
const SortIcon    = (key) => sortKey.value !== key ? null : sortAsc.value ? ChevronUp : ChevronDown
</script>

<template>
  <div class="min-h-screen bg-slate-50 p-6" @click="showCalendarPopover = false">

    <!-- Заголовок + фильтр -->
    <div class="mb-6 flex items-center justify-between flex-wrap gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900 flex items-center gap-2">
          <BarChart3 class="w-6 h-6 text-indigo-600" />
          P&amp;L по артикулу
        </h1>
        <p class="text-sm text-slate-500 mt-1">Финансовый результат по каждому артикулу из еженедельного отчёта WB</p>
      </div>

      <!-- Календарь (как в Финансовых потерях) -->
      <div class="relative">
        <div
          class="flex items-center bg-white border border-slate-200 rounded-2xl px-5 py-3 cursor-pointer hover:border-blue-500 transition-colors shadow-sm"
          @click.stop="showCalendarPopover = !showCalendarPopover"
        >
          <Calendar class="w-4 h-4 text-blue-500 mr-3" />
          <span class="text-sm font-bold text-slate-700">{{ periodLabel }}</span>
        </div>
        <div v-if="showCalendarPopover" class="absolute right-0 mt-3 bg-white border border-slate-100 rounded-3xl shadow-2xl z-[150] p-5 w-80" @click.stop>
          <div class="mb-4 border-b border-slate-100 pb-3 space-y-2">
            <div class="grid grid-cols-2 gap-1.5">
              <button @click.stop="setCurrentMonth"  :class="activePeriodLabel === 'current_month' ? 'bg-slate-800 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700'" class="text-center text-xs font-black py-2 rounded-xl transition-all">Текущий месяц</button>
              <button @click.stop="setPreviousMonth" :class="activePeriodLabel === 'prev_month'    ? 'bg-slate-800 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700'" class="text-center text-xs font-black py-2 rounded-xl transition-all">Прошлый месяц</button>
            </div>
            <div class="grid grid-cols-4 gap-1">
              <button v-for="m in MONTHS_PRESETS" :key="m.label" @click.stop="selectSpecificMonth(m)" :class="activePeriodLabel === m.label ? 'bg-slate-800 text-white' : 'hover:bg-slate-100 text-slate-600'" class="text-center text-[11px] font-bold py-1.5 rounded-lg transition-all">{{ m.label }}</button>
            </div>
          </div>
          <div class="flex justify-between items-center mb-4">
            <button @click="changeCalendarMonth(-1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500"><ChevronLeft class="w-4 h-4"/></button>
            <span class="text-sm font-black text-slate-800">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
            <button @click="changeCalendarMonth(1)"  class="p-2 hover:bg-slate-100 rounded-xl text-slate-500"><ChevronRight class="w-4 h-4"/></button>
          </div>
          <div class="grid grid-cols-7 gap-1 text-center text-[10px] font-black text-slate-400 uppercase mb-2">
            <div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div>
          </div>
          <div class="grid grid-cols-7 gap-1">
            <div
              v-for="(d, idx) in calendarDays" :key="idx"
              @click="handleCalendarDayClick(d)"
              :class="[
                'h-9 flex items-center justify-center text-sm font-bold rounded-xl select-none',
                !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer',
                d.dateStr === startDate || d.dateStr === endDate ? 'bg-blue-600 text-white shadow-md' : '',
                d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-blue-50 text-blue-700' : '',
                d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : ''
              ]"
            >{{ d.day }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- KPI карточки -->
    <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-5 gap-4 mb-6">
      <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div class="text-xs text-slate-400 mb-1">Выручка</div>
        <div class="text-xl font-bold text-slate-800">{{ fmt(totals.revenue) }}</div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div class="text-xs text-slate-400 mb-1">К выплате (WB)</div>
        <div class="text-xl font-bold text-slate-800">{{ fmt(totals.payout) }}</div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div class="text-xs text-slate-400 mb-1">Себестоимость</div>
        <div class="text-xl font-bold text-slate-800">{{ fmt(totals.cogs) }}</div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div class="text-xs text-slate-400 mb-1">Валовая прибыль</div>
        <div :class="['text-xl font-bold', profitColor(totals.gross)]">{{ fmt(totals.gross) }}</div>
      </div>
      <div :class="['rounded-xl border p-4 shadow-sm', profitBg(totals.net)]">
        <div class="text-xs text-slate-400 mb-1">Чистая прибыль</div>
        <div :class="['text-xl font-bold', profitColor(totals.net)]">{{ fmt(totals.net) }}</div>
      </div>
    </div>

    <!-- Поиск -->
    <div class="mb-4 flex items-center gap-2 bg-white border border-slate-200 rounded-xl px-4 py-2 shadow-sm max-w-sm">
      <Search class="w-4 h-4 text-slate-400" />
      <input v-model="search" placeholder="Поиск по артикулу..." class="text-sm outline-none flex-1 bg-transparent" />
    </div>

    <!-- Загрузка -->
    <div v-if="loading" class="text-center py-20 text-slate-400 text-sm animate-pulse">Загрузка данных...</div>

    <!-- Таблица -->
    <div v-else class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-100 bg-slate-50">
            <th class="text-left px-4 py-3 font-medium text-slate-500 whitespace-nowrap sticky left-0 bg-slate-50 z-10">Артикул</th>
            <th @click="setSort('sales_count')"      class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Продажи <component :is="SortIcon('sales_count')" class="w-3 h-3" /></span></th>
            <th @click="setSort('returns_count')"    class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Возвраты <component :is="SortIcon('returns_count')" class="w-3 h-3" /></span></th>
            <th @click="setSort('return_rate')"      class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">% возвр. <component :is="SortIcon('return_rate')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_revenue')"    class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Выручка <component :is="SortIcon('total_revenue')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_payout')"     class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">К выплате <component :is="SortIcon('total_payout')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_commission')" class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Комиссия <component :is="SortIcon('total_commission')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_delivery')"   class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Логистика <component :is="SortIcon('total_delivery')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_storage')"    class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Хранение <component :is="SortIcon('total_storage')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_penalty')"    class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Штрафы <component :is="SortIcon('total_penalty')" class="w-3 h-3" /></span></th>
            <th @click="setSort('total_cogs')"       class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">С/с-ть <component :is="SortIcon('total_cogs')" class="w-3 h-3" /></span></th>
            <th @click="setSort('gross_profit')"     class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Вал. прибыль <component :is="SortIcon('gross_profit')" class="w-3 h-3" /></span></th>
            <th @click="setSort('net_profit')"       class="cursor-pointer px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap hover:text-slate-800"><span class="flex items-center justify-end gap-1">Чист. прибыль <component :is="SortIcon('net_profit')" class="w-3 h-3" /></span></th>
            <th class="px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap">Сред. цена</th>
            <th class="px-3 py-3 text-right text-slate-500 font-medium whitespace-nowrap">Комиссия %</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="filtered.length === 0">
            <td colspan="15" class="text-center py-12 text-slate-400">
              Нет данных за выбранный период
            </td>
          </tr>
          <tr v-for="row in filtered" :key="row.sku" class="border-b border-slate-50 hover:bg-slate-50 transition-colors">
            <td class="px-4 py-3 font-mono text-slate-800 sticky left-0 bg-white hover:bg-slate-50 font-medium">
              {{ row.sku }}
              <span v-if="!row.has_cog" class="ml-1 text-amber-500 text-xs" title="Нет себестоимости">⚠</span>
            </td>
            <td class="px-3 py-3 text-right text-slate-700">{{ row.sales_count }}</td>
            <td class="px-3 py-3 text-right"><span :class="row.returns_count > 0 ? 'text-red-600 font-medium' : 'text-slate-400'">{{ row.returns_count }}</span></td>
            <td class="px-3 py-3 text-right"><span :class="row.return_rate > 20 ? 'text-red-600 font-bold' : row.return_rate > 10 ? 'text-amber-600' : 'text-slate-600'">{{ fmtN(row.return_rate) }}%</span></td>
            <td class="px-3 py-3 text-right text-slate-700">{{ fmt(row.total_revenue) }}</td>
            <td class="px-3 py-3 text-right text-slate-700">{{ fmt(row.total_payout) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmt(row.total_commission) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmt(row.total_delivery) }}</td>
            <td class="px-3 py-3 text-right text-xs" :class="row.total_storage > 0 ? 'text-amber-600' : 'text-slate-400'">{{ fmt(row.total_storage) }}</td>
            <td class="px-3 py-3 text-right text-xs" :class="row.total_penalty < 0 ? 'text-red-600 font-medium' : 'text-slate-400'">{{ fmt(row.total_penalty) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ row.has_cog ? fmt(row.total_cogs) : '—' }}</td>
            <td class="px-3 py-3 text-right font-medium" :class="profitColor(row.gross_profit)">{{ row.has_cog ? fmt(row.gross_profit) : '—' }}</td>
            <td class="px-3 py-3 text-right font-bold" :class="profitColor(row.net_profit)">{{ row.has_cog ? fmt(row.net_profit) : '—' }}</td>
            <td class="px-3 py-3 text-right text-slate-600 text-xs">{{ fmt(row.avg_retail_price) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmtN(row.avg_commission_pct) }}%</td>
          </tr>
        </tbody>
        <tfoot v-if="filtered.length > 0">
          <tr class="bg-slate-50 border-t-2 border-slate-200 font-semibold">
            <td class="px-4 py-3 text-slate-700 sticky left-0 bg-slate-50">Итого ({{ filtered.length }} арт.)</td>
            <td class="px-3 py-3 text-right text-slate-700">{{ totals.sales }}</td>
            <td class="px-3 py-3 text-right text-red-600">{{ totals.returns }}</td>
            <td class="px-3 py-3 text-right text-slate-500">{{ totals.sales ? fmtN(totals.returns / totals.sales * 100) : 0 }}%</td>
            <td class="px-3 py-3 text-right text-slate-700">{{ fmt(totals.revenue) }}</td>
            <td class="px-3 py-3 text-right text-slate-700">{{ fmt(totals.payout) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmt(totals.commission) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmt(totals.delivery) }}</td>
            <td class="px-3 py-3 text-right text-amber-600 text-xs">{{ fmt(totals.storage) }}</td>
            <td class="px-3 py-3 text-right text-red-600 text-xs">{{ fmt(totals.penalty) }}</td>
            <td class="px-3 py-3 text-right text-slate-500 text-xs">{{ fmt(totals.cogs) }}</td>
            <td class="px-3 py-3 text-right font-bold" :class="profitColor(totals.gross)">{{ fmt(totals.gross) }}</td>
            <td class="px-3 py-3 text-right font-bold" :class="profitColor(totals.net)">{{ fmt(totals.net) }}</td>
            <td colspan="2"></td>
          </tr>
        </tfoot>
      </table>
    </div>

    <div class="mt-4 text-xs text-slate-400 space-y-0.5">
      <p>Валовая прибыль = К выплате WB − Себестоимость</p>
      <p>Чистая прибыль = Валовая − Хранение − Штрафы − Удержания − Перевыставление логистики возвратов</p>
      <p>⚠ — нет себестоимости в wb_cogs, прибыль не рассчитана</p>
    </div>
  </div>
</template>
