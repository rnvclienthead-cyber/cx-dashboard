<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { DollarSign, TrendingDown, Package, AlertCircle, Calendar, ChevronLeft, ChevronRight, Search, X, Grid, BarChart3, ChevronDown } from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const rawData = ref([])
const loading = ref(true)

// Календарь
const todayObj = new Date()
const startOfMonthStr = `${todayObj.getFullYear()}-${String(todayObj.getMonth() + 1).padStart(2, '0')}-01`
const endOfTodayStr = `${todayObj.getFullYear()}-${String(todayObj.getMonth() + 1).padStart(2, '0')}-${String(todayObj.getDate()).padStart(2, '0')}`

const startDate = ref(startOfMonthStr)
const endDate = ref(endOfTodayStr)
const showCalendarPopover = ref(false)
const calendarYear = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

const abcXyzChart = ref(null)
const trendChart = ref(null)
const invoiceChart = ref(null)

const selectedSku = ref('Все')
const skuSearch = ref('')
const showSkuDropdown = ref(false)
const expandedSku = ref(null)
const activeFilter = ref('revenue') // 'revenue' | 'profit' | 'cost' | 'risk'

const toggleFilter = (type) => {
  activeFilter.value = activeFilter.value === type ? 'revenue' : type
}

const loadFinances = async () => {
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/finances/loss-analytics?platform=${platformStore.platform}`)
    const json = await res.json()
    rawData.value = json.data || []
  } catch (e) {
    console.error("Ошибка загрузки финансов:", e)
  } finally {
    loading.value = false
    await nextTick()
    renderCharts()
  }
}

// --- ЛОГИКА КАЛЕНДАРЯ ---
const changeCalendarMonth = (offset) => {
  calendarMonth.value += offset
  if (calendarMonth.value < 0) { calendarMonth.value = 11; calendarYear.value-- } 
  else if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
}

const setPreviousMonth = () => {
  const d = new Date()
  d.setMonth(d.getMonth() - 1)
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const lastDay = new Date(year, d.getMonth() + 1, 0).getDate()
  startDate.value = `${year}-${month}-01`
  endDate.value = `${year}-${month}-${lastDay}`
  calendarYear.value = year
  calendarMonth.value = d.getMonth()
  showCalendarPopover.value = false
}

const handleCalendarDayClick = (d) => {
  if (!d.isCurrentMonth || !d.dateStr) return
  if (!startDate.value || (startDate.value && endDate.value)) { startDate.value = d.dateStr; endDate.value = '' } 
  else {
    if (d.dateStr < startDate.value) { startDate.value = d.dateStr; endDate.value = '' } 
    else { endDate.value = d.dateStr; showCalendarPopover.value = false }
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
    const mStr = String(calendarMonth.value + 1).padStart(2, '0'); const dStr = String(i).padStart(2, '0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})

const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

// Получить ключи последних 6 месяцев (YYYY-MM)
const getLast6MonthsKeys = () => {
  const d = new Date()
  const res = []
  for(let i=5; i>=0; i--) {
    const d2 = new Date(d.getFullYear(), d.getMonth() - i, 1)
    const y = d2.getFullYear()
    const m = String(d2.getMonth() + 1).padStart(2, '0')
    res.push(`${y}-${m}`)
  }
  return res
}

// --- ФИЛЬТРАЦИЯ ---
const dateFilteredData = computed(() => {
  if (!startDate.value || !endDate.value) return rawData.value
  return rawData.value.filter(r => r.created_dt >= startDate.value && r.created_dt <= endDate.value)
})

const skuFilteredData = computed(() => {
  if (selectedSku.value === 'Все') return dateFilteredData.value
  return dateFilteredData.value.filter(r => r.sku === selectedSku.value)
})

const skuOptions = computed(() => ['Все', ...Array.from(new Set(rawData.value.map(d => d.sku)))].sort())
const filteredSkuList = computed(() => skuOptions.value.filter(s => String(s).toLowerCase().includes(skuSearch.value.toLowerCase())))

// --- ВЫЧИСЛЕНИЯ KPI ---
const totalLoss = computed(() => {
  return skuFilteredData.value
    .filter(r => r.status === 'Одобрено' && r.cost_type !== 'none')
    .reduce((sum, r) => sum + Number(r.cost), 0)
})

const pendingLoss = computed(() => {
  return skuFilteredData.value
    .filter(r => ['На рассмотрении', 'Активная'].includes(r.status) && r.cost_type !== 'none')
    .reduce((sum, r) => sum + Number(r.cost), 0)
})

// Недополученный доход: цена продажи × одобренные возвраты
const lostRevenue = computed(() => {
  return skuFilteredData.value
    .filter(r => r.status === 'Одобрено' && r.has_retail_price)
    .reduce((sum, r) => sum + Number(r.retail_price || 0), 0)
})

// Недополученная прибыль: (цена - себестоимость) × одобренные возвраты
const lostProfit = computed(() => {
  return skuFilteredData.value
    .filter(r => r.status === 'Одобрено' && r.has_retail_price && r.cost_type !== 'none')
    .reduce((sum, r) => sum + Math.max(0, Number(r.retail_price || 0) - Number(r.cost || 0)), 0)
})

// Есть ли данные о ценах продажи?
const hasRetailPrices = computed(() => skuFilteredData.value.some(r => r.has_retail_price))
const retailPriceCoverage = computed(() => {
  const approved = skuFilteredData.value.filter(r => r.status === 'Одобрено')
  if (!approved.length) return 0
  return Math.round(approved.filter(r => r.has_retail_price).length / approved.length * 100)
})

const skuTableData = computed(() => {
  const map = {}
  dateFilteredData.value.forEach(r => {
    if (!map[r.sku]) {
      map[r.sku] = {
        sku: r.sku, abc: r.abc_group, xyz: r.xyz_group,
        defectsCount: 0, approvedLoss: 0, pendingLoss: 0,
        lostRevenue: 0, lostProfit: 0,
        knownCostCount: 0, totalCostSum: 0,
        retailCount: 0, totalRetailSum: 0,
        costType: 'none', invoices: {}
      }
    }
    const m = map[r.sku]

    if (r.status === 'Одобрено') {
      m.defectsCount++
      if (r.cost_type !== 'none') {
        m.approvedLoss += Number(r.cost)
        const inv = r.invoice
        if (!m.invoices[inv]) m.invoices[inv] = { count: 0, sum: 0 }
        m.invoices[inv].count++
        m.invoices[inv].sum += Number(r.cost)
      }
      if (r.has_retail_price) {
        const rp = Number(r.retail_price || 0)
        m.lostRevenue += rp
        m.retailCount++
        m.totalRetailSum += rp
        if (r.cost_type !== 'none') {
          m.lostProfit += Math.max(0, rp - Number(r.cost || 0))
        }
      }
    } else if (r.status === 'На рассмотрении' && r.cost_type !== 'none') {
      m.pendingLoss += Number(r.cost)
    }

    if (r.cost_type !== 'none') {
      m.knownCostCount++
      m.totalCostSum += Number(r.cost)
      if (r.cost_type === 'exact' || m.costType === 'none') m.costType = r.cost_type
    }
  })

  const sortKey = activeFilter.value === 'revenue' ? 'lostRevenue'
    : activeFilter.value === 'profit' ? 'lostProfit'
    : activeFilter.value === 'risk' ? 'pendingLoss'
    : 'approvedLoss'

  return Object.values(map)
    .filter(item => item.defectsCount > 0 || item.pendingLoss > 0)
    .map(item => ({
      ...item,
      avgCost:        item.knownCostCount > 0 ? item.totalCostSum / item.knownCostCount : 0,
      avgRetailPrice: item.retailCount    > 0 ? item.totalRetailSum / item.retailCount  : 0,
      avgProfit:      (item.retailCount > 0 && item.knownCostCount > 0)
                        ? (item.totalRetailSum / item.retailCount) - (item.totalCostSum / item.knownCostCount)
                        : 0,
      invoicesList: Object.entries(item.invoices).map(([inv, d]) => ({ invoice: inv, ...d })).sort((a,b) => b.sum - a.sum)
    }))
    .sort((a, b) => b[sortKey] - a[sortKey])
})

const tableAmountLabel = computed(() => {
  if (activeFilter.value === 'revenue') return 'Недопол. доход'
  if (activeFilter.value === 'profit')  return 'Недопол. прибыль'
  if (activeFilter.value === 'risk')    return 'В риске (с/с)'
  return 'Потери по с/с'
})
const tableAmount = (row) => {
  if (activeFilter.value === 'revenue') return row.lostRevenue
  if (activeFilter.value === 'profit')  return row.lostProfit
  if (activeFilter.value === 'risk')    return row.pendingLoss
  return row.approvedLoss
}

const formatMoney = (val) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(val)

// --- ПОСТРОЕНИЕ ГРАФИКОВ ---
const renderCharts = () => {
  if (!abcXyzChart.value || !trendChart.value || !invoiceChart.value) return

  // 1. МАТРИЦА ABC-XYZ — значения зависят от активного фильтра
  const matrixData = { A: { X:{s:0, t:{}}, Y:{s:0, t:{}}, Z:{s:0, t:{}}, '-':{s:0, t:{}} }, B: { X:{s:0, t:{}}, Y:{s:0, t:{}}, Z:{s:0, t:{}}, '-':{s:0, t:{}} }, C: { X:{s:0, t:{}}, Y:{s:0, t:{}}, Z:{s:0, t:{}}, '-':{s:0, t:{}} } };
  const af = activeFilter.value
  const matrixSource = dateFilteredData.value.filter(r => {
    if (af === 'risk')    return r.status === 'На рассмотрении' && r.cost_type !== 'none'
    if (af === 'revenue') return r.status === 'Одобрено' && r.has_retail_price
    if (af === 'profit')  return r.status === 'Одобрено' && r.has_retail_price && r.cost_type !== 'none'
    return r.status === 'Одобрено' && r.cost_type !== 'none'
  })

  matrixSource.forEach(r => {
      const a = r.abc_group || 'C'; const x = r.xyz_group || '-'
      if (!matrixData[a] || !matrixData[a][x]) return
      const val = af === 'revenue' ? Number(r.retail_price || 0)
                : af === 'profit'  ? Math.max(0, Number(r.retail_price || 0) - Number(r.cost || 0))
                : Number(r.cost)
      matrixData[a][x].s += val
      matrixData[a][x].t[r.sku] = (matrixData[a][x].t[r.sku] || 0) + val
  })

  const yLabels = ['C', 'B', 'A']
  const xLabels = ['X', 'Y', 'Z', '-']
  const zValues = yLabels.map(y => xLabels.map(x => matrixData[y][x].s))
  const textValues = yLabels.map(y => xLabels.map(x => matrixData[y][x].s > 0 ? formatMoney(matrixData[y][x].s) : ''))
  
  const customData = yLabels.map(y => xLabels.map(x => {
      const skuObj = matrixData[y][x].t
      const sorted = Object.entries(skuObj).sort((a,b) => b[1] - a[1]).slice(0, 3)
      if (sorted.length === 0) return 'Нет данных'
      return sorted.map(s => `• ${s[0]}: ${formatMoney(s[1])}`).join('<br>')
  }))

  // Находим максимум для расчета контраста текста
  let maxZ = 0;
  zValues.forEach(row => row.forEach(val => { if (val > maxZ) maxZ = val; }));

  // Создаем слой аннотаций, чтобы текст читался на любом фоне
  const heatmapAnnotations = [];
  yLabels.forEach((y, idxY) => {
      xLabels.forEach((x, idxX) => {
          const val = matrixData[y][x].s;
          if (val > 0) {
              const textColor = val > (maxZ * 0.35) ? 'white' : '#334155'; // Если фон темный - текст белый
              heatmapAnnotations.push({
                  x: x, y: y,
                  text: `<b>${formatMoney(val)}</b>`,
                  font: { family: 'Inter, sans-serif', size: 11, color: textColor },
                  showarrow: false
              });
          }
      });
  });

  const heatmapTrace = {
      z: zValues, x: xLabels, y: yLabels, type: 'heatmap',
      colorscale: [[0, '#f8fafc'], [1, '#e11d48']],
      text: textValues, customdata: customData,
      hovertemplate: "Класс %{y}-%{x}<br>Потери: %{text}<br>---<br><b>Топ товары в блоке:</b><br>%{customdata}<extra></extra>",
      showscale: false
  }

  Plotly.newPlot(abcXyzChart.value, [heatmapTrace], {
      margin: { t: 20, l: 40, r: 20, b: 40 },
      xaxis: { title: 'XYZ (Стабильность спроса)', tickfont: { weight: 'bold' } },
      yaxis: { title: 'ABC (Объем продаж)', tickfont: { weight: 'bold' } },
      annotations: heatmapAnnotations,
      height: 300, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent'
  }, { responsive: true, displayModeBar: false })


  // 2. ДИНАМИКА ПО МЕСЯЦАМ (Только месяцы с данными, полные названия)
  const last6Keys = getLast6MonthsKeys()
  const monthsMap = {}
  last6Keys.forEach(k => monthsMap[k] = 0)
  
  const trendSource = rawData.value.filter(r => {
      if (selectedSku.value !== 'Все' && r.sku !== selectedSku.value) return false
      const m = r.created_dt ? r.created_dt.substring(0, 7) : ''
      if (!last6Keys.includes(m)) return false
      if (af === 'risk')    return r.status === 'На рассмотрении' && r.cost_type !== 'none'
      if (af === 'revenue') return r.status === 'Одобрено' && r.has_retail_price
      if (af === 'profit')  return r.status === 'Одобрено' && r.has_retail_price && r.cost_type !== 'none'
      return r.status === 'Одобрено' && r.cost_type !== 'none'
  })

  trendSource.forEach(r => {
      const m = r.created_dt.substring(0, 7)
      const val = af === 'revenue' ? Number(r.retail_price || 0)
                : af === 'profit'  ? Math.max(0, Number(r.retail_price || 0) - Number(r.cost || 0))
                : Number(r.cost)
      monthsMap[m] += val
  })

  // Оставляем только те месяцы, в которых убыток > 0
  const activeKeys = last6Keys.filter(k => monthsMap[k] > 0)
  
  const xLabelsActive = activeKeys.map(m => {
      const [y, mo] = m.split('-'); 
      return `${monthNames[Number(mo)-1]} ${y}` // Полное название месяца и год
  })

  const barTrace = {
      x: xLabelsActive, y: activeKeys.map(k => monthsMap[k]), type: 'bar',
      marker: { color: '#2563eb', borderRadius: 4 },
      text: activeKeys.map(k => formatMoney(monthsMap[k])), 
      textposition: 'outside',
      textfont: { size: 10, color: '#475569', weight: 'bold' },
      hovertemplate: "%{x}<br>Потери: %{text}<extra></extra>"
  }

  Plotly.newPlot(trendChart.value, [barTrace], {
      margin: { t: 30, l: 60, r: 20, b: 40 }, height: 300,
      yaxis: { rangemode: 'tozero', showgrid: true, gridcolor: '#f1f5f9' },
      xaxis: { type: 'category', tickfont: { weight: 'bold' } },
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent'
  }, { responsive: true, displayModeBar: false })


  // 3. РЕЙТИНГ ПО ИНВОЙСАМ
  const invSource = dateFilteredData.value.filter(r => r.cost_type !== 'none' && r.status === 'Одобрено' && r.invoice && r.invoice !== 'Не указан')
  const invMap = {}
  invSource.forEach(r => {
      invMap[r.invoice] = (invMap[r.invoice] || 0) + Number(r.cost)
  })
  
  const sortedInvs = Object.entries(invMap).sort((a,b) => b[1] - a[1]).slice(0, 10).reverse() 
  
  const invTrace = {
      y: sortedInvs.map(i => i[0]), x: sortedInvs.map(i => i[1]),
      type: 'bar', orientation: 'h', marker: { color: '#fbbf24', borderRadius: 4 },
      text: sortedInvs.map(i => formatMoney(i[1])),
      textposition: 'outside',
      textfont: { size: 10, color: '#475569', weight: 'bold' },
      hovertemplate: "Инвойс: %{y}<br>Потери: %{text}<extra></extra>" // Заменили %{x} на %{text}, чтобы избежать 'k'
  }
  
  Plotly.newPlot(invoiceChart.value, [invTrace], {
      margin: { t: 20, l: 120, r: 90, b: 40 }, height: 350,
      xaxis: { rangemode: 'tozero', showgrid: true, gridcolor: '#f1f5f9' },
      yaxis: { type: 'category', tickfont: { weight: 'bold' } },
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent'
  }, { responsive: true, displayModeBar: false })
}

watch(() => platformStore.platform, loadFinances)
watch([startDate, endDate, selectedSku, activeFilter], () => {
  nextTick(() => renderCharts())
})

onMounted(loadFinances)
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen font-sans max-w-[1600px] text-slate-800 antialiased" @click="showCalendarPopover = false; showSkuDropdown = false">
    
    <div class="flex flex-col md:flex-row justify-between md:items-end gap-4 mb-8 border-b border-slate-200 pb-5">
      <div class="flex items-center gap-4">
        <div class="p-3 bg-rose-600 text-white rounded-xl shadow-lg shadow-rose-200/50">
          <DollarSign class="w-6 h-6" />
        </div>
        <div>
          <h1 class="text-xl font-black tracking-tight text-slate-900">Cost of Quality (COQ)</h1>
          <p class="text-sm text-slate-500 font-medium">Финансовые потери от производственного брака</p>
        </div>
      </div>
      
      <div class="flex gap-4">
        <div class="relative w-64">
          <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-4 py-3 cursor-pointer shadow-sm hover:border-blue-500 transition-colors" @click.stop="showSkuDropdown = !showSkuDropdown">
            <Search class="w-4 h-4 text-blue-500 mr-2" />
            <span class="text-sm font-bold text-slate-700 truncate flex-1">{{ selectedSku === 'Все' ? 'Все артикулы' : selectedSku }}</span>
            <X v-if="selectedSku !== 'Все'" class="w-4 h-4 text-slate-300 hover:text-red-500" @click.stop="selectedSku = 'Все'" />
          </div>
          <div v-if="showSkuDropdown" class="absolute right-0 mt-2 bg-white border border-slate-100 rounded-2xl shadow-xl z-50 p-3 w-72" @click.stop>
            <input type="text" v-model="skuSearch" class="w-full border border-slate-200 bg-slate-50 rounded-xl p-2 text-sm mb-2 focus:outline-none focus:border-blue-500" placeholder="Поиск..." />
            <div class="max-h-60 overflow-y-auto custom-scroll pr-1">
              <div v-for="sku in filteredSkuList" :key="sku" @click="selectedSku = sku; showSkuDropdown = false" class="p-2 text-sm font-bold hover:bg-blue-50 rounded-lg cursor-pointer text-slate-700">{{ sku }}</div>
            </div>
          </div>
        </div>

        <div class="relative">
          <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-5 py-3 cursor-pointer hover:border-blue-500 transition-colors shadow-sm" @click.stop="showCalendarPopover = !showCalendarPopover">
            <Calendar class="w-4 h-4 text-blue-500 mr-3" />
            <span class="text-sm font-bold text-slate-700">{{ formatDateDisplay(startDate) }} — {{ formatDateDisplay(endDate) }}</span>
          </div>
          <div v-if="showCalendarPopover" class="absolute right-0 mt-3 bg-white border border-slate-100 rounded-3xl shadow-2xl z-[150] p-5 w-80" @click.stop>
            <div class="mb-4 border-b border-slate-100 pb-3">
              <button @click.stop="setPreviousMonth" class="w-full text-center text-xs font-black bg-blue-50 hover:bg-blue-100 text-blue-700 py-2.5 rounded-xl transition-all shadow-sm tracking-wide uppercase border border-blue-100/50">Прошлый месяц</button>
            </div>
            <div class="flex justify-between items-center mb-4">
              <button @click="changeCalendarMonth(-1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500"><ChevronLeft class="w-4 h-4"/></button>
              <span class="text-sm font-black text-slate-800">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
              <button @click="changeCalendarMonth(1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500"><ChevronRight class="w-4 h-4"/></button>
            </div>
            <div class="grid grid-cols-7 gap-1 text-center text-[10px] font-black text-slate-400 uppercase mb-2">
              <div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div>
            </div>
            <div class="grid grid-cols-7 gap-1">
              <div v-for="(d, idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)" :class="['h-9 flex items-center justify-center text-sm font-bold rounded-xl select-none', !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer', d.dateStr === startDate || d.dateStr === endDate ? 'bg-blue-600 text-white shadow-md' : '', d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-blue-50 text-blue-700' : '', d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : '']">{{ d.day }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-400 font-bold tracking-wide animate-pulse">
      💸 Подсчет финансовой модели...
    </div>

    <div v-else class="space-y-6">
      
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5">

        <!-- Недополученный доход -->
        <div @click="toggleFilter('revenue')"
             :class="['rounded-3xl p-6 shadow-sm relative overflow-hidden border cursor-pointer transition-all select-none',
                      activeFilter === 'revenue' ? 'ring-2 ring-indigo-400 border-indigo-400 bg-indigo-50' : hasRetailPrices ? 'bg-white border-indigo-200 hover:border-indigo-400' : 'bg-slate-50 border-slate-200 opacity-60']">
          <div class="flex items-center gap-2 mb-2">
            <DollarSign class="w-5 h-5 text-indigo-500" />
            <span class="text-[10px] font-black text-indigo-600 uppercase tracking-widest">Недополученный доход</span>
          </div>
          <div v-if="hasRetailPrices" class="text-3xl font-black text-slate-900 tracking-tight">{{ formatMoney(lostRevenue) }}</div>
          <div v-else class="text-base font-bold text-slate-400">Нет данных о ценах</div>
          <div class="text-[10px] font-semibold text-slate-400 mt-2">
            <span v-if="hasRetailPrices">Цена продажи × одобренные возвраты ({{ retailPriceCoverage }}% покрытие)</span>
            <span v-else>Заполнится после синхронизации продаж</span>
          </div>
        </div>

        <!-- Недополученная прибыль -->
        <div @click="toggleFilter('profit')"
             :class="['rounded-3xl p-6 shadow-sm relative overflow-hidden border cursor-pointer transition-all select-none',
                      activeFilter === 'profit' ? 'ring-2 ring-purple-400 border-purple-400 bg-purple-50' : hasRetailPrices ? 'bg-white border-purple-200 hover:border-purple-400' : 'bg-slate-50 border-slate-200 opacity-60']">
          <div class="flex items-center gap-2 mb-2">
            <TrendingDown class="w-5 h-5 text-purple-500" />
            <span class="text-[10px] font-black text-purple-600 uppercase tracking-widest">Недополученная прибыль</span>
          </div>
          <div v-if="hasRetailPrices" class="text-3xl font-black text-slate-900 tracking-tight">{{ formatMoney(lostProfit) }}</div>
          <div v-else class="text-base font-bold text-slate-400">Нет данных о ценах</div>
          <div class="text-[10px] font-semibold text-slate-400 mt-2">
            <span v-if="hasRetailPrices">(Цена − с/с) × одобренные возвраты</span>
            <span v-else>Заполнится после синхронизации продаж</span>
          </div>
        </div>

        <!-- Потери по себестоимости -->
        <div @click="toggleFilter('cost')"
             :class="['rounded-3xl p-6 shadow-sm relative overflow-hidden border cursor-pointer transition-all select-none',
                      activeFilter === 'cost' ? 'ring-2 ring-rose-400 border-rose-400 bg-rose-50' : 'bg-white border-rose-200 hover:border-rose-400']">
          <div class="flex items-center gap-2 mb-2">
            <TrendingDown class="w-5 h-5 text-rose-500" />
            <span class="text-[10px] font-black text-rose-500 uppercase tracking-widest">Потери по себестоимости</span>
          </div>
          <div class="text-3xl font-black text-slate-900 tracking-tight">{{ formatMoney(totalLoss) }}</div>
          <div class="text-[10px] font-semibold text-slate-400 mt-2">Одобренные возвраты × с/с</div>
        </div>

        <!-- В риске -->
        <div @click="toggleFilter('risk')"
             :class="['rounded-3xl p-6 shadow-sm relative overflow-hidden border cursor-pointer transition-all select-none',
                      activeFilter === 'risk' ? 'ring-2 ring-amber-400 border-amber-400 bg-amber-50' : 'bg-white border-amber-200 hover:border-amber-400']">
          <div class="flex items-center gap-2 mb-2">
            <AlertCircle class="w-5 h-5 text-amber-500" />
            <span class="text-[10px] font-black text-amber-600 uppercase tracking-widest">В риске</span>
          </div>
          <div class="text-3xl font-black text-slate-900 tracking-tight">{{ formatMoney(pendingLoss) }}</div>
          <div class="text-[10px] font-semibold text-slate-400 mt-2">На рассмотрении × с/с</div>
        </div>

      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
          <h3 class="text-sm font-black text-slate-800 flex items-center gap-2 mb-4"><Grid class="w-4 h-4 text-blue-500"/> Финансовая матрица (ABC-XYZ)</h3>
          <div ref="abcXyzChart" class="w-full"></div>
        </div>
        <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
          <h3 class="text-sm font-black text-slate-800 flex items-center gap-2 mb-4"><BarChart3 class="w-4 h-4 text-blue-500"/> График потерь (последние 6 мес.)</h3>
          <div ref="trendChart" class="w-full"></div>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 bg-slate-50/50 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <Package class="w-5 h-5 text-slate-500"/>
            <h2 class="text-sm font-black text-slate-800 tracking-wide">Рейтинг потерь по артикулам</h2>
          </div>
        </div>
        
        <div class="overflow-y-auto max-h-[600px] custom-scroll">
          <table class="w-full text-left border-collapse text-sm">
            <thead>
              <tr class="bg-white text-slate-400 border-b border-slate-200 uppercase font-bold text-[10px] tracking-wider sticky top-0 shadow-sm z-10">
                <th class="p-4 w-12 text-center">#</th>
                <th class="p-4 min-w-[180px]">Артикул продавца</th>
                <th class="p-4 text-center w-20">Группа</th>
                <th class="p-4 text-center w-20">Штук</th>
                <th class="p-4 text-right w-32">Ср. цена продажи</th>
                <th class="p-4 text-right w-28">Ср. прибыль</th>
                <th class="p-4 text-right w-32">Ср. С/С</th>
                <th class="p-4 text-right w-36">{{ tableAmountLabel }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 font-semibold text-slate-700">
              <template v-for="(row, idx) in skuTableData" :key="row.sku">
                
                <tr class="hover:bg-slate-50 transition-colors cursor-pointer group" @click="expandedSku = expandedSku === row.sku ? null : row.sku">
                  <td class="p-4 text-center text-slate-400">
                    <div class="flex items-center justify-center gap-2">
                       <ChevronDown :class="['w-4 h-4 transition-transform text-blue-400', expandedSku === row.sku ? 'rotate-180 text-blue-600' : '']" />
                       {{ idx + 1 }}
                    </div>
                  </td>
                  <td class="p-4 font-bold text-slate-900 truncate">{{ row.sku }}</td>
                  <td class="p-4 text-center">
                    <span class="px-2 py-1 rounded text-[10px] font-black bg-slate-100 text-slate-600">{{ row.abc }}-{{ row.xyz }}</span>
                  </td>
                  <td class="p-4 text-center font-bold text-rose-500">{{ row.defectsCount }}</td>
                  
                  <!-- Ср. цена продажи -->
                  <td class="p-4 text-right">
                    <span :class="row.avgRetailPrice > 0 ? 'text-slate-600 font-semibold' : 'text-slate-300'">
                      {{ row.avgRetailPrice > 0 ? formatMoney(row.avgRetailPrice) : '—' }}
                    </span>
                  </td>

                  <!-- Ср. прибыль -->
                  <td class="p-4 text-right">
                    <span :class="row.avgProfit > 0 ? 'text-emerald-600 font-semibold' : row.avgProfit < 0 ? 'text-rose-500 font-semibold' : 'text-slate-300'">
                      {{ row.avgRetailPrice > 0 && row.avgCost > 0 ? formatMoney(row.avgProfit) : '—' }}
                    </span>
                  </td>

                  <!-- Ср. С/С -->
                  <td class="p-4 text-right">
                    <span :class="row.costType === 'none' ? 'text-slate-300' : 'text-slate-600 font-semibold'">
                      {{ row.costType !== 'none' ? formatMoney(row.avgCost) : '—' }}
                    </span>
                    <div v-if="row.costType === 'approximate'" class="text-[9px] text-amber-500 font-black">~ прибл.</div>
                  </td>

                  <!-- Динамическая сумма -->
                  <td class="p-4 text-right text-base font-black">
                    <span v-if="tableAmount(row) <= 0" class="text-slate-300">—</span>
                    <span v-else :class="activeFilter === 'risk' ? 'text-amber-600' : activeFilter === 'revenue' ? 'text-indigo-600' : activeFilter === 'profit' ? 'text-purple-600' : 'text-rose-600'">
                      {{ formatMoney(tableAmount(row)) }}
                    </span>
                  </td>
                </tr>

                <tr v-if="expandedSku === row.sku" class="bg-slate-50/50 border-b border-slate-100">
                   <td colspan="8" class="p-4 px-12">
                      <div class="bg-white border border-blue-100 rounded-xl p-4 shadow-sm animate-in slide-in-from-top-2">
                         <h4 class="text-xs font-black text-slate-800 mb-3 flex items-center gap-2">
                            <Package class="w-3 h-3 text-blue-500" /> Детализация убытков по инвойсам:
                         </h4>
                         <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
                            <div v-for="inv in row.invoicesList" :key="inv.invoice" class="bg-slate-50 border border-slate-100 rounded-lg p-3 text-xs shadow-sm hover:border-blue-200 transition-colors">
                               <div class="font-bold text-slate-700 truncate mb-1"># {{ inv.invoice }}</div>
                               <div class="text-slate-500 flex justify-between items-center">
                                  <span>{{ inv.count }} шт.</span>
                                  <span class="text-rose-600 font-bold">{{ formatMoney(inv.sum) }}</span>
                               </div>
                            </div>
                         </div>
                      </div>
                   </td>
                </tr>

              </template>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm mt-6">
        <h3 class="text-sm font-black text-slate-800 flex items-center gap-2 mb-4"><BarChart3 class="w-4 h-4 text-blue-500"/> Рейтинг убытков по инвойсам (Топ 10)</h3>
        <p class="text-xs text-slate-400 mb-2">Отображаются одобренные возвраты с привязанными инвойсами за выбранный период.</p>
        <div ref="invoiceChart" class="w-full"></div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>