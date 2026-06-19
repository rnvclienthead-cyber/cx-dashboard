<script setup>
import { ref, onMounted, computed, watch, nextTick } from 'vue'
import Plotly from 'plotly.js-dist-min'
import { Calendar, Search, Star, Package, X, TrendingUp, TrendingDown, ChevronLeft, ChevronRight, Check, ChevronsUpDown, ChevronUp, ChevronDown as ChevronDownIcon, Upload } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import { usePermissionsStore } from '../stores/permissions'

const platformStore = usePlatformStore()
const permissionsStore = usePermissionsStore()
const ratingsData    = ref([])
const loading        = ref(true)
const ymSummary      = ref([])
const wbSummary      = ref([])
const summaryLoading = ref(false)

// ─── Календарь ───────────────────────────────────────────────────────────────
const startDate = ref('')
const endDate   = ref('')
const showCalendarPopover = ref(false)
const todayObj     = new Date()
const calendarYear  = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const monthNames = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]

const setPreviousMonth = () => {
  const d = new Date(); d.setMonth(d.getMonth() - 1)
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const lastDay = new Date(year, d.getMonth() + 1, 0).getDate()
  startDate.value = `${year}-${month}-01`
  endDate.value   = `${year}-${month}-${String(lastDay).padStart(2,'0')}`
  calendarYear.value = year; calendarMonth.value = d.getMonth()
  showCalendarPopover.value = false
}

// ─── Фильтры ─────────────────────────────────────────────────────────────────
const searchQuery    = ref('')
const dropdownOpen   = ref(false)
const selectedSkus   = ref([])
const isNovinkiFilter = ref(false)
const activeCard     = ref(null)
const heatmapMode    = ref('absolute')
const groupBy        = ref('day')

// ─── Сортировка таблиц ───────────────────────────────────────────────────────
const ymSortCol = ref('total_reviews')
const ymSortDir = ref('desc')
const ymUploadStatus = ref(null)

const uploadYmXlsx = async (e) => {
  const file = e.target.files?.[0]
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  ymUploadStatus.value = null
  try {
    const res = await apiFetch('/api/ratings/ym/upload', { method: 'POST', body: fd })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      ymUploadStatus.value = { ok: false, message: err.detail || `Ошибка ${res.status}` }
      return
    }
    const data = await res.json()
    ymUploadStatus.value = { ok: true, message: `Загружено: ${data.skus_imported} SKU за ${data.report_date}` }
    await fetchSummaries()
  } catch (err) {
    ymUploadStatus.value = { ok: false, message: String(err) }
  }
  e.target.value = ''
}
const wbSortCol = ref('total_reviews')
const wbSortDir = ref('desc')

const setWbSort = (col) => {
  if (wbSortCol.value === col) wbSortDir.value = wbSortDir.value === 'asc' ? 'desc' : 'asc'
  else { wbSortCol.value = col; wbSortDir.value = 'desc' }
}
const setYmSort = (col) => {
  if (ymSortCol.value === col) ymSortDir.value = ymSortDir.value === 'asc' ? 'desc' : 'asc'
  else { ymSortCol.value = col; ymSortDir.value = 'desc' }
}

// ─── Группировка ─────────────────────────────────────────────────────────────
const getGroupKey = (dateStr) => {
  if (groupBy.value === 'day') return dateStr
  if (groupBy.value === 'month') return dateStr.substring(0, 7)
  const d = new Date(dateStr)
  const thu = new Date(d); thu.setDate(d.getDate() + 4 - (d.getDay() || 7))
  const yr = thu.getFullYear()
  const wn = Math.ceil(((thu - new Date(yr, 0, 1)) / 864e5 + 1) / 7)
  return `${yr}-W${String(wn).padStart(2, '0')}`
}

const groupKeyLabel = (key) => {
  if (groupBy.value === 'day') return formatDateDisplay(key)
  if (groupBy.value === 'month') {
    const [y, m] = key.split('-')
    return `${monthNames[parseInt(m) - 1].substring(0, 3)} ${y}`
  }
  const [y, wStr] = key.split('-W')
  const jan1 = new Date(parseInt(y), 0, 1)
  const dow = jan1.getDay() || 7
  const mon = new Date(parseInt(y), 0, 1 + (parseInt(wStr) - 1) * 7 - (dow - 1))
  return formatDateDisplay(mon.toISOString().substring(0, 10))
}

const aggregateByGroup = (data) => {
  if (groupBy.value === 'day') return data
  const map = {}
  data.forEach(r => {
    const gk  = getGroupKey(r.date)
    const key = `${r.supplier_article}||${gk}`
    if (!map[key]) {
      map[key] = {
        supplier_article: r.supplier_article, date: gk, is_new: r.is_new,
        _lastRatingDate: '', _lastRating: 0,
        _lastDate: '', _lastReviewCount: 0,
        daily_new: 0,
        stars_1: 0, stars_2: 0, stars_3: 0, stars_4: 0, stars_5: 0
      }
    }
    const g = map[key]
    if (r.average_rating > 0 && r.date >= g._lastRatingDate) {
      g._lastRatingDate = r.date; g._lastRating = r.average_rating
    }
    if (r.date > g._lastDate) {
      g._lastDate = r.date; g._lastReviewCount = r.review_count || 0
      g.stars_1 = r.stars_1; g.stars_2 = r.stars_2
      g.stars_3 = r.stars_3; g.stars_4 = r.stars_4; g.stars_5 = r.stars_5
    }
    g.daily_new = (g.daily_new || 0) + (r.daily_new || 0)
  })
  return Object.values(map).map(g => ({
    supplier_article: g.supplier_article, date: g.date, is_new: g.is_new,
    average_rating: g._lastRating ? parseFloat(g._lastRating.toFixed(2)) : 0,
    review_count: g._lastReviewCount, daily_new: g.daily_new,
    stars_1: g.stars_1, stars_2: g.stars_2, stars_3: g.stars_3, stars_4: g.stars_4, stars_5: g.stars_5
  }))
}

const axisHeaderRef     = ref(null)
const categoryChartRefs = {}
const summaryChartRefs  = {}
const structureRef = ref(null)
const expandedCategories = ref(new Set())

