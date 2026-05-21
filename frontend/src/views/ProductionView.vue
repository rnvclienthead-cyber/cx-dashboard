<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { LineChart, LayoutGrid, PackageOpen, X, BarChart2 } from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'

const claims = ref([])
const loading = ref(true)
const plotlyChart = ref(null)

const today = new Date()
const y = today.getFullYear()
const m = String(today.getMonth() + 1).padStart(2, '0')
const d = String(today.getDate()).padStart(2, '0')

const startDate = ref(`${y}-${m}-01`)
const endDate = ref(`${y}-${m}-${d}`)
const selectedSku = ref('Все')
const selectedInvoice = ref('Все')

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const fetchData = async () => {
  loading.value = true
  console.time("Время обработки данных")
  try {
    const res = await fetch('http://127.0.0.1:8001/api/v1/analytics/production-claims')
    const json = await res.json()
    claims.value = json.data || []
  } catch (e) { 
    console.error("Ошибка загрузки:", e) 
  } finally { 
    loading.value = false
    console.timeEnd("Время обработки данных")
  }
}

// 🛡️ НЕУБИВАЕМЫЙ ПАРСЕР ДАТ С ПОДДЕРЖКОЙ ISO И ОБЫЧНЫХ СТРОК
const parseDate = (dString) => {
  if (!dString) return new Date(0)
  if (typeof dString === 'number') return new Date(dString)
  
  // Если бэк прислал ISO-строку (например, "2026-05-01T12:00:00")
  if (typeof dString === 'string' && dString.includes('-')) {
    return new Date(dString)
  }
  
  // Если классический русский формат даты "ДД.ММ.ГГГГ"
  if (typeof dString === 'string' && dString.includes('.')) {
    const parts = dString.split(' ')[0].split('.')
    if (parts.length === 3) return new Date(`${parts[2]}-${parts[1]}-${parts[0]}`)
  }
  
  return new Date(dString)
}

// РАБОТАЮЩАЯ ФИЛЬТРАЦИЯ ПО ДАТАМ И СЕЛЕКТАМ
const filteredClaims = computed(() => {
  const fStart = new Date(startDate.value)
  fStart.setHours(0, 0, 0, 0)
  
  const fEnd = new Date(endDate.value)
  fEnd.setHours(23, 59, 59, 999)

  return claims.value.filter(c => {
    const rawDate = c['Дата и время оформления заявки на возврат'] || c['Дата заказа']
    const cDate = parseDate(rawDate)
    
    if (cDate.getTime() === 0 || isNaN(cDate.getTime())) return false
    if (cDate < fStart || cDate > fEnd) return false
    
    if (selectedSku.value !== 'Все' && c['Артикул продавца'] !== selectedSku.value) return false
    if (selectedInvoice.value !== 'Все' && c['Инвойс'] !== selectedInvoice.value) return false
    
    return true
  })
})

const skuList = computed(() => ['Все', ...Array.from(new Set(claims.value.map(c => c['Артикул продавца'] || 'Без артикула'))).sort()])
const invoiceList = computed(() => ['Все', ...Array.from(new Set(claims.value.map(c => c['Инвойс'] || 'Не указан'))).filter(i => i !== 'Не указан' && i !== '0').sort()])

const kpis = computed(() => {
  const total = filteredClaims.value.length
  let tagged = 0
  let corrected = 0
  
  filteredClaims.value.forEach(c => {
    const hasTags = Object.keys(CATEGORIES).some(id => ['1','1.0','+','true','да'].includes(String(c[id] || '').trim().toLowerCase()))
    if (hasTags) tagged++
    
    const corr = String(c['Корректировка'] || '').toLowerCase().trim()
    if (corr && !['nan', 'none', 'null', 'подтверждено', 'нет тегов'].includes(corr)) corrected++
  })
  
  const accuracy = tagged > 0 ? ((1 - (corrected / tagged)) * 100).toFixed(1) : 0
  const processed_percent = total > 0 ? ((tagged / total) * 100).toFixed(1) : 0
  
  return { total, tagged, processed_percent, corrected, accuracy }
})

