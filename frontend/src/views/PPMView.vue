<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { AlertTriangle, TrendingUp, TableProperties, Calendar, Search, Download, ChevronLeft, ChevronRight, ChevronDown, X, FileText, CheckCircle2, History, Ban } from 'lucide-vue-next'
import Plotly from 'plotly.js-dist-min'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const rawDataset = ref([])
const claimsDetail = ref([])
const ordersByFactory = ref([])
const loading = ref(true)
const trendChart = ref(null)

const categoryPhotosState = ref({})
const excludedPhotos = ref(new Set())
const exhaustedPhotos = ref(new Set())
const isGenerating = ref(false)

const currentUserRole = computed(() => localStorage.getItem('role') || '')
const expandedLogId = ref(null)
const enabledCategories = ref({})  // { catKey: true/false } — тумблеры групп дефектов
const assortmentNames = ref({})    // { supplier_article: name_ru }
const skuActDates = ref([])        // акты по SKU для маркеров на основном графике

const showLogsModal = ref(false)
const claimLogs = ref([])
const isLogsLoading = ref(false)
const activeLogTab = ref('acts')
const manualClaims = ref([])
const isManualLoading = ref(false)
const newManualClaim = ref({ send_date: '', send_text: '', invoice_ref: '', factory_name: '', container_num: '', manual_status: '', who_sent: '' })
const showAddManual = ref(false)

// Отчёт PPM
const showReportModal = ref(false)
const reportForm = ref({ period_type: 'month', start_date: '', end_date: '', factories: [], include_containers: false, include_costs: false })
const isReportGenerating = ref(false)
const reportFactories = ref([])
const reportFactorySearch = ref('')
const filteredReportFactories = computed(() => {
  const q = reportFactorySearch.value.toLowerCase()
  return q ? reportFactories.value.filter(f => f.toLowerCase().includes(q)) : reportFactories.value
})

// Уведомления по инвойсам с исправлениями
const correctionNotifications = ref([])
const showCorrectionBadge = computed(() => correctionNotifications.value.length > 0)

const MANUAL_STATUS_OPTIONS = ['На проверке', 'Отправлено', 'Прочитано', 'Отвечено', 'Закрыто']

// Метаданные для автодополнения
const claimsMetadata = ref({ factories: [], invoices: [], containers: [] })

// Поиск в выпадающих полях
const manualSearch = ref({ factory: '', invoice: '', container: '' })
const manualDropdown = ref({ factory: false, invoice: false, container: false })

const filteredMeta = (field, query) => {
  const list = claimsMetadata.value[field === 'invoice' ? 'invoices' : field === 'container' ? 'containers' : 'factories'] || []
  const q = query.toLowerCase()
  return q ? list.filter(v => v.toLowerCase().includes(q)).slice(0, 40) : list.slice(0, 40)
}

const setManualField = (fieldName, value) => {
  newManualClaim.value[fieldName] = value
  const key = fieldName === 'factory_name' ? 'factory' : fieldName === 'invoice_ref' ? 'invoice' : 'container'
  manualSearch.value[key] = value
  manualDropdown.value[key] = false
}

// Поиск в ячейке таблицы
const cellSearch = ref({})
const cellDropdown = ref({})
const openCellDropdown = (id, field) => {
  const key = `${id}_${field}`
  cellDropdown.value = { [key]: true }
}
const setCellField = (claim, fieldName, value, id, field) => {
  claim[fieldName] = value
  cellDropdown.value = {}
  saveManualReply(claim)
}
const closeCellDropdowns = () => { cellDropdown.value = {} }

const manualStats = computed(() => {
  const total = manualClaims.value.length
  const replied = manualClaims.value.filter(c => c.reply_text || c.reply_date).length
  const sent = manualClaims.value.filter(c => c.send_date && !(c.reply_text || c.reply_date)).length
  return { total, sent, replied }
})

// Журнал актов: мультиселекты
const openParamsDropdownId = ref(null)
const openDeviationDropdownId = ref(null)
const CONTROLLED_PARAMS_OPTIONS = ['Внешний вид','Размеры / геометрия','Комплектация','Материал','Прочность','Маркировка','Функциональность','Упаковка']
const DEVIATION_OPTIONS = ['Не хватает комплектующих','Повреждения деталей','Качество материалов','Хлипкость конструкции','Несоответствие размеров','Прочие производственные дефекты']

// Ресайз колонок с сохранением в localStorage
const COL_WIDTHS_KEY = 'ppm_acts_col_widths_v2'
const colWidths = ref(JSON.parse(localStorage.getItem(COL_WIDTHS_KEY) || '{}'))
let _resizeState = null

const startResize = (e, colIdx) => {
  e.preventDefault()
  const th = e.target.closest('th')
  _resizeState = { colIdx, startX: e.clientX, startWidth: th.offsetWidth }
  const onMove = (ev) => {
    if (!_resizeState) return
    colWidths.value = { ...colWidths.value, [_resizeState.colIdx]: Math.max(40, _resizeState.startWidth + ev.clientX - _resizeState.startX) }
  }
  const onUp = () => {
    if (_resizeState) localStorage.setItem(COL_WIDTHS_KEY, JSON.stringify(colWidths.value))
    _resizeState = null
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
const getColStyle = (idx, defaultMin) => {
  const w = colWidths.value[idx]
  return w ? `width:${w}px;min-width:${w}px;max-width:${w}px` : `min-width:${defaultMin}px`
}

// Состояние строки акта
const isRowLocked = (log) => log.send_status === 'Отправлен'
const isRowFullyLocked = (log) => isRowLocked(log) && !!(log.estimated_improvement_date)
const isRowPartialDone = (log) => isRowLocked(log) && !!(log.factory_reply && log.factory_reply.trim())
const isRowDone = (log) => isRowPartialDone(log) && !!(log.correction_invoice && log.correction_invoice.trim()) && !!(log.estimated_improvement_date)
const rowBgClass = (log) => {
  if (isRowDone(log)) return 'bg-amber-100'
  if (isRowPartialDone(log)) return 'bg-yellow-100'
  if (isRowLocked(log)) return 'bg-green-50'
  return ''
}

// Мультиселект: Контролируемые параметры
const isParamSelected = (log, opt) => (log.controlled_params || '').split(';').map(s => s.trim()).filter(Boolean).includes(opt)
const toggleParam = async (log, opt) => {
  const cur = (log.controlled_params || '').split(';').map(s => s.trim()).filter(Boolean)
  const idx = cur.indexOf(opt)
  if (idx >= 0) cur.splice(idx, 1); else cur.push(opt)
  log.controlled_params = cur.join('; ')
  await saveLogField(log, 'controlled_params')
}

// Мультиселект: Отклонение
const isDeviationSelected = (log, opt) => (log.deviation || '').split(';').map(s => s.trim()).filter(Boolean).includes(opt)
const toggleDeviation = async (log, opt) => {
  const cur = (log.deviation || '').split(';').map(s => s.trim()).filter(Boolean)
  const idx = cur.indexOf(opt)
  if (idx >= 0) cur.splice(idx, 1); else cur.push(opt)
  log.deviation = cur.join('; ')
  await saveLogField(log, 'deviation')
}

// Разблокировка акта (только для admin)
const unlockLog = async (id) => {
  if (!confirm('Разблокировать акт? Статус отправки будет сброшен на "Не отправлен".')) return
  try {
    await apiFetch(`/api/v1/analytics/claim-logs/${id}/unlock`, { method: 'PUT' })
    fetchLogs()
  } catch (e) { alert(e.message || 'Ошибка разблокировки') }
}

// Смена статуса отправки: валидация + авто-дата
const handleSendStatusChange = async (log) => {
  if (log.send_status === 'Отправлен') {
    const missing = []
    if (!log.ra_date) missing.push('Дата РА')
    if (!log.invoice_ref) missing.push('№ инвойса')
    if (!log.who_sent) missing.push('Кто отправил')
    if (missing.length > 0) {
      alert(`Невозможно изменить статус. Заполните обязательные поля: ${missing.join(', ')}`)
      log.send_status = 'Не отправлен'
      return
    }
    if (!log.send_date) log.send_date = new Date().toISOString().split('T')[0]
  }
  await saveLogField(log, 'send_status', 'send_date')
}

const selectedGroupFilter = ref(null)
const selectedFactory = ref('Уточняется')

const togglePhotoSelection = (url) => {
  const newSet = new Set(excludedPhotos.value)
  if (newSet.has(url)) newSet.delete(url)
  else newSet.add(url)
  excludedPhotos.value = newSet
}

const todayObj = new Date()
const startOfMonthStr = `${todayObj.getFullYear()}-${String(todayObj.getMonth() + 1).padStart(2, '0')}-01`
const endOfTodayStr = `${todayObj.getFullYear()}-${String(todayObj.getMonth() + 1).padStart(2, '0')}-${String(todayObj.getDate()).padStart(2, '0')}`

const startDate = ref(startOfMonthStr)
const endDate = ref(endOfTodayStr)
const showCalendarPopover = ref(false)
const calendarYear = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

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

const selectedSku = ref('Все')
const skuSearch = ref('')
const showSkuDropdown = ref(false)
const clickedSku = ref(null)

const claimForm = ref({
  factory: 'Уточняется', invoices: '', period: '',
  desc_ru: '', desc_cn: '', cause_ru: 'Нарушение при производстве', cause_cn: '生产过程异常',
  invoicesData: [] 
})

const lightbox = ref({ isOpen: false, photos: [], index: 0 })
const criticalPopup = ref({ isOpen: false, group: null })
const photoCommentMap = ref({})

const openCriticalPopup = (g, event) => {
  event.stopPropagation()
  criticalPopup.value = { isOpen: true, group: g }
}

const CLAIM_CATEGORIES_LOGIC = {
  "Shortage": { ids: [1, 2], ru: "Не хватает комплектующих изделий", cn: "缺少配件", cause_ru: "Отклонение в процессе сборки (комплектации)", cause_cn: "装配/配套过程偏差" },
  "Damage": { ids: [4, 5], ru: "Повреждения деталей", cn: "零部件损坏 / 部件有损坏", cause_ru: "Нарушение при производственном процессе", cause_cn: "生产过程异常" },
  "Flimsy": { ids: [7, 9], ru: "Качество материалов и хлипкость", cn: "材料质量不良 / 不牢固", cause_ru: "Использование некачественного сырья", cause_cn: "原材料质量缺陷" }
}

const loadPpmData = async () => {
  loading.value = true
  const p = platformStore.platform
  try {
    const res1 = await apiFetch(`/api/v1/analytics/ppm-dataset?platform=${p}`)
    const json1 = await res1.json()
    rawDataset.value = json1.data || []

    const res2 = await apiFetch(`/api/v1/analytics/ppm-claims?platform=${p}`)
    const json2 = await res2.json()

    claimsDetail.value = (json2.data || []).map(c => {
      c['supplier_article'] = c['supplier_article'] !== undefined && c['supplier_article'] !== null ? String(c['supplier_article']).trim() : 'Без артикула'
      c['invoice_num'] = c['invoice_num'] !== undefined && c['invoice_num'] !== null ? String(c['invoice_num']).trim() : 'Не указан'
      c.photos = c.photos || c.db_photos || ""
      return c
    })

    const res3 = await apiFetch(`/api/v1/analytics/orders-by-factory?platform=${p}`)
    ordersByFactory.value = (await res3.json()).data || []

    // Загружаем имена товаров для акта
    const res4 = await apiFetch('/api/v1/analytics/assortment-names')
    if (res4.ok) { assortmentNames.value = (await res4.json()).data || {} }
  } catch (e) {
    console.error("Ошибка загрузки данных PPM:", e)
  } finally {
    loading.value = false
    await nextTick()
    buildChart()
  }
}

const fetchLogs = async () => {
  isLogsLoading.value = true
  isManualLoading.value = true
  try {
    const [resActs, resManual] = await Promise.all([
      apiFetch('/api/v1/analytics/claim-logs'),
      apiFetch('/api/v1/analytics/manual-claims')
    ])
    if (resActs.ok) {
      const logs = (await resActs.json()).data || []
      // Авто-расчёт повторяемости: Повторный если есть более ранний акт по тому же SKU
      const skuMinId = {}
      logs.forEach(l => {
        if (l.status !== 'Аннулирован') {
          if (!(l.sku in skuMinId) || l.id < skuMinId[l.sku]) skuMinId[l.sku] = l.id
        }
      })
      claimLogs.value = logs.map(log => {
        if (!log.repeatability && log.status !== 'Аннулирован') {
          log.repeatability = (skuMinId[log.sku] !== undefined && skuMinId[log.sku] < log.id) ? 'Повторный' : 'Первичный'
          saveLogField(log, 'repeatability')
        }
        return log
      })
    }
    if (resManual.ok) { manualClaims.value = (await resManual.json()).data || [] }
  } catch (e) { console.error(e) }
  finally { isLogsLoading.value = false; isManualLoading.value = false }
}

const submitManualClaim = async () => {
  if (!newManualClaim.value.send_date || !newManualClaim.value.send_text) return alert("Заполните дату и текст")
  try {
    await apiFetch('/api/v1/analytics/manual-claims', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newManualClaim.value)
    })
    showAddManual.value = false
    newManualClaim.value = { send_date: '', send_text: '', invoice_ref: '', factory_name: '', container_num: '', manual_status: '', who_sent: '' }
    fetchLogs()
  } catch (e) { console.error(e) }
}