// ─── Загрузка данных ─────────────────────────────────────────────────────────
const fetchRatings = async () => {
  loading.value = true
  try {
    const res = await apiFetch(`/api/ratings?platform=${platformStore.platform}`)
    ratingsData.value = await res.json()
    const today = new Date()
    const yyyy = today.getFullYear(), mm = String(today.getMonth() + 1).padStart(2,'0'), dd = String(today.getDate()).padStart(2,'0')
    endDate.value = `${yyyy}-${mm}-${dd}`; startDate.value = `${yyyy}-${mm}-01`
  } catch (e) { console.error(e) } finally {
    loading.value = false
    await nextTick(); renderCharts()
  }
}

const fetchSummaries = async () => {
  summaryLoading.value = true
  const p = platformStore.platform
  try {
    if (p === 'ym' || p === 'all') {
      const res = await apiFetch('/api/ratings/ym/summary')
      ymSummary.value = await res.json()
    }
    if (p === 'wb' || p === 'all') {
      const res = await apiFetch('/api/ratings/wb/summary')
      wbSummary.value = await res.json()
    }
  } catch (e) { console.error(e) } finally { summaryLoading.value = false }
}

// ─── Календарь ───────────────────────────────────────────────────────────────
const changeCalendarMonth = (dir) => {
  calendarMonth.value += dir
  if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
  else if (calendarMonth.value < 0) { calendarMonth.value = 11; calendarYear.value-- }
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
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

// ─── Артикулы ────────────────────────────────────────────────────────────────
const skuOptions = computed(() => [...new Set(ratingsData.value.map(r => r.supplier_article))].sort())
const filteredSkuOptions = computed(() => {
  if (!searchQuery.value) return skuOptions.value
  return skuOptions.value.filter(s => s.toLowerCase().includes(searchQuery.value.toLowerCase()))
})
const toggleSku = (sku) => {
  if (selectedSkus.value.includes(sku)) selectedSkus.value = selectedSkus.value.filter(s => s !== sku)
  else selectedSkus.value.push(sku)
}
const removeSku = (sku) => { selectedSkus.value = selectedSkus.value.filter(s => s !== sku) }
const selectAll = () => { selectedSkus.value = [...skuOptions.value] }
const clearAll  = () => { selectedSkus.value = [] }

const toggleCardFilter = (type) => { activeCard.value = activeCard.value === type ? null : type }

// ─── Фильтрованные данные ─────────────────────────────────────────────────────
const dateFilteredData = computed(() => {
  if (!startDate.value || !endDate.value) return ratingsData.value
  return ratingsData.value.filter(r => r.date >= startDate.value && r.date <= endDate.value)
})

const baseDeltaAnalysis = computed(() => {
  const baseData = aggregateByGroup(dateFilteredData.value)
  if (!baseData.length) return []
  const skuGroups = {}
  baseData.forEach(r => {
    if (!skuGroups[r.supplier_article]) skuGroups[r.supplier_article] = []
    skuGroups[r.supplier_article].push(r)
  })
  const analysis = []
  Object.keys(skuGroups).forEach(sku => {
    const items = skuGroups[sku].sort((a, b) => a.date.localeCompare(b.date))
    const nonZeroItems = items.filter(i => i.average_rating > 0)
    if (!nonZeroItems.length) return
    const startItem = nonZeroItems[0]
    const endItem   = nonZeroItems[nonZeroItems.length - 1]
    const isSinglePeriod = nonZeroItems.length === 1
    const ratingDelta = endItem.average_rating - startItem.average_rating
    let stars = []
    if (isSinglePeriod) {
      stars = [endItem.stars_1, endItem.stars_2, endItem.stars_3, endItem.stars_4, endItem.stars_5]
    } else {
      stars = [
        endItem.stars_1 - startItem.stars_1, endItem.stars_2 - startItem.stars_2,
        endItem.stars_3 - startItem.stars_3, endItem.stars_4 - startItem.stars_4,
        endItem.stars_5 - startItem.stars_5
      ].map(v => Math.max(v, 0))
    }
    analysis.push({
      sku,
      currentRating: endItem.average_rating,
      ratingDelta: Number(ratingDelta.toFixed(2)),
      startPeriodLabel: groupKeyLabel(startItem.date),
      endPeriodLabel:   groupKeyLabel(endItem.date),
      stars,
      totalReviews: stars.reduce((a, b) => a + b, 0),
      is_new: endItem.is_new
    })
  })
  return analysis
})

const periodCompareLabel = computed(() => {
  const grouped = aggregateByGroup(dateFilteredData.value)
  const keys = [...new Set(grouped.map(r => r.date))].sort()
  if (!keys.length) return ''
  const first = groupKeyLabel(keys[0])
  const last  = groupKeyLabel(keys[keys.length - 1])
  if (keys.length === 1) {
    return groupBy.value === 'week' ? `нед. ${first}` : first
  }
  if (groupBy.value === 'week') return `нед. ${first} / нед. ${last}`
  return `${first} / ${last}`
})

const summary = computed(() => {
  let data = baseDeltaAnalysis.value
  if (isNovinkiFilter.value) data = data.filter(d => d.is_new)
  if (selectedSkus.value.length) data = data.filter(d => selectedSkus.value.includes(d.sku))
  const total = data.length
  const avg = total > 0 ? (data.reduce((s, i) => s + i.currentRating, 0) / total).toFixed(2) : "0.00"
  return { total, badGrowth: data.filter(a => a.ratingDelta < 0).length, goodGrowth: data.filter(a => a.ratingDelta > 0).length, avg }
})

const finalDeltaAnalysis = computed(() => {
  let data = baseDeltaAnalysis.value
  if (isNovinkiFilter.value) data = data.filter(d => d.is_new)
  if (selectedSkus.value.length) data = data.filter(d => selectedSkus.value.includes(d.sku))
  if (activeCard.value === 'dropped') data = data.filter(d => d.ratingDelta < 0)
  if (activeCard.value === 'grown')   data = data.filter(d => d.ratingDelta > 0)
  return data
})

const fullyFilteredData = computed(() => {
  const allowedSkus = new Set(finalDeltaAnalysis.value.map(d => d.sku))
  return dateFilteredData.value.filter(r => allowedSkus.has(r.supplier_article))
})

// ─── Сводные таблицы (с фильтром по SKU и сортировкой) ──────────────────────
const sortedYmSummary = computed(() => {
  let data = ymSummary.value
  if (isNovinkiFilter.value) data = data.filter(r => r.is_new)
  if (selectedSkus.value.length) data = data.filter(r => selectedSkus.value.includes(r.supplier_article))
  const col = ymSortCol.value, dir = ymSortDir.value
  return [...data].sort((a, b) => {
    const v = a[col] ?? 0, w = b[col] ?? 0
    if (typeof v === 'string') return dir === 'asc' ? v.localeCompare(w) : w.localeCompare(v)
    return dir === 'asc' ? v - w : w - v
  })
})

const sortedWbSummary = computed(() => {
  let data = wbSummary.value
  if (isNovinkiFilter.value) data = data.filter(r => r.is_new)
  if (selectedSkus.value.length) data = data.filter(r => selectedSkus.value.includes(r.supplier_article))
  const col = wbSortCol.value, dir = wbSortDir.value
  return [...data].sort((a, b) => {
    const v = a[col] ?? 0, w = b[col] ?? 0
    if (typeof v === 'string') return dir === 'asc' ? v.localeCompare(w) : w.localeCompare(v)
    return dir === 'asc' ? v - w : w - v
  })
})

// ─── Категории SKU ───────────────────────────────────────────────────────────
const skuCategoryMap = computed(() => {
  const map = {}
  ratingsData.value.forEach(r => { if (r.supplier_article) map[r.supplier_article] = r.category || 'Прочее' })
  return map
})

// Стабильный порядок категорий по полным (нефильтрованным) данным
const baseCategoryOrder = computed(() => {
  const sizes = {}
  ratingsData.value.forEach(r => {
    const cat = r.category || 'Прочее'
    if (!sizes[cat]) sizes[cat] = new Set()
    sizes[cat].add(r.supplier_article)
  })
  return Object.entries(sizes)
    .sort((a, b) => b[1].size - a[1].size)
    .map(([cat]) => cat)
})

const categoryGroups = computed(() => {
  const skusInView = [...new Set(fullyFilteredData.value.map(r => r.supplier_article))]
  const groups = {}
  skusInView.forEach(sku => {
    const cat = skuCategoryMap.value[sku] || 'Прочее'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(sku)
  })
  const order = baseCategoryOrder.value
  return Object.entries(groups).sort((a, b) => {
    const ai = order.indexOf(a[0])
    const bi = order.indexOf(b[0])
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
  })
})

const categoryStats = computed(() => {
  const stats = {}
  baseDeltaAnalysis.value.forEach(d => {
    const cat = skuCategoryMap.value[d.sku] || 'Прочее'
    if (!stats[cat]) stats[cat] = { sum: 0, count: 0 }
    stats[cat].sum += d.currentRating
    stats[cat].count++
  })
  const res = {}
  Object.entries(stats).forEach(([cat, s]) => {
    res[cat] = s.count ? (s.sum / s.count).toFixed(2) : null
  })
  return res
})

const toggleCategory = (catName) => {
  const s = new Set(expandedCategories.value)
  if (s.has(catName)) {
    // Сворачиваем: v-if уберёт full-div из DOM автоматически
    s.delete(catName)
    expandedCategories.value = s
    nextTick(() => renderSummaryChart(catName))
  } else {
    // Раскрываем: сначала чистим summary, потом v-if создаст full-div
    const sumEl = summaryChartRefs[catName]
    if (sumEl) Plotly.purge(sumEl)
    s.add(catName)
    expandedCategories.value = s
    // renderCategoryChart вызывается из ref-коллбека через requestAnimationFrame
  }
}

// Ref-коллбек для full-chart div (v-if — элемент создаётся заново при раскрытии)
const onCategoryChartMounted = (catName) => (el) => {
  if (el) {
    categoryChartRefs[catName] = el
    requestAnimationFrame(() => renderCategoryChart(catName))
  } else {
    delete categoryChartRefs[catName]
  }
}

// ─── Мини-тепловая карта группы (среднее по SKU, 1 строка) ───────────────────
const renderSummaryChart = (catName) => {
  const el = summaryChartRefs[catName]
  if (!el) return

  const catEntry = categoryGroups.value.find(([n]) => n === catName)
  const catSkus  = catEntry ? catEntry[1] : []
  if (!catSkus.length) return

  // Глобальные groupKeys — чтобы x-ось совпадала с шапкой и другими группами
  const allAgg    = aggregateByGroup(fullyFilteredData.value)
  const groupKeys = [...new Set(allAgg.map(r => r.date))].sort()
  if (!groupKeys.length) return
  const xLabels   = groupKeys.map(k => groupKeyLabel(k))

  // Категорийные данные только для вычисления среднего
  const catAgg = aggregateByGroup(fullyFilteredData.value.filter(r => catSkus.includes(r.supplier_article)))

  // Среднее значение по группе за каждый период (null → белая клетка)
  const avgRow = groupKeys.map(gk => {
    const recs = catAgg.filter(r => r.date === gk && r.average_rating > 0)
    if (!recs.length) return null
    return recs.reduce((s, r) => s + r.average_rating, 0) / recs.length
  })

  let zRow, colorscale, zmin, zmax, hoverTpl

  if (heatmapMode.value === 'delta') {
    // Дельта от предыдущего периода
    zRow = avgRow.map((v, i) => {
      if (v === null) return null
      const prev = avgRow.slice(0, i).reverse().find(x => x !== null)
      return prev !== undefined ? v - prev : 0
    })
    colorscale = [[0,'#ef4444'],[0.5,'#ffffff'],[1,'#22c55e']]
    zmin = -0.05; zmax = 0.05
    hoverTpl = '%{x}<br>Δ рейтинга группы: <b>%{z:+.2f}</b><extra></extra>'
  } else {
    zRow = avgRow
    colorscale = [[0,'#ef4444'],[0.8,'#fef08a'],[1,'#22c55e']]
    zmin = 3.5; zmax = 5.0
    hoverTpl = '%{x}<br>Ср. рейтинг группы: <b>%{z:.2f} ★</b><extra></extra>'
  }

  // В дельта-режиме показываем числа в ячейках
  const textRow  = heatmapMode.value === 'delta'
    ? [zRow.map(v => v === null ? '' : v > 0 ? `+${v.toFixed(2)}` : v === 0 ? '' : v.toFixed(2))]
    : [[]]
  const textTpl  = heatmapMode.value === 'delta' ? '%{text}' : ''

  Plotly.newPlot(el, [{
    z: [zRow], x: xLabels, y: [catName],
    type: 'heatmap', colorscale, zmin, zmax,
    xgap: 3, ygap: 0, showscale: false, showlegend: false,
    text: textRow, texttemplate: textTpl,
    textfont: { size: 10, weight: 'bold', color: '#334155' },
    hovertemplate: hoverTpl
  }], {
    margin: { l: 200, r: 20, t: 2, b: 2 },
    height: 40,
    xaxis: { showticklabels: false, showgrid: false, ticks: '', showline: false, zeroline: false },
    yaxis: { showgrid: false, tickfont: { size: 11, weight: 'bold' }, ticks: '' }
  }, { responsive: true, displayModeBar: false })
}

// ─── Единая шапка дат (sticky) для всей матрицы ──────────────────────────────
const renderAxisHeader = () => {
  const el = axisHeaderRef.value
  if (!el) return

  const agg       = aggregateByGroup(fullyFilteredData.value)
  const groupKeys = [...new Set(agg.map(r => r.date))].sort()
  if (!groupKeys.length) { Plotly.purge(el); return }
  const xLabels = groupKeys.map(k => groupKeyLabel(k))

  Plotly.newPlot(el, [{
    x: xLabels, y: Array(xLabels.length).fill(0),
    type: 'scatter', mode: 'markers', opacity: 0,
    showlegend: false, hoverinfo: 'none'
  }], {
    margin: { l: 200, r: 20, t: 2, b: 30 },
    height: 40,
    xaxis: {
      showticklabels: true, tickangle: 0,
      showgrid: false, ticks: '', showline: false, zeroline: false,
      tickfont: { size: 9, weight: 'bold', color: '#475569' },
      automargin: false
    },
    yaxis: { visible: false, range: [-1, 1], fixedrange: true },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)'
  }, { responsive: true, displayModeBar: false })
}

