<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { AlertTriangle, TrendingUp, TableProperties, FileSpreadsheet } from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'

const rawDataset = ref([])
const claimsDetail = ref([]) 
const loading = ref(true)
const trendChart = ref(null)

// Настройка фильтра дат
const today = new Date()
const startDate = ref(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-01`)
const endDate = ref(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`)
const selectedObjects = ref(['[Все артикулы]'])
const clickedSku = ref(null)

const claimForm = ref({
  factory: 'Уточняется', number: '', invoices: '', period: '',
  desc_ru: '', desc_cn: '', cause_ru: 'Нарушение при производстве', cause_cn: '生产过程异常'
})

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const loadPpmData = async () => {
  loading.value = true
  try {
    const res1 = await fetch('http://127.0.0.1:8001/api/v1/analytics/ppm-dataset')
    const json1 = await res1.json()
    rawDataset.value = json1.data || []

    const res2 = await fetch('http://127.0.0.1:8001/api/v1/analytics/production-claims')
    const json2 = await res2.json()
    claimsDetail.value = json2.data || []
  } catch (e) {
    console.error("Ошибка загрузки данных PPM:", e)
  } finally {
    loading.value = false
  }
}

// Селектор объектов на основе кириллического ключа базы
const skuOptions = computed(() => {
  if (!rawDataset.value.length) return ['[Все артикулы]']
  const skus = Array.from(new Set(rawDataset.value.map(d => d['Артикул']))).filter(Boolean).sort()
  return ['[Все артикулы]', '[Вся Группа A]', '[Вся Группа B]', '[Вся Группа C]', ...skus]
})

// Помесячная фильтрация и агрегация (Идеально устойчива к часовым поясам)
const processedTableData = computed(() => {
  if (!rawDataset.value.length) return []
  
  const startYearMonth = startDate.value.substring(0, 7) 
  const endYearMonth = endDate.value.substring(0, 7)

  const activeSkus = new Set()
  if (selectedObjects.value.includes('[Все артикулы]') || selectedObjects.value.length === 0) {
    rawDataset.value.forEach(d => { if (d['Артикул']) activeSkus.add(d['Артикул']) })
  } else {
    selectedObjects.value.forEach(obj => {
      if (obj.startsWith('[Вся Группа')) {
        const letter = obj.replace('[Вся Группа ', '').replace(']', '')
        rawDataset.value.forEach(d => { if (d['ABC_Группа'] === letter && d['Артикул']) activeSkus.add(d['Артикул']) })
      } else {
        activeSkus.add(obj)
      }
    })
  }

  const filtered = rawDataset.value.filter(d => {
    if (!d['Месяц_ДТ']) return false
    const dYearMonth = d['Месяц_ДТ'].substring(0, 7)
    return activeSkus.has(d['Артикул']) && dYearMonth >= startYearMonth && dYearMonth <= endYearMonth
  })

  const map = {}
  filtered.forEach(d => {
    const sku = d['Артикул']
    if (!map[sku]) {
      map[sku] = { 
        'Артикул': sku, 
        'ABC_Группа': d['ABC_Группа'] || 'C', 
        'Класс XYZ': d['Класс XYZ'] || '-', 
        'Брак': 0, 
        'Заказы': 0 
      }
    }
    map[sku]['Брак'] += d['Брак'] || 0
    map[sku]['Заказы'] += d['Заказы'] || 0
  })

  return Object.values(map).map(item => {
    const ppm = item['Заказы'] > 0 ? Math.floor((item['Брак'] / item['Заказы']) * 1000000) : 0
    const pct = item['Заказы'] > 0 ? (item['Брак'] / item['Заказы']) * 100 : 0
    return { ...item, ppm, pct }
  }).sort((a, b) => a['ABC_Группа'].localeCompare(b['ABC_Группа']) || b.ppm - a.ppm)
})

const groupMetrics = computed(() => {
  const result = { 
    A: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0 }, 
    B: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0 }, 
    C: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0 } 
  }
  
  processedTableData.value.forEach(row => {
    const g = row['ABC_Группа']
    if (result[g]) {
      result[g].total++
      if (row.ppm > 10000) result[g].bad++
      result[g].defects += row['Брак']
      result[g].orders += row['Заказы']
    }
  })

  Object.keys(result).forEach(k => {
    result[k].ppm = result[k].orders > 0 ? Math.floor((result[k].defects / result[k].orders) * 1000000) : 0
  })
  return result
})