const saveManualReply = async (claim) => {
  try {
    await apiFetch(`/api/v1/analytics/manual-claims/${claim.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reply_date: claim.reply_date || '', reply_text: claim.reply_text || '',
        invoice_ref: claim.invoice_ref || null, factory_name: claim.factory_name || null,
        container_num: claim.container_num || null, manual_status: claim.manual_status || null,
        who_sent: claim.who_sent || null
      })
    })
  } catch (e) { console.error(e) }
}

const q = (v) => `"${String(v||'').replace(/"/g,'""')}"`

const exportToExcel = () => {
  let csv = "﻿"
  if (activeLogTab.value === 'acts') {
    csv += ['№ РА','Отчетный месяц','Инициатор','Артикул','Наименование товара','Кол-во',
      '№ инвойса / контейнера','Название завода','Дата РА','№ партии',
      'Дата производства','Дата продажи','Стадия освоения','Объект',
      'Контролируемые параметры','Отклонение','Общее описание отклонения',
      'Повторяемость дефекта','Статус претензии','Причина отклонения',
      'Группа АВС','Завод/не завод','Кто отправил','Дата отправки РА на завод',
      'Название чата','Статус отправки','Дата ответа от завода','Что ответил завод',
      'Комментарии','Внутренний статус'].map(q).join(';') + '\n'
    claimLogs.value.forEach(r => {
      csv += [
        q(r.act_number), q(r.report_month), q(r.initiator), q(r.sku), q(r.product_name),
        r.qty||r.defects_count||'', q(r.invoice_ref), q(r.supplier), r.ra_date||r.created_at||'',
        q(r.batch_num), r.production_date||'', r.sale_date||'', q(r.stage), q(r.object_type),
        q(r.controlled_params), q(r.deviation), q(r.deviation_desc), q(r.repeatability),
        q(r.claim_status), q(r.deviation_cause), q(r.abc_group), q(r.factory_type),
        q(r.who_sent), r.send_date||'', q(r.chat_name), q(r.send_status),
        r.factory_reply_date||'', q(r.factory_reply), q(r.comments), q(r.status)
      ].join(';') + '\n'
    })
  } else {
    csv += [q('Номер обращения'),q('Дата отправки'),q('Текст отправления'),q('Дата ответа'),q('Текст ответа')].join(';') + '\n'
    manualClaims.value.forEach(r => {
      csv += [q(r.ticket_number), r.send_date||'', q(r.send_text), r.reply_date||'', q(r.reply_text)].join(';') + '\n'
    })
  }
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const link = document.createElement("a")
  link.href = URL.createObjectURL(blob)
  link.download = `Реестр_${activeLogTab.value === 'acts' ? 'РА' : 'Обращения'}_${new Date().toLocaleDateString('ru')}.csv`
  link.click()
}

const fetchClaimsMetadata = async () => {
  try {
    const res = await apiFetch('/api/v1/analytics/claims-metadata')
    if (res.ok) claimsMetadata.value = (await res.json()).data || { factories: [], invoices: [], containers: [] }
  } catch {}
}

const openLogsModal = () => {
  showLogsModal.value = true
  fetchLogs()
  fetchClaimsMetadata()
}

const openAddManual = () => {
  showAddManual.value = !showAddManual.value
  if (showAddManual.value) {
    newManualClaim.value.who_sent = localStorage.getItem('username') || ''
    manualSearch.value = { factory: newManualClaim.value.factory_name || '', invoice: newManualClaim.value.invoice_ref || '', container: newManualClaim.value.container_num || '' }
  }
}

const cancelLog = async (id) => {
  if (!confirm("Вы уверены, что хотите аннулировать этот акт?")) return
  try {
    await apiFetch(`/api/v1/analytics/claim-logs/${id}/cancel`, { method: 'PUT' })
    fetchLogs()
  } catch (e) { alert(e.message || 'Ошибка аннулирования') }
}

const deleteLog = async (id) => {
  if (!confirm('Удалить акт полностью из системы? Это действие необратимо.')) return
  try {
    await apiFetch(`/api/v1/analytics/claim-logs/${id}`, { method: 'DELETE' })
    fetchLogs()
  } catch (e) { alert(e.message || 'Ошибка удаления') }
}

const saveLogField = async (log, ...fields) => {
  try {
    const body = {}
    fields.forEach(f => { body[f] = log[f] || null })
    await apiFetch(`/api/v1/analytics/claim-logs/${log.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    })
  } catch (e) { console.error(e) }
}

const saveFactoryReply = async (log) => {
  try {
    await apiFetch(`/api/v1/analytics/claim-logs/${log.id}/reply`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reply: log.factory_reply || '' })
    })
  } catch (e) { console.error(e) }
}

const redownloadLog = async (log) => {
  if (!log.pdf_payload) return alert("Данные акта не сохранились.")
  try {
    const payload = typeof log.pdf_payload === 'string' ? JSON.parse(log.pdf_payload) : log.pdf_payload
    payload.is_redownload = true
    payload.existing_act_number = log.act_number
    const response = await apiFetch('/api/v1/analytics/export-claim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    })
    if (!response.ok) throw new Error()
    const blob = await response.blob()
    
    // 1. Форматируем номер акта (добавляем нули до 3 знаков, если это число)
    let actNumber = log.act_number || 'б-н';
    if (/^\d+$/.test(actNumber)) {
      actNumber = actNumber.padStart(3, '0');
    }

    // 2. Получаем дату. Для архивного акта логичнее брать его родную дату (log.ra_date), 
    // которая хранится в формате YYYY-MM-DD, и менять дефисы на точки.
    let dateStr = '';
    if (log.ra_date) {
      // Превращаем "2026-05-12" в "2026.05.12"
      dateStr = log.ra_date.substring(0, 10).replace(/-/g, '.');
    } else {
      // Фолбэк на текущую дату, если в журнале почему-то нет даты РА
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      dateStr = `${year}.${month}.${day}`;
    }

    // 3. Скачиваем файл по новому единому шаблону
    const link = document.createElement('a'); 
    link.href = URL.createObjectURL(blob); 
    
    // Результат: "РА № 092 - 2-СБ-Grey 2026.05.12.pdf"
    link.download = `РА № ${actNumber} - ${log.sku} ${dateStr}.pdf`; 
    
    link.click()
  } catch (e) { alert("Не удалось скачать акт.") }
}

// Действующий завод по артикулу = тот, по которому больше всего РЕАЛЬНЫХ (не 'Уточняется') претензий
const activeFactoryBySku = computed(() => {
  const tally = {}
  claimsDetail.value.forEach(c => {
    const sku = String(c['supplier_article'] || '').trim()
    const f = c['factory_name']
    if (!sku || !f || f === 'Уточняется') return
    if (!tally[sku]) tally[sku] = {}
    tally[sku][f] = (tally[sku][f] || 0) + 1
  })
  const res = {}
  Object.keys(tally).forEach(sku => {
    let best = null, bn = 0
    Object.entries(tally[sku]).forEach(([f, n]) => { if (n > bn) { best = f; bn = n } })
    if (best) res[sku] = best
  })
  return res
})

// Эффективный завод претензии: реальный завод; если он не определён ('Уточняется') —
// подставляем действующий завод артикула, чтобы такие возвраты не висели отдельной строкой
const effFactory = (c) => {
  const f = c['factory_name'] || 'Уточняется'
  if (f !== 'Уточняется') return f
  const sku = String(c['supplier_article'] || '').trim()
  return activeFactoryBySku.value[sku] || 'Уточняется'
}

const baseTableData = computed(() => {
  if (!rawDataset.value.length) return []
  const startYMD = startDate.value
  const endYMD = endDate.value

  // ── 1. Набираем заказы за период (для ABC/XYZ и таблицы) ────────────────
  const skuOrders = {}
  const articleMonthOrders = {}  // для XYZ: помесячные объёмы по артикулу
  rawDataset.value.filter(d => {
    if (!d['month_dt']) return false
    const rowDate = d['month_dt'].substring(0, 10)
    return rowDate >= startYMD.substring(0, 7) + "-01" && rowDate <= endYMD
  }).forEach(d => {
    const sku = String(d['article'] || 'Без артикула').trim()
    // abc/xyz из бэкенда — начальные значения, будут заменены period-aware расчётом ниже
    if (!skuOrders[sku]) skuOrders[sku] = { orders: 0, abc: d['abc_group'] || 'C', xyz: d['xyz_class'] || 'Z' }
    skuOrders[sku].orders += Number(d['orders']) || 0
    // копим помесячные данные для XYZ
    if (!articleMonthOrders[sku]) articleMonthOrders[sku] = []
    articleMonthOrders[sku].push(Number(d['orders']) || 0)
  })

  // ── 2. ABC за выбранный период (cumulative-share по заказам) ─────────────
  // Пересчитываем для тех же дат что смотрит пользователь: выбрал апрель–июнь →
  // A/B/C определяются именно по объёму апрель–июнь, а не за весь год.
  const totalPeriodOrders = Object.values(skuOrders).reduce((s, v) => s + v.orders, 0)
  if (totalPeriodOrders > 0) {
    const sorted = Object.entries(skuOrders).sort(([,a],[,b]) => b.orders - a.orders)
    let cum = 0
    for (const [sku, data] of sorted) {
      const prevShare = cum / totalPeriodOrders
      skuOrders[sku].abc = prevShare < 0.80 ? 'A' : prevShare < 0.95 ? 'B' : 'C'
      cum += data.orders
    }
  }

  // ── 3. XYZ за выбранный период (CV по месяцам, минимум 3 точки) ──────────
  // Если период слишком короткий (<3 месяцев данных) — оставляем значение из бэкенда
  // (trailing 26 недель), чтобы XYZ не был пустым для коротких выборок.
  for (const [sku, months] of Object.entries(articleMonthOrders)) {
    if (months.length < 3) continue  // недостаточно точек — оставляем бэкендное значение
    const mean = months.reduce((a, b) => a + b) / months.length
    if (mean === 0) { skuOrders[sku].xyz = 'Z'; continue }
    const variance = months.map(v => (v - mean) ** 2).reduce((a, b) => a + b) / months.length
    const cv = Math.sqrt(variance) / mean
    skuOrders[sku].xyz = cv < 0.25 ? 'X' : cv < 0.50 ? 'Y' : 'Z'
  }

  // Заказы с разбивкой по заводам (за период); 'Уточняется' прицепляем к действующему заводу артикула
  const ordersByFac = {}    // sku -> { завод: заказы }
  const ordersUnclear = {}  // sku -> заказы без определённого завода
  ordersByFactory.value.filter(d => {
    if (!d['month_dt']) return false
    const rowDate = d['month_dt'].substring(0, 10)
    return rowDate >= startYMD.substring(0, 7) + "-01" && rowDate <= endYMD
  }).forEach(d => {
    const sku = String(d['article'] || '').trim()
    const fac = d['factory_name'] || 'Уточняется'
    const n = Number(d['orders']) || 0
    if (fac === 'Уточняется') { ordersUnclear[sku] = (ordersUnclear[sku] || 0) + n }
    else { if (!ordersByFac[sku]) ordersByFac[sku] = {}; ordersByFac[sku][fac] = (ordersByFac[sku][fac] || 0) + n }
  })
  // заказы для строки (артикул + завод): свой завод + безынвойсные, если это действующий завод
  const ordersForRow = (sku, factory) => {
    const hasSplit = ordersByFac[sku] || (sku in ordersUnclear)
    if (!hasSplit) return skuOrders[sku]?.orders || 0   // нет разбивки -> общие заказы
    const own = (ordersByFac[sku] && ordersByFac[sku][factory]) || 0
    const unclear = (activeFactoryBySku.value[sku] === factory) ? (ordersUnclear[sku] || 0) : 0
    return own + unclear
  }

  const map = {}
  const validVals = ['1','1.0','+','true','да', 't']
  
  claimsDetail.value.forEach(c => {
    const sku = String(c['supplier_article'] || '').trim()
    if (!sku || (selectedSku.value !== 'Все' && sku !== selectedSku.value)) return

    let isDefect = false
    for (let i=1; i<=13; i++) {
        const val = c[`cat_${i}`]
        if (val === true || validVals.includes(String(val || '').trim().toLowerCase())) { 
            isDefect = true; break; 
        }
    }
    if (!isDefect) return 

    let claimDateStr = ''
    if (c['created_dt']) {
        claimDateStr = String(c['created_dt']).substring(0, 10)
    } else if (c['claim_date']) {
        claimDateStr = String(c['claim_date']).substring(0, 10)
    }
    
    if (claimDateStr && claimDateStr >= startYMD && claimDateStr <= endYMD) {
      const factory = effFactory(c)
      const key = `${sku}___${factory}`
      
      if (!map[key]) {
        map[key] = {
          'Артикул': sku, 'Завод': factory,
          'ABC_Группа': skuOrders[sku]?.abc || 'C',
          'Класс XYZ': skuOrders[sku]?.xyz || '-',
          'Брак': 0, 'Заказы': ordersForRow(sku, factory)
        }
      }
      map[key]['Брак'] += 1
    }
  })
  
  Object.keys(skuOrders).forEach(sku => {
    const hasDefects = Object.keys(map).some(k => k.startsWith(`${sku}___`))
    if (!hasDefects && skuOrders[sku].orders > 0 && (selectedSku.value === 'Все' || selectedSku.value === sku)) {
      const knownFactoryClaim = claimsDetail.value.find(c => String(c['supplier_article'] || '').trim() === sku && c['factory_name'] && c['factory_name'] !== 'Уточняется')
      // Если претензий нет — берём завод из orders-by-factory (там уже работает fallback на wb_assortment)
      const assignedFactory = knownFactoryClaim
        ? knownFactoryClaim['factory_name']
        : (ordersByFac[sku] ? Object.entries(ordersByFac[sku]).sort((a,b) => b[1]-a[1])[0]?.[0] : null) || 'Уточняется'
      map[`${sku}___${assignedFactory}`] = {
        'Артикул': sku, 'Завод': assignedFactory,
        'ABC_Группа': skuOrders[sku].abc, 'Класс XYZ': skuOrders[sku].xyz,
        'Брак': 0, 'Заказы': skuOrders[sku].orders
      }
    }
  })

  // PPM и % считаем от ОБЩИХ заказов SKU (skuOrders, те же данные что и в графике),
  // а не от фабричных заказов из orders-by-factory. Это делает цифры сопоставимыми с графиком.
  // Колонка «Заказы» показывает фабричные заказы (traceable через invoice-chain) — для справки.
  return Object.values(map).map(item => {
    const sku = item['Артикул']
    const totalSkuOrders = skuOrders[sku]?.orders || 0
    const ppm = totalSkuOrders > 0 ? Math.floor((item['Брак'] / totalSkuOrders) * 1000000) : 0
    const pct = totalSkuOrders > 0 ? (item['Брак'] / totalSkuOrders) * 100 : 0
    return { ...item, ppm, pct }
  })
})

const getFactoryCount = (sku) => {
  const factories = new Set()
  processedTableData.value.forEach(r => { if (r['Артикул'] === sku) factories.add(r['Завод']) })
  return factories.size
}

const groupMetrics = computed(() => {
  const result = {
    A: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0, prevBad: 0, delta: 0, badSkus: [], entered: [], left: [] },
    B: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0, prevBad: 0, delta: 0, badSkus: [], entered: [], left: [] },
    C: { total: 0, bad: 0, ppm: 0, defects: 0, orders: 0, prevBad: 0, delta: 0, badSkus: [], entered: [], left: [] }
  }

  const skuAgg = {}
  baseTableData.value.forEach(row => {
    const sku = row['Артикул']
    if (!skuAgg[sku]) {
        skuAgg[sku] = { group: row['ABC_Группа'], orders: row['Заказы'], defects: 0, isBad: false, ppm: 0 }
        result[row['ABC_Группа']].total++
        result[row['ABC_Группа']].orders += row['Заказы']
    }
    skuAgg[sku].defects += row['Брак']
    if (row.ppm > 10000) skuAgg[sku].isBad = true
    if (row.ppm > skuAgg[sku].ppm) skuAgg[sku].ppm = row.ppm
  })

  const curBad = { A: new Set(), B: new Set(), C: new Set() }
  Object.entries(skuAgg).forEach(([sku, s]) => {
      result[s.group].defects += s.defects
      if (s.isBad) {
        result[s.group].bad++
        result[s.group].badSkus.push({ sku, ppm: s.ppm })
        curBad[s.group].add(String(sku).trim())
      }
  })

  // PPM группы = взвешенное среднее баров чарта:
  // для каждого месяца берём System если есть (синий бар), иначе External (серый).
  // Это точно соответствует тому что видит пользователь на графике.
  const startYM = (startDate.value || '').slice(0, 7)
  const endYM   = (endDate.value   || '').slice(0, 7)
  const gMonthSrc = {}   // g -> { month -> { System: {o,d}, External: {o,d} } }
  ;['A','B','C'].forEach(g => { gMonthSrc[g] = {} })
  rawDataset.value.forEach(d => {
    const m = (d['month_dt'] || '').slice(0, 7)
    if (!m || m < startYM || m > endYM) return
    const sku = String(d['article'] || '').trim()
    const g   = periodSkuAbc.value[sku] || 'C'
    const src = d['source'] || 'System'
    if (!gMonthSrc[g][m]) gMonthSrc[g][m] = {}
    if (!gMonthSrc[g][m][src]) gMonthSrc[g][m][src] = { o: 0, d: 0 }
    gMonthSrc[g][m][src].o += Number(d['orders'])  || 0
    gMonthSrc[g][m][src].d += Number(d['defects']) || 0
  })
  Object.keys(result).forEach(k => {
    let totO = 0, totD = 0
    Object.values(gMonthSrc[k]).forEach(srcs => {
      const src = (srcs['System']?.o || 0) > 0 ? 'System' : 'External'
      totO += srcs[src]?.o || 0
      totD += srcs[src]?.d || 0
    })
    result[k].ppm = totO > 0 ? Math.floor((totD / totO) * 1000000) : 0
    result[k].badSkus.sort((a, b) => b.ppm - a.ppm)
  })

  if (startDate.value && rawDataset.value.length > 0) {
    const d = new Date(startDate.value)
    d.setMonth(d.getMonth() - 1)
    const prevMonthStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`

    const prevMap = {}
    rawDataset.value.forEach(row => {
      if (!row['month_dt'] || !row['month_dt'].startsWith(prevMonthStr)) return
      const sku = row['article'] || 'Без артикула'
      if (!prevMap[sku]) { prevMap[sku] = { group: row['abc_group'] || 'C', defects: 0, orders: 0 } }
      prevMap[sku].defects += Number(row['defects']) || 0
      prevMap[sku].orders += Number(row['orders']) || 0
    })

    const prevBadSet = { A: new Set(), B: new Set(), C: new Set() }
    Object.entries(prevMap).forEach(([sku, skuRow]) => {
      const ppm = skuRow.orders > 0 ? Math.floor((skuRow.defects / skuRow.orders) * 1000000) : 0
      const g = skuRow.group
      if (ppm > 10000 && result[g]) { result[g].prevBad++; prevBadSet[g].add(String(sku).trim()) }
    })

    Object.keys(result).forEach(k => {
      result[k].delta = result[k].bad - result[k].prevBad
      // конкретные артикулы изменения
      result[k].entered = [...curBad[k]].filter(s => !prevBadSet[k].has(s))   // стали критическими
      result[k].left = [...prevBadSet[k]].filter(s => !curBad[k].has(s))       // перестали быть критическими
    })
  }
  return result
})

const processedTableData = computed(() => {
  let data = baseTableData.value
  if (selectedGroupFilter.value) { data = data.filter(r => r['ABC_Группа'] === selectedGroupFilter.value) }
  return data.sort((a, b) => a['ABC_Группа'].localeCompare(b['ABC_Группа']) || b.ppm - a.ppm)
})

const skuOptions = computed(() => ['Все', ...Array.from(new Set(rawDataset.value.map(d => d['article'])))].sort())
const filteredSkuList = computed(() => skuOptions.value.filter(s => String(s).toLowerCase().includes(skuSearch.value.toLowerCase())))

// Маппинг SKU → период-ABC (из baseTableData, тот же пересчёт что в карточках)
const periodSkuAbc = computed(() => {
  const map = {}
  baseTableData.value.forEach(row => { map[String(row['Артикул']).trim()] = row['ABC_Группа'] })
  return map
})

const buildChart = () => {
  if (!trendChart.value) return
  try {
    const targetSku = clickedSku.value || (selectedSku.value !== 'Все' ? selectedSku.value : null)
    const endYMD = endDate.value.substring(0, 7)
    
    const chartSource = rawDataset.value.filter(d => {
        const dMonth = d['month_dt'] ? String(d['month_dt']).substring(0, 7) : ''
        if (dMonth > endYMD) return false 

        if (targetSku) return String(d['article']).trim().toLowerCase() === String(targetSku).trim().toLowerCase()
        if (selectedGroupFilter.value) return (periodSkuAbc.value[String(d['article']).trim()] || 'C') === selectedGroupFilter.value
        return true
    })

    const monthSourceMap = {}
    chartSource.forEach(d => {
      if (d['month_str'] && d['month_str'].includes('Дек')) return
      const monthLabel = d['month_str'] || 'Неизвестно'
      const source = d['source']
      const key = `${monthLabel}_${source}`
      if (!monthSourceMap[key]) {
        monthSourceMap[key] = { month_dt: d['month_dt'] || '', label: monthLabel, source: source, defects: 0, orders: 0 }
      }
      monthSourceMap[key].defects += Number(d['defects']) || 0
      monthSourceMap[key].orders += Number(d['orders']) || 0 
    })

    const sortedKeysAll = Object.values(monthSourceMap).sort((a, b) => a.month_dt.localeCompare(b.month_dt))

    const totalDefectsByMonth = {}; const totalOrdersByMonth = {}
    sortedKeysAll.forEach(t => {
      if (!totalDefectsByMonth[t.label]) totalDefectsByMonth[t.label] = 0
      if (!totalOrdersByMonth[t.label]) totalOrdersByMonth[t.label] = 0
      totalDefectsByMonth[t.label] += t.defects
      totalOrdersByMonth[t.label] = Math.max(totalOrdersByMonth[t.label], t.orders)
    })

    // Показываем только месяцы где есть хотя бы один дефект (есть столбец PPM)
    const xLabels = [...new Set(sortedKeysAll.map(t => t.label))].filter(l => totalDefectsByMonth[l] > 0)
    const sortedKeys = sortedKeysAll.filter(t => xLabels.includes(t.label))
    
    const defectValues = xLabels.map(l => totalDefectsByMonth[l] || 0)
    const orderValues = xLabels.map(l => totalOrdersByMonth[l] || 0)

    // Расчёт PPM по месяцу (общий, по всем источникам) для дельты
    const ppmByMonth = {}
    xLabels.forEach(l => {
      const d = totalDefectsByMonth[l] || 0
      const o = totalOrdersByMonth[l] || 0
      ppmByMonth[l] = o > 0 ? Math.floor((d / o) * 1000000) : 0
    })

    const fmtDelta = (cur, prev) => {
      if (!prev || prev === 0) return ''
      const d = ((cur - prev) / prev * 100).toFixed(1)
      return parseFloat(d) > 0 ? `(+${d}%)` : `(${d}%)`
    }

    const ppmDeltaArr = xLabels.map((l, i) => i === 0 ? '' : fmtDelta(ppmByMonth[l], ppmByMonth[xLabels[i-1]]))
    const ordersDeltaArr = xLabels.map((l, i) => i === 0 ? '' : fmtDelta(orderValues[i], orderValues[i-1]))
    const defectsDeltaArr = xLabels.map((l, i) => i === 0 ? '' : fmtDelta(defectValues[i], defectValues[i-1]))

    const traces = []

    const extTimeline = sortedKeys.filter(t => t.source === 'External')
    if (extTimeline.length > 0) {
      const extY = extTimeline.map(t => t.orders > 0 ? Math.floor((t.defects / t.orders) * 1000000) : 0)
      const extDelta = extTimeline.map(t => ppmDeltaArr[xLabels.indexOf(t.label)] || '')
      traces.push({
        x: extTimeline.map(t => t.label), y: extY,
        customdata: extDelta,
        name: 'PPM (История)', type: 'bar', marker: { color: '#cbd5e1' },
        text: extY,
        textposition: 'inside', insidetextanchor: 'middle', textfont: { color: '#1e293b', weight: '900', size: 16 },
        hovertemplate: 'PPM (История): %{y} <b>%{customdata}</b><extra></extra>'
      })
    }

    const sysTimeline = sortedKeys.filter(t => t.source === 'System')
    if (sysTimeline.length > 0) {
      const sysY = sysTimeline.map(t => t.orders > 0 ? Math.floor((t.defects / t.orders) * 1000000) : 0)
      const sysDelta = sysTimeline.map(t => ppmDeltaArr[xLabels.indexOf(t.label)] || '')
      traces.push({
        x: sysTimeline.map(t => t.label), y: sysY,
        customdata: sysDelta,
        name: 'PPM (Система)', type: 'bar', marker: { color: '#1e3a8a' },
        text: sysY,
        textposition: 'inside', insidetextanchor: 'middle', textfont: { color: '#ffffff', weight: '900', size: 16 },
        hovertemplate: 'PPM (Система): %{y} <b>%{customdata}</b><extra></extra>'
      })
    }

    if (sortedKeys.length > 0) {
      traces.push({ x: xLabels, y: orderValues, customdata: ordersDeltaArr, name: 'Чистые Заказы', type: 'scatter', mode: 'lines', line: { color: '#10b981', width: 2, dash: 'dot' }, yaxis: 'y2', hovertemplate: 'Общие заказы: %{y} шт <b>%{customdata}</b><extra></extra>' })
      traces.push({ x: xLabels, y: defectValues, customdata: defectsDeltaArr, name: 'Кол-во брака', type: 'scatter', mode: 'lines+markers+text', text: defectValues.map(v => v > 0 ? `${v} шт` : ''), textposition: 'top center', textfont: { color: '#e11d48', size: 11, weight: 'bold' }, line: { color: '#e11d48', width: 3, dash: 'solid' }, marker: { size: 7 }, yaxis: 'y3', hovertemplate: 'Брак: %{y} шт <b>%{customdata}</b><extra></extra>' })
    }

    const allPpm = sortedKeys.map(t => t.orders > 0 ? Math.floor((t.defects / t.orders) * 1000000) : 0)
    const yLimit = Math.max(20000, Math.max(...allPpm, 0) * 1.15)
    const maxDefects = Math.max(...defectValues, 10)

    let chartTitle = 'Совокупная динамика качества'
    if (targetSku) chartTitle = `Динамика качества: ${targetSku}`
    else if (selectedGroupFilter.value) chartTitle = `Динамика качества: Группа ${selectedGroupFilter.value}`

    const shapes = [
      { type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 10000, y1: 10000, line: { color: '#e11d48', width: 2, dash: 'dash' } }
    ]
    const annotations = []

    // Маркеры отправленных актов — только на графике конкретного SKU
    if (targetSku && skuActDates.value.length > 0) {
      const monthToLabel = {}
      sortedKeys.forEach(t => { if (t.month_dt) monthToLabel[t.month_dt.substring(0,7)] = t.label })
      const sortedMonthEntries = Object.entries(monthToLabel).sort(([a], [b]) => a.localeCompare(b))
      const actX = [], actY = [], actText = []
      skuActDates.value.forEach(act => {
        const dateStr = act.send_date || act.ra_date || act.created_at
        if (!dateStr) return
        const ym = String(dateStr).substring(0,7)

        // Ищем месяц: точное совпадение → ближайший будущий → последний в графике (акт новее данных)
        let label = monthToLabel[ym]
        let placeAfter = false
        if (!label) {
          const nextEntry = sortedMonthEntries.find(([k]) => k >= ym)
          if (nextEntry) {
            label = nextEntry[1]
          } else if (sortedMonthEntries.length > 0) {
            label = sortedMonthEntries[sortedMonthEntries.length - 1][1]
            placeAfter = true  // акт новее всех данных — линия после последнего столбца
          }
        }
        if (!label) return
        const idx = xLabels.indexOf(label)
        if (idx < 0) return

        // xPos: после последнего столбца если акт новее данных; между предыдущим и текущим иначе
        // idx - 0.5 может уйти за левый край при idx=0 — ставим после первого столбца в таком случае
        const xPos = placeAfter ? idx + 0.5 : (idx > 0 ? idx - 0.5 : 0.5)

        shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: xPos, x1: xPos, y0: 0, y1: 1, line: { color: '#f59e0b', width: 2, dash: 'dot' } })
        annotations.push({ xref: 'x', yref: 'paper', x: xPos, y: 1.04, text: `📋 ${act.act_number}`, showarrow: false, font: { size: 9, color: '#92400e' }, bgcolor: '#fef3c7', bordercolor: '#f59e0b', borderwidth: 1, borderpad: 3 })
        actX.push(label); actY.push(yLimit * 0.5)
        const reply = (act.factory_reply || '').trim()
        actText.push(`<b>Акт №${act.act_number}</b><br>Отправлен: ${act.send_date || '—'}<br>${reply ? 'Ответ: ' + reply.substring(0, 100) + (reply.length > 100 ? '...' : '') : 'Ответ завода не добавлен'}`)
      })
      if (actX.length > 0) {
        traces.push({ x: actX, y: actY, mode: 'markers', type: 'scatter', marker: { color: 'rgba(0,0,0,0)', size: 20 }, name: 'Акты', showlegend: false, hovertemplate: '%{text}<extra></extra>', text: actText })
      }
    }

    const layout = {
      title: chartTitle, font: { family: 'Inter, sans-serif' }, barmode: 'stack', height: 420,
      margin: { t: skuActDates.value.length > 0 && targetSku ? 52 : 40, b: 40, l: 60, r: 40 },
      legend: { orientation: 'h', y: -0.2, x: 0.5, xanchor: 'center' },
      xaxis: { type: 'category', categoryorder: 'array', categoryarray: xLabels, showgrid: false },
      yaxis: { title: 'Индекс PPM', range: [0, yLimit], side: 'left', showgrid: true, gridcolor: '#f1f5f9' },
      yaxis2: { overlaying: 'y', side: 'right', showticklabels: false, showgrid: false, rangemode: 'tozero' },
      yaxis3: { overlaying: 'y', side: 'right', showticklabels: false, showgrid: false, range: [0, maxDefects * 1.3] },
      hovermode: 'x unified', paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      shapes, annotations
    }
    Plotly.newPlot(trendChart.value, traces, layout, { responsive: true, displayModeBar: false })
  } catch (err) { console.error(err) }
}