// ─── Построение одной тепловой карты для набора SKU ──────────────────────────
const renderCategoryChart = (catName) => {
  const el = categoryChartRefs[catName]
  if (!el) return

  const catEntry = categoryGroups.value.find(([n]) => n === catName)
  const catSkus  = catEntry ? catEntry[1].slice().sort().reverse() : []
  if (!catSkus.length) return

  // Глобальные groupKeys для выравнивания по общей оси дат
  const allAgg    = aggregateByGroup(fullyFilteredData.value)
  const groupKeys = [...new Set(allAgg.map(r => r.date))].sort()
  const xLabels   = groupKeys.map(k => groupKeyLabel(k))

  const agg = aggregateByGroup(fullyFilteredData.value.filter(r => catSkus.includes(r.supplier_article)))

  const mainZ = [], mainText = [], mainCustom = [], zeroZ = [], zeroText = []

  catSkus.forEach(sku => {
    const rowMZ = [], rowMT = [], rowMC = [], rowZZ = [], rowZT = []
    const recs  = agg.filter(r => r.supplier_article === sku).sort((a, b) => a.date.localeCompare(b.date))
    groupKeys.forEach(gk => {
      const rec = recs.find(r => r.date === gk)
      if (!rec) { rowMZ.push(null); rowMT.push(''); rowMC.push([0,0]); rowZZ.push(null); rowZT.push(''); return }
      const idx  = recs.findIndex(r => r.date === gk)
      const prev = idx > 0 ? recs[idx - 1] : null
      const newRevs = rec.daily_new > 0 ? rec.daily_new : (prev ? Math.max(0, rec.review_count - prev.review_count) : rec.review_count)
      if (rec.average_rating === 0) {
        rowMZ.push(null); rowMT.push(''); rowMC.push([0, rec.review_count])
        // "Нов." только если реально есть оценки (не просто пустой синк)
        rowZZ.push(rec.review_count > 0 ? 0 : null)
        rowZT.push(rec.review_count > 0 ? 'Нов.' : '')
      } else {
        rowZZ.push(null); rowZT.push('')
        if (heatmapMode.value === 'absolute') {
          rowMZ.push(rec.average_rating); rowMT.push('')
        } else {
          const prevNZ = recs.slice(0, idx).filter(r => r.average_rating > 0).at(-1)
          const diff = prevNZ ? rec.average_rating - prevNZ.average_rating : 0
          rowMZ.push(diff)
          rowMT.push(diff > 0 ? `+${diff.toFixed(2)}` : diff < 0 ? `${diff.toFixed(2)}` : '0.00')
        }
        rowMC.push([newRevs, rec.review_count])
      }
    })
    mainZ.push(rowMZ); mainText.push(rowMT); mainCustom.push(rowMC)
    zeroZ.push(rowZZ); zeroText.push(rowZT)
  })

  let colorscale, zmin, zmax
  if (heatmapMode.value === 'absolute') {
    colorscale = [[0,'#ef4444'],[0.8,'#fef08a'],[1,'#22c55e']]; zmin = 3.5; zmax = 5.0
  } else {
    colorscale = [[0,'#ef4444'],[0.5,'#ffffff'],[1,'#22c55e']]; zmin = -0.05; zmax = 0.05
  }
  const cellH  = groupBy.value === 'day' ? 28 : groupBy.value === 'week' ? 36 : 44
  const traces = []

  if (zeroZ.some(row => row.some(v => v !== null))) {
    traces.push({
      z: zeroZ, x: xLabels, y: catSkus, type: 'heatmap',
      colorscale: [[0,'#f1f5f9'],[1,'#f1f5f9']], zmin: -0.5, zmax: 0.5,
      showscale: false, showlegend: false, xgap: 3, ygap: 3,
      text: zeroText, texttemplate: '%{text}', textfont: { size: 9, color: '#94a3b8' },
      hovertemplate: '%{y}<br>%{x}<br>Первые продажи — рейтинг ещё формируется<extra></extra>'
    })
  }
  traces.push({
    z: mainZ, x: xLabels, y: catSkus, type: 'heatmap',
    colorscale, zmin, zmax, xgap: 3, ygap: 3, showlegend: false, showscale: false,
    text: mainText, texttemplate: heatmapMode.value === 'delta' ? '%{text}' : '',
    textfont: { size: 10, weight: 'bold', color: '#334155' },
    customdata: mainCustom,
    hovertemplate: heatmapMode.value === 'delta'
      ? '%{y}<br>%{x}<br>Δ рейтинга: <b>%{text}</b><extra></extra>'
      : '%{y}<br>%{x}<br>Рейтинг: <b>%{z:.2f} ★</b><br>Новых оценок: <b>+%{customdata[0]}</b><extra></extra>'
  })

  Plotly.newPlot(el, traces, {
    margin: { l: 200, r: 20, t: 5, b: 5 },
    height: Math.max(80, catSkus.length * cellH + 20),
    xaxis: { showticklabels: false, showgrid: false, ticks: '', showline: false, zeroline: false },
    yaxis: { showgrid: false, tickfont: { size: 11, weight: 'bold' }, ticks: '' }
  }, { responsive: true, displayModeBar: false })

  el.on('plotly_click', ev => {
    if (ev.points?.length > 0) {
      const clickedSku = ev.points[0].y
      selectedSkus.value = selectedSkus.value.includes(clickedSku) ? [] : [clickedSku]
    }
  })
}