const buildChart = () => {
  if (!trendChart.value) return

  const targetSku = clickedSku.value
  const chartSource = rawDataset.value.filter(d => !targetSku || d['Артикул'] === targetSku)

  const timelineMap = {}
  chartSource.forEach(d => {
    const key = `${d['Месяц_ДТ']}_${d['Source']}`
    if (!timelineMap[key]) {
      timelineMap[key] = { month: d['Месяц_ДТ'], label: d['Месяц_Стр'], source: d['Source'], defects: 0, orders: 0 }
    }
    timelineMap[key].defects += d['Брак'] || 0
    timelineMap[key].orders += d['Заказы'] || 0
  })

  const sortedTimeline = Object.values(timelineMap).sort((a, b) => a.month.localeCompare(b.month))
  
  const sources = {
    'External': { x: [], y: [], defects: [], name: 'История', color: '#f39c12' },
    'System': { x: [], y: [], defects: [], name: 'Система', color: '#3b82f6' }
  }

  sortedTimeline.forEach(t => {
    if (sources[t.source]) {
      const ppm = t.orders > 0 ? Math.floor((t.defects / t.orders) * 1000000) : 0
      sources[t.source].x.push(t.label)
      sources[t.source].y.push(ppm)
      sources[t.source].defects.push(t.defects)
    }
  })

  const traces = []
  Object.keys(sources).forEach(k => {
    if (sources[k].x.length > 0) {
      traces.push({
        x: sources[k].x, y: sources[k].y, name: sources[k].name,
        type: 'bar', marker: { color: sources[k].color }, text: sources[k].y, textposition: 'outside'
      })
    }
  })

  const allX = [...sources.External.x, ...sources.System.x]
  const allDefects = [...sources.External.defects, ...sources.System.defects]
  if (allX.length > 0) {
    traces.push({
      x: allX, y: allDefects, name: 'Кол-во брака (шт)',
      type: 'scatter', mode: 'lines+markers', line: { color: '#e74c3c', width: 3, dash: 'dot' }, yaxis: 'y2'
    })
  }

  const layout = {
    barmode: 'group', height: 350, margin: { t: 20, b: 40, l: 50, r: 50 },
    legend: { orientation: 'h', y: -0.2 },
    yaxis: { title: 'PPM', side: 'left', showgrid: false },
    yaxis2: { title: 'Количество брака', overlaying: 'y', side: 'right', showgrid: true },
    hovermode: 'x unified',
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent'
  }

  Plotly.newPlot(trendChart.value, traces, layout, { responsive: true })
}

watch(clickedSku, async (newSku) => {
  if (!newSku) return
  await nextTick()
  buildChart()

  const skuClaims = claimsDetail.value.filter(c => c['Артикул продавца'] === newSku)
  const uniqueInvoices = Array.from(new Set(skuClaims.map(c => c['Инвойс']).filter(i => i && i !== 'Не указан')))
  
  claimForm.value.invoices = uniqueInvoices.join(', ')
  claimForm.value.period = `${new Date(startDate.value).toLocaleDateString('ru-RU')} - ${new Date(endDate.value).toLocaleDateString('ru-RU')}`
  
  const issues = []
  Object.keys(CATEGORIES).forEach(id => {
    const count = skuClaims.filter(c => ['1','1.0','+','true','да'].includes(String(c[id] || '').trim().toLowerCase())).length
    if (count > 0) issues.push(`${CATEGORIES[id]} (${count} шт.)`)
  })
  claimForm.value.desc_ru = issues.join('\n')
})

const handleRowClick = (sku) => {
  clickedSku.value = clickedSku.value === sku ? null : sku
}