watch([startDate, endDate, selectedSku, clickedSku, selectedGroupFilter, skuActDates], () => { nextTick(() => buildChart()) })

// Загружаем акты по SKU для маркеров на основном графике
watch(clickedSku, async (newSku) => {
  if (!newSku) { skuActDates.value = []; return }
  try {
    const res = await apiFetch(`/api/v1/analytics/claim-logs/by-sku/${encodeURIComponent(newSku)}`)
    if (res.ok) { skuActDates.value = (await res.json()).data || [] }
  } catch { skuActDates.value = [] }
})

const handleRowClick = (row) => {
  if (clickedSku.value === row['Артикул'] && selectedFactory.value === row['Завод']) {
    clickedSku.value = null
    selectedFactory.value = 'Уточняется'
  } else {
    clickedSku.value = row['Артикул']
    selectedFactory.value = row['Завод']
  }
}

const currentSkuClaims = computed(() => {
  if (!clickedSku.value) return []
  const targetSku = String(clickedSku.value).trim().toLowerCase()
  const targetFactory = String(selectedFactory.value).trim().toLowerCase()
  const startYMD = startDate.value
  const endYMD = endDate.value
  
  return claimsDetail.value.filter(c => {
    const sku = String(c['supplier_article'] || '').trim().toLowerCase()
    const factory = String(effFactory(c)).trim().toLowerCase()

    if (sku !== targetSku || factory !== targetFactory) return false

    let claimDateStr = ''
    if (c['created_dt']) {
        claimDateStr = String(c['created_dt']).substring(0, 10)
    } else if (c['claim_date']) {
        claimDateStr = String(c['claim_date']).substring(0, 10)
    }
    
    return claimDateStr && claimDateStr >= startYMD && claimDateStr <= endYMD
  })
})