// ─── Графики ─────────────────────────────────────────────────────────────────
const renderCharts = () => {
  if (!fullyFilteredData.value.length) {
    if (structureRef.value) Plotly.purge(structureRef.value)
    return
  }
  renderAxisHeader()
  // Раскрытые категории: перерисовываем при смене дат/режима/группировки
  expandedCategories.value.forEach(cat => {
    const el = categoryChartRefs[cat]
    if (el) renderCategoryChart(cat)
  })
  // Свёрнутые — мини-строка со средним
  categoryGroups.value.forEach(([catName]) => {
    if (!expandedCategories.value.has(catName)) renderSummaryChart(catName)
  })

  const groupLabel    = groupBy.value === 'day' ? 'день' : groupBy.value === 'week' ? 'неделю' : 'месяц'
  const structureData = [...finalDeltaAnalysis.value].sort((a, b) => b.totalReviews - a.totalReviews)

  if (structureData.length > 0 && structureRef.value) {
    const starColors = ['#ef4444','#f97316','#f59e0b','#84cc16','#22c55e']
    const traces2 = []
    for (let si = 0; si < 5; si++) {
      traces2.push({
        x: structureData.map(d => d.sku), y: structureData.map(d => d.stars[si]),
        name: `${si + 1} ★`, type: 'bar', marker: { color: starColors[si] }
      })
    }
    Plotly.newPlot(structureRef.value, traces2, {
      barmode: 'stack', margin: { l: 50, r: 20, t: 10, b: 120 }, height: 450,
      xaxis: { tickangle: -45, tickfont: { size: 10, weight: 'bold' } },
      yaxis: { title: `Отзывов за ${groupLabel}` },
      legend: { orientation: 'h', y: -0.4, xanchor: 'center', x: 0.5 }
    }, { responsive: true })
  }
}