// Подготовка матрицы
const matrixData = computed(() => {
  const skus = Array.from(new Set(filteredClaims.value.map(c => c['Артикул продавца'] || 'Без артикула'))).sort()
  const matrix = []
  
  Object.keys(CATEGORIES).forEach(catId => {
    const row = { id: catId, name: CATEGORIES[catId], total: 0, cells: {} }
    skus.forEach(sku => row.cells[sku] = 0)
    
    filteredClaims.value.forEach(c => {
      if (['1','1.0','+','true','да'].includes(String(c[catId] || '').trim().toLowerCase())) {
        const sKey = c['Артикул продавца'] || 'Без артикула'
        if (row.cells[sKey] !== undefined) {
          row.cells[sKey]++
          row.total++
        }
      }
    })
    matrix.push(row)
  })
  return { skus, rows: matrix.filter(r => r.total > 0).sort((a,b) => a.id - b.id) }
})

const getCellColor = (count, maxCount) => {
  if (count === 0) return 'bg-slate-50 text-transparent'
  const intensity = Math.min(1, count / (maxCount || 1))
  if (intensity > 0.7) return 'bg-blue-600 text-white font-bold'
  if (intensity > 0.4) return 'bg-blue-400 text-white font-bold'
  return 'bg-blue-100 text-blue-900'
}
const maxDefects = computed(() => Math.max(...matrixData.value.rows.flatMap(r => Object.values(r.cells)), 1))

const topInvoices = computed(() => {
  const map = {}
  filteredClaims.value.forEach(c => {
    const hasTags = Object.keys(CATEGORIES).some(id => ['1','1.0','+','true','да'].includes(String(c[id] || '').trim().toLowerCase()))
    if (!hasTags) return

    const inv = c['Инвойс'] || 'Не указан'
    if (inv === 'Не указан' || inv === '' || inv === '0') return
    if (!map[inv]) map[inv] = { count: 0, supplies: new Set() }
    map[inv].count++
    map[inv].supplies.add(c['Номер поставки_ОРИГИНАЛ'] || c['Номер поставки'])
  })
  return Object.entries(map).map(([inv, d]) => ({ inv, count: d.count, supplies: Array.from(d.supplies) })).sort((a,b) => b.count - a.count).slice(0, 15)
})
const maxInvoiceCount = computed(() => Math.max(...topInvoices.value.map(i => i.count), 1))

// БЕЗОПАСНЫЙ СВЯЗАННЫЙ ВАТЧЕР ДЛЯ ТРЕНДОВ И КОРРЕКТНОЙ РАБОТЫ PLOTLY
watch(selectedSku, async (newSku) => {
  if (newSku === 'Все') return
  
  await nextTick() // Ждем, пока нода графика появится в DOM дереве
  try {
    const res = await fetch(`http://127.0.0.1:8001/api/v1/analytics/sku-trend/${encodeURIComponent(newSku)}`)
    if (res.ok) {
      const json = await res.json()
      renderPlotlyChart(json.data || [])
    }
  } catch(e) { 
    console.error("Ошибка обновления графика Plotly:", e) 
  }
})

const renderPlotlyChart = (data) => {
  if (!plotlyChart.value) return

  const grouped = {}
  data.forEach(d => {
    if (!grouped[d.Источник]) grouped[d.Источник] = { x: [], y: [] }
    grouped[d.Источник].x.push(d.Месяц)
    grouped[d.Источник].y.push(d.Количество)
  })

  const traces = Object.keys(grouped).map(source => ({
    x: grouped[source].x,
    y: grouped[source].y,
    name: source,
    type: source === 'Общий брак' ? 'scatter' : 'bar',
    mode: source === 'Общий брак' ? 'lines+markers' : undefined,
    line: source === 'Общий брак' ? { color: '#e74c3c', width: 3, dash: 'dot' } : undefined,
    yaxis: source === 'Общий брак' ? 'y2' : 'y'
  }))

  const layout = {
    title: `Динамика дефектов: ${selectedSku.value}`,
    barmode: 'stack',
    height: 400,
    margin: { t: 40, l: 50, r: 50, b: 60 },
    legend: { orientation: 'h', y: -0.25, x: 0.5, xanchor: 'center' },
    yaxis: { title: 'Кол-во заявок', side: 'left' },
    yaxis2: { title: 'Исторический общий брак', overlaying: 'y', side: 'right', showgrid: false },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent'
  }

  Plotly.newPlot(plotlyChart.value, traces, layout, { responsive: true })
}