const activeDefectCategories = computed(() => {
  try {
    const result = []
    if (!clickedSku.value) return []
    const validVals = ['1','1.0','+','true','да', 't']
    const assignedSrids = new Set() 

    Object.keys(CLAIM_CATEGORIES_LOGIC).forEach(groupKey => {
      const config = CLAIM_CATEGORIES_LOGIC[groupKey]
      let matchedClaims = []
      currentSkuClaims.value.forEach(c => {
        const srid = String(c.srid || c.SRID || c.id)
        if (assignedSrids.has(srid)) return 
        const hasMatch = config.ids.some(id => {
            const val = c[`cat_${id}`]
            return val === true || validVals.includes(String(val || '').trim().toLowerCase())
        })
        if (hasMatch) { matchedClaims.push(c); assignedSrids.add(srid) }
      })
      if (matchedClaims.length > 0) {
        const invCounts = {}
        matchedClaims.forEach(c => {
          const inv = String(c['invoice_num'] || '').trim()
          if (inv && !['nan', 'none', '', 'не указан', '0', '-'].includes(inv.toLowerCase())) { invCounts[inv] = (invCounts[inv] || 0) + 1 }
        })
        const groupedInvoices = Object.entries(invCounts).map(([name, count]) => ({ name, count }))
        const visiblePhotos = categoryPhotosState.value[groupKey]?.visible || []
        result.push({ key: groupKey, name: config.ru, name_cn: config.cn, count: matchedClaims.length, list: matchedClaims, photos: visiblePhotos, invoices: groupedInvoices })
      }
    })

    let otherClaims = []
    currentSkuClaims.value.forEach(c => {
      const srid = String(c.srid || c.SRID || c.id)
      if (assignedSrids.has(srid)) return 
      let hasAnyValidTag = false
      for (let i = 1; i <= 11; i++) { 
         const val = c[`cat_${i}`]
         if (val === true || validVals.includes(String(val || '').trim().toLowerCase())) { hasAnyValidTag = true; break } 
      }
      if (hasAnyValidTag) { otherClaims.push(c); assignedSrids.add(srid) }
    })
    if (otherClaims.length > 0) {
        const invCounts = {}
        otherClaims.forEach(c => {
          const inv = String(c['invoice_num'] || '').trim()
          if (inv && !['nan', 'none', '', 'не указан'].includes(inv.toLowerCase())) { invCounts[inv] = (invCounts[inv] || 0) + 1 }
        })
        const groupedInvoices = Object.entries(invCounts).map(([name, count]) => ({ name, count }))
        const visiblePhotos = categoryPhotosState.value['others']?.visible || []
        result.push({ key: "others", name: "Прочие производственные дефекты", name_cn: "其他生产缺陷", count: otherClaims.length, list: otherClaims, photos: visiblePhotos, invoices: groupedInvoices })
    }
    return result
  } catch (err) { return [] }
})

const refreshCategoryPhotos = (catKey) => {
  const state = categoryPhotosState.value[catKey]
  if (!state || state.all.length <= state.visible.length) return
  let available = state.all.filter(p => !state.visible.includes(p) && !exhaustedPhotos.value.has(p))
  if (available.length === 0) {
      state.all.forEach(p => exhaustedPhotos.value.delete(p))
      available = state.all.filter(p => !state.visible.includes(p))
  }
  let aIdx = 0
  const newExcluded = new Set(excludedPhotos.value)
  const updatedVisible = state.visible.map(img => {
    if (newExcluded.has(img) && aIdx < available.length) {
      const replacement = available[aIdx]; aIdx++; newExcluded.delete(img); exhaustedPhotos.value.add(replacement)
      return replacement
    }
    return img
  })
  categoryPhotosState.value[catKey].visible = updatedVisible
  excludedPhotos.value = newExcluded
  // подгружаем следующую партию заранее, чтобы повторная замена была мгновенной
  preloadImages(available)
}

// Предзагрузка фото в фон: браузер кэширует картинки заранее, чтобы замена
// невыбранных фото, прокрутка и формирование акта были мгновенными (без ожидания сети)
const preloadImages = (urls) => {
  (urls || []).forEach(u => { if (u) { const im = new Image(); im.decoding = 'async'; im.src = u } })
}

const parsePhotos = (str) => {
  if (!str || str === 'nan' || String(str).toLowerCase() === 'none') return []
  return String(str).split(' ').map(g => {
    let url = g.split('|').pop().trim()
    if (url.startsWith('//')) url = 'https:' + url
    return url
  }).filter(url => url.startsWith('http'))
}

// Следим и за SKU и за заводом: при смене завода (тот же SKU, другой завод) нужно
// перестроить categoryPhotosState, иначе фото остаются от предыдущего завода.
watch([clickedSku, selectedFactory], async ([newSku]) => {
  if (!newSku) return
  excludedPhotos.value.clear()
  
  claimForm.value.factory = selectedFactory.value
  claimForm.value.period = `${formatDateDisplay(startDate.value)} - ${formatDateDisplay(endDate.value)}`

  // Группируем строго по ИНВОЙСУ. Контейнер и дату берём по первому непустому совпадению в
  // пределах инвойса (данные о контейнере/дате заполнены не в каждой строке). Если контейнера
  // нет — дату тоже не показываем (они идут парой).
  const invMap = {}
  currentSkuClaims.value.forEach(c => {
      const inv = String(c['invoice_num'] || '').trim()
      const isInvValid = inv && !['nan', 'none', '', 'не указан'].includes(inv.toLowerCase())
      const cont = c['container_num'] && c['container_num'] !== 'None' ? String(c['container_num']).trim() : ''
      const sDate = c['shipment_date'] && c['shipment_date'] !== 'None' ? String(c['shipment_date']).substring(0,10) : ''
      if (!isInvValid && !cont) return
      const key = isInvValid ? inv.toLowerCase() : `__noinv__${cont}`
      if (!invMap[key]) invMap[key] = { invoice: isInvValid ? inv : '—', container: '', date: '', count: 0 }
      invMap[key].count += 1
      if (!invMap[key].container && cont) invMap[key].container = cont
      if (!invMap[key].date && sDate) invMap[key].date = sDate
  })
  claimForm.value.invoicesData = Object.values(invMap).map(r => {
      const container = r.container || '—'
      const date = container === '—' ? '—' : (r.date || '—')
      return { invoice: r.invoice || '—', container, date, count: r.count }
  })

  const issues_ru = []; const issues_cn = []; const combined_causes_ru = []; const combined_causes_cn = []
  const validVals = ['1','1.0','+','true','да', 't']
  Object.keys(CLAIM_CATEGORIES_LOGIC).forEach(key => {
    const config = CLAIM_CATEGORIES_LOGIC[key]
    let groupCount = currentSkuClaims.value.filter(c => {
        const val1 = c[`cat_${config.ids[0]}`]
        const val2 = c[`cat_${config.ids[1]}`]
        return val1 === true || val2 === true || 
               validVals.includes(String(val1 || '').trim().toLowerCase()) ||
               validVals.includes(String(val2 || '').trim().toLowerCase())
    }).length
    if (groupCount > 0) {
      issues_ru.push(`${config.ru} (${groupCount} шт.)`)
      issues_cn.push(`${config.cn} (${groupCount} 件)`)
      if (!combined_causes_ru.includes(config.cause_ru)) combined_causes_ru.push(config.cause_ru)
      if (!combined_causes_cn.includes(config.cause_cn)) combined_causes_cn.push(config.cause_cn)
    }
  })
  claimForm.value.desc_ru = issues_ru.join('\n') || 'Прочие дефекты'
  claimForm.value.desc_cn = issues_cn.join('\n') || '其他生产缺陷'
  claimForm.value.cause_ru = combined_causes_ru.join('\n') || 'Нарушение при производстве'
  claimForm.value.cause_cn = combined_causes_cn.join('\n') || '生产过程异常'

  await nextTick()
  const states = {}
  const assignedSrids = new Set()
  const usedPhotoUrls = new Set()  // глобальная дедупликация фото между блоками
  const newPhotoCommentMap = {}
  const categoriesList = [{ key: 'Shortage', ids: [1, 2] }, { key: 'Damage', ids: [4, 5] }, { key: 'Flimsy', ids: [7, 9] }, { key: 'others', ids: [] }]

  categoriesList.forEach(cat => {
    let matched = []
    currentSkuClaims.value.forEach(c => {
      const srid = String(c.srid || c.SRID || c.id)
      if (assignedSrids.has(srid)) return
      if (cat.key === 'others') {
        let hasAny = false
        for (let i = 1; i <= 11; i++) {
           if (c[`cat_${i}`] === true || validVals.includes(String(c[`cat_${i}`] || '').trim().toLowerCase())) { hasAny = true; break }
        }
        if (hasAny) { matched.push(c); assignedSrids.add(srid) }
      } else {
        if (cat.ids.some(id => c[`cat_${id}`] === true || validVals.includes(String(c[`cat_${id}`] || '').trim().toLowerCase()))) { matched.push(c); assignedSrids.add(srid) }
      }
    })
    const poolSet = new Set()
    matched.forEach(c => {
        const photosStr = c.photos || c.db_photos || ""
        const comment = String(c.user_comment || '').trim()
        if (photosStr) {
          parsePhotos(photosStr).forEach(url => {
            if (url && !usedPhotoUrls.has(url)) {
              poolSet.add(url)
              // Привязываем комментарий к фото (первый непустой комментарий у данного URL)
              if (comment && !newPhotoCommentMap[url]) newPhotoCommentMap[url] = comment
            }
          })
        }
    })
    const poolArr = Array.from(poolSet)
    poolArr.forEach(u => usedPhotoUrls.add(u))  // отмечаем как использованные
    states[cat.key] = { all: poolArr, visible: poolArr.slice(0, 12) }
  })
  categoryPhotosState.value = states
  photoCommentMap.value = newPhotoCommentMap
  // Инициализируем тумблеры групп (все включены по умолчанию)
  const newEnabled = {}
  categoriesList.forEach(cat => { newEnabled[cat.key] = true })
  enabledCategories.value = newEnabled
  // Прогреваем кэш браузера: все фото пулов грузятся в фоне -> замена/прокрутка/акт мгновенные
  Object.values(states).forEach(s => preloadImages(s.all))

  // Авто-фильтрация: убираем коробки и штрих-коды сразу (фоновый запрос, не блокирует UI)
  const poolUrls = [...new Set(Object.values(states).flatMap(s => s.all))]
  if (poolUrls.length > 0) {
    apiFetch('/api/v1/analytics/filter-photos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: poolUrls.slice(0, 50) })
    }).then(r => r.json()).then(data => {
      const newExcl = new Set(excludedPhotos.value)
      ;(data.results || []).forEach(item => { if (!item.keep) newExcl.add(item.url) })
      excludedPhotos.value = newExcl
    }).catch(() => {})
  }
})