watch(groupBy, (newMode) => {
  const today = new Date()
  const yyyy = today.getFullYear(), mm = String(today.getMonth()+1).padStart(2,'0'), dd = String(today.getDate()).padStart(2,'0')
  endDate.value = `${yyyy}-${mm}-${dd}`
  if (newMode === 'day') startDate.value = `${yyyy}-${mm}-01`
  else if (newMode === 'week') { const s = new Date(today); s.setDate(s.getDate()-55); startDate.value = s.toISOString().substring(0,10) }
  else { const s = new Date(today); s.setMonth(s.getMonth()-6); startDate.value = s.toISOString().substring(0,10) }
})

watch([fullyFilteredData, startDate, endDate, heatmapMode, groupBy], () => { nextTick(() => renderCharts()) })
watch(() => platformStore.platform, () => { fetchRatings(); fetchSummaries() })

// Авто-раскрытие всех групп при активных фильтрах; свёртывание при снятии
watch([selectedSkus, isNovinkiFilter, activeCard], () => {
  const anyFilter = selectedSkus.value.length > 0 || isNovinkiFilter.value || activeCard.value !== null
  if (anyFilter) {
    const allCats = new Set(categoryGroups.value.map(([n]) => n))
    // Очищаем summary-чарты для раскрываемых категорий
    allCats.forEach(cat => {
      if (!expandedCategories.value.has(cat)) {
        const el = summaryChartRefs[cat]
        if (el) Plotly.purge(el)
      }
    })
    expandedCategories.value = allCats
  } else {
    // Сворачиваем все — очищаем full-чарты
    expandedCategories.value.forEach(cat => {
      const el = categoryChartRefs[cat]
      if (el) Plotly.purge(el)
    })
    expandedCategories.value = new Set()
  }
  nextTick(() => setTimeout(() => renderCharts(), 0))
})

onMounted(async () => {
  await fetchRatings()
  fetchSummaries()
  nextTick(() => renderCharts())
})