const modalData = ref({ isOpen: false, title: '', claims: [] })
const openMatrixDetails = (sku, catId, catName) => {
  const details = filteredClaims.value.filter(c => (c['Артикул продавца'] || 'Без артикула') === sku && ['1','1.0','+','true','да'].includes(String(c[catId] || '').trim().toLowerCase()))
  modalData.value = { isOpen: true, title: `📦 ${sku} | 🛠 ${catName}`, claims: details }
}
const openInvoiceDetails = (invoiceObj) => {
  modalData.value = { isOpen: true, title: `🧾 Инвойс: ${invoiceObj.inv}`, claims: filteredClaims.value.filter(c => c['Инвойс'] === invoiceObj.inv) }
}
const parsePhotos = (str) => str ? str.split(' ').map(g => g.split('|').pop().replace(/^\/\//, 'https://')).slice(0, 6) : []

onMounted(fetchData)
</script>

<template>
  <div class="p-6 w-full mx-auto pb-20 relative bg-slate-50 min-h-screen">
    <div class="flex items-center gap-3 mb-6">
      <div class="p-3 bg-emerald-100 text-emerald-600 rounded-xl"><LineChart class="w-6 h-6" /></div>
      <h1 class="text-2xl font-black text-slate-800 tracking-tight">Отчет производства</h1>
    </div>

    <div class="bg-white p-5 rounded-2xl border shadow-sm mb-6 grid grid-cols-1 md:grid-cols-4 gap-4">
      <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Начало</label><input type="date" v-model="startDate" class="w-full border border-slate-200 rounded-xl p-2.5 text-sm focus:outline-none focus:border-blue-500" /></div>
      <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Конец</label><input type="date" v-model="endDate" class="w-full border border-slate-200 rounded-xl p-2.5 text-sm focus:outline-none focus:border-blue-500" /></div>
      <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Артикул (Для тренда)</label><select v-model="selectedSku" class="w-full border border-slate-200 rounded-xl p-2.5 text-sm bg-white focus:outline-none focus:border-blue-500"><option v-for="sku in skuList" :key="sku" :value="sku">{{ sku }}</option></select></div>
      <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Инвойс / Поставка</label><select v-model="selectedInvoice" class="w-full border border-slate-200 rounded-xl p-2.5 text-sm bg-white focus:outline-none focus:border-blue-500"><option v-for="inv in invoiceList" :key="inv" :value="inv">{{ inv }}</option></select></div>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-500 font-medium animate-pulse">⚙️ База данных обрабатывается на сервере. Пожалуйста, подождите...</div>
    
    <div v-else class="space-y-6">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Всего заявок в периоде</div><div class="text-3xl font-black text-slate-800">{{ kpis.total }}</div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Размечено ИИ</div><div class="text-3xl font-black text-blue-600">{{ kpis.tagged }} <span class="text-sm font-semibold text-slate-400">({{ kpis.processed_percent }}%)</span></div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Изменено вручную</div><div class="text-3xl font-black text-amber-500">{{ kpis.corrected }}</div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Точность ИИ</div><div class="text-3xl font-black text-emerald-600">{{ kpis.accuracy }}%</div></div>
      </div>

      <div class="bg-white border rounded-2xl shadow-sm p-6">
        <div class="flex items-center gap-2 mb-4">
          <BarChart2 class="w-5 h-5 text-blue-500"/>
          <h2 class="text-lg font-bold text-slate-800">Динамика дефектов по месяцам</h2>
        </div>
        <div v-if="selectedSku === 'Все'" class="h-48 flex items-center justify-center border-2 border-dashed border-slate-100 rounded-xl text-slate-400 font-medium">
          Выберите конкретный артикул в панели фильтров, чтобы раскрыть интерактивный тренд
        </div>
        <div v-else class="w-full">
          <div ref="plotlyChart" class="w-full"></div>
        </div>
      </div>

      <div class="bg-white border rounded-2xl shadow-sm p-6 w-full overflow-hidden">
        <div class="flex items-center gap-2 mb-4">
          <LayoutGrid class="w-5 h-5 text-indigo-500" />
          <h2 class="text-lg font-bold text-slate-800">Матрица состояния</h2>
        </div>
        
        <div class="w-full overflow-x-auto">
          <table class="w-full border-collapse text-xs table-fixed">
            <thead>
              <tr class="bg-slate-50/75 text-slate-500 border-b border-slate-100">
                <th class="p-3 text-left font-bold w-64 sticky left-0 bg-white z-10 border-r border-slate-100">Причина / Артикул</th>
                <th v-for="sku in matrixData.skus" :key="sku" class="p-2 text-center font-bold truncate max-w-[90px]" :title="sku">
                  {{ sku }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="row in matrixData.rows" :key="row.id" class="hover:bg-slate-50/50 transition-colors">
                <td class="p-3 text-slate-700 font-semibold sticky left-0 bg-white z-10 border-r border-slate-100 truncate shadow-[2px_0_5px_rgba(0,0,0,0.02)]" :title="row.name">
                  {{ row.name }} <span class="text-xs text-slate-400 font-normal ml-1">[{{ row.total }}]</span>
                </td>
                <td v-for="sku in matrixData.skus" :key="sku" class="p-1 text-center">
                  <div 
                    @click="row.cells[sku] > 0 ? openMatrixDetails(sku, row.id, row.name) : null"
                    :class="['h-9 flex items-center justify-center rounded-lg text-xs transition-all duration-150', getCellColor(row.cells[sku], maxDefects), row.cells[sku] > 0 ? 'cursor-pointer hover:scale-105 hover:shadow-sm' : '']"
                  >
                    {{ row.cells[sku] > 0 ? row.cells[sku] : '' }}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white border rounded-2xl shadow-sm p-6">
        <div class="flex items-center gap-2 mb-6"><PackageOpen class="w-5 h-5 text-orange-500" /><h2 class="text-lg font-bold text-slate-800">Проблемные инвойсы (Топ-15)</h2></div>
        <div class="space-y-4">
          <div v-for="inv in topInvoices" :key="inv.inv" class="cursor-pointer group animate-fade-in" @click="openInvoiceDetails(inv)">
            <div class="flex justify-between text-sm mb-1"><span class="font-bold text-slate-700 group-hover:text-blue-600 transition-colors">🧾 {{ inv.inv }}</span><span class="font-semibold text-slate-500">{{ inv.count }} шт.</span></div>
            <div class="w-full bg-slate-100 rounded-full h-3 overflow-hidden"><div class="bg-orange-400 h-full rounded-full group-hover:bg-orange-500 transition-all duration-300" :style="{ width: `${(inv.count / maxInvoiceCount) * 100}%` }"></div></div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="modalData.isOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs" @click.self="modalData.isOpen = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col">
        <div class="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50 rounded-t-2xl"><h3 class="text-base font-black text-slate-800">{{ modalData.title }}</h3><button @click="modalData.isOpen = false" class="text-slate-400 hover:text-slate-600 transition-colors"><X class="w-5 h-5"/></button></div>
        <div class="p-6 overflow-y-auto space-y-4 bg-slate-100/30 flex-1">
          <div v-for="claim in modalData.claims" :key="claim.SRID" class="p-4 border border-slate-100 rounded-xl bg-white shadow-xs flex gap-4">
            <div class="flex-1">
              <div class="text-xs text-slate-400 mb-2 font-medium">Инвойс: <b class="text-slate-700 font-bold">{{ claim['Инвойс'] || '---' }}</b> | Поставка: <span class="font-semibold text-slate-600">{{ claim['Номер поставки_ОРИГИНАЛ'] || '---' }}</span></div>
              <p class="text-sm text-slate-700 bg-slate-50/75 p-3 rounded-lg border border-slate-100/50 leading-relaxed font-medium">{{ claim['Комментарий покупателя'] || 'Покупатель не оставил текстового комментария.' }}</p>
            </div>
            <div v-if="claim.photos" class="w-1/3 border-l border-slate-100 pl-4 flex flex-wrap gap-2 content-start justify-end">
              <a v-for="(img, i) in parsePhotos(claim.photos)" :key="i" :href="img" target="_blank" class="block w-12 h-12 rounded-lg overflow-hidden border border-slate-200 bg-slate-50 hover:scale-105 transition-transform"><img :src="img" class="w-full h-full object-cover" referrerpolicy="no-referrer" /></a>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>