onMounted(loadPpmData)
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen">
    <div class="flex items-center gap-3 mb-6">
      <div class="p-3 bg-red-100 text-red-600 rounded-xl"><AlertTriangle class="w-6 h-6" /></div>
      <h1 class="text-2xl font-black text-slate-800 tracking-tight">Уровень PPM и Классификация</h1>
    </div>

    <div class="bg-white p-5 rounded-2xl border shadow-sm mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
      <div>
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">Период анализа</label>
        <div class="flex gap-2">
          <input type="date" v-model="startDate" class="w-full border border-slate-200 rounded-xl p-2 text-xs focus:outline-none focus:border-blue-500" />
          <input type="date" v-model="endDate" class="w-full border border-slate-200 rounded-xl p-2 text-xs focus:outline-none focus:border-blue-500" />
        </div>
      </div>
      <div class="md:col-span-2">
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">Объекты (Группы / Артикулы)</label>
        <select v-model="selectedObjects" multiple class="w-full border border-slate-200 rounded-xl p-1.5 text-xs focus:outline-none focus:border-blue-500 h-11">
          <option v-for="opt in skuOptions" :key="opt" :value="opt">{{ opt }}</option>
        </select>
      </div>
    </div>

    <div v-if="loading" class="text-center py-20 text-slate-400 font-medium animate-pulse">🔄 База синхронизирована. Происходит пересчет коэффициентов PPM...</div>

    <div v-else class="space-y-6">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div v-for="g in ['A', 'B', 'C']" :key="g" class="bg-white p-5 rounded-2xl border shadow-xs">
          <div class="flex justify-between items-center mb-2">
            <span class="text-xs font-black px-2.5 py-1 rounded-md bg-slate-100 text-slate-700">Группа {{ g }}</span>
            <span class="text-xs font-bold text-red-500">{{ groupMetrics[g].bad }} проблемных</span>
          </div>
          <div class="text-2xl font-black text-slate-800">{{ groupMetrics[g].total }} SKU</div>
          <div class="text-xs font-bold text-slate-500 mt-2">Средний PPM: <span class="text-slate-800 font-extrabold">{{ groupMetrics[g].ppm.toLocaleString() }}</span></div>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div class="bg-white border rounded-2xl shadow-sm p-5 lg:col-span-3 overflow-hidden">
          <div class="flex items-center gap-2 mb-4"><TableProperties class="w-4 h-4 text-slate-400"/><h2 class="text-sm font-bold text-slate-800">Спецификация дефектов по SKU</h2></div>
          <div class="overflow-x-auto max-h-[400px]">
            <table class="w-full text-left text-xs border-collapse">
              <thead>
                <tr class="bg-slate-50 text-slate-500 border-b border-slate-100 uppercase font-bold">
                  <th class="p-2.5">Артикул</th>
                  <th class="p-2.5 text-center">ABC</th>
                  <th class="p-2.5 text-center">XYZ</th>
                  <th class="p-2.5 text-right">Заказы</th>
                  <th class="p-2.5 text-right">Брак</th>
                  <th class="p-2.5 text-right">PPM</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="row in processedTableData" :key="row['Артикул']" @click="handleRowClick(row['Артикул'])" :class="['cursor-pointer transition-colors', clickedSku === row['Артикул'] ? 'bg-blue-50' : row.ppm > 10000 ? 'bg-red-50 hover:bg-red-100/70 text-red-900' : 'hover:bg-slate-50']">
                  <td class="p-2.5 font-bold">{{ row['Артикул'] }}</td>
                  <td class="p-2.5 text-center font-semibold">{{ row['ABC_Группа'] }}</td>
                  <td class="p-2.5 text-center font-semibold">{{ row['Класс XYZ'] }}</td>
                  <td class="p-2.5 text-right font-medium">{{ row['Заказы'].toLocaleString() }}</td>
                  <td class="p-2.5 text-right font-medium">{{ row['Брак'].toLocaleString() }}</td>
                  <td class="p-2.5 text-right font-black">{{ row.ppm.toLocaleString() }}</td>
                </tr>
                <tr v-if="!processedTableData.length">
                  <td colspan="6" class="p-8 text-center text-slate-400 font-medium">Нет данных за выбранный диапазон месяцев.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="bg-white border rounded-2xl shadow-sm p-5 lg:col-span-2">
          <div class="flex items-center gap-2 mb-4"><TrendingUp class="w-4 h-4 text-blue-500"/><h2 class="text-sm font-bold text-slate-800">Динамика дефектов по месяцам</h2></div>
          <div v-if="!clickedSku" class="h-64 flex items-center justify-center text-slate-400 border-2 border-dashed border-slate-100 rounded-xl text-center p-4 text-xs font-medium">Выберите конкретный артикул в левой таблице,<br/>чтобы развернуть глубокий исторический таймлайн брака</div>
          <div v-else ref="trendChart" class="w-full"></div>
        </div>
      </div>
    </div>
  </div>
</template>