// ─── Вспомогательные ─────────────────────────────────────────────────────────
const starsDisplay = (r) => '★'.repeat(Math.round(r)) + '☆'.repeat(Math.max(0, 5 - Math.round(r)))
const ratingColor  = (r) => r >= 4.5 ? 'text-green-600' : r >= 4.0 ? 'text-lime-600' : r >= 3.5 ? 'text-yellow-600' : 'text-red-600'
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen font-sans max-w-[1600px] text-slate-800 antialiased"
       @click="dropdownOpen = false; showCalendarPopover = false">

    <!-- ── Заголовок ── -->
    <div class="flex items-center justify-between mb-8 pb-5 border-b border-slate-200">
      <div class="flex items-center gap-4">
        <div class="p-3 bg-blue-600 text-white rounded-xl shadow-lg shadow-blue-200/50"><Star class="w-6 h-6" /></div>
        <h1 class="text-xl font-black tracking-tight text-slate-900">Рейтинг товаров. Детализация оценок</h1>
        <div class="flex bg-slate-100 rounded-xl p-1 ml-2">
          <button @click="groupBy = 'day'"   :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all', groupBy==='day'   ? 'bg-white shadow text-blue-700' : 'text-slate-500 hover:text-slate-700']">День</button>
          <button @click="groupBy = 'week'"  :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all', groupBy==='week'  ? 'bg-white shadow text-blue-700' : 'text-slate-500 hover:text-slate-700']">Неделя</button>
          <button @click="groupBy = 'month'" :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all', groupBy==='month' ? 'bg-white shadow text-blue-700' : 'text-slate-500 hover:text-slate-700']">Месяц</button>
        </div>
      </div>
      <div class="flex items-center gap-4">
        <label :class="['flex items-center gap-2 px-4 py-3 rounded-2xl border cursor-pointer font-bold text-sm shadow-sm transition-all', isNovinkiFilter ? 'bg-blue-50 border-blue-400 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300']">
          <input type="checkbox" v-model="isNovinkiFilter" class="hidden" />
          <div class="w-4 h-4 rounded border flex items-center justify-center transition-colors" :class="isNovinkiFilter ? 'bg-blue-600 border-blue-600' : 'border-slate-300'"><Check v-if="isNovinkiFilter" class="w-3 h-3 text-white" /></div>
          Только Новинки
        </label>
        <div class="relative">
          <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-5 py-3 cursor-pointer hover:border-blue-500 transition-colors shadow-sm" @click.stop="showCalendarPopover = !showCalendarPopover">
            <Calendar class="w-4 h-4 text-blue-500 mr-3" />
            <span class="text-sm font-bold text-slate-700">{{ formatDateDisplay(startDate) }} — {{ formatDateDisplay(endDate) }}</span>
          </div>
          <div v-if="showCalendarPopover" class="absolute right-0 mt-3 bg-white border border-slate-100 rounded-3xl shadow-2xl z-[150] p-5 w-80" @click.stop>
            <div class="mb-4 border-b border-slate-100 pb-3">
              <button @click.stop="setPreviousMonth" class="w-full text-center text-xs font-black bg-blue-50 hover:bg-blue-100 text-blue-700 py-2.5 rounded-xl transition-all tracking-wide uppercase">Прошлый месяц</button>
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
              <div v-for="(d,idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)"
                   :class="['h-9 flex items-center justify-center text-sm font-bold rounded-xl transition-all select-none',
                     !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer',
                     d.dateStr === startDate || d.dateStr === endDate ? 'bg-blue-600 text-white shadow-md' : '',
                     d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-blue-50 text-blue-700' : '',
                     d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : '']">{{ d.day }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Поиск артикулов ── -->
    <div class="relative mb-8" @click.stop>
      <div class="flex items-center flex-wrap gap-2 bg-white border border-slate-200 rounded-2xl p-3 shadow-sm min-h-[56px] cursor-text focus-within:ring-2 focus-within:ring-blue-500" @click="dropdownOpen = true">
        <Search class="w-5 h-5 text-slate-400 ml-2 mr-1" />
        <div v-for="sku in selectedSkus" :key="sku" class="flex items-center bg-slate-100 border border-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-sm font-bold">
          {{ sku }}
          <X class="w-4 h-4 ml-2 cursor-pointer text-slate-400 hover:text-red-500" @click.stop="removeSku(sku)" />
        </div>
        <input v-model="searchQuery" placeholder="Найти и выбрать артикул..." class="flex-1 outline-none text-sm font-semibold text-slate-700 placeholder-slate-400 min-w-[200px] ml-2" @focus="dropdownOpen = true" />
      </div>
      <div v-if="dropdownOpen" class="absolute z-50 w-full mt-2 bg-white border border-slate-200 rounded-xl shadow-xl max-h-72 overflow-y-auto">
        <div class="sticky top-0 bg-white border-b border-slate-100 flex justify-between p-3 z-10">
          <button @click="selectAll" class="text-xs font-bold text-blue-600 hover:text-blue-800">Выбрать все</button>
          <button @click="clearAll"  class="text-xs font-bold text-red-500 hover:text-red-700">Очистить все</button>
        </div>
        <div v-for="sku in filteredSkuOptions" :key="sku" class="p-3 hover:bg-slate-50 cursor-pointer text-sm font-bold text-slate-700 flex items-center border-b border-slate-50 last:border-0" @click="toggleSku(sku)">
          <div class="w-5 h-5 border-2 rounded mr-3 flex items-center justify-center" :class="selectedSkus.includes(sku) ? 'bg-blue-500 border-blue-500' : 'border-slate-300'">
            <Check v-if="selectedSkus.includes(sku)" class="w-3 h-3 text-white" />
          </div>
          {{ sku }}
        </div>
        <div v-if="!filteredSkuOptions.length" class="p-4 text-center text-sm text-slate-400">Ничего не найдено</div>
      </div>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-400 font-bold animate-pulse">Подгружаем аналитику...</div>

    <div v-else class="space-y-6 animate-fade-in">

      <!-- ── KPI карточки ── -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center gap-4">
          <div class="p-3 bg-blue-50 text-blue-600 rounded-xl"><Package class="w-6 h-6" /></div>
          <div>
            <div class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1">Всего товаров</div>
            <div class="text-2xl font-black text-slate-800">{{ summary.total }} SKU</div>
          </div>
        </div>
        <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center gap-4">
          <div class="p-3 bg-amber-50 text-amber-500 rounded-xl"><Star class="w-6 h-6" /></div>
          <div>
            <div class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1">Средний балл</div>
            <div class="text-2xl font-black text-slate-800">{{ summary.avg }}</div>
          </div>
        </div>
        <div @click="toggleCardFilter('dropped')" :class="['p-5 rounded-2xl border shadow-sm flex items-center gap-4 cursor-pointer transition-all hover:shadow-md', activeCard==='dropped' ? 'bg-red-50 border-red-400 ring-2 ring-red-400/20' : 'bg-white border-red-200']">
          <div class="p-3 bg-red-100 text-red-600 rounded-xl"><TrendingDown class="w-6 h-6" /></div>
          <div>
            <div class="text-xs font-bold uppercase tracking-wider text-red-400 mb-1">Оценка снизилась</div>
            <div class="text-2xl font-black text-red-600">{{ summary.badGrowth }} SKU</div>
            <div class="text-xs text-red-400 mt-0.5">{{ periodCompareLabel }}</div>
          </div>
        </div>
        <div @click="toggleCardFilter('grown')" :class="['p-5 rounded-2xl border shadow-sm flex items-center gap-4 cursor-pointer transition-all hover:shadow-md', activeCard==='grown' ? 'bg-green-50 border-green-400 ring-2 ring-green-400/20' : 'bg-white border-green-200']">
          <div class="p-3 bg-green-100 text-green-600 rounded-xl"><TrendingUp class="w-6 h-6" /></div>
          <div>
            <div class="text-xs font-bold uppercase tracking-wider text-green-500 mb-1">Оценка выросла</div>
            <div class="text-2xl font-black text-green-600">{{ summary.goodGrowth }} SKU</div>
            <div class="text-xs text-green-500 mt-0.5">{{ periodCompareLabel }}</div>
          </div>
        </div>
      </div>

      <!-- ── Тепловая карта (по группам) ── -->
      <div class="bg-white rounded-3xl border border-slate-200 shadow-sm">
        <div class="px-6 py-5 flex justify-between items-center border-b border-slate-100">
          <h3 class="text-base font-black text-slate-800 flex items-center gap-3">
            <span class="w-2.5 h-6 bg-amber-400 rounded-full"></span>
            Тепловая матрица состояний
          </h3>
          <div class="flex items-center gap-3">
            <span class="text-xs text-slate-400">Кликните на категорию для раскрытия</span>
            <div class="flex bg-slate-100 rounded-lg p-1">
              <button @click="heatmapMode='absolute'" :class="{'bg-white shadow text-slate-800': heatmapMode==='absolute','text-slate-500': heatmapMode!=='absolute'}" class="px-4 py-1.5 text-xs font-bold rounded-md transition-all">Абсолют</button>
              <button @click="heatmapMode='delta'"    :class="{'bg-white shadow text-slate-800': heatmapMode==='delta',   'text-slate-500': heatmapMode!=='delta'}"    class="px-4 py-1.5 text-xs font-bold rounded-md transition-all">Динамика (+/-)</button>
            </div>
          </div>
        </div>

        <!-- ── Единая шапка дат (sticky при скролле) ── -->
        <div ref="axisHeaderRef" class="sticky top-0 z-20 bg-white border-b border-slate-100 shadow-sm"></div>

        <div v-for="([catName, catSkus], idx) in categoryGroups" :key="catName"
             :class="['border-slate-100', idx < categoryGroups.length - 1 ? 'border-b' : '']">

          <!-- ── Свёрнуто: мини-карта + прозрачный оверлей на Y-label для клика ── -->
          <div v-show="!expandedCategories.has(catName)" class="relative">
            <div :ref="el => { if (el) summaryChartRefs[catName] = el }" class="w-full"></div>
            <div @click="toggleCategory(catName)"
                 class="absolute inset-y-0 left-0 w-[204px] cursor-pointer z-10
                        flex items-center justify-end pr-2 group
                        hover:bg-blue-50/40 transition-colors">
              <ChevronRight class="w-3.5 h-3.5 text-slate-200 group-hover:text-blue-400 transition-colors flex-shrink-0" />
            </div>
          </div>

          <!-- ── Раскрыто: тонкая шапка-коллапсер + полная карта (v-if!) ── -->
          <div v-if="expandedCategories.has(catName)">
            <div @click="toggleCategory(catName)"
                 class="flex items-center gap-2 px-4 py-2 cursor-pointer hover:bg-slate-50
                        transition-colors select-none border-b border-slate-100">
              <ChevronDownIcon class="w-4 h-4 text-slate-400 flex-shrink-0" />
              <span class="font-bold text-slate-700 text-sm flex-1">{{ catName }}</span>
              <span class="text-xs text-slate-400">{{ catSkus.length }} SKU</span>
              <span v-if="categoryStats[catName]"
                    :class="['text-sm font-black ml-1', ratingColor(+categoryStats[catName])]">
                {{ categoryStats[catName] }} ★
              </span>
            </div>
            <div :ref="onCategoryChartMounted(catName)" class="pb-2"></div>
          </div>

        </div>
      </div>

      <!-- ── Динамика отзывов ── -->
      <div class="bg-white p-6 rounded-3xl border border-slate-200 shadow-sm">
        <h3 class="text-lg font-black text-slate-800 mb-1 flex items-center gap-3">
          <span class="w-2.5 h-6 bg-blue-500 rounded-full"></span>
          Динамика отзывов
          <span class="text-sm font-semibold text-slate-400">({{ groupBy==='day' ? 'прирост за период' : groupBy==='week' ? 'прирост по неделям' : 'прирост по месяцам' }})</span>
        </h3>
        <p class="text-xs text-slate-400 mb-6">Отсортировано по количеству новых оценок.</p>
        <div ref="structureRef" class="w-full"></div>
      </div>

      <!-- ══ Сводная таблица ВБ ══ -->
      <div v-if="platformStore.platform === 'wb' || platformStore.platform === 'all'"
           class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-5 border-b border-slate-100 flex items-center justify-between">
          <h3 class="text-base font-black text-slate-800 flex items-center gap-3">
            <span class="w-2.5 h-6 bg-purple-500 rounded-full"></span>
            Текущий рейтинг товаров — Wildberries
          </h3>
          <span class="text-xs text-slate-400">{{ wbSummary[0]?.snapshot_date ? 'Обновлено: ' + formatDateDisplay(wbSummary[0].snapshot_date.substring(0,10)) : '' }}</span>
        </div>
        <div v-if="summaryLoading" class="py-10 text-center text-sm text-slate-400 animate-pulse">Загрузка...</div>
        <div v-else-if="!sortedWbSummary.length" class="py-10 text-center text-sm text-slate-400">
          Нет данных. Запустите <code class="bg-slate-100 px-1 rounded">python3 worker.py</code> для синхронизации.
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wider select-none">
                <th @click="setWbSort('supplier_article')" class="px-5 py-3 text-left font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center gap-1">Артикул <component :is="wbSortCol==='supplier_article' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setWbSort('average_rating')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">Рейтинг <component :is="wbSortCol==='average_rating' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setWbSort('total_reviews')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">Всего оценок <component :is="wbSortCol==='total_reviews' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setWbSort('new_today')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">+Вчера <component :is="wbSortCol==='new_today' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setWbSort('stars_5')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-green-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★5 <component :is="wbSortCol==='stars_5' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                  <div class="text-[9px] font-normal text-slate-400 mt-0.5">за 12 мес.</div>
                </th>
                <th @click="setWbSort('stars_4')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-lime-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★4 <component :is="wbSortCol==='stars_4' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                  <div class="text-[9px] font-normal text-slate-400 mt-0.5">за 12 мес.</div>
                </th>
                <th @click="setWbSort('stars_3')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-yellow-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★3 <component :is="wbSortCol==='stars_3' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                  <div class="text-[9px] font-normal text-slate-400 mt-0.5">за 12 мес.</div>
                </th>
                <th @click="setWbSort('stars_2')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-orange-500 transition-colors">
                  <div class="flex items-center justify-center gap-1">★2 <component :is="wbSortCol==='stars_2' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                  <div class="text-[9px] font-normal text-slate-400 mt-0.5">за 12 мес.</div>
                </th>
                <th @click="setWbSort('stars_1')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-red-500 transition-colors">
                  <div class="flex items-center justify-center gap-1">★1 <component :is="wbSortCol==='stars_1' ? (wbSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                  <div class="text-[9px] font-normal text-slate-400 mt-0.5">за 12 мес.</div>
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50">
              <tr v-for="row in sortedWbSummary" :key="row.supplier_article" class="hover:bg-slate-50 transition-colors">
                <td class="px-5 py-3 font-bold text-slate-800">{{ row.supplier_article }}</td>
                <td class="px-4 py-3 text-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <span :class="['text-lg font-black', ratingColor(row.average_rating)]">{{ row.average_rating.toFixed(2) }}</span>
                    <span class="text-xs tracking-widest" :class="ratingColor(row.average_rating)">{{ starsDisplay(row.average_rating) }}</span>
                  </div>
                </td>
                <td class="px-4 py-3 text-center font-bold text-slate-700">{{ (row.total_reviews||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center">
                  <span v-if="row.new_today > 0" class="inline-flex items-center text-green-700 font-bold bg-green-50 px-2 py-0.5 rounded-full text-xs">+{{ row.new_today }}</span>
                  <span v-else class="text-slate-300 text-xs">—</span>
                </td>
                <td class="px-4 py-3 text-center text-green-700 font-semibold">{{ (row.stars_5||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-lime-700 font-semibold">{{ (row.stars_4||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-yellow-700 font-semibold">{{ (row.stars_3||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-orange-600 font-semibold">{{ (row.stars_2||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-red-600 font-semibold">{{ (row.stars_1||0).toLocaleString('ru') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ══ Сводная таблица ЯМ ══ -->
      <div v-if="platformStore.platform === 'ym' || platformStore.platform === 'all'"
           class="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden">
        <div class="px-6 py-5 border-b border-slate-100 flex items-center justify-between">
          <h3 class="text-base font-black text-slate-800 flex items-center gap-3">
            <span class="w-2.5 h-6 bg-yellow-400 rounded-full"></span>
            Текущий рейтинг товаров — Яндекс Маркет
          </h3>
          <div class="flex items-center gap-3">
            <span class="text-xs text-slate-400">{{ ymSummary[0]?.snapshot_date ? 'Снимок: ' + formatDateDisplay(ymSummary[0].snapshot_date) : '' }}</span>
            <label v-if="permissionsStore.isAdmin" class="cursor-pointer flex items-center gap-1.5 text-xs font-semibold text-yellow-700 bg-yellow-50 border border-yellow-200 px-3 py-1.5 rounded-xl hover:bg-yellow-100 transition-colors">
              <Upload class="w-3.5 h-3.5" />
              Загрузить XLSX
              <input type="file" accept=".xlsx" class="hidden" @change="uploadYmXlsx" />
            </label>
          </div>
        </div>
        <div v-if="ymUploadStatus" class="px-6 py-2 text-xs" :class="ymUploadStatus.ok ? 'text-emerald-600 bg-emerald-50' : 'text-rose-600 bg-rose-50'">
          {{ ymUploadStatus.message }}
        </div>
        <div v-if="summaryLoading" class="py-10 text-center text-sm text-slate-400 animate-pulse">Загрузка...</div>
        <div v-else-if="!sortedYmSummary.length" class="py-10 text-center text-sm text-slate-400">
          Нет данных. Загрузите файл <b>business_rating_report_*.xlsx</b> из кабинета ЯМ → Аналитика → Рейтинги.
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wider select-none">
                <th @click="setYmSort('supplier_article')" class="px-5 py-3 text-left font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center gap-1">Артикул <component :is="ymSortCol==='supplier_article' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th class="px-5 py-3 text-left font-bold text-slate-400">Товар</th>
                <th @click="setYmSort('average_rating')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">Рейтинг <component :is="ymSortCol==='average_rating' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('weekly_delta')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">Δ нед. <component :is="ymSortCol==='weekly_delta' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('total_reviews')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">Всего оценок <component :is="ymSortCol==='total_reviews' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('new_today')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 transition-colors">
                  <div class="flex items-center justify-center gap-1">+Вчера <component :is="ymSortCol==='new_today' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('stars_5')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-green-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★5 <component :is="ymSortCol==='stars_5' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('stars_4')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-lime-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★4 <component :is="ymSortCol==='stars_4' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('stars_3')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-yellow-600 transition-colors">
                  <div class="flex items-center justify-center gap-1">★3 <component :is="ymSortCol==='stars_3' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('stars_2')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-orange-500 transition-colors">
                  <div class="flex items-center justify-center gap-1">★2 <component :is="ymSortCol==='stars_2' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
                <th @click="setYmSort('stars_1')" class="px-4 py-3 text-center font-bold cursor-pointer hover:text-slate-800 text-red-500 transition-colors">
                  <div class="flex items-center justify-center gap-1">★1 <component :is="ymSortCol==='stars_1' ? (ymSortDir==='asc' ? ChevronUp : ChevronDownIcon) : ChevronsUpDown" class="w-3.5 h-3.5 opacity-50" /></div>
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50">
              <tr v-for="row in sortedYmSummary" :key="row.supplier_article" class="hover:bg-slate-50 transition-colors">
                <td class="px-5 py-3 font-bold text-slate-800">{{ row.supplier_article }}</td>
                <td class="px-5 py-3 text-xs text-slate-400 max-w-[160px] truncate" :title="row.product_name">{{ row.product_name || '—' }}</td>
                <td class="px-4 py-3 text-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <span :class="['text-lg font-black', ratingColor(row.average_rating)]">{{ row.average_rating.toFixed(2) }}</span>
                    <span class="text-xs tracking-widest" :class="ratingColor(row.average_rating)">{{ starsDisplay(row.average_rating) }}</span>
                  </div>
                </td>
                <td class="px-4 py-3 text-center">
                  <span v-if="row.weekly_delta > 0" class="text-green-600 font-bold text-xs">+{{ row.weekly_delta.toFixed(2) }}</span>
                  <span v-else-if="row.weekly_delta < 0" class="text-red-500 font-bold text-xs">{{ row.weekly_delta.toFixed(2) }}</span>
                  <span v-else class="text-slate-300 text-xs">—</span>
                </td>
                <td class="px-4 py-3 text-center font-bold text-slate-700">{{ (row.total_reviews||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center">
                  <span v-if="row.new_today > 0" class="inline-flex items-center text-green-700 font-bold bg-green-50 px-2 py-0.5 rounded-full text-xs">+{{ row.new_today }}</span>
                  <span v-else class="text-slate-300 text-xs">—</span>
                </td>
                <td class="px-4 py-3 text-center text-green-700 font-semibold">{{ (row.stars_5||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-lime-700 font-semibold">{{ (row.stars_4||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-yellow-700 font-semibold">{{ (row.stars_3||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-orange-600 font-semibold">{{ (row.stars_2||0).toLocaleString('ru') }}</td>
                <td class="px-4 py-3 text-center text-red-600 font-semibold">{{ (row.stars_1||0).toLocaleString('ru') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.animate-fade-in { animation: fadeIn 0.2s ease-in-out; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
</style>