const generatePDFClaim = async (isTest = false) => {
  if (isGenerating.value) return
  if (!isTest) {
    await fetchLogs()
    const duplicate = claimLogs.value.find(l =>
      l.sku === clickedSku.value &&
      l.period_str === claimForm.value.period &&
      String(l.supplier || '').trim() === String(claimForm.value.factory || '').trim() &&
      l.status === 'Активен')
    if (duplicate) {
        if (!confirm(`Внимание! По артикулу ${clickedSku.value} (завод «${claimForm.value.factory}») за период "${claimForm.value.period}" уже выпущен акт №${duplicate.act_number}. Вы уверены, что хотите создать еще один?`)) return
    }
  }
  isGenerating.value = true
  try {
    // ── ШАГИ 1: Фиксированные данные — НЕ зависят от тумблеров ─────────────
    // Кол-во брака и % берём из ВСЕх категорий (до любой фильтрации по тумблерам)
    // и из строки таблицы-спецификации. Тумблеры не должны их менять.
    const _allCats = activeDefectCategories.value   // ВСЕ категории, без фильтра
    const _specRow = baseTableData.value.find(r =>
      String(r['Артикул']).trim().toLowerCase() === String(clickedSku.value).trim().toLowerCase() &&
      String(r['Завод']).trim().toLowerCase() === String(selectedFactory.value).trim().toLowerCase()
    )
    const totalDefects = _specRow
      ? _specRow['Брак']
      : (_allCats.reduce((s, c) => s + c.count, 0) || currentSkuClaims.value.length)
    const currentPpmPct = _specRow ? _specRow['pct'].toFixed(2) : '0.00'
    // ────────────────────────────────────────────────────────────────────────

    // ── ШАГ 2: Тумблерная фильтрация — только для фото и описания ──────────
    const enabledCats = activeDefectCategories.value.filter(c => enabledCategories.value[c.key] !== false)

    // Фото только из включённых категорий
    const photoPayload = []
    enabledCats.forEach(catData => {
      if (catData?.photos?.length > 0) {
        const selectedUrls = catData.photos.filter(url => !excludedPhotos.value.has(url))
        if (selectedUrls.length > 0) photoPayload.push({ ru: catData.name, cn: catData.name_cn, urls: selectedUrls.slice(0, 12) })
      }
    })

    // Описание брака и ключевые причины — из включённых категорий
    const issues_ru = [], issues_cn = [], causes_ru = [], causes_cn = []
    enabledCats.forEach(cat => {
      const cfg = CLAIM_CATEGORIES_LOGIC[cat.key]
      issues_ru.push(cfg ? cfg.ru : cat.name)
      issues_cn.push(cfg ? cfg.cn : cat.name_cn)
      if (cfg) {
        if (!causes_ru.includes(cfg.cause_ru)) causes_ru.push(cfg.cause_ru)
        if (!causes_cn.includes(cfg.cause_cn)) causes_cn.push(cfg.cause_cn)
      }
    })

    // Имя товара из ассортиментной матрицы
    const productName = assortmentNames.value[clickedSku.value] || assortmentNames.value[String(clickedSku.value).trim()] || ''

    let chartImageBase64 = ""
    const invN = (claimForm.value.invoicesData || []).length
    const chartH = Math.max(150, Math.min(500, 500 - invN * 38))
    if (trendChart.value && trendChart.value.data) {
      try {
        const exportTraces = trendChart.value.data.filter(t => t.type === 'bar').map(t => ({ ...t, marker: { color: '#1e3a8a' }, name: 'PPM', text: t.y.map(val => val > 0 ? val : ''), textfont: { color: '#ffffff', size: 16, weight: '900' } }))
        // Для PDF-акта: убираем маркеры актов (shapes/annotations с желтыми линиями)
        const exportShapes = (trendChart.value.layout?.shapes || []).filter(s => s.line?.color !== '#f59e0b')
        const exportLayout = { ...trendChart.value.layout, width: 850, height: chartH, showlegend: false, paper_bgcolor: '#f8fafc', plot_bgcolor: '#f8fafc', barmode: 'stack', margin: { t: 10, b: 20, l: 40, r: 20 }, shapes: exportShapes, annotations: [] }
        chartImageBase64 = await Plotly.toImage({data: exportTraces, layout: exportLayout}, { format: 'png', scale: 2 })
        chartImageBase64 = chartImageBase64.replace(/^data:image\/png;base64,/, "")
      } catch (e) { console.error(e) }
    }

    const skuRow = _specRow
    const mainInvoice = claimForm.value.invoicesData.map(i => i.invoice).filter(i => i && i !== '—').join(', ')
    const periodStart = new Date(startDate.value)
    const _months = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
    const reportMonth = `${_months[periodStart.getMonth()]} ${periodStart.getFullYear()}`
    const whoSent = localStorage.getItem('username') || ''

    const payload = {
      supplier: claimForm.value.factory, period: claimForm.value.period,
      invoices_list: claimForm.value.invoicesData, sku: clickedSku.value,
      name: productName, name_cn: '产品',
      defects: totalDefects, ppm_pct: parseFloat(currentPpmPct),
      desc_ru: issues_ru.join('\n') || 'Прочие дефекты',
      desc_cn: issues_cn.join('\n') || '其他生产缺陷',
      cause_ru: causes_ru.join('\n') || claimForm.value.cause_ru,
      cause_cn: causes_cn.join('\n') || claimForm.value.cause_cn,
      photo_groups: photoPayload, chart_data: [{ image: chartImageBase64, height: chartH }],
      is_test: isTest,
      report_month: reportMonth,
      abc_group: skuRow?.['ABC_Группа'] || '',
      product_name_ru: productName,
      invoice_ref: mainInvoice,
      deviation: enabledCats.map(c => c.name).join('; '),
      claim_status: 'На проверке',
      initiator: 'Wildberries',
      factory_type: 'завод',
      who_sent: whoSent,
    }
    const response = await apiFetch('/api/v1/analytics/export-claim', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    if (!response.ok) throw new Error()
    const blob = await response.blob()

    // 1. Пытаемся взять чистый номер из кастомного заголовка (если он настроен на бэке)
    // или используем исправленное безопасное регулярное выражение
    let actNumber = isTest ? 'TEST' : '';
    
    // Сначала смотрим прямой кастомный заголовок (самый надежный способ без парсинга)
    const customActHeader = response.headers.get('X-Act-Number') || response.headers.get('x-act-number');
    
    if (customActHeader) {
      actNumber = customActHeader.trim();
    } else if (!isTest) {
      // Фолбэк: парсим Content-Disposition безопасным методом (без жадного .+)
      const disposition = response.headers.get('Content-Disposition') || response.headers.get('content-disposition');
      if (disposition) {
        // [^; \t]+ и [^"]+ строго отсекают кавычки и параметры заголовка Nginx/FastAPI
        const match = disposition.match(/filename\*=UTF-8''([^; \t]+)/i) || disposition.match(/filename="([^"]+)"/i);
        if (match && match[1]) {
          actNumber = decodeURIComponent(match[1]).trim().replace(/\.pdf$/i, '');
        }
      }
    }
    
    if (!actNumber && !isTest) actNumber = 'б-н';

    // Добавляем нули до 3 знаков (например, "92" -> "092")
    if (/^\d+$/.test(actNumber)) {
      actNumber = actNumber.padStart(3, '0');
    }

    // 2. Получаем текущую дату создания в формате YYYY.MM.DD
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const dateStr = `${year}.${month}.${day}`;

    // 3. Скачиваем файл по единому шаблону
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    
    // Результат: "РА № 092 - 2-СБ-Grey 2026.05.12.pdf"
    link.download = `РА № ${actNumber} - ${clickedSku.value} ${dateStr}.pdf`; 
    
    link.click();
  } catch (err) { alert("Не удалось сгенерировать рекламационный акт.") } finally { isGenerating.value = false }
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
    const mStr = String(calendarMonth.value + 1).padStart(2, '0')
    const dStr = String(i).padStart(2, '0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})

const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

// Инициализация дат периода для отчёта
const initReportDates = (type) => {
  const now = new Date()
  if (type === 'week') {
    const day = now.getDay() || 7
    const mon = new Date(now); mon.setDate(now.getDate() - day - 6)
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
    reportForm.value.start_date = mon.toISOString().split('T')[0]
    reportForm.value.end_date = sun.toISOString().split('T')[0]
  } else if (type === 'month') {
    reportForm.value.start_date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2,'0')}-01`
    reportForm.value.end_date = now.toISOString().split('T')[0]
  } else if (type === 'quarter') {
    const q = Math.floor(now.getMonth() / 3)
    reportForm.value.start_date = `${now.getFullYear()}-${String(q * 3 + 1).padStart(2,'0')}-01`
    reportForm.value.end_date = now.toISOString().split('T')[0]
  }
}

watch(() => reportForm.value.period_type, (t) => { if (t !== 'custom') initReportDates(t) })

const openReportModal = () => {
  initReportDates(reportForm.value.period_type)
  const factories = [...new Set(claimsDetail.value.map(c => c.factory_name).filter(f => f && f !== 'Уточняется'))]
  reportFactories.value = factories.sort()
  reportFactorySearch.value = ''
  showReportModal.value = true
}

const downloadReport = async () => {
  if (!reportForm.value.start_date || !reportForm.value.end_date) return alert('Укажите период')
  isReportGenerating.value = true
  try {
    const p = platformStore.platform
    const params = new URLSearchParams({
      start_date: reportForm.value.start_date,
      end_date: reportForm.value.end_date,
      platform: p,
      period_type: reportForm.value.period_type,
      include_containers: reportForm.value.include_containers,
      include_costs: reportForm.value.include_costs,
    })
    if (reportForm.value.factories.length > 0) {
      params.set('factories', reportForm.value.factories.join(','))
    }
    const res = await apiFetch(`/api/v1/analytics/ppm-report-excel?${params}`)
    if (!res.ok) throw new Error()
    const blob = await res.blob()
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `PPM_Отчет_${reportForm.value.start_date}_${reportForm.value.end_date}.xlsx`
    link.click()
    showReportModal.value = false
  } catch (e) { alert('Ошибка генерации отчёта') }
  finally { isReportGenerating.value = false }
}

const fetchCorrectionNotifications = async () => {
  try {
    const res = await apiFetch('/api/v1/analytics/correction-notifications')
    if (res.ok) correctionNotifications.value = (await res.json()).data || []
  } catch {}
}

watch(() => platformStore.platform, () => {
  loadPpmData()
})

onMounted(() => {
  loadPpmData()
  fetchCorrectionNotifications()
  document.addEventListener('click', () => {
    openParamsDropdownId.value = null
    openDeviationDropdownId.value = null
  })
})
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen font-sans max-w-[1600px] text-slate-800 antialiased">

    <div class="flex items-center justify-between mb-8 pb-5 border-b border-slate-200">
      <div class="flex items-center gap-4">
        <div class="p-3 bg-blue-600 text-white rounded-xl shadow-lg shadow-blue-200/50">
          <TrendingUp class="w-6 h-6" />
        </div>
        <h1 class="text-xl font-black tracking-tight text-slate-900">Уровень PPM. Рекламационные акты</h1>
      </div>
      
      <div class="flex items-center gap-4">
        <!-- Уведомление об инвойсах с исправлениями -->
        <div v-if="showCorrectionBadge" class="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-2xl px-4 py-2.5 text-xs font-bold text-blue-700 cursor-pointer hover:bg-blue-100 transition-colors shadow-sm" @click="showLogsModal = true; fetchLogs(); activeLogTab = 'acts'">
          🔔 {{ correctionNotifications.length }} инвойс(а) с исправлениями прибыли
        </div>
        <a href="/registry" target="_blank" class="flex items-center gap-2 bg-white border border-slate-200 rounded-2xl px-5 py-3 hover:bg-slate-50 transition-colors shadow-sm font-bold text-sm text-slate-700 no-underline">
          <History class="w-4 h-4 text-blue-500" />
          Реестр актов
        </a>
        <button @click="openReportModal" class="flex items-center gap-2 bg-white border border-slate-200 rounded-2xl px-5 py-3 hover:bg-slate-50 transition-colors shadow-sm font-bold text-sm text-slate-700">
          <Download class="w-4 h-4 text-green-500" />
          Отчет по заводу
        </button>

        <div class="relative">
          <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-5 py-3 cursor-pointer hover:border-blue-500 transition-colors shadow-sm" @click="showCalendarPopover = !showCalendarPopover">
            <Calendar class="w-4 h-4 text-blue-500 mr-3" />
            <span class="text-sm font-bold text-slate-700">{{ formatDateDisplay(startDate) }} — {{ formatDateDisplay(endDate) }}</span>
          </div>
          <div v-if="showCalendarPopover" class="absolute right-0 mt-3 bg-white border border-slate-100 rounded-3xl shadow-2xl z-[150] p-5 w-80 animate-in zoom-in-95 duration-200">
            <div class="mb-4 border-b border-slate-100 pb-3">
              <button @click="setPreviousMonth" class="w-full text-center text-xs font-black bg-blue-50 hover:bg-blue-100 text-blue-700 py-2.5 rounded-xl transition-all shadow-sm tracking-wide uppercase border border-blue-100/50">Прошлый месяц</button>
            </div>
            <div class="flex justify-between items-center mb-4">
              <button @click="changeCalendarMonth(-1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500 transition-colors"><ChevronLeft class="w-4 h-4"/></button>
              <span class="text-sm font-black text-slate-800 tracking-tight">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
              <button @click="changeCalendarMonth(1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500 transition-colors"><ChevronRight class="w-4 h-4"/></button>
            </div>
            <div class="grid grid-cols-7 gap-1 text-center text-[10px] font-black text-slate-400 uppercase mb-2">
              <div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div>
            </div>
            <div class="grid grid-cols-7 gap-1">
              <div v-for="(d, idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)" :class="['h-9 flex items-center justify-center text-sm font-bold rounded-xl transition-all select-none', !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer', d.dateStr === startDate || d.dateStr === endDate ? 'bg-white text-blue-600 shadow-md ring-2 ring-blue-600' : '', d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-blue-50 text-blue-700' : '', d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : '']">{{ d.day }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showLogsModal" class="fixed inset-0 z-[400] bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div class="bg-white rounded-3xl shadow-2xl w-full max-w-6xl overflow-hidden flex flex-col max-h-[90vh] animate-in zoom-in-95 duration-300">
        <div class="p-6 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
          <div class="flex flex-col gap-3">
            <div class="flex items-center gap-3">
              <History class="w-6 h-6 text-blue-600" />
              <h2 class="text-lg font-black text-slate-900">Единый реестр претензий</h2>
            </div>
            <div class="flex bg-slate-200/50 p-1 rounded-xl w-fit">
              <button @click="activeLogTab = 'acts'" :class="['px-4 py-1.5 text-sm font-bold rounded-lg transition-all', activeLogTab === 'acts' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']">Сгенерированные рекламационные акты</button>
              <button @click="activeLogTab = 'manual'" :class="['px-4 py-1.5 text-sm font-bold rounded-lg transition-all', activeLogTab === 'manual' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']">Ручные отправления</button>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <button @click="exportToExcel" class="flex items-center gap-2 px-4 py-2 bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 rounded-xl transition-colors font-bold text-sm shadow-sm">
              <Download class="w-4 h-4" /> В Excel
            </button>
            <button @click="showLogsModal = false" class="p-2 hover:bg-slate-200 rounded-xl text-slate-500 transition-colors"><X class="w-5 h-5"/></button>
          </div>
        </div>
        
        <div v-if="activeLogTab === 'acts'" class="overflow-y-auto custom-scroll flex-1 bg-white">
          <div v-if="isLogsLoading" class="text-center py-10 text-slate-400 font-bold animate-pulse">Загрузка журнала...</div>
          <div v-else-if="claimLogs.length === 0" class="text-center py-10 text-slate-400 font-bold">Акты еще не создавались</div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-left border-collapse text-xs" style="min-width:2100px;table-layout:fixed">
              <thead class="sticky top-0 z-10">
                <tr class="bg-slate-50 text-slate-400 border-b border-slate-200 uppercase font-black text-[9px] tracking-wider">
                  <th class="p-2.5 sticky left-0 bg-slate-50 z-20 relative select-none overflow-hidden" :style="getColStyle(0,120)">№ РА<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,0)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(1,90)">Дата РА<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,1)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(2,110)">Артикул<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,2)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(3,130)">Наименование<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,3)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(4,120)">Завод<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,4)"></div></th>
                  <th class="p-2.5 text-center relative select-none overflow-hidden" :style="getColStyle(5,55)">Кол-во<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,5)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(6,90)">Отчет. месяц<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,6)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(7,100)">Инициатор<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,7)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(8,120)">Инвойсы<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,8)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(9,55)">ABC<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,9)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(10,90)">Завод/не завод<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,10)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(11,150)">Отклонение<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,11)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(12,90)">Повторяемость<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,12)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(13,100)">Кто отправил<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,13)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(14,115)">Статус отправки<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,14)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(15,95)">Дата отправки<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,15)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(16,200)">Ответ завода<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,16)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(17,95)">Дата ответа<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,17)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(18,130)">Инвойс испр.<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,18)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(19,110)">Прим. дата улучш.<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,19)"></div></th>
                  <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(20,120)">Комментарии<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,20)"></div></th>
                  <th class="p-2.5 text-center relative select-none overflow-hidden" :style="getColStyle(21,68)">Статус<div class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-blue-400 transition-colors" @mousedown.stop="startResize($event,21)"></div></th>
                  <th class="p-2.5 text-center sticky right-0 bg-slate-50 z-20 select-none" :style="getColStyle(22,115)">Действия</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 text-slate-700">
                <template v-for="(log, logIndex) in claimLogs" :key="log.id">
                  <tr :class="['transition-colors', expandedLogId === log.id ? 'ring-inset ring-1 ring-blue-300' : '', isRowDone(log) ? 'bg-amber-100 hover:bg-amber-200/60' : isRowPartialDone(log) ? 'row-stripe' : isRowLocked(log) ? 'bg-green-50 hover:bg-green-100/60' : 'hover:bg-slate-50/80']"
                       :style="isRowPartialDone(log) && !isRowDone(log) ? { backgroundPosition: `${(logIndex * 37) % 95}px 0` } : {}">
                    <!-- 0: № РА + стрелка раскрытия -->
                    <td class="p-2 sticky left-0 z-10 overflow-hidden" :class="rowBgClass(log) || 'bg-white'">
                      <div class="flex items-center gap-1">
                        <button @click="expandedLogId = expandedLogId === log.id ? null : log.id" class="text-slate-400 hover:text-blue-600 flex-shrink-0 transition-colors">
                          <ChevronDown :class="['w-3.5 h-3.5 transition-transform', expandedLogId === log.id ? 'rotate-180 text-blue-600' : '']" />
                        </button>
                        <div>
                          <div class="font-black text-blue-700 text-xs leading-tight">{{ log.act_number }}</div>
                          <div class="text-[9px] text-slate-400 mt-0.5">{{ log.created_at }}</div>
                        </div>
                      </div>
                    </td>
                    <!-- 1: Дата РА -->
                    <td class="p-2 overflow-hidden">
                      <input type="date" v-model="log.ra_date" @blur="saveLogField(log,'ra_date')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default text-slate-600' : 'border-transparent hover:border-slate-200 focus:border-blue-400 focus:bg-white bg-transparent']" />
                    </td>
                    <!-- 2: Артикул -->
                    <td class="p-2 font-bold overflow-hidden truncate">{{ log.sku }}</td>
                    <!-- 3: Наименование -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.product_name" @blur="saveLogField(log,'product_name')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none truncate', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 4: Завод -->
                    <td class="p-2 text-slate-600 overflow-hidden truncate">{{ log.supplier }}</td>
                    <!-- 5: Кол-во -->
                    <td class="p-2 text-center font-bold text-red-500">{{ log.qty || log.defects_count }}</td>
                    <!-- 6: Отчет. месяц -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.report_month" @blur="saveLogField(log,'report_month')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 7: Инициатор -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.initiator" @blur="saveLogField(log,'initiator')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 8: Инвойсы -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.invoice_ref" @blur="saveLogField(log,'invoice_ref')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 9: ABC -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.abc_group" @blur="saveLogField(log,'abc_group')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 10: Завод/не завод -->
                    <td class="p-2 overflow-hidden">
                      <select v-model="log.factory_type" @change="saveLogField(log,'factory_type')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']">
                        <option value="">—</option><option>завод</option><option>не завод</option>
                      </select>
                    </td>
                    <!-- 11: Отклонение (мультиселект, вертикально) -->
                    <td class="p-2 overflow-hidden relative">
                      <div v-if="isRowLocked(log)" class="text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">{{ (log.deviation || '—').replace(/;\s*/g, '\n') }}</div>
                      <div v-else class="relative" @click.stop>
                        <div class="w-full text-xs p-1 border border-transparent hover:border-slate-200 rounded cursor-pointer min-h-[22px] whitespace-pre-wrap leading-relaxed"
                             @click="openDeviationDropdownId = openDeviationDropdownId === log.id ? null : log.id; openParamsDropdownId = null">
                          {{ (log.deviation || '').replace(/;\s*/g, '\n') || '—' }}
                        </div>
                        <div v-if="openDeviationDropdownId === log.id" class="absolute left-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-xl shadow-xl p-2 min-w-[230px]">
                          <div v-for="opt in DEVIATION_OPTIONS" :key="opt">
                            <label class="flex items-center gap-2 p-1.5 hover:bg-slate-50 rounded-lg cursor-pointer">
                              <input type="checkbox" :checked="isDeviationSelected(log, opt)" @change="toggleDeviation(log, opt)" class="rounded accent-blue-600" />
                              <span class="text-xs">{{ opt }}</span>
                            </label>
                          </div>
                          <button @click="openDeviationDropdownId = null" class="mt-1 text-[10px] text-blue-600 font-bold w-full text-center py-1 hover:bg-blue-50 rounded">✓ Готово</button>
                        </div>
                      </div>
                    </td>
                    <!-- 12: Повторяемость -->
                    <td class="p-2 overflow-hidden">
                      <select v-model="log.repeatability" @change="saveLogField(log,'repeatability')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']">
                        <option value="">—</option><option>Первичный</option><option>Повторный</option>
                      </select>
                    </td>
                    <!-- 13: Кто отправил -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.who_sent" @blur="saveLogField(log,'who_sent')" :disabled="isRowLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" placeholder="—" />
                    </td>
                    <!-- 14: Статус отправки -->
                    <td class="p-2 overflow-hidden">
                      <select v-model="log.send_status" @change="handleSendStatusChange(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none font-semibold', isRowLocked(log) ? 'border-green-200 bg-green-100 text-green-800 cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']">
                        <option value="">—</option><option>Не отправлен</option><option>Отправлен</option><option>Доставлен</option><option>Прочитан</option>
                      </select>
                    </td>
                    <!-- 15: Дата отправки (авто при Отправлен) -->
                    <td class="p-2 overflow-hidden">
                      <div v-if="isRowLocked(log)" class="text-xs text-slate-600 font-medium px-1">{{ log.send_date || '—' }}</div>
                      <input v-else type="date" v-model="log.send_date" @blur="saveLogField(log,'send_date')"
                        class="w-full text-xs p-1 border border-transparent hover:border-slate-200 rounded focus:border-blue-400 bg-transparent outline-none" />
                    </td>
                    <!-- 16: Ответ завода (редактируемо пока не полная блокировка) -->
                    <td class="p-2 overflow-hidden">
                      <textarea v-model="log.factory_reply" @blur="saveLogField(log,'factory_reply')" :disabled="isRowFullyLocked(log)" rows="2" placeholder="Ответ завода..."
                        :class="['w-full text-xs p-1 border rounded outline-none resize-none', isRowFullyLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent focus:bg-white']"></textarea>
                    </td>
                    <!-- 17: Дата ответа (редактируемо пока не полная блокировка) -->
                    <td class="p-2 overflow-hidden">
                      <input type="date" v-model="log.factory_reply_date" @blur="saveLogField(log,'factory_reply_date')" :disabled="isRowFullyLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowFullyLocked(log) ? 'border-transparent bg-transparent cursor-default text-slate-600' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" />
                    </td>
                    <!-- 18: Примерный инвойс (редактируемо пока не полная блокировка) -->
                    <td class="p-2 overflow-hidden">
                      <input v-model="log.correction_invoice" @blur="saveLogField(log,'correction_invoice')" :disabled="isRowFullyLocked(log)" placeholder="Номер инвойса..."
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowFullyLocked(log) ? 'border-transparent bg-transparent cursor-default text-slate-600' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" />
                    </td>
                    <!-- 19: Примерная дата улучшения (последнее поле — заполнение закрывает строку) -->
                    <td class="p-2 overflow-hidden">
                      <input type="date" v-model="log.estimated_improvement_date" @blur="saveLogField(log,'estimated_improvement_date')" :disabled="isRowFullyLocked(log)"
                        :class="['w-full text-xs p-1 border rounded outline-none', isRowFullyLocked(log) ? 'border-transparent bg-transparent cursor-default text-slate-600 font-bold' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent']" />
                    </td>
                    <!-- 20: Комментарии -->
                    <td class="p-2 overflow-hidden">
                      <textarea v-model="log.comments" @blur="saveLogField(log,'comments')" :disabled="isRowLocked(log)" rows="2" placeholder="Комментарии..."
                        :class="['w-full text-xs p-1 border rounded outline-none resize-none', isRowLocked(log) ? 'border-transparent bg-transparent cursor-default' : 'border-transparent hover:border-slate-200 focus:border-blue-400 bg-transparent focus:bg-white']"></textarea>
                    </td>
                    <!-- 21: Статус акта -->
                    <td class="p-2 text-center">
                      <span :class="['px-2 py-0.5 text-[9px] uppercase tracking-wider rounded font-black', log.status === 'Активен' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700']">{{ log.status }}</span>
                    </td>
                    <!-- 22: Действия (sticky right) -->
                    <td class="p-2 sticky right-0 z-10" :class="rowBgClass(log) || 'bg-white'">
                      <div class="flex flex-col gap-1">
                        <button @click="redownloadLog(log)" :disabled="!log.pdf_payload" class="w-full justify-center text-blue-600 hover:text-white hover:bg-blue-600 border border-blue-100 bg-blue-50 p-1 rounded-lg transition-colors inline-flex items-center gap-1 text-[10px] font-bold disabled:opacity-30 disabled:cursor-not-allowed">
                          <Download class="w-3 h-3" /> Скачать
                        </button>
                        <button v-if="log.status === 'Активен' && !isRowLocked(log)" @click="cancelLog(log.id)" class="w-full justify-center text-orange-500 hover:text-white hover:bg-orange-500 border border-orange-100 bg-orange-50 p-1 rounded-lg transition-colors inline-flex items-center gap-1 text-[10px] font-bold">
                          <Ban class="w-3 h-3" /> Отмена
                        </button>
                        <div v-else-if="isRowLocked(log) && !isRowFullyLocked(log)" class="text-[9px] text-green-700 font-bold text-center bg-green-100 rounded p-1">🔒 Отправлен</div>
                        <div v-else-if="isRowFullyLocked(log)" class="text-[9px] text-amber-700 font-bold text-center bg-amber-100 rounded p-1">🏁 Завершён</div>
                        <button v-if="currentUserRole === 'admin' && isRowLocked(log)" @click="unlockLog(log.id)" class="w-full justify-center text-yellow-700 hover:text-white hover:bg-yellow-500 border border-yellow-200 bg-yellow-50 p-1 rounded-lg transition-colors inline-flex items-center gap-1 text-[10px] font-bold">
                          🔓 Разблок.
                        </button>
                        <button v-if="currentUserRole === 'admin'" @click="deleteLog(log.id)" class="w-full justify-center text-red-500 hover:text-white hover:bg-red-500 border border-red-100 bg-red-50 p-1 rounded-lg transition-colors inline-flex items-center gap-1 text-[10px] font-bold">
                          <X class="w-3 h-3" /> Удалить
                        </button>
                      </div>
                    </td>
                  </tr>
                  <!-- Раскрытая строка с дополнительными полями -->
                  <tr v-if="expandedLogId === log.id" class="bg-blue-50/30 border-b border-blue-100">
                    <td colspan="23" class="px-4 py-3">
                      <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs">
                        <!-- Статус претензии (перенесён сюда) -->
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Статус претензии</div>
                          <select v-model="log.claim_status" @change="saveLogField(log,'claim_status')" :disabled="isRowLocked(log)"
                            class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 text-xs max-w-[160px] w-full">
                            <option value="">—</option><option>На проверке</option><option>Принята</option><option>Отклонена</option><option>Отменена</option>
                          </select>
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">№ партии</div>
                          <input v-model="log.batch_num" @blur="saveLogField(log,'batch_num')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" placeholder="—" />
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Дата производства</div>
                          <input type="date" v-model="log.production_date" @blur="saveLogField(log,'production_date')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" />
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Дата продажи</div>
                          <input type="date" v-model="log.sale_date" @blur="saveLogField(log,'sale_date')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" />
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Стадия освоения</div>
                          <input v-model="log.stage" @blur="saveLogField(log,'stage')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" placeholder="—" />
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Объект</div>
                          <input v-model="log.object_type" @blur="saveLogField(log,'object_type')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" placeholder="—" />
                        </div>
                        <!-- Контролируемые параметры (мультиселект) -->
                        <div class="relative" @click.stop>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Контр. параметры</div>
                          <div v-if="isRowLocked(log)" class="text-xs text-slate-600 p-1 whitespace-pre-wrap leading-relaxed max-w-[160px]">{{ (log.controlled_params || '—').replace(/;\s*/g, '\n') }}</div>
                          <div v-else>
                            <div class="p-1 border border-slate-200 rounded-lg bg-white cursor-pointer min-h-[28px] whitespace-pre-wrap leading-relaxed max-w-[160px] w-full"
                                 @click="openParamsDropdownId = openParamsDropdownId === log.id ? null : log.id; openDeviationDropdownId = null">
                              {{ (log.controlled_params || '').replace(/;\s*/g, '\n') || '—' }}
                            </div>
                            <div v-if="openParamsDropdownId === log.id" class="absolute left-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-xl shadow-xl p-2 min-w-[210px]">
                              <div v-for="opt in CONTROLLED_PARAMS_OPTIONS" :key="opt">
                                <label class="flex items-center gap-2 p-1.5 hover:bg-slate-50 rounded-lg cursor-pointer">
                                  <input type="checkbox" :checked="isParamSelected(log, opt)" @change="toggleParam(log, opt)" class="rounded accent-blue-600" />
                                  <span class="text-xs">{{ opt }}</span>
                                </label>
                              </div>
                              <button @click="openParamsDropdownId = null" class="mt-1 text-[10px] text-blue-600 font-bold w-full text-center py-1 hover:bg-blue-50 rounded">✓ Готово</button>
                            </div>
                          </div>
                        </div>
                        <div>
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Название чата</div>
                          <input v-model="log.chat_name" @blur="saveLogField(log,'chat_name')" :disabled="isRowLocked(log)" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 max-w-[160px] w-full" placeholder="—" />
                        </div>
                        <div class="md:col-span-2">
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Общее описание отклонения</div>
                          <textarea v-model="log.deviation_desc" @blur="saveLogField(log,'deviation_desc')" :disabled="isRowLocked(log)" rows="2" class="w-full p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 resize-none" placeholder="—"></textarea>
                        </div>
                        <div class="md:col-span-2">
                          <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Причина отклонения</div>
                          <textarea v-model="log.deviation_cause" @blur="saveLogField(log,'deviation_cause')" :disabled="isRowLocked(log)" rows="2" class="w-full p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 resize-none" placeholder="—"></textarea>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="activeLogTab === 'manual'" class="overflow-y-auto custom-scroll flex-1 bg-white flex flex-col">
          <!-- Статистика -->
          <div class="flex-shrink-0 flex items-center gap-5 px-6 py-3 bg-slate-50 border-b border-slate-100 text-xs font-bold">
            <span class="text-slate-500">Всего: <span class="text-slate-800">{{ manualStats.total }}</span></span>
            <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>Ожидают ответа: <span class="text-amber-600">{{ manualStats.sent }}</span></span>
            <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-green-400"></span>Отвечено: <span class="text-green-600">{{ manualStats.replied }}</span></span>
            <button @click="openAddManual" class="ml-auto px-3 py-1.5 bg-blue-600 text-white rounded-lg font-bold text-xs hover:bg-blue-700 transition-colors">+ Новое обращение</button>
          </div>

          <div v-if="showAddManual" class="bg-blue-50 border-b border-blue-100 px-6 py-4 animate-in slide-in-from-top-2" @click.self="closeCellDropdowns">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-2">
              <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Дата отправки*</label><input type="date" v-model="newManualClaim.send_date" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white"></div>
              <!-- Кто отправил — авто из профиля -->
              <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Кто отправил</label><input v-model="newManualClaim.who_sent" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white" placeholder="Авто из профиля"></div>
              <!-- Завод — поиск -->
              <div class="relative">
                <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Завод</label>
                <input v-model="manualSearch.factory" @input="manualDropdown.factory = true; newManualClaim.factory_name = manualSearch.factory"
                       @focus="manualDropdown.factory = true"
                       class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400" placeholder="Поиск завода..." />
                <div v-if="manualDropdown.factory && filteredMeta('factory', manualSearch.factory).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-40 overflow-y-auto">
                  <div v-for="v in filteredMeta('factory', manualSearch.factory)" :key="v" @mousedown.prevent="setManualField('factory_name', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer">{{ v }}</div>
                </div>
              </div>
              <!-- Инвойс — поиск -->
              <div class="relative">
                <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">№ инвойса</label>
                <input v-model="manualSearch.invoice" @input="manualDropdown.invoice = true; newManualClaim.invoice_ref = manualSearch.invoice"
                       @focus="manualDropdown.invoice = true"
                       class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400" placeholder="Поиск инвойса..." />
                <div v-if="manualDropdown.invoice && filteredMeta('invoice', manualSearch.invoice).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-40 overflow-y-auto">
                  <div v-for="v in filteredMeta('invoice', manualSearch.invoice)" :key="v" @mousedown.prevent="setManualField('invoice_ref', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer font-mono">{{ v }}</div>
                </div>
              </div>
              <!-- Контейнер — поиск -->
              <div class="relative">
                <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">№ контейнера</label>
                <input v-model="manualSearch.container" @input="manualDropdown.container = true; newManualClaim.container_num = manualSearch.container"
                       @focus="manualDropdown.container = true"
                       class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400" placeholder="Поиск контейнера..." />
                <div v-if="manualDropdown.container && filteredMeta('container', manualSearch.container).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-40 overflow-y-auto">
                  <div v-for="v in filteredMeta('container', manualSearch.container)" :key="v" @mousedown.prevent="setManualField('container_num', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer font-mono">{{ v }}</div>
                </div>
              </div>
              <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Статус</label>
                <select v-model="newManualClaim.manual_status" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white">
                  <option value="">—</option><option v-for="o in MANUAL_STATUS_OPTIONS" :key="o">{{ o }}</option>
                </select>
              </div>
            </div>
            <div class="flex gap-2">
              <div class="flex-1"><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Текст отправления*</label>
                <textarea v-model="newManualClaim.send_text" rows="2" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white resize-none"></textarea>
              </div>
              <div class="flex items-end gap-2 pb-1">
                <button @click="submitManualClaim" class="bg-blue-600 text-white px-4 py-2 rounded-lg font-bold text-xs hover:bg-blue-700">Создать</button>
              </div>
            </div>
          </div>

          <div v-if="isManualLoading" class="text-center py-10 text-slate-400 font-bold animate-pulse">Загрузка журнала...</div>
          <div v-else-if="manualClaims.length === 0" class="text-center py-10 text-slate-400 font-bold">Ручные претензии еще не добавлялись</div>
          <div v-else class="flex-1 overflow-auto">
            <table class="w-full text-left border-collapse text-xs" style="min-width:1100px">
              <thead class="sticky top-0 z-10 bg-slate-50 border-b border-slate-200">
                <tr class="text-slate-400 uppercase font-black text-[9px] tracking-wider">
                  <th class="p-2.5 w-28">Номер</th>
                  <th class="p-2.5 w-26">Дата отпр.</th>
                  <th class="p-2.5 w-28">Кто отправил</th>
                  <th class="p-2.5 w-28">Завод</th>
                  <th class="p-2.5 w-30">№ инвойса</th>
                  <th class="p-2.5 w-30">№ контейнера</th>
                  <th class="p-2.5 w-28">Статус</th>
                  <th class="p-2.5">Текст отправления</th>
                  <th class="p-2.5 w-28">Дата ответа</th>
                  <th class="p-2.5">Текст ответа</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 font-semibold text-slate-700">
                <tr v-for="claim in manualClaims" :key="claim.id"
                    :class="['transition-colors', (claim.reply_text || claim.reply_date) ? 'bg-green-50 hover:bg-green-100/60' : claim.send_date ? 'bg-amber-50 hover:bg-amber-100/50' : 'hover:bg-slate-50']">
                  <td class="p-2.5 font-black text-amber-600 whitespace-nowrap text-xs">{{ claim.ticket_number }}</td>
                  <td class="p-2.5 text-xs text-slate-700">{{ claim.send_date }}</td>
                  <td class="p-2.5 text-xs text-slate-600 font-medium">{{ claim.who_sent || '—' }}</td>
                  <!-- Завод с datalist -->
                  <td class="p-2.5">
                    <input v-model="claim.factory_name" @blur="saveManualReply(claim)" :list="`factory-list-m-${claim.id}`"
                           class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300" placeholder="—">
                    <datalist :id="`factory-list-m-${claim.id}`"><option v-for="v in claimsMetadata.factories" :key="v" :value="v"/></datalist>
                  </td>
                  <!-- Инвойс с datalist -->
                  <td class="p-2.5">
                    <input v-model="claim.invoice_ref" @blur="saveManualReply(claim)" :list="`invoice-list-m-${claim.id}`"
                           class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300 font-mono" placeholder="—">
                    <datalist :id="`invoice-list-m-${claim.id}`"><option v-for="v in claimsMetadata.invoices" :key="v" :value="v"/></datalist>
                  </td>
                  <!-- Контейнер с datalist -->
                  <td class="p-2.5">
                    <input v-model="claim.container_num" @blur="saveManualReply(claim)" :list="`container-list-m-${claim.id}`"
                           class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300 font-mono" placeholder="—">
                    <datalist :id="`container-list-m-${claim.id}`"><option v-for="v in claimsMetadata.containers" :key="v" :value="v"/></datalist>
                  </td>
                  <td class="p-2.5">
                    <select v-model="claim.manual_status" @change="saveManualReply(claim)" class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white">
                      <option value="">—</option><option v-for="o in MANUAL_STATUS_OPTIONS" :key="o">{{ o }}</option>
                    </select>
                  </td>
                  <td class="p-2.5 text-xs whitespace-pre-wrap text-slate-500 max-w-xs">{{ claim.send_text }}</td>
                  <td class="p-2.5"><input type="date" v-model="claim.reply_date" @blur="saveManualReply(claim)" class="w-full p-1.5 text-xs border border-slate-200 rounded bg-slate-50 focus:bg-white outline-none transition-all"></td>
                  <td class="p-2.5"><textarea v-model="claim.reply_text" @blur="saveManualReply(claim)" placeholder="Введите ответ..." rows="2" class="w-full text-xs p-2 border border-slate-200 rounded-lg bg-slate-50 focus:bg-white outline-none resize-none transition-all"></textarea></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div class="relative mb-8">
      <div class="flex items-center bg-white border border-slate-200 rounded-2xl px-5 py-4 cursor-pointer shadow-sm hover:shadow-md transition-shadow" @click="showSkuDropdown = !showSkuDropdown">
        <Search class="w-5 h-5 text-slate-400 mr-4" />
        <span class="text-sm font-bold text-slate-700 flex-1">{{ selectedSku === 'Все' ? 'Найти и выбрать артикул...' : selectedSku }}</span>
        <X v-if="selectedSku !== 'Все'" class="w-5 h-5 text-slate-300 hover:text-red-500 transition-colors" @click.stop="selectedSku = 'Все'; clickedSku = null" />
      </div>
      <div v-if="showSkuDropdown" class="absolute top-full left-0 right-0 mt-2 bg-white border border-slate-100 rounded-2xl shadow-xl z-[100] p-3 overflow-hidden">
        <input type="text" v-model="skuSearch" class="w-full border border-slate-200 bg-slate-50 rounded-xl p-3 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all" placeholder="Поиск по артикулу..." @click.stop />
        <div class="max-h-60 overflow-y-auto custom-scroll pr-1">
          <div v-for="sku in filteredSkuList" :key="sku" @click="selectedSku = sku; clickedSku = (sku === 'Все' ? null : sku); showSkuDropdown = false" class="p-3 text-sm font-bold hover:bg-blue-50 rounded-xl cursor-pointer transition-colors text-slate-700">{{ sku }}</div>
        </div>
      </div>
    </div>

    <div v-if="loading" class="text-center py-24 text-slate-400 font-bold tracking-wide animate-pulse">⚙️ Загрузка данных экосистемы...</div>

    <div v-else class="space-y-8">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div v-for="g in ['A', 'B', 'C']" :key="g"
             @click="selectedGroupFilter = selectedGroupFilter === g ? null : g; clickedSku = null; selectedFactory = 'Уточняется'"
             :class="['p-6 rounded-3xl transition-all relative cursor-pointer border', 
                      selectedGroupFilter === g ? 'bg-blue-50 border-blue-400 ring-2 ring-blue-400/20 shadow-md' : 'bg-white border-slate-200 hover:shadow-lg shadow-sm']">
          <div class="absolute inset-0 overflow-hidden rounded-3xl pointer-events-none">
             <div class="absolute -right-4 -top-4 w-24 h-24 bg-slate-100 rounded-full opacity-50"></div>
          </div>
          <div class="flex justify-between items-center mb-4 relative z-10">
            <span class="text-[11px] font-black px-3 py-1.5 rounded-lg bg-blue-100 text-blue-800 uppercase tracking-widest">Группа {{ g }}</span>
          </div>
          <div class="flex items-baseline gap-4 relative z-10">
            <div class="text-3xl font-black text-slate-900 tracking-tight">{{ groupMetrics[g].total }} <span class="text-lg text-slate-400 font-bold ml-0.5">SKU</span></div>
            <div class="flex items-baseline gap-2">
              <button @click.stop="openCriticalPopup(g, $event)"
                      :class="['flex items-baseline gap-1 rounded-xl px-2 py-1 transition-colors', groupMetrics[g].bad > 0 ? 'hover:bg-rose-100 cursor-pointer' : 'cursor-default']">
                <span class="text-3xl font-black text-rose-400/90 tracking-tight">{{ groupMetrics[g].bad }}</span>
                <span class="text-lg text-rose-400/70 font-bold ml-0.5">крит.</span>
              </button>
              <div v-if="groupMetrics[g].entered.length || groupMetrics[g].left.length" class="flex items-baseline gap-3">
                <span v-if="groupMetrics[g].entered.length" class="text-3xl font-black text-red-600 flex items-baseline">+{{ groupMetrics[g].entered.length }}<span class="text-sm font-bold text-red-600/70 ml-0.5">стали</span></span>
                <span v-if="groupMetrics[g].left.length" class="text-3xl font-black text-emerald-500 flex items-baseline">−{{ groupMetrics[g].left.length }}<span class="text-sm font-bold text-emerald-500/70 ml-0.5">вышли</span></span>
              </div>
            </div>
          </div>
          <div class="text-sm font-semibold text-slate-500 mt-3 relative z-10">Средний PPM: <span class="text-slate-900 font-black ml-1">{{ groupMetrics[g].ppm.toLocaleString() }}</span></div>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm">
        <div ref="trendChart" class="w-full"></div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 bg-slate-50/50 flex items-center gap-3">
          <TableProperties class="w-5 h-5 text-blue-600"/>
          <h2 class="text-sm font-black text-slate-800 tracking-wide">
            Спецификация по артикулам <span v-if="selectedGroupFilter" class="text-blue-500 ml-2">(Фильтр: Группа {{ selectedGroupFilter }})</span>
          </h2>
        </div>
        <div class="overflow-y-auto max-h-[500px] custom-scroll">
          <table class="w-full text-left border-collapse table-fixed text-sm">
            <thead>
              <tr class="bg-white text-slate-400 border-b border-slate-200 uppercase font-bold text-[10px] tracking-wider sticky top-0 z-10 shadow-sm">
                <th class="p-4 w-48 sticky left-0 bg-white border-r border-slate-100">Артикул продавца</th>
                <th class="p-4 text-center w-16">ABC</th>
                <th class="p-4 text-center w-16">XYZ</th>
                <th class="p-4 text-right w-28">Заказы</th>
                <th class="p-4 text-right w-24">Брак</th>
                <th class="p-4 text-right w-24">% брака</th>
                <th class="p-4 text-right w-32 text-slate-600">PPM</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 font-semibold text-slate-700">
              <tr v-for="row in processedTableData" 
                  :key="row['Артикул'] + '___' + row['Завод']" 
                  @click="handleRowClick(row)" 
                  :class="['cursor-pointer transition-colors border-l-4 group', clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'bg-blue-600 text-white border-blue-800 shadow-md' : (row.ppm > 10000 ? 'bg-red-50/30 hover:bg-red-50 border-red-400' : 'hover:bg-slate-50 border-transparent')]">
                
                <td :class="['p-4 font-bold border-r border-slate-100 truncate shadow-xs sticky left-0 transition-colors', clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'bg-blue-600 text-white' : 'bg-white text-slate-900 group-hover:bg-slate-50']">
                    {{ row['Артикул'] }}
                    <span v-if="getFactoryCount(row['Артикул']) > 1" class="ml-2 text-[9px] bg-slate-100 border border-slate-200 text-slate-400 px-1.5 py-0.5 rounded uppercase font-black">Разделен</span>
                    <div class="text-[10px] text-slate-400 font-normal mt-0.5 tracking-wide">Фабрика: <span class="text-blue-600 font-bold">{{ row['Завод'] }}</span></div>
                </td>
                <td class="p-4 text-center" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-blue-200' : 'text-slate-500']">{{ row['ABC_Группа'] }}</td>
                <td class="p-4 text-center" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-blue-300' : 'text-slate-400']">{{ row['Класс XYZ'] }}</td>
                <td class="p-4 text-right font-bold" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-white' : 'text-slate-600']">{{ row['Заказы'].toLocaleString() }}</td>
                <td class="p-4 text-right font-bold" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-white' : 'text-red-500']">{{ row['Брак'].toLocaleString() }}</td>
                <td class="p-4 text-right font-bold" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-blue-200' : 'text-blue-600']">{{ row.pct.toFixed(2) }}%</td>
                <td class="p-4 text-right font-black" :class="[clickedSku === row['Артикул'] && selectedFactory === row['Завод'] ? 'text-white' : 'text-slate-900']">{{ row.ppm.toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="clickedSku" class="bg-white border border-slate-200 rounded-3xl p-8 shadow-lg shadow-slate-200/50 space-y-8 animate-in slide-in-from-bottom-4 fade-in duration-300">
        <div class="flex items-center justify-between border-b pb-5 border-slate-100">
          <div>
            <h3 class="text-lg font-black text-slate-900 tracking-tight">Анализ причин дефектов</h3>
            <div class="flex items-center gap-4 mt-2">
              <div class="text-sm font-semibold text-slate-500">Артикул: <span class="text-blue-600 font-bold">{{ clickedSku }}</span> (Завод: {{ selectedFactory }})</div>
            </div>
          </div>
          <button @click="clickedSku = null" class="text-slate-400 hover:text-slate-700 p-2 rounded-xl hover:bg-slate-100 transition-colors"><X class="w-6 h-6"/></button>
        </div>
        
        <div v-if="activeDefectCategories.length === 0" class="text-sm text-slate-400 font-bold py-12 text-center bg-slate-50 rounded-2xl border border-dashed border-slate-200">Дефекты не обнаружены в выбранном периоде</div>
        
        <div v-else class="flex flex-col gap-10">
          <div v-for="cat in activeDefectCategories" :key="cat.key" class="flex flex-col border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
            
            <div class="mb-5 flex flex-wrap gap-4 items-start justify-between">
              <div class="flex items-center gap-3">
                <!-- Тумблер: включить/исключить группу из акта -->
                <label class="flex items-center gap-1.5 cursor-pointer select-none group/toggle" :title="enabledCategories[cat.key] !== false ? 'Включено в акт — нажмите чтобы исключить' : 'Исключено из акта — нажмите чтобы включить'">
                  <div :class="['relative w-9 h-5 rounded-full transition-colors', enabledCategories[cat.key] !== false ? 'bg-blue-600' : 'bg-slate-300']"
                       @click="enabledCategories[cat.key] = !(enabledCategories[cat.key] !== false)">
                    <div :class="['absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform', enabledCategories[cat.key] !== false ? 'translate-x-4' : 'translate-x-0.5']"></div>
                  </div>
                  <span :class="['text-[10px] font-black uppercase tracking-wider', enabledCategories[cat.key] !== false ? 'text-blue-600' : 'text-slate-400']">
                    {{ enabledCategories[cat.key] !== false ? 'В акт' : 'Исключён' }}
                  </span>
                </label>
                <div :class="['text-base font-black flex items-center gap-2', enabledCategories[cat.key] !== false ? 'text-slate-800' : 'text-slate-400 line-through']">
                  <span class="w-2 h-2 rounded-full bg-red-500 flex-shrink-0"></span> {{ cat.name }}
                  <span class="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-md ml-2">{{ cat.count }} шт.</span>
                </div>
              </div>
              <div class="flex flex-wrap gap-2 max-w-xl justify-end">
                <span v-for="inv in cat.invoices" :key="inv.name" class="px-2.5 py-1 bg-slate-50 border border-slate-200 text-xs font-bold rounded-lg text-slate-600 shadow-sm">
                  {{ inv.name }} <span class="text-slate-400 ml-1">({{ inv.count }} шт)</span>
                </span>
              </div>
            </div>
            
            <div v-if="categoryPhotosState[cat.key]?.all.length > categoryPhotosState[cat.key]?.visible.length" class="mb-4 flex justify-end">
                <button @click="refreshCategoryPhotos(cat.key)" class="text-xs bg-slate-100 hover:bg-blue-50 text-slate-600 hover:text-blue-600 border border-slate-200 hover:border-blue-200 px-3 py-1.5 rounded-xl font-bold transition-all flex items-center gap-1.5 shadow-xs">
                    🔄 Заменить невыбранные фото (Всего в пуле: {{ categoryPhotosState[cat.key]?.all.length }} шт)
                </button>
            </div>

            <div v-if="cat.photos.length > 0" class="w-full grid grid-cols-2 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-4">
              <div v-for="(img, idx) in cat.photos" :key="idx" 
                  @click="togglePhotoSelection(img)"
                  :class="['aspect-square rounded-2xl border-4 overflow-hidden cursor-pointer group relative transition-all duration-300', 
                            excludedPhotos.has(img) ? 'border-transparent opacity-40 grayscale scale-95' : 'border-blue-500 shadow-lg shadow-blue-200 hover:-translate-y-1']">
                
                <img :src="img" loading="eager" decoding="async" class="w-full h-full object-cover" />
                
                <div @click.stop="lightbox = { isOpen: true, photos: cat.photos, index: idx }" 
                    class="absolute bottom-2 right-2 bg-black/70 p-2.5 rounded-xl hover:bg-black text-white opacity-0 group-hover:opacity-100 transition-opacity shadow-lg">
                  <Search class="w-4 h-4" />
                </div>
                
                <div v-if="!excludedPhotos.has(img)" class="absolute top-2 left-2 bg-blue-500 rounded-full w-8 h-8 flex items-center justify-center text-white shadow-lg backdrop-blur-sm">
                  <CheckCircle2 class="w-5 h-5" />
                </div>
              </div>
            </div>
            <div v-else class="text-xs text-slate-400 font-bold p-4 bg-slate-50 rounded-xl text-center">Фотографии отсутствуют</div>

          </div>
        </div>

        <div class="bg-gradient-to-br from-blue-600 to-indigo-700 rounded-3xl p-8 mt-10 shadow-xl text-white relative overflow-hidden">
          <div class="absolute top-0 right-0 w-64 h-64 bg-white opacity-5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/3"></div>

          <div class="relative z-10 flex flex-col md:flex-row items-center justify-between gap-8">
            <div class="max-w-xl">
              <h4 class="text-2xl font-black tracking-tight mb-2 flex items-center gap-3">
                <FileText class="w-8 h-8 text-blue-200" /> Рекламационный акт
              </h4>
              <p class="text-blue-100 font-medium text-sm leading-relaxed">Фото коробок и штрих-кодов уже убраны автоматически. Снимите выделение с оставшихся нежелательных фотографий — они не попадут в акт.</p>
            </div>

            <div class="w-full md:w-auto flex flex-col sm:flex-row gap-3">
              <button @click="generatePDFClaim(true)" :disabled="isGenerating"
                      :class="['w-full md:w-auto px-6 py-4 bg-blue-500/20 border border-white/40 text-white rounded-2xl text-sm font-black transition-all flex items-center justify-center gap-2',
                               isGenerating ? 'opacity-70 cursor-wait' : 'hover:bg-blue-500/40 active:scale-95']">
                🧪 Тестовый акт
              </button>
              <button @click="generatePDFClaim(false)" :disabled="isGenerating"
                      :class="['w-full md:w-auto px-8 py-4 bg-white text-blue-700 rounded-2xl text-sm font-black transition-all flex items-center justify-center gap-3 shadow-xl',
                               isGenerating ? 'opacity-70 cursor-wait' : 'hover:scale-105 active:scale-95']">
                <Download v-if="!isGenerating" class="w-5 h-5" />
                {{ isGenerating ? '⏳ Формирование акта...' : 'Скачать акт (PDF)' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Lightbox с комментарием клиента -->
    <!-- Lightbox: комментарий overlay в верхнем левом углу фото -->
    <div v-if="lightbox.isOpen" class="fixed inset-0 z-[300] bg-slate-900/95 backdrop-blur-sm flex flex-col animate-in fade-in duration-200">
      <div class="flex justify-between items-center px-6 pt-5 pb-3 text-white">
        <span class="text-sm font-black text-slate-400 uppercase tracking-widest">{{ lightbox.index + 1 }} / {{ lightbox.photos.length }}</span>
        <button @click="lightbox.isOpen = false" class="p-3 bg-white/10 hover:bg-red-500 rounded-xl text-white transition-colors"><X class="w-6 h-6"/></button>
      </div>
      <div class="flex-1 flex items-center justify-between px-6 pb-6">
        <button @click="lightbox.index = (lightbox.index - 1 + lightbox.photos.length) % lightbox.photos.length" class="p-4 bg-white/10 hover:bg-white/20 text-white rounded-2xl transition-colors backdrop-blur-md flex-shrink-0"><ChevronLeft class="w-8 h-8"/></button>
        <!-- Обёртка фото + комментарий-накладка -->
        <div class="flex-1 max-h-[85vh] flex items-center justify-center p-4">
          <!-- pt/pl = пространство для комментария снаружи фото -->
          <div class="relative inline-block" :style="photoCommentMap[lightbox.photos[lightbox.index]] ? 'padding-top: 80px; padding-left: 90px;' : ''">
            <img :src="lightbox.photos[lightbox.index]" class="max-w-full max-h-[72vh] object-contain rounded-2xl shadow-2xl block" />

            <!-- Комментарий: правый нижний угол блока = точка (15%, 15%) фото (нахлёст 15%) -->
            <div v-if="photoCommentMap[lightbox.photos[lightbox.index]]"
                 class="absolute z-20"
                 style="bottom: 85%; right: 85%;">
              <div class="bg-white/95 backdrop-blur-sm border border-slate-200 rounded-2xl shadow-2xl overflow-hidden"
                   style="width: 260px;">
                <!-- Шапка блока в стиле приложения -->
                <div class="bg-blue-600 px-4 py-2.5 flex items-center gap-2">
                  <div class="w-2 h-2 rounded-full bg-white/80 flex-shrink-0"></div>
                  <span class="text-white text-[11px] font-black uppercase tracking-widest">Комментарий покупателя</span>
                </div>
                <!-- Текст -->
                <div class="px-4 py-3">
                  <p class="text-slate-800 text-sm font-medium leading-relaxed"
                     style="overflow: hidden; display: -webkit-box; -webkit-line-clamp: 8; -webkit-box-orient: vertical;">
                    {{ photoCommentMap[lightbox.photos[lightbox.index]] }}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
        <button @click="lightbox.index = (lightbox.index + 1) % lightbox.photos.length" class="p-4 bg-white/10 hover:bg-white/20 text-white rounded-2xl transition-colors backdrop-blur-md flex-shrink-0"><ChevronRight class="w-8 h-8"/></button>
      </div>
    </div>

    <!-- Модальное окно генерации отчёта -->
    <div v-if="showReportModal" class="fixed inset-0 z-[400] bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div class="bg-white rounded-3xl shadow-2xl w-full max-w-lg overflow-hidden animate-in zoom-in-95 duration-300">
        <div class="p-6 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
          <div>
            <h2 class="text-base font-black text-slate-900">Отчет по заводу</h2>
            <p class="text-xs text-slate-500 mt-1">Выберите период, заводы и настройки</p>
          </div>
          <button @click="showReportModal = false" class="p-2 hover:bg-slate-200 rounded-xl text-slate-500 transition-colors"><X class="w-5 h-5"/></button>
        </div>
        <div class="p-6 space-y-5 max-h-[75vh] overflow-y-auto custom-scroll">
          <!-- Период -->
          <div>
            <label class="text-[10px] font-black text-slate-400 uppercase block mb-2">Период</label>
            <div class="flex flex-wrap gap-2 mb-3">
              <button v-for="[val, label] in [['week','Неделя'],['month','Месяц'],['quarter','Квартал'],['custom','Выборочный']]" :key="val"
                      @click="reportForm.period_type = val"
                      :class="['px-3 py-1.5 text-xs font-bold rounded-xl border transition-all', reportForm.period_type === val ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200 hover:border-blue-400']">
                {{ label }}
              </button>
            </div>
            <div class="flex items-center gap-2">
              <div class="flex-1">
                <label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Начало</label>
                <input type="date" v-model="reportForm.start_date" :disabled="reportForm.period_type !== 'custom'" class="w-full p-2 border border-slate-200 rounded-xl text-xs outline-none focus:border-blue-400 disabled:bg-slate-50 disabled:text-slate-400" />
              </div>
              <span class="text-slate-400 font-bold mt-4">—</span>
              <div class="flex-1">
                <label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Конец</label>
                <input type="date" v-model="reportForm.end_date" :disabled="reportForm.period_type !== 'custom'" class="w-full p-2 border border-slate-200 rounded-xl text-xs outline-none focus:border-blue-400 disabled:bg-slate-50 disabled:text-slate-400" />
              </div>
            </div>
          </div>

          <!-- Заводы (мультиселект) -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <label class="text-[10px] font-black text-slate-400 uppercase">Заводы <span class="text-slate-300 normal-case font-normal">(пусто = все)</span></label>
              <button v-if="reportForm.factories.length > 0" @click="reportForm.factories = []" class="text-[10px] text-red-400 hover:text-red-600 font-bold">Сбросить</button>
            </div>
            <input v-model="reportFactorySearch" type="text" placeholder="Поиск завода..." class="w-full p-2 mb-2 border border-slate-200 rounded-xl text-xs outline-none focus:border-blue-400" />
            <div class="max-h-36 overflow-y-auto border border-slate-200 rounded-xl bg-slate-50/50 custom-scroll">
              <label v-for="f in filteredReportFactories" :key="f"
                     :class="['flex items-center gap-2.5 px-3 py-2 hover:bg-white cursor-pointer transition-colors', reportForm.factories.includes(f) ? 'bg-blue-50' : '']">
                <input type="checkbox" :value="f" v-model="reportForm.factories" class="rounded accent-blue-600 flex-shrink-0" />
                <span :class="['text-xs font-medium', reportForm.factories.includes(f) ? 'text-blue-700 font-bold' : 'text-slate-700']">{{ f }}</span>
              </label>
              <div v-if="filteredReportFactories.length === 0" class="px-3 py-3 text-xs text-slate-400 text-center">Не найдено</div>
            </div>
            <div v-if="reportForm.factories.length > 0" class="flex flex-wrap gap-1.5 mt-2">
              <span v-for="f in reportForm.factories" :key="f"
                    class="flex items-center gap-1 bg-blue-100 text-blue-700 text-[10px] font-bold px-2 py-0.5 rounded-lg">
                {{ f }}
                <button @click="reportForm.factories = reportForm.factories.filter(x => x !== f)" class="hover:text-red-500 transition-colors">×</button>
              </span>
            </div>
          </div>

          <!-- Тумблеры -->
          <div>
            <label class="text-[10px] font-black text-slate-400 uppercase block mb-3">Дополнительные колонки</label>
            <div class="flex flex-col gap-3">
              <label class="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-200 cursor-pointer hover:bg-blue-50/50 transition-colors">
                <div>
                  <div class="text-xs font-bold text-slate-700">Контейнеры</div>
                  <div class="text-[10px] text-slate-400 mt-0.5">Список контейнеров по каждому артикулу</div>
                </div>
                <div :class="['relative w-10 h-5 rounded-full transition-colors flex-shrink-0', reportForm.include_containers ? 'bg-blue-600' : 'bg-slate-300']"
                     @click.prevent="reportForm.include_containers = !reportForm.include_containers">
                  <div :class="['absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform', reportForm.include_containers ? 'translate-x-5' : 'translate-x-0.5']"></div>
                </div>
              </label>
              <label class="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-200 cursor-pointer hover:bg-blue-50/50 transition-colors">
                <div>
                  <div class="text-xs font-bold text-slate-700">Сумма издержек</div>
                  <div class="text-[10px] text-slate-400 mt-0.5">Издержки = брак × себестоимость из wb_cogs</div>
                </div>
                <div :class="['relative w-10 h-5 rounded-full transition-colors flex-shrink-0', reportForm.include_costs ? 'bg-blue-600' : 'bg-slate-300']"
                     @click.prevent="reportForm.include_costs = !reportForm.include_costs">
                  <div :class="['absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform', reportForm.include_costs ? 'translate-x-5' : 'translate-x-0.5']"></div>
                </div>
              </label>
            </div>
          </div>
        </div>
        <div class="px-6 pb-6 pt-3 flex gap-3 justify-end border-t border-slate-100">
          <button @click="showReportModal = false" class="px-4 py-2 text-sm font-bold text-slate-600 hover:bg-slate-100 rounded-xl transition-colors">Отмена</button>
          <button @click="downloadReport" :disabled="isReportGenerating"
                  :class="['flex items-center gap-2 px-5 py-2 bg-blue-600 text-white text-sm font-bold rounded-xl transition-all', isReportGenerating ? 'opacity-70 cursor-wait' : 'hover:bg-blue-700 active:scale-95']">
            <Download class="w-4 h-4" />
            {{ isReportGenerating ? 'Генерация...' : 'Скачать Excel' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Модальное окно критических товаров -->
    <div v-if="criticalPopup.isOpen" class="fixed inset-0 z-[350] bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200" @click="criticalPopup.isOpen = false">
      <div class="bg-white rounded-3xl shadow-2xl w-full max-w-2xl overflow-hidden animate-in zoom-in-95 duration-300" @click.stop>
        <div class="p-6 border-b border-slate-100 flex justify-between items-center bg-slate-50">
          <div>
            <div class="text-xs font-black text-slate-400 uppercase tracking-widest mb-1">Группа {{ criticalPopup.group }}</div>
            <h2 class="text-xl font-black text-slate-900">Критические товары</h2>
            <p class="text-sm text-slate-500 mt-1">SKU с уровнем брака выше нормы (&gt;10 000 PPM)</p>
          </div>
          <button @click="criticalPopup.isOpen = false" class="p-2 hover:bg-slate-200 rounded-xl text-slate-500 transition-colors"><X class="w-5 h-5"/></button>
        </div>
        <div class="p-6 space-y-6 max-h-[60vh] overflow-y-auto custom-scroll" v-if="criticalPopup.group">
          <!-- Текущие критические -->
          <div v-if="groupMetrics[criticalPopup.group].badSkus.length > 0">
            <div class="text-xs font-black text-slate-500 uppercase tracking-widest mb-3">Критические сейчас</div>
            <div class="space-y-2">
              <div v-for="item in groupMetrics[criticalPopup.group].badSkus" :key="item.sku"
                   class="flex items-center justify-between bg-red-50 border border-red-100 rounded-2xl px-5 py-3">
                <span class="font-bold text-slate-800 text-sm">{{ item.sku }}</span>
                <span class="text-sm font-black text-red-600 bg-red-100 px-3 py-1 rounded-lg">{{ item.ppm.toLocaleString() }} PPM</span>
              </div>
            </div>
          </div>
          <div v-else class="text-center py-6 text-slate-400 font-bold">Нет критических товаров</div>

          <!-- Стали критическими -->
          <div v-if="groupMetrics[criticalPopup.group].entered.length > 0" class="border-t border-slate-100 pt-5">
            <div class="text-xs font-black text-red-500 uppercase tracking-widest mb-3">⚠ Стали критическими (новые)</div>
            <div class="space-y-2">
              <div v-for="sku in groupMetrics[criticalPopup.group].entered" :key="sku"
                   class="flex items-center gap-3 bg-red-50 border border-red-200 rounded-2xl px-5 py-3">
                <span class="w-2 h-2 rounded-full bg-red-500 flex-shrink-0"></span>
                <span class="font-bold text-slate-800 text-sm">{{ sku }}</span>
              </div>
            </div>
          </div>

          <!-- Вышли из критических -->
          <div v-if="groupMetrics[criticalPopup.group].left.length > 0" class="border-t border-slate-100 pt-5">
            <div class="text-xs font-black text-emerald-600 uppercase tracking-widest mb-3">✓ Вышли из критических</div>
            <div class="space-y-2">
              <div v-for="sku in groupMetrics[criticalPopup.group].left" :key="sku"
                   class="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-2xl px-5 py-3">
                <span class="w-2 h-2 rounded-full bg-emerald-500 flex-shrink-0"></span>
                <span class="font-bold text-slate-800 text-sm">{{ sku }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-stripe {
  background: repeating-linear-gradient(-45deg, #fff 0px, #fff 15px, #fef9c3 15px, #fef9c3 95px) !important;
}
.custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>