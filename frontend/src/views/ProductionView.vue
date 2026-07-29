<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { LineChart, LayoutGrid, PackageOpen, X, BarChart2, Search, Download, ChevronLeft, ArrowRight, ChevronRight, Image as ImageIcon, Calendar, Filter, MessageSquare } from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const claims = ref([])
const knownSkus = ref([])
const loading = ref(true)
const plotlyChart = ref(null)

const today = new Date()
const y = today.getFullYear()
const m = String(today.getMonth() + 1).padStart(2, '0')
const d = String(today.getDate()).padStart(2, '0')

const startDate = ref(`${y}-${m}-01`)
const endDate = ref(`${y}-${m}-${d}`)
const selectedSku = ref('Все')
const skuSearch = ref('')
const showSkuDropdown = ref(false)
const selectedInvoice = ref('Все')
const invoiceSearch = ref('')
const showInvoiceDropdown = ref(false)

// Режим матрицы: 'ai' — ИИ-теги (cat_1..13), 'ym_reasons' — причины Маркета (только ЯМ)
const matrixMode = ref('ai')

// --- ОКНО ЕДИНСТВЕННОГО КАЛЕНДАРЬ ---
const showCalendarPopover = ref(false)
const calendarYear = ref(today.getFullYear())
const calendarMonth = ref(today.getMonth())
const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
const activePeriodLabel = ref('current_month')
const _cy = today.getFullYear()
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
}
const setCurrentMonth = () => {
  const now = new Date()
  const y = now.getFullYear(), m = String(now.getMonth()+1).padStart(2,'0'), d2 = String(now.getDate()).padStart(2,'0')
  startDate.value = `${y}-${m}-01`; endDate.value = `${y}-${m}-${d2}`
  activePeriodLabel.value = 'current_month'; calendarYear.value = y; calendarMonth.value = now.getMonth()
  showCalendarPopover.value = false
}

// --- КНОПКА "ПРОШЛЫЙ МЕСЯЦ" ---
const setPreviousMonth = () => {
  const d = new Date()
  d.setMonth(d.getMonth() - 1)
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const lastDay = new Date(year, d.getMonth() + 1, 0).getDate()
  startDate.value = `${year}-${month}-01`
  endDate.value = `${year}-${month}-${lastDay}`
  calendarYear.value = year; calendarMonth.value = d.getMonth()
  activePeriodLabel.value = 'prev_month'
  showCalendarPopover.value = false
}

const modalData = ref({ isOpen: false, title: '', claims: [], rawSku: '', catId: '' })
const lightbox = ref({ isOpen: false, photos: [], index: 0 })

// ── Умный поиск ──────────────────────────────────────────────────────────────
const smartQuery      = ref('')
const smartCategory   = ref('Все')
const smartSkus       = ref([])
const smartDateFrom   = ref('')
const smartDateTo     = ref('')
const smartOnlyVideo  = ref(false)
const smartPage       = ref(1)
const SMART_PAGE_SIZE = 20
const showSmartSkuDropdown = ref(false)
const smartSkuSearch  = ref('')

const smartCategories = computed(() => {
  const cats = new Set(claims.value.map(c => c.category || 'Прочее').filter(Boolean))
  return ['Все', ...Array.from(cats).sort()]
})

const smartSkuList = computed(() => Array.from(new Set(claims.value.map(c => c['Артикул продавца'] || ''))).filter(Boolean).sort())
const filteredSmartSkuList = computed(() => smartSkuList.value.filter(s => String(s).toLowerCase().includes(smartSkuSearch.value.toLowerCase())))
const toggleSmartSku = (sku) => {
  const idx = smartSkus.value.indexOf(sku)
  if (idx === -1) smartSkus.value.push(sku)
  else smartSkus.value.splice(idx, 1)
}

const smartResults = computed(() => {
  const q = smartQuery.value.trim().toLowerCase()
  return claims.value.filter(c => {
    if (smartCategory.value !== 'Все' && (c.category || 'Прочее') !== smartCategory.value) return false
    if (smartSkus.value.length > 0 && !smartSkus.value.includes(String(c['Артикул продавца']))) return false
    if (smartDateFrom.value && c.claim_date_iso && c.claim_date_iso < smartDateFrom.value) return false
    if (smartDateTo.value && c.claim_date_iso && c.claim_date_iso > smartDateTo.value) return false
    if (q) {
      const comment = (c['Комментарий покупателя'] || '').toLowerCase()
      const похожий = (c['Похожий отзыв'] || '').toLowerCase()
      if (!comment.includes(q) && !похожий.includes(q)) return false
    }
    if (smartOnlyVideo.value && !parseVideos(c.videos || c.db_videos || '').length) return false
    return true
  })
})

const smartResultsPaged = computed(() => smartResults.value.slice(0, smartPage.value * SMART_PAGE_SIZE))
const hasMoreSmartResults = computed(() => smartResultsPaged.value.length < smartResults.value.length)

watch([smartQuery, smartCategory, smartSkus, smartDateFrom, smartDateTo, smartOnlyVideo], () => { smartPage.value = 1 }, { deep: true })

const openSmartClaim = async (claim) => {
  modalData.value = {
    isOpen: true,
    title: `🔍 ${claim['Артикул продавца']} · ${claim.claim_date_iso || ''}`,
    claims: [claim],
    rawSku: claim['Артикул продавца'],
    catId: ''
  }
  if (claim.photos === undefined || claim.photos === null) {
    try {
      const res = await apiFetch(`/api/v1/analytics/claim-media/${claim.SRID || claim.srid}`)
      const media = await res.json()
      claim.photos = media.photos || ""
      claim.videos = media.video_paths || ""
    } catch { claim.photos = ""; claim.videos = "" }
  }
}

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const fetchData = async () => {
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/analytics/production-claims?platform=${platformStore.platform}`)
    const json = await res.json()
    claims.value = json.data || []
    knownSkus.value = json.known_skus || []
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

watch(() => platformStore.platform, (p) => {
  // У WB нет причин Маркета — возвращаемся к ИИ-тегам
  if (p !== 'ym') matrixMode.value = 'ai'
  fetchData()
})

// --- ЛОГИКА ЕДИНСТВЕННОГО ОКНА КАЛЕНДАРЯ ---
const calendarDays = computed(() => {
  const firstDay = new Date(calendarYear.value, calendarMonth.value, 1).getDay()
  const padding = firstDay === 0 ? 6 : firstDay - 1
  const totalDays = new Date(calendarYear.value, calendarMonth.value + 1, 0).getDate()
  const days = []
  
  const prevTotal = new Date(calendarYear.value, calendarMonth.value, 0).getDate()
  for (let i = padding - 1; i >= 0; i--) {
    days.push({ day: prevTotal - i, isCurrentMonth: false, dateStr: null })
  }
  for (let i = 1; i <= totalDays; i++) {
    const mStr = String(calendarMonth.value + 1).padStart(2, '0')
    const dStr = String(i).padStart(2, '0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})

const handleCalendarDayClick = (d) => {
  if (!d.isCurrentMonth || !d.dateStr) return
  if (!startDate.value || (startDate.value && endDate.value)) {
    startDate.value = d.dateStr
    endDate.value = ''
  } else {
    if (d.dateStr < startDate.value) {
      startDate.value = d.dateStr
      endDate.value = ''
    } else {
      endDate.value = d.dateStr
      showCalendarPopover.value = false
    }
  }
}
const changeCalendarMonth = (dir) => {
  calendarMonth.value += dir
  if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
  else if (calendarMonth.value < 0) { calendarMonth.value = 11; calendarYear.value-- }
}
const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

// НАДЕЖНЫЙ СЕРВЕРНЫЙ ФИЛЬТР (Все 656 одобренных рекламаций на месте)
const filteredClaims = computed(() => {
  return claims.value.filter(c => {
    if (!c.claim_date_iso) return false
    if (c.claim_date_iso < startDate.value || c.claim_date_iso > endDate.value) return false
    if (selectedSku.value !== 'Все' && String(c['Артикул продавца']) !== String(selectedSku.value)) return false
    if (selectedInvoice.value !== 'Все' && String(c['Инвойс']) !== String(selectedInvoice.value)) return false
    return true
  })
})

const skuList = computed(() => {
  const fromClaims = claims.value.map(c => c['Артикул продавца'] || 'Без артикула')
  const fromAssortment = knownSkus.value.map(s => s.supplier_article)
  return ['Все', ...Array.from(new Set([...fromClaims, ...fromAssortment])).sort()]
})
const filteredSkuList = computed(() => skuList.value.filter(s => String(s).toLowerCase().includes(skuSearch.value.toLowerCase())))
const invoiceList = computed(() => ['Все', ...Array.from(new Set(claims.value.map(c => c['Инвойс'] || 'Не указан'))).filter(i => i !== 'Не указан' && i !== '0').sort()])
const filteredInvoiceList = computed(() => invoiceList.value.filter(i => String(i).toLowerCase().includes(invoiceSearch.value.toLowerCase())))

const matrixData = computed(() => {
  const fromClaims = filteredClaims.value.map(c => c['Артикул продавца'] || 'Без артикула')
  const fromAssortment = knownSkus.value.map(s => s.supplier_article)
  const skus = Array.from(new Set([...fromClaims, ...fromAssortment])).sort()
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

// Вторая матрица — по причинам/субпричинам ЯМ.
// Логика ключа: субпричина (если есть и не UNKNOWN), иначе основная причина.
// Это даёт точные ярлыки: «Некомплект», «Повреждён» вместо размытого «Брак / качество».
const reasonMatrixData = computed(() => {
  const fromClaims = filteredClaims.value.map(c => c['Артикул продавца'] || 'Без артикула')
  const fromAssortment = knownSkus.value.map(s => s.supplier_article)
  const skus = Array.from(new Set([...fromClaims, ...fromAssortment])).sort()
  const map = {}

  filteredClaims.value.forEach(c => {
    if (c.platform !== 'ym') return

    const hasSub = c.subreason_type && c.subreason_type !== 'UNKNOWN'
    const code = hasSub ? c.subreason_type : (c.reason_type || 'NONE')
    const name = hasSub ? (c['Субпричина ЯМ'] || code) : (c['Причина ЯМ'] || 'Без причины')

    if (!map[code]) {
      map[code] = { id: code, name, total: 0, cells: {} }
      skus.forEach(s => map[code].cells[s] = 0)
    }
    const sKey = c['Артикул продавца'] || 'Без артикула'
    if (map[code].cells[sKey] !== undefined) {
      map[code].cells[sKey]++
      map[code].total++
    }
  })

  const rows = Object.values(map).filter(r => r.total > 0).sort((a, b) => b.total - a.total)
  return { skus, rows }
})

// Активная матрица в зависимости от режима
const activeMatrix = computed(() => matrixMode.value === 'ym_reasons' ? reasonMatrixData.value : matrixData.value)

const maxDefects = computed(() => Math.max(...activeMatrix.value.rows.flatMap(r => Object.values(r.cells)), 1))
const getCellColor = (count, maxCount) => {
  if (count === 0) return 'bg-slate-50 text-transparent'
  const intensity = count / (maxCount || 1)
  if (intensity > 0.7) return 'bg-blue-600 text-white font-bold'
  if (intensity > 0.4) return 'bg-blue-400 text-white font-bold'
  return 'bg-blue-100 text-blue-900'
}

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

// --- СВОДКА ПО ИНВОЙСАМ С ХОВЕРОМ ТЕКСТА ---
const topInvoices = computed(() => {
  const map = {}
  filteredClaims.value.forEach(c => {
    const hasTags = Object.keys(CATEGORIES).some(id => ['1','1.0','+','true','да'].includes(String(c[id] || '').trim().toLowerCase()))
    if (!hasTags) return

    const inv = c['Инвойс'] || 'Не указан'
    if (inv === 'Не указан' || inv === '' || inv === '0') return
    if (!map[inv]) map[inv] = { count: 0, supplies: new Set(), skus: {} }
    
    map[inv].count++
    map[inv].supplies.add(c['Номер поставки_ОРИГИНАЛ'] || c['Номер поставки'] || '---')
    
    const sku = c['Артикул продавца'] || 'Без артикула'
    map[inv].skus[sku] = (map[inv].skus[sku] || 0) + 1
  })
  
  return Object.entries(map).map(([inv, d]) => {
    const skuSummary = Object.entries(d.skus).map(([s, cnt]) => `${s} (${cnt} шт)`).join(' • ')
    const topSkus = Object.entries(d.skus).sort((a,b) => b[1]-a[1]).slice(0, 3)
    return {
      inv,
      count: d.count,
      supplies: Array.from(d.supplies),
      skus: topSkus,
      hoverText: `Артикулы: ${skuSummary} | Поставки: ${Array.from(d.supplies).join(', ')}`
    }
  }).sort((a,b) => b.count - a.count).slice(0, 15)
})
const maxInvoiceCount = computed(() => Math.max(...topInvoices.value.map(i => i.count), 1))

// ВАТЧЕР ДЛЯ ТРЕНДОВ ГРАФИКА
watch(selectedSku, async (newSku) => {
  if (newSku === 'Все') {
    if (plotlyChart.value) Plotly.purge(plotlyChart.value)
    return
  }
  await nextTick()
  try {
    const res = await apiFetch(`/api/v1/analytics/sku-trend/${encodeURIComponent(newSku)}`)
    if (res.ok) {
      const json = await res.json()
      renderPlotlyChart(json.data || [])
    }
  } catch(e) { console.error(e) }
})

// 🔥 ЖЕЛЕЗНЫЙ ИСПРАВЛЕННЫЙ ЖУРНАЛ СБОРКИ ГРАФИКА ДЛЯ PLOTLY
const renderPlotlyChart = (data) => {
  if (!plotlyChart.value) return
  const grouped = {}
  
  // Хронологическая сортировка дат для оси X
  const uniqueDates = Array.from(new Set(data.map(d => d.Месяц || d.Mesyats || ''))).filter(Boolean).sort()
  const sortedLabels = uniqueDates.map(dateStr => {
    if (dateStr.includes('-')) {
      const parts = dateStr.split('-')
      const monthIdx = parseInt(parts[1]) - 1
      if (monthIdx >= 0 && monthIdx < 12) return `${monthNames[monthIdx]} ${parts[0]}`
    }
    return dateStr
  })

  // Контрастная палитра
  const distinctColors = {
    1: '#3b82f6', 2: '#60a5fa', 3: '#f59e0b', 4: '#ef4444', 5: '#f87171',
    6: '#10b981', 7: '#ec4899', 8: '#6b7280', 9: '#8b5cf6', 10: '#06b6d4',
    11: '#14b8a6', 12: '#eab308', 13: '#78350f'
  }

  data.forEach(d => {
    if (!grouped[d.Источник]) grouped[d.Источник] = { x: [], y: [] }
    const dateStr = d.Месяц || d.Mesyats || ''
    let label = dateStr
    if (dateStr && dateStr.includes('-')) {
      const parts = dateStr.split('-')
      const monthIdx = parseInt(parts[1]) - 1
      if (monthIdx >= 0 && monthIdx < 12) label = `${monthNames[monthIdx]} ${parts[0]}`
    }
    grouped[d.Источник].x.push(label)
    grouped[d.Источник].y.push(d.Количество)
  })

  const traces = Object.keys(grouped).map(source => {
    const isHist = source === 'Общий брак'
    const traceObj = {
      x: grouped[source].x,
      y: grouped[source].y,
      name: source,
      type: 'bar',
      hovertemplate: `<b>${source}</b><br>Количество: %{y}<extra></extra>`,
      marker: {
        line: { color: '#ffffff', width: 1.5 }
      }
    }
    if (isHist) {
      traceObj.marker.color = '#94a3b8'
    } else {
      const catId = parseInt(source.split('.')[0])
      if (distinctColors[catId]) traceObj.marker.color = distinctColors[catId]
    }
    return traceObj
  })

  const layout = {
    title: `Динамика дефектов по месяцам: ${selectedSku.value}`,
    barmode: 'stack',
    height: 400,         // Оптимальная высота под размер карточки
    bargap: 0.15,        // Минимальный зазор: столбцы станут плотными и толстыми
    showlegend: false,   // Сводка отключена (Пункт 3)
    margin: { t: 50, l: 50, r: 40, b: 50 }, // Сбалансированные отступы: график растянется во всю высоту рамки
    xaxis: { 
      type: 'category',
      categoryorder: 'array',
      categoryarray: sortedLabels
    },
    yaxis: { title: 'Кол-во заявок' },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent'
  }
  Plotly.newPlot(plotlyChart.value, traces, layout, { responsive: true })
}

const downloadImg = async (url, filename = 'photo.jpg') => {
  try {
    const response = await apiFetch(url)
    const blob = await response.blob()
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = filename
    link.click()
  } catch (e) { window.open(url, '_blank') }
}

const downloadAllFromModal = async () => {
  const allUrls = modalData.value.claims.flatMap(c => parsePhotos(c.photos))
  for (let [i, url] of allUrls.entries()) {
    await new Promise(r => setTimeout(r, 150))
    downloadImg(url, `claim_${modalData.value.rawSku}_${i+1}.jpg`)
  }
}

const openLightbox = (photos, startIndex) => { lightbox.value = { isOpen: true, photos, index: startIndex } }
const nextPhoto = () => { lightbox.value.index = (lightbox.value.index + 1) % lightbox.value.photos.length }
const prevPhoto = () => { lightbox.value.index = (lightbox.value.index - 1 + lightbox.value.photos.length) % lightbox.value.photos.length }

const openMatrixDetails = async (sku, catId, catName) => {
  const details = filteredClaims.value.filter(c => {
    if (String(c['Артикул продавца']) !== String(sku)) return false
    if (matrixMode.value === 'ym_reasons') {
      return c.platform === 'ym' && String(c.reason_type || 'NONE') === String(catId)
    }
    return ['1','1.0','+','true','да'].includes(String(c[catId] || '').trim().toLowerCase())
  })
  modalData.value = { isOpen: true, title: `📦 ${sku} | 🛠 ${catName}`, claims: details, rawSku: sku, catId }

  for (let c of details) {
    // lazy-load только для WB (photos=undefined/null); Ozon/YM уже имеют photos="" — не трогаем
    if (c.photos === undefined || c.photos === null) {
      try {
        const res = await apiFetch(`/api/v1/analytics/claim-media/${c.SRID || c.srid}`)
        const media = await res.json()
        c.photos = media.photos || ""
        c.videos = media.video_paths || ""
      } catch { c.photos = ""; c.videos = "" }
    }
  }
}

const openInvoiceDetails = async (invoiceObj) => {
  const details = claims.value.filter(c => String(c['Инвойс']).trim() === String(invoiceObj.inv).trim())
  modalData.value = { isOpen: true, title: `🧾 Инвойс: ${invoiceObj.inv}`, claims: details, rawSku: invoiceObj.inv }
  for (let c of details) {
    if (c.photos === undefined || c.photos === null) {
      try {
        const res = await apiFetch(`/api/v1/analytics/claim-media/${c.SRID || c.srid}`)
        const media = await res.json()
        c.photos = media.photos || ""
        c.videos = media.video_paths || ""
      } catch { c.photos = ""; c.videos = "" }
    }
  }
}

const parsePhotos = (str) => str ? str.split(' ').map(g => g.split('|').pop().replace(/^\/\//, 'https://')).slice(0, 8) : []
const parseVideos = (str) => str ? (str.match(/\/\/[^\s"'\]\[,;<>]+/g) || []).map(u => 'https:' + u) : []

onMounted(fetchData)
</script>

<template>
  <div class="p-3 md:p-4 w-full mx-auto pb-20 relative bg-slate-50 min-h-screen font-sans max-w-[1600px]">
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4 md:mb-6">
      <div class="flex items-center gap-3">
        <div class="p-2.5 bg-blue-600 text-white rounded-xl shadow-lg shadow-blue-200"><LineChart class="w-6 h-6" /></div>
        <h1 class="text-xl font-black text-slate-800 uppercase tracking-wider">Карта проблем</h1>
      </div>
      
      <div class="relative">
        <div class="flex items-center bg-white border border-slate-200 rounded-xl px-4 py-2.5 cursor-pointer hover:border-blue-500 transition-colors shadow-xs" @click="showCalendarPopover = !showCalendarPopover">
          <Calendar class="w-4 h-4 text-slate-400 mr-2.5" />
          <span class="text-xs font-bold text-slate-700 tracking-tight">
            {{ activePeriodLabel === 'current_month' ? 'Текущий месяц' : activePeriodLabel === 'prev_month' ? 'Прошлый месяц' : activePeriodLabel || (formatDateDisplay(startDate) + ' — ' + formatDateDisplay(endDate)) }}
          </span>
        </div>
        <div v-if="showCalendarPopover" class="absolute right-0 mt-2 bg-white border border-slate-100 rounded-2xl shadow-2xl z-[150] p-4 w-72 animate-in zoom-in-95 duration-150">
          <div class="mb-4 border-b border-slate-100 pb-3 space-y-2">
            <div class="grid grid-cols-2 gap-1.5">
              <button @click.stop="setCurrentMonth" :class="activePeriodLabel === 'current_month' ? 'bg-slate-800 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700'" class="text-center text-xs font-black py-2 rounded-xl transition-all">Текущий месяц</button>
              <button @click.stop="setPreviousMonth" :class="activePeriodLabel === 'prev_month' ? 'bg-slate-800 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700'" class="text-center text-xs font-black py-2 rounded-xl transition-all">Прошлый месяц</button>
            </div>
            <div class="grid grid-cols-4 gap-1">
              <button v-for="m in MONTHS_PRESETS" :key="m.label" @click.stop="selectSpecificMonth(m)" :class="activePeriodLabel === m.label ? 'bg-slate-800 text-white' : 'hover:bg-slate-100 text-slate-600'" class="text-center text-[11px] font-bold py-1.5 rounded-lg transition-all">{{ m.label }}</button>
            </div>
          </div>
          <div class="flex justify-between items-center mb-3">
            <button @click="changeCalendarMonth(-1)" class="p-1 hover:bg-slate-100 rounded-lg text-xs font-bold">◀</button>
            <span class="text-xs font-black text-slate-700 uppercase tracking-tighter">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
            <button @click="changeCalendarMonth(1)" class="p-1 hover:bg-slate-100 rounded-lg text-xs font-bold">▶</button>
          </div>
          <div class="grid grid-cols-7 gap-1 text-center text-[10px] font-black text-slate-400 uppercase mb-2">
            <div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div>
          </div>
          <div class="grid grid-cols-7 gap-1">
            <div v-for="(d, idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)" :class="['h-7 flex items-center justify-center text-xs font-bold rounded-md transition-all select-none', !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer', d.dateStr === startDate || d.dateStr === endDate ? 'bg-blue-600 text-white' : '', d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-blue-50 text-blue-600' : '', d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : '']">{{ d.day }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <div class="relative">
        <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-4 py-3 cursor-pointer group" @click="showSkuDropdown = !showSkuDropdown">
          <Search class="w-4 h-4 text-slate-400 mr-3 group-hover:text-blue-500 transition-colors" />
          <span class="text-sm font-bold text-slate-700 flex-1">{{ selectedSku === 'Все' ? 'Поиск по артикулу...' : selectedSku }}</span>
          <X v-if="selectedSku !== 'Все'" class="w-4 h-4 text-slate-300 hover:text-red-500" @click.stop="selectedSku = 'Все'" />
        </div>
        <div v-if="showSkuDropdown" class="absolute top-full left-0 right-0 mt-2 bg-white border rounded-2xl shadow-2xl z-[100] p-2">
          <input type="text" v-model="skuSearch" class="w-full border-none bg-slate-50 rounded-xl p-3 text-sm mb-2 focus:ring-2 focus:ring-blue-100" placeholder="Начните вводить..." @click.stop />
          <div class="max-h-60 overflow-y-auto custom-scroll">
            <div v-for="sku in filteredSkuList" :key="sku" @click="selectedSku = sku; showSkuDropdown = false" class="p-3 text-sm font-semibold hover:bg-blue-50 hover:text-blue-600 rounded-xl cursor-pointer">{{ sku }}</div>
          </div>
        </div>
      </div>
      <div class="relative">
        <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-4 py-3 cursor-pointer group" @click="showInvoiceDropdown = !showInvoiceDropdown">
          <PackageOpen class="w-4 h-4 text-slate-400 mr-3 group-hover:text-blue-500 transition-colors" />
          <span class="text-sm font-bold text-slate-700 flex-1">{{ selectedInvoice === 'Все' ? 'Поиск по инвойсу...' : selectedInvoice }}</span>
          <X v-if="selectedInvoice !== 'Все'" class="w-4 h-4 text-slate-300 hover:text-red-500" @click.stop="selectedInvoice = 'Все'" />
        </div>
        <div v-if="showInvoiceDropdown" class="absolute top-full left-0 right-0 mt-2 bg-white border rounded-2xl shadow-2xl z-[100] p-2">
          <input type="text" v-model="invoiceSearch" class="w-full border-none bg-slate-50 rounded-xl p-3 text-sm mb-2 focus:ring-2 focus:ring-blue-100" placeholder="Номер инвойса..." @click.stop />
          <div class="max-h-60 overflow-y-auto custom-scroll">
            <div v-for="inv in filteredInvoiceList" :key="inv" @click="selectedInvoice = inv; showInvoiceDropdown = false" class="p-3 text-sm font-semibold hover:bg-blue-50 hover:text-blue-600 rounded-xl cursor-pointer">{{ inv }}</div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-500 font-medium animate-pulse">⚙️ База данных обрабатывается на сервере. Пожалуйста, подождите...</div>

    <div v-else class="space-y-6">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Всего заявок в периоде</div><div class="text-3xl font-black text-slate-800">{{ kpis.total }}</div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Размечено ИИ</div><div class="text-3xl font-black text-blue-600">{{ kpis.tagged }} <span class="text-sm font-semibold text-slate-400">({{ kpis.processed_percent }}%)</span></div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Изменено вручную</div><div class="text-3xl font-black text-amber-500">{{ kpis.corrected }}</div></div>
        <div class="bg-white border p-5 rounded-2xl shadow-sm"><div class="text-xs font-bold text-slate-400 uppercase">Точность ИИ</div><div class="text-3xl font-black text-emerald-600">{{ kpis.accuracy }}%</div></div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 bg-slate-50/50 flex justify-between items-center">
          <div class="flex items-center gap-2"><LayoutGrid class="w-4 h-4 text-blue-500" /><h2 class="text-sm font-black text-slate-700 uppercase">Матрица проблем</h2></div>
          <!-- Переключатель карт: ЯМ и Ozon -->
          <div v-if="platformStore.platform === 'ym' || platformStore.platform === 'ozon'" class="flex items-center gap-1 bg-slate-100 rounded-xl p-1">
            <button
              @click="matrixMode = 'ai'"
              :class="['px-3 py-1.5 text-[11px] font-bold rounded-lg transition-all', matrixMode === 'ai' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']"
            >ИИ-теги</button>
            <button
              @click="matrixMode = 'ym_reasons'"
              :class="['px-3 py-1.5 text-[11px] font-bold rounded-lg transition-all', matrixMode === 'ym_reasons' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']"
            >Причины Маркета</button>
          </div>
        </div>
        <div class="overflow-auto max-h-[calc(100vh-350px)] custom-scroll">
          <table class="w-full border-collapse table-fixed min-w-max">
            <thead>
            <tr class="sticky top-0 z-40 bg-white border-b border-slate-100">
              <th class="w-56 p-4 text-left font-black text-slate-400 text-[10px] uppercase sticky left-0 bg-white border-r z-50 shadow-[2px_0_5px_rgba(0,0,0,0.02)]">Причина / Артикул</th>
              
              <th v-for="sku in activeMatrix.skus" :key="sku" class="w-[16px] min-w-[16px] max-w-[16px] h-36 p-0 relative border-r border-slate-100 last:border-0 bg-white group cursor-pointer">
                
                <div class="hidden group-hover:block absolute top-2 left-1/2 -translate-x-1/2 px-5 py-3 bg-slate-900 text-white text-base font-black rounded-xl shadow-2xl whitespace-nowrap z-[100] animate-in fade-in zoom-in-95 duration-75">
                  📦 {{ sku }}
                </div>
                
                <div class="absolute inset-x-0 top-2 bottom-2 flex justify-center items-center">
                  <span 
                    :style="{
                      writingMode: 'vertical-rl',
                      transform: 'rotate(180deg)',
                      textAlign: 'center',
                      width: '100%',
                      lineHeight: '1'
                    }"
                    class="font-normal text-slate-500 uppercase tracking-tighter"
                  >
                    {{ sku }}
                  </span>
                </div>

              </th>
            </tr>
          </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="row in activeMatrix.rows" :key="row.id" class="group border-b border-slate-50 last:border-0 hover:bg-slate-50 transition-colors">
                <td class="p-3 text-[11px] font-bold text-slate-600 sticky left-0 bg-white group-hover:bg-slate-50 z-30 border-r border-slate-100 shadow-[2px_0_5px_rgba(0,0,0,0.02)] truncate" :title="row.name">
                  <span v-if="matrixMode === 'ai'" class="text-blue-500 mr-1 opacity-50">{{ row.id }}</span> {{ row.name }}
                </td>
                <td v-for="sku in activeMatrix.skus" :key="sku" class="p-0 text-center border-r border-slate-100 last:border-0 w-[16px] min-w-[16px] max-w-[16px]">
                  <div 
                    v-if="row.cells[sku] > 0"
                    @click="openMatrixDetails(sku, row.id, row.name)"
                    :class="['h-8 flex items-center justify-center rounded-xs text-[9px] font-bold cursor-pointer hover:scale-105 transition-transform active:scale-95', getCellColor(row.cells[sku], maxDefects)]"
                  >
                    {{ row.cells[sku] }}
                  </div>
                  <div v-else class="h-8 bg-slate-50/20 rounded-xs"></div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-2xl shadow-xs p-5">
        <div class="flex items-center gap-2 mb-4"><PackageOpen class="w-4 h-4 text-orange-500" /><h2 class="text-xs font-black text-slate-700 uppercase tracking-wider">Проблемные инвойсы (Топ-15)</h2></div>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div v-for="inv in topInvoices" :key="inv.inv" class="p-3 border rounded-xl bg-slate-50/30 hover:border-orange-200 cursor-pointer group" :title="inv.hoverText" @click="openInvoiceDetails(inv)">
            <div class="flex justify-between text-xs font-bold mb-1"><span class="text-slate-700 group-hover:text-blue-600 truncate mr-2">🧾 {{ inv.inv }}</span><span class="text-orange-600">{{ inv.count }} шт</span></div>
            <div class="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden mb-2"><div class="bg-orange-400 h-full rounded-full group-hover:bg-orange-500 transition-all" :style="{ width: `${(inv.count / maxInvoiceCount) * 100}%` }"></div></div>
            <div class="flex flex-wrap gap-1">
              <span v-for="[sku, cnt] in inv.skus" :key="sku" class="text-[10px] font-bold px-1.5 py-0.5 bg-blue-50 text-blue-600 border border-blue-100 rounded-md truncate max-w-[120px]" :title="`${sku} — ${cnt} шт`">{{ sku }} <span class="text-blue-400">{{ cnt }}</span></span>
            </div>
          </div>
        </div>
      </div>

      <!-- ══ УМНЫЙ ПОИСК ПО КОММЕНТАРИЯМ ══ -->
      <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <MessageSquare class="w-4 h-4 text-violet-500" />
            <h2 class="text-sm font-black text-slate-700 uppercase tracking-wider">Поиск по комментариям</h2>
          </div>
          <span v-if="smartResults.length" class="text-xs font-bold text-violet-600 bg-violet-50 px-3 py-1 rounded-full">
            {{ smartResults.length }} {{ smartResults.length === 1 ? 'результат' : smartResults.length < 5 ? 'результата' : 'результатов' }}
          </span>
        </div>

        <!-- Фильтры поиска -->
        <div class="p-5 border-b border-slate-100 space-y-3">
          <!-- Текстовый поиск -->
          <div class="relative">
            <Search class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            <input
              v-model="smartQuery"
              type="text"
              placeholder="Поиск по тексту комментария..."
              class="w-full pl-10 pr-4 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-400 transition"
            />
            <button v-if="smartQuery" @click="smartQuery = ''" class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-300 hover:text-red-400 transition">
              <X class="w-4 h-4" />
            </button>
          </div>

          <!-- Фильтры: категория + артикул + период -->
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <!-- Категория товара -->
            <select v-model="smartCategory"
              class="px-3 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-400 transition font-medium text-slate-700">
              <option v-for="cat in smartCategories" :key="cat" :value="cat">{{ cat === 'Все' ? 'Все категории' : cat }}</option>
            </select>

            <!-- Артикулы (множественный выбор) -->
            <div class="relative sm:col-span-1">
              <div class="flex items-center bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 cursor-pointer min-h-[42px]" @click="showSmartSkuDropdown = !showSmartSkuDropdown">
                <span v-if="smartSkus.length === 0" class="text-sm font-medium text-slate-400 flex-1">Все артикулы</span>
                <div v-else class="flex flex-wrap gap-1 flex-1 min-w-0">
                  <span v-for="s in smartSkus" :key="s"
                    class="inline-flex items-center gap-1 text-[11px] font-bold px-1.5 py-0.5 bg-violet-100 text-violet-700 rounded-md">
                    {{ s }}
                    <button @click.stop="toggleSmartSku(s)" class="hover:text-red-500"><X class="w-3 h-3" /></button>
                  </span>
                </div>
                <X v-if="smartSkus.length > 0" class="w-3.5 h-3.5 text-slate-300 hover:text-red-400 flex-shrink-0 ml-1" @click.stop="smartSkus = []" />
              </div>
              <div v-if="showSmartSkuDropdown" class="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-[100] p-2">
                <input type="text" v-model="smartSkuSearch" class="w-full bg-slate-50 rounded-lg p-2 text-sm mb-1 focus:outline-none" placeholder="Поиск артикула..." @click.stop />
                <div class="max-h-52 overflow-y-auto custom-scroll">
                  <div v-for="s in filteredSmartSkuList" :key="s"
                    @click.stop="toggleSmartSku(s)"
                    :class="['flex items-center gap-2 px-3 py-2 text-sm font-semibold rounded-lg cursor-pointer transition-colors',
                      smartSkus.includes(s) ? 'bg-violet-50 text-violet-700' : 'hover:bg-slate-50 text-slate-700']">
                    <div :class="['w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors',
                      smartSkus.includes(s) ? 'bg-violet-600 border-violet-600' : 'border-slate-300']">
                      <svg v-if="smartSkus.includes(s)" class="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 10 10">
                        <path d="M1.5 5l2.5 2.5 4.5-4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                      </svg>
                    </div>
                    {{ s }}
                  </div>
                </div>
              </div>
            </div>

            <!-- Период от -->
            <input type="date" v-model="smartDateFrom"
              class="px-3 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-400 transition text-slate-700" />

            <!-- Период до -->
            <input type="date" v-model="smartDateTo"
              class="px-3 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-400 transition text-slate-700" />
          </div>

          <!-- Только с видео -->
          <button @click="smartOnlyVideo = !smartOnlyVideo"
            :class="['flex items-center gap-1.5 px-3 py-2 text-xs font-bold rounded-xl border transition',
              smartOnlyVideo ? 'bg-violet-600 border-violet-600 text-white' : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100']">
            🎥 Только с видео
          </button>
        </div>

        <!-- Результаты -->
        <div class="divide-y divide-slate-50">
          <div v-if="!smartQuery && smartCategory === 'Все' && smartSkus.length === 0 && !smartDateFrom && !smartDateTo"
            class="py-12 text-center text-slate-400 text-sm font-medium">
            Введите слово или выберите фильтр — найдём все подходящие возвраты с комментариями
          </div>

          <div v-else-if="smartResults.length === 0"
            class="py-10 text-center text-slate-400 text-sm font-medium">
            Ничего не найдено — попробуйте другой запрос
          </div>

          <div v-else>
            <div v-for="claim in smartResultsPaged" :key="claim.SRID || claim.srid"
              @click="openSmartClaim(claim)"
              class="px-5 py-4 hover:bg-violet-50/40 cursor-pointer transition-colors group">
              <div class="flex items-start gap-4">
                <!-- Превью фото -->
                <div class="flex-shrink-0 w-14 h-14 rounded-xl overflow-hidden border border-slate-100 bg-slate-50">
                  <img v-if="parsePhotos(claim.photos || '').length"
                    :src="parsePhotos(claim.photos || '')[0]"
                    class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
                  <div v-else class="w-full h-full flex items-center justify-center text-slate-300">
                    <ImageIcon class="w-5 h-5" />
                  </div>
                </div>

                <!-- Данные -->
                <div class="flex-1 min-w-0">
                  <div class="flex flex-wrap items-center gap-2 mb-1.5">
                    <span class="text-xs font-black text-slate-700">{{ claim['Артикул продавца'] }}</span>
                    <span v-if="claim.category && claim.category !== 'Прочее'"
                      class="text-[10px] font-bold px-2 py-0.5 bg-violet-100 text-violet-700 rounded-full">{{ claim.category }}</span>
                    <span class="text-[10px] font-medium text-slate-400">{{ claim.claim_date_iso }}</span>
                    <span v-if="claim['Инвойс'] && claim['Инвойс'] !== 'Не указан'"
                      class="text-[10px] font-medium text-slate-400">🧾 {{ claim['Инвойс'] }}</span>
                    <span v-if="parsePhotos(claim.photos || '').length"
                      class="text-[10px] font-bold text-blue-500">📷 {{ parsePhotos(claim.photos || '').length }}</span>
                    <span v-if="parseVideos(claim.videos || claim.db_videos || '').length"
                      class="text-[10px] font-bold text-rose-500">🎥 {{ parseVideos(claim.videos || claim.db_videos || '').length }}</span>
                  </div>
                  <p class="text-sm text-slate-600 leading-snug line-clamp-2">
                    <span v-if="claim['Комментарий покупателя']">"{{ claim['Комментарий покупателя'] }}"</span>
                    <span v-else class="text-slate-300 italic">Без комментария</span>
                  </p>
                </div>

                <ArrowRight class="w-4 h-4 text-slate-300 group-hover:text-violet-500 flex-shrink-0 mt-1 transition-colors" />
              </div>
            </div>

            <!-- Показать ещё -->
            <div v-if="hasMoreSmartResults" class="px-5 py-4 text-center">
              <button @click="smartPage++"
                class="px-6 py-2.5 text-sm font-bold text-violet-600 bg-violet-50 hover:bg-violet-100 rounded-xl transition-colors">
                Показать ещё (осталось {{ smartResults.length - smartResultsPaged.length }})
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
        <div class="flex items-center gap-2 mb-4"><BarChart2 class="w-5 h-5 text-indigo-500"/><h2 class="text-base font-black text-slate-800 uppercase tracking-tight">Динамика по месяцам</h2></div>
        
        <div class="w-full relative min-h-[250px]">
          <div v-if="selectedSku === 'Все'" class="absolute inset-0 flex items-center justify-center border-2 border-dashed border-slate-100 rounded-2xl text-slate-400 text-sm font-bold uppercase tracking-widest bg-white z-10">
            Выберите конкретный артикул в панели фильтров, чтобы раскрыть интерактивный тренд
          </div>
          
          <div ref="plotlyChart" class="w-full h-[400px]"></div>
        </div>
      </div>
    </div>

    <div v-if="modalData.isOpen" class="fixed inset-0 z-[200] flex items-end sm:items-center justify-center sm:p-4 bg-slate-900/60 backdrop-blur-md" @click.self="modalData.isOpen = false">
      <div class="bg-white rounded-t-[2rem] sm:rounded-[2rem] shadow-2xl w-full sm:max-w-5xl max-h-[92vh] flex flex-col overflow-hidden animate-in zoom-in duration-200">
        <div class="p-4 md:p-6 border-b border-slate-100 flex justify-between items-start gap-3 bg-slate-50/50">
          <h3 class="text-base md:text-lg font-black text-slate-800 leading-tight">{{ modalData.title }}</h3>
          <div class="flex items-center gap-2 flex-shrink-0">
            <button @click="downloadAllFromModal" class="hidden sm:flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-xl text-xs font-bold hover:bg-blue-700 transition-colors shadow-lg shadow-blue-100"><Download class="w-4 h-4" /> Скачать все</button>
            <button @click="modalData.isOpen = false" class="p-2 bg-white border rounded-xl text-slate-400 hover:text-red-500 transition-colors"><X class="w-5 h-5"/></button>
          </div>
        </div>

        <div class="p-4 md:p-6 overflow-y-auto custom-scroll space-y-4 bg-white flex-1">
          <div v-for="claim in modalData.claims" :key="claim.SRID || claim.srid" class="p-4 md:p-5 border border-slate-100 rounded-2xl bg-slate-50/30 hover:border-blue-100 transition-colors">
            <div class="flex flex-col sm:flex-row gap-4">
              <div class="flex-1 min-w-0">
                <div class="flex flex-wrap items-center gap-3 mb-3">
                  <div class="text-[10px] font-black text-slate-400 uppercase">Инвойс: <span class="text-slate-800 ml-1">{{ claim['Инвойс'] || '---' }}</span></div>
                  <div v-if="claim['Артикул продавца']" class="text-[10px] font-black px-2 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-100">
                    SKU: {{ claim['Артикул продавца'] }}
                  </div>
                  <div class="text-[10px] font-black text-slate-400 uppercase">Поставка: <span class="text-blue-600 ml-1">{{ claim['Номер поставки_ОРИГИНАЛ'] || claim['Номер поставки'] || '---' }}</span></div>
                  <!-- Причина возврата (ЯМ = классификация Маркета, Ozon = строка причины) -->
                  <div v-if="claim['Причина ЯМ']" class="text-[10px] font-black px-2 py-1 rounded-full bg-rose-50 text-rose-600 uppercase tracking-tight">
                    {{ claim['Причина ЯМ'] }}<span v-if="claim['Субпричина ЯМ']" class="font-bold text-rose-400"> · {{ claim['Субпричина ЯМ'] }}</span>
                  </div>
                  <div v-if="claim.platform === 'ozon' && claim['Название товара']" class="text-[10px] font-semibold px-2 py-1 rounded-full bg-blue-50 text-blue-500 max-w-[200px] truncate" :title="claim['Название товара']">
                    {{ claim['Название товара'] }}
                  </div>
                </div>
                <p class="text-sm text-slate-700 leading-relaxed font-medium bg-white p-4 rounded-xl border border-slate-100 shadow-sm italic">"{{ claim['Комментарий покупателя'] || (claim.platform === 'ozon' ? 'FBO — комментарий покупателя недоступен' : 'Без комментария') }}"</p>
                <!-- Похожий отзыв по этому SKU (справочно, жёсткой связи с возвратом нет) -->
                <div v-if="claim['Похожий отзыв']" class="mt-2 text-xs bg-amber-50/60 border border-amber-100 rounded-xl p-3">
                  <div class="text-[10px] font-black text-amber-600 uppercase tracking-tight mb-1">💬 Похожий отзыв по этому SKU<span v-if="claim['Похожий отзыв оценка']"> · {{ claim['Похожий отзыв оценка'] }}★</span></div>
                  <p class="text-slate-600 whitespace-pre-line leading-snug">{{ claim['Похожий отзыв'] }}</p>
                </div>
              </div>

              <div class="grid grid-cols-4 gap-2 content-start sm:w-[280px] md:w-[380px]">
                <div v-for="(img, idx) in parsePhotos(claim.photos)" :key="idx" class="relative group aspect-square rounded-lg overflow-hidden border border-white shadow-sm cursor-zoom-in" @click="openLightbox(parsePhotos(claim.photos), idx)">
                  <img :src="img" class="w-full h-full object-cover group-hover:scale-110 transition-transform duration-300" />
                  <button @click.stop="downloadImg(img, `claim_${claim.SRID || claim.srid}_${idx+1}.jpg`)" class="absolute bottom-1 right-1 p-1 bg-black/50 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"><Download class="w-3 h-3" /></button>
                </div>
                <div v-if="claim.photos === undefined || claim.photos === null" class="col-span-4 py-4 text-center text-xs text-slate-400 animate-pulse">Загрузка фото...</div>
                <div v-else-if="claim.platform === 'ozon' && !parsePhotos(claim.photos).length" class="col-span-4 py-4 text-center text-xs text-slate-400">Фото недоступны (FBO)</div>
                <a v-for="(vid, idx) in parseVideos(claim.videos || claim.db_videos)" :key="`v${idx}`" :href="vid" target="_blank" rel="noopener"
                  class="relative group aspect-square rounded-lg overflow-hidden border border-white shadow-sm bg-slate-800 flex items-center justify-center col-span-2">
                  <span class="text-white text-[10px] font-bold flex flex-col items-center gap-1">
                    <span class="text-2xl">🎥</span>Видео {{ idx + 1 }}
                  </span>
                </a>
              </div>
            </div>
          </div>
          <div v-if="modalData.claims.length === 0" class="py-12 text-center text-slate-400 text-sm font-semibold">Нет данных по этому инвойсу</div>
        </div>
      </div>
    </div>

    <div v-if="lightbox.isOpen" class="fixed inset-0 z-[300] bg-black/95 flex flex-col animate-in fade-in duration-300">
      <div class="flex justify-between items-center p-6 text-white">
        <span class="text-xs font-black uppercase tracking-widest text-slate-400">{{ lightbox.index + 1 }} / {{ lightbox.photos.length }}</span>
        <div class="flex gap-4">
          <button @click="downloadImg(lightbox.photos[lightbox.index], 'gallery.jpg')" class="p-3 bg-white/10 hover:bg-white/20 rounded-full transition-colors"><Download class="w-6 h-6"/></button>
          <button @click="lightbox.isOpen = false" class="p-3 bg-red-500/20 hover:bg-red-500 text-white rounded-full transition-colors"><X class="w-6 h-6"/></button>
        </div>
      </div>
      <div class="flex-1 flex items-center justify-between px-6 relative">
        <button @click="prevPhoto" class="p-4 bg-white/5 hover:bg-white/10 text-white rounded-full"><ChevronLeft class="w-10 h-10"/></button>
        <div class="relative max-w-4xl max-h-[70vh] flex items-center justify-center">
            <img :src="lightbox.photos[lightbox.index]" class="max-w-full max-h-full object-contain shadow-2xl rounded-lg" />
        </div>
        <button @click="nextPhoto" class="p-4 bg-white/5 hover:bg-white/10 text-white rounded-full"><ChevronRight class="w-10 h-10"/></button>
      </div>
      <div class="h-24 flex items-center justify-center gap-2 overflow-x-auto p-4">
        <div v-for="(img, idx) in lightbox.photos" :key="idx" @click="lightbox.index = idx" :class="['w-14 h-14 rounded-lg overflow-hidden cursor-pointer border-2 transition-all', lightbox.index === idx ? 'border-blue-500 scale-110 shadow-lg' : 'border-transparent opacity-40 hover:opacity-100']">
          <img :src="img" class="w-full h-full object-cover" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 10px; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }
</style>