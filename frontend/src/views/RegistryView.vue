<script setup>
import { ref, computed, onMounted } from 'vue'
import { Download, X, Ban, Search, SlidersHorizontal, ChevronDown, Plus } from 'lucide-vue-next'
import { apiFetch } from '../api'

// ── Данные ────────────────────────────────────────────────────────────────────
const logs = ref([])
const manualClaims = ref([])
const loading = ref(true)
const activeTab = ref('acts')
const currentUserRole = computed(() => localStorage.getItem('role') || '')

// ── Поиск и фильтры ───────────────────────────────────────────────────────────
const search = ref('')
const showFilters = ref(false)
const filters = ref({ sku: '', supplier: '', claim_status: '', send_status: '', factory_type: '', abc_group: '' })
const expandedId = ref(null)

// ── Ручные обращения ──────────────────────────────────────────────────────────
const showAddManual = ref(false)
const newManual = ref({ send_date: '', send_text: '', invoice_ref: '', factory_name: '', container_num: '', manual_status: '', who_sent: '' })

const MANUAL_STATUS_OPTIONS = ['На проверке', 'Отправлено', 'Прочитано', 'Отвечено', 'Закрыто']

// Метаданные для автодополнения
const claimsMetadata = ref({ factories: [], invoices: [], containers: [] })
const metaSearch = ref({ factory: '', invoice: '', container: '' })
const metaDropdown = ref({ factory: false, invoice: false, container: false })

const filteredMeta = (field, query) => {
  const list = claimsMetadata.value[field === 'invoice' ? 'invoices' : field === 'container' ? 'containers' : 'factories'] || []
  const q = (query || '').toLowerCase()
  return q ? list.filter(v => v.toLowerCase().includes(q)).slice(0, 40) : list.slice(0, 40)
}
const setMetaField = (fieldName, value) => {
  newManual.value[fieldName] = value
  const key = fieldName === 'factory_name' ? 'factory' : fieldName === 'invoice_ref' ? 'invoice' : 'container'
  metaSearch.value[key] = value
  metaDropdown.value[key] = false
}

const manualStats = computed(() => {
  const total = manualClaims.value.length
  const replied = manualClaims.value.filter(c => c.reply_text || c.reply_date).length
  const sent = manualClaims.value.filter(c => c.send_date && !(c.reply_text || c.reply_date)).length
  return { total, sent, replied }
})

// ── Варианты выпадающих списков ───────────────────────────────────────────────
const MONTH_NAMES = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
const INITIATOR_OPTIONS = ['Wildberries','Ozone','Yandex Market','Склад ФФ','Прочее']
const STAGE_OPTIONS = ['Опытный образец','Установочная партия','Серийная партия']
const REPEATABILITY_OPTIONS = ['Первый','Повторный']  // п.3: "Первичный" → "Первый"
const CLAIM_STATUS_OPTIONS = ['На проверке','Принята','Отклонена','Отменена']
const SEND_STATUS_OPTIONS = ['Не отправлен','Отправлен','Доставлен','Прочитан']
const FACTORY_TYPE_OPTIONS = ['завод','не завод']
const OBJECT_OPTIONS = ['Товар','Буклет','Упаковка внутренняя','Упаковка наружная']
const PARAMS_OPTIONS = [
  'Ассортиментные характеристики','Технические характеристики материала',
  'Габаритные характеристики','Характеристики производственных процессов',
  'Функциональные характеристики','Информационно-графические характеристики'
]
const DEVIATION_OPTIONS = [
  'Недостача','Излишек','Пересортица','Неполный перечень сопроводительной документации',
  'Неверный материал','Повреждения поверхности','Загрязнение','Деформация',
  'Отклонения в конструкции','Неверный вес','Неверный размер','Неверная форма',
  'Неверный объем','Повреждения отливки','Повреждения в сварке',
  'Повреждения в штамповке/резке','Повреждения ЛКП','Повреждения сборки',
  'Несобираемость','Неработоспособность','Неисправность механизма','Неустойчивость',
  'Ненадежность соединения','Орфографические ошибки','Перекосы',
  'Отклонение в нанесении','Отклонение в содержании','Прочее'
]

// ── п.6: Формат даты ДД.ММ.ГГГГ ──────────────────────────────────────────────
const formatRuDate = (d) => {
  if (!d) return '—'
  const s = String(d).trim()
  if (s.includes('.') && s.length >= 8) return s  // уже в нужном формате
  const parts = s.split('-')
  if (parts.length === 3) return `${parts[2]}.${parts[1]}.${parts[0]}`
  return s
}

// ── п.5: Отчетный месяц — нормализация хранимого значения ────────────────────
// Приводим к виду "Месяц ГГГГ", нормализуем старые значения
const normalizeMonth = (v) => {
  if (!v) return ''
  // Уже в нужном виде "Июнь 2026"
  if (/^[А-ЯЁ][а-яё]+\s+\d{4}$/.test(v.trim())) return v.trim()
  // "июнь 2026" → "Июнь 2026"
  if (/^[а-яё]+\s+\d{4}$/.test(v.trim())) return v.trim().replace(/^./, c => c.toUpperCase())
  // Просто название месяца без года — оставляем как есть
  return v
}

// ── Ресайз колонок ────────────────────────────────────────────────────────────
const COL_WIDTHS_KEY = 'registry_col_widths_v2'
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

// ── Загрузка ──────────────────────────────────────────────────────────────────
const fetchAll = async () => {
  loading.value = true
  try {
    const [r1, r2] = await Promise.all([
      apiFetch('/api/v1/analytics/claim-logs'),
      apiFetch('/api/v1/analytics/manual-claims')
    ])
    if (r1.ok) {
      const data = (await r1.json()).data || []
      // Авто-повторяемость
      const skuMinId = {}
      data.forEach(l => {
        if (l.status !== 'Аннулирован') {
          if (!(l.sku in skuMinId) || l.id < skuMinId[l.sku]) skuMinId[l.sku] = l.id
        }
      })
      logs.value = data.map(log => {
        // п.3: миграция "Первичный" → "Первый"
        if (log.repeatability === 'Первичный') {
          log.repeatability = 'Первый'
          saveField(log, 'repeatability')
        } else if (!log.repeatability && log.status !== 'Аннулирован') {
          log.repeatability = (skuMinId[log.sku] !== undefined && skuMinId[log.sku] < log.id) ? 'Повторный' : 'Первый'
          saveField(log, 'repeatability')
        }
        // п.4: восстанавливаем все инвойсы из pdf_payload
        if (log.pdf_payload) {
          try {
            const payload = typeof log.pdf_payload === 'string' ? JSON.parse(log.pdf_payload) : log.pdf_payload
            const allInvoices = (payload.invoices_list || [])
              .map(i => i.invoice).filter(i => i && i !== '—')
            if (allInvoices.length > 1) {
              const joined = allInvoices.join(', ')
              if (log.invoice_ref !== joined) {
                log.invoice_ref = joined
                saveField(log, 'invoice_ref')
              }
            }
          } catch {}
        }
        // п.5: нормализация отчётного месяца
        const nm = normalizeMonth(log.report_month)
        if (nm && nm !== log.report_month) {
          log.report_month = nm
          saveField(log, 'report_month')
        }
        return log
      })
    }
    if (r2.ok) manualClaims.value = (await r2.json()).data || []
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}
const fetchMetadata = async () => {
  try {
    const res = await apiFetch('/api/v1/analytics/claims-metadata')
    if (res.ok) claimsMetadata.value = (await res.json()).data || { factories: [], invoices: [], containers: [] }
  } catch {}
}

const openAddManualForm = () => {
  showAddManual.value = !showAddManual.value
  if (showAddManual.value) {
    newManual.value.who_sent = localStorage.getItem('username') || ''
    metaSearch.value = { factory: newManual.value.factory_name || '', invoice: newManual.value.invoice_ref || '', container: newManual.value.container_num || '' }
  }
}

onMounted(() => { fetchAll(); fetchMetadata() })

// ── Фильтр ────────────────────────────────────────────────────────────────────
const filtered = computed(() => {
  let data = logs.value
  const q = search.value.toLowerCase()
  if (q) data = data.filter(r =>
    [r.act_number, r.sku, r.supplier, r.deviation, r.claim_status, r.send_status, r.report_month]
      .some(v => String(v || '').toLowerCase().includes(q))
  )
  if (filters.value.sku) data = data.filter(r => String(r.sku || '').toLowerCase().includes(filters.value.sku.toLowerCase()))
  if (filters.value.supplier) data = data.filter(r => String(r.supplier || '').toLowerCase().includes(filters.value.supplier.toLowerCase()))
  if (filters.value.claim_status) data = data.filter(r => r.claim_status === filters.value.claim_status)
  if (filters.value.send_status) data = data.filter(r => r.send_status === filters.value.send_status)
  if (filters.value.factory_type) data = data.filter(r => r.factory_type === filters.value.factory_type)
  if (filters.value.abc_group) data = data.filter(r => String(r.abc_group || '').toUpperCase() === filters.value.abc_group.toUpperCase())
  return data
})
const activeFiltersCount = computed(() => Object.values(filters.value).filter(v => v).length)
const resetFilters = () => { Object.keys(filters.value).forEach(k => { filters.value[k] = '' }) }

// ── Lock-логика ───────────────────────────────────────────────────────────────
const isRowLocked = (log) => log.send_status === 'Отправлен'
const isRowFullyLocked = (log) => isRowLocked(log) && !!(log.estimated_improvement_date)
const isRowPartialDone = (log) => isRowLocked(log) && !!(log.factory_reply && log.factory_reply.trim())
const isRowDone = (log) => isRowPartialDone(log) && !!(log.correction_invoice && log.correction_invoice.trim()) && !!(log.estimated_improvement_date)
const rowBgClass = (log) => {
  if (isRowDone(log)) return 'bg-amber-100'
  if (isRowPartialDone(log)) return 'bg-yellow-100'
  if (isRowLocked(log)) return 'bg-green-50'
  return 'bg-white'
}

// ── Multi-select helpers ──────────────────────────────────────────────────────
const parseMulti = (v) => v ? String(v).split('\n').map(s => s.trim()).filter(Boolean) : []
const toggleMulti = (log, field, val) => {
  const cur = parseMulti(log[field])
  const idx = cur.indexOf(val)
  if (idx >= 0) cur.splice(idx, 1); else cur.push(val)
  log[field] = cur.join('\n')
}
const isSelected = (log, field, val) => parseMulti(log[field]).includes(val)
const openDropdowns = ref({})
const toggleDropdown = (key) => { openDropdowns.value = { ...openDropdowns.value, [key]: !openDropdowns.value[key] } }
const closeDropdown = (key) => { openDropdowns.value[key] = false }

// ── Сохранение ────────────────────────────────────────────────────────────────
const saveField = async (log, ...fields) => {
  try {
    const body = {}
    fields.forEach(f => { body[f] = log[f] || null })
    await apiFetch(`/api/v1/analytics/claim-logs/${log.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    })
  } catch (e) { console.error(e) }
}

// ── Смена статуса отправки: валидация + авто-дата ─────────────────────────────
const handleSendStatusChange = async (log) => {
  if (log.send_status === 'Отправлен') {
    const missing = []
    if (!log.ra_date) missing.push('Дата РА')
    if (!log.invoice_ref) missing.push('№ инвойса')
    if (!log.who_sent) missing.push('Кто отправил')
    if (missing.length > 0) {
      alert(`Невозможно изменить статус. Заполните: ${missing.join(', ')}`)
      log.send_status = 'Не отправлен'
      return
    }
    if (!log.send_date) log.send_date = new Date().toISOString().split('T')[0]
  }
  await saveField(log, 'send_status', 'send_date')
}

// ── Действия ──────────────────────────────────────────────────────────────────
const cancelLog = async (id) => {
  if (!confirm('Аннулировать акт?')) return
  try { await apiFetch(`/api/v1/analytics/claim-logs/${id}/cancel`, { method: 'PUT' }); fetchAll() }
  catch (e) { alert(e.message || 'Ошибка') }
}
const deleteLog = async (id) => {
  if (!confirm('Удалить акт полностью? Необратимо.')) return
  try { await apiFetch(`/api/v1/analytics/claim-logs/${id}`, { method: 'DELETE' }); fetchAll() }
  catch (e) { alert(e.message || 'Ошибка') }
}
const unlockLog = async (id) => {
  if (!confirm('Разблокировать акт? Статус отправки сбросится на "Не отправлен".')) return
  try { await apiFetch(`/api/v1/analytics/claim-logs/${id}/unlock`, { method: 'PUT' }); fetchAll() }
  catch (e) { alert(e.message || 'Ошибка разблокировки') }
}
const redownload = async (log) => {
  if (!log.pdf_payload) return alert('Данные акта не сохранились.')
  try {
    const payload = typeof log.pdf_payload === 'string' ? JSON.parse(log.pdf_payload) : log.pdf_payload
    payload.is_redownload = true; payload.existing_act_number = log.act_number
    const res = await apiFetch('/api/v1/analytics/export-claim', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const blob = await res.blob(); const link = document.createElement('a')
    link.href = URL.createObjectURL(blob); link.download = `${log.act_number}_${log.sku}.pdf`; link.click()
  } catch { alert('Не удалось скачать.') }
}

// ── Ручные обращения ──────────────────────────────────────────────────────────
const submitManual = async () => {
  if (!newManual.value.send_date || !newManual.value.send_text) return alert('Заполните дату и текст')
  try {
    await apiFetch('/api/v1/analytics/manual-claims', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newManual.value) })
    showAddManual.value = false
    newManual.value = { send_date: '', send_text: '', invoice_ref: '', factory_name: '', container_num: '', manual_status: '', who_sent: '' }
    metaSearch.value = { factory: '', invoice: '', container: '' }
    fetchAll()
  } catch (e) { console.error(e) }
}
const saveManual = async (claim) => {
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

// ── CSV-экспорт ───────────────────────────────────────────────────────────────
const q = (v) => `"${String(v || '').replace(/"/g, '""')}"`
const exportCsv = () => {
  const headers = ['№ РА','Отч. месяц','Инициатор','Артикул','Наименование','Кол-во','Инвойсы','Завод','Дата РА',
    '№ партии','Дата пр-ва','Дата продажи','Стадия','Объект','Контр. параметры','Отклонение','Описание отклонения',
    'Повторяемость','Ст. претензии','Причина','ABC','Завод/не завод','Кто отправил','Ст. отправки','Дата отправки',
    'Чат','Дата ответа','Ответ завода','Комментарии','Ст. акта']
  let csv = '﻿' + headers.map(q).join(';') + '\n'
  logs.value.forEach(r => {
    csv += [q(r.act_number),q(r.report_month),q(r.initiator),q(r.sku),q(r.product_name),
      r.qty||r.defects_count||'',q(r.invoice_ref),q(r.supplier),r.ra_date||'',q(r.batch_num),
      r.production_date||'',r.sale_date||'',q(r.stage),q(r.object_type),q(r.controlled_params),
      q(r.deviation),q(r.deviation_desc),q(r.repeatability),q(r.claim_status),q(r.deviation_cause),
      q(r.abc_group),q(r.factory_type),q(r.who_sent),q(r.send_status),r.send_date||'',q(r.chat_name),
      r.factory_reply_date||'',q(r.factory_reply),q(r.comments),q(r.status)].join(';') + '\n'
  })
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob)
  link.download = `Реестр_РА_${new Date().toLocaleDateString('ru')}.csv`; link.click()
}

// ── Стили ─────────────────────────────────────────────────────────────────────
const inp = 'w-full text-xs p-1 border border-transparent hover:border-slate-300 rounded-md focus:border-blue-400 focus:bg-white bg-transparent outline-none transition-colors'
const inpLocked = 'text-xs text-slate-600'
const statusCls = (s) => s === 'Активен' ? 'bg-green-100 text-green-700' : s === 'Аннулирован' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-500'
const sendCls = (s) => !s || s === 'Не отправлен' ? 'bg-slate-100 text-slate-500' : s === 'Отправлен' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
const claimCls = (s) => s === 'Принята' ? 'bg-green-100 text-green-700' : s === 'Отклонена' || s === 'Отменена' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-500'
</script>

<template>
  <div class="h-screen flex flex-col bg-slate-50 font-sans antialiased overflow-hidden">

    <!-- ── Шапка ─────────────────────────────────────────────────────────────── -->
    <div class="flex-shrink-0 bg-white border-b border-slate-200 px-5 py-3 flex items-center justify-between z-30 shadow-sm">
      <div class="flex items-center gap-4">
        <h1 class="text-base font-black text-slate-900">Реестр претензий</h1>
        <div class="flex bg-slate-100 p-0.5 rounded-xl">
          <button @click="activeTab='acts'" :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all', activeTab==='acts' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']">
            Рекламационные акты <span class="ml-1 text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">{{ logs.length }}</span>
          </button>
          <button @click="activeTab='manual'" :class="['px-3 py-1.5 text-xs font-bold rounded-lg transition-all', activeTab==='manual' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700']">
            Ручные обращения <span class="ml-1 text-[10px] bg-slate-200 text-slate-600 px-1.5 py-0.5 rounded-full">{{ manualClaims.length }}</span>
          </button>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <div class="relative">
          <Search class="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input v-model="search" placeholder="Поиск..." class="pl-8 pr-3 py-1.5 text-xs border border-slate-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 w-56 transition-all" />
        </div>
        <div class="relative" v-if="activeTab==='acts'">
          <button @click="showFilters = !showFilters"
                  :class="['flex items-center gap-1.5 px-3 py-1.5 border rounded-xl text-xs font-bold transition-all', activeFiltersCount > 0 ? 'bg-blue-600 text-white border-blue-600' : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50']">
            <SlidersHorizontal class="w-3.5 h-3.5" /> Фильтры
            <span v-if="activeFiltersCount > 0" class="bg-white text-blue-600 rounded-full w-4 h-4 flex items-center justify-center text-[9px] font-black">{{ activeFiltersCount }}</span>
          </button>
          <div v-if="showFilters" class="absolute right-0 top-full mt-2 bg-white border border-slate-200 rounded-2xl shadow-2xl z-50 p-4 w-80">
            <div class="flex justify-between items-center mb-3">
              <span class="text-xs font-black text-slate-700 uppercase tracking-wider">Фильтры</span>
              <button @click="resetFilters(); showFilters=false" class="text-[10px] text-red-500 hover:text-red-700 font-bold">Сбросить</button>
            </div>
            <div class="space-y-2.5">
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Артикул</label><input v-model="filters.sku" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400" placeholder="Поиск по артикулу..." /></div>
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Завод</label><input v-model="filters.supplier" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400" placeholder="Название завода..." /></div>
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Группа ABC</label><select v-model="filters.abc_group" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400 bg-white"><option value="">Все</option><option>A</option><option>B</option><option>C</option></select></div>
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Статус претензии</label><select v-model="filters.claim_status" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400 bg-white"><option value="">Все</option><option v-for="o in CLAIM_STATUS_OPTIONS" :key="o">{{ o }}</option></select></div>
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Статус отправки</label><select v-model="filters.send_status" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400 bg-white"><option value="">Все</option><option v-for="o in SEND_STATUS_OPTIONS" :key="o">{{ o }}</option></select></div>
              <div><label class="text-[10px] font-black text-slate-400 uppercase block mb-1">Завод/не завод</label><select v-model="filters.factory_type" class="w-full p-2 text-xs border border-slate-200 rounded-lg outline-none focus:border-blue-400 bg-white"><option value="">Все</option><option v-for="o in FACTORY_TYPE_OPTIONS" :key="o">{{ o }}</option></select></div>
            </div>
            <div class="mt-3 pt-3 border-t border-slate-100 text-center text-[10px] text-slate-400">Показано: {{ filtered.length }} из {{ logs.length }}</div>
          </div>
        </div>
        <button v-if="activeTab==='acts'" @click="exportCsv" class="flex items-center gap-1.5 px-3 py-1.5 bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 rounded-xl font-bold text-xs transition-colors">
          <Download class="w-3.5 h-3.5" /> CSV
        </button>
        <button v-if="activeTab==='manual'" @click="openAddManualForm" class="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white hover:bg-blue-700 rounded-xl font-bold text-xs transition-colors">
          <Plus class="w-3.5 h-3.5" /> Новое обращение
        </button>
      </div>
    </div>

    <!-- ── Форма нового ручного обращения ─────────────────────────────────────── -->
    <div v-if="showAddManual && activeTab==='manual'" class="flex-shrink-0 bg-blue-50 border-b border-blue-100 px-5 py-3">
      <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2 mb-2">
        <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Дата отправки*</label><input type="date" v-model="newManual.send_date" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none" /></div>
        <!-- Кто отправил — авто -->
        <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Кто отправил</label><input v-model="newManual.who_sent" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none" placeholder="Авто из профиля" /></div>
        <!-- Завод — поиск -->
        <div class="relative">
          <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Завод</label>
          <input v-model="metaSearch.factory" @input="metaDropdown.factory = true; newManual.factory_name = metaSearch.factory" @focus="metaDropdown.factory = true"
                 class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400" placeholder="Поиск завода..." />
          <div v-if="metaDropdown.factory && filteredMeta('factory', metaSearch.factory).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-36 overflow-y-auto">
            <div v-for="v in filteredMeta('factory', metaSearch.factory)" :key="v" @mousedown.prevent="setMetaField('factory_name', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer">{{ v }}</div>
          </div>
        </div>
        <!-- Инвойс — поиск -->
        <div class="relative">
          <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">№ инвойса</label>
          <input v-model="metaSearch.invoice" @input="metaDropdown.invoice = true; newManual.invoice_ref = metaSearch.invoice" @focus="metaDropdown.invoice = true"
                 class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400 font-mono" placeholder="Поиск инвойса..." />
          <div v-if="metaDropdown.invoice && filteredMeta('invoice', metaSearch.invoice).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-36 overflow-y-auto">
            <div v-for="v in filteredMeta('invoice', metaSearch.invoice)" :key="v" @mousedown.prevent="setMetaField('invoice_ref', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer font-mono">{{ v }}</div>
          </div>
        </div>
        <!-- Контейнер — поиск -->
        <div class="relative">
          <label class="text-[10px] font-black text-blue-800 uppercase block mb-1">№ контейнера</label>
          <input v-model="metaSearch.container" @input="metaDropdown.container = true; newManual.container_num = metaSearch.container" @focus="metaDropdown.container = true"
                 class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none focus:border-blue-400 font-mono" placeholder="Поиск контейнера..." />
          <div v-if="metaDropdown.container && filteredMeta('container', metaSearch.container).length" class="absolute z-50 left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-xl max-h-36 overflow-y-auto">
            <div v-for="v in filteredMeta('container', metaSearch.container)" :key="v" @mousedown.prevent="setMetaField('container_num', v)" class="px-3 py-1.5 text-xs hover:bg-blue-50 cursor-pointer font-mono">{{ v }}</div>
          </div>
        </div>
        <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Статус</label>
          <select v-model="newManual.manual_status" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none">
            <option value="">—</option><option v-for="o in MANUAL_STATUS_OPTIONS" :key="o">{{ o }}</option>
          </select>
        </div>
        <div class="flex items-end gap-2">
          <button @click="submitManual" class="bg-blue-600 text-white px-3 py-2 rounded-lg font-bold text-xs hover:bg-blue-700 whitespace-nowrap">Создать</button>
          <button @click="showAddManual=false" class="px-2 py-2 rounded-lg font-bold text-xs hover:bg-slate-200 text-slate-500"><X class="w-4 h-4" /></button>
        </div>
      </div>
      <div><label class="text-[10px] font-black text-blue-800 uppercase block mb-1">Текст отправления*</label>
        <input v-model="newManual.send_text" class="w-full p-2 border border-slate-200 rounded-lg text-xs bg-white outline-none" placeholder="Текст обращения..." />
      </div>
    </div>

    <div v-if="loading" class="flex-1 flex items-center justify-center text-slate-400 font-bold animate-pulse">⚙️ Загрузка...</div>

    <!-- ── Таблица актов ──────────────────────────────────────────────────────── -->
    <div v-else-if="activeTab==='acts'" class="flex-1 overflow-auto">
      <table class="text-left border-collapse text-xs" style="min-width:2200px;width:100%;table-layout:fixed">
        <thead class="bg-slate-100 border-b-2 border-slate-300" style="position:sticky;top:0;z-index:20">
          <tr class="text-slate-500 uppercase font-black text-[9px] tracking-wider">

            <!-- Вспомогательный компонент resize-handle используется как slot внутри th -->
            <!-- col 0: № РА -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(0,90)" style="position:sticky;left:0;background:#f1f5f9;z-index:30">
              № РА
              <div class="resize-handle" @mousedown.stop="startResize($event,0)"><div class="resize-bar"></div></div>
            </th>
            <!-- col 1: Артикул -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(1,130)" style="position:sticky;left:90px;background:#f1f5f9;z-index:30">
              Артикул
              <div class="resize-handle" @mousedown.stop="startResize($event,1)"><div class="resize-bar"></div></div>
            </th>
            <!-- col 2 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(2,130)">Наименование<div class="resize-handle" @mousedown.stop="startResize($event,2)"><div class="resize-bar"></div></div></th>
            <!-- col 3: Дата РА -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(3,100)">Дата РА<div class="resize-handle" @mousedown.stop="startResize($event,3)"><div class="resize-bar"></div></div></th>
            <!-- col 4 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(4,120)">Завод<div class="resize-handle" @mousedown.stop="startResize($event,4)"><div class="resize-bar"></div></div></th>
            <!-- col 5 -->
            <th class="p-2.5 text-center relative select-none overflow-hidden" :style="getColStyle(5,55)">Кол-во<div class="resize-handle" @mousedown.stop="startResize($event,5)"><div class="resize-bar"></div></div></th>
            <!-- col 6 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(6,150)">Описание отклонения<div class="resize-handle" @mousedown.stop="startResize($event,6)"><div class="resize-bar"></div></div></th>
            <!-- col 7 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(7,120)">Комментарии<div class="resize-handle" @mousedown.stop="startResize($event,7)"><div class="resize-bar"></div></div></th>
            <!-- col 8: Отч. месяц -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(8,110)">Отч. месяц<div class="resize-handle" @mousedown.stop="startResize($event,8)"><div class="resize-bar"></div></div></th>
            <!-- col 9 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(9,90)">Инициатор<div class="resize-handle" @mousedown.stop="startResize($event,9)"><div class="resize-bar"></div></div></th>
            <!-- col 10 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(10,130)">Инвойсы<div class="resize-handle" @mousedown.stop="startResize($event,10)"><div class="resize-bar"></div></div></th>
            <!-- col 11 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(11,200)">Контр. параметры<div class="resize-handle" @mousedown.stop="startResize($event,11)"><div class="resize-bar"></div></div></th>
            <!-- col 12 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(12,200)">Отклонение<div class="resize-handle" @mousedown.stop="startResize($event,12)"><div class="resize-bar"></div></div></th>
            <!-- col 13 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(13,100)">Повторяемость<div class="resize-handle" @mousedown.stop="startResize($event,13)"><div class="resize-bar"></div></div></th>
            <!-- col 14 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(14,100)">Кто отправил<div class="resize-handle" @mousedown.stop="startResize($event,14)"><div class="resize-bar"></div></div></th>
            <!-- col 15: Статус отправки (первым) -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(15,115)">Статус отправки<div class="resize-handle" @mousedown.stop="startResize($event,15)"><div class="resize-bar"></div></div></th>
            <!-- col 16: Дата отправки -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(16,100)">Дата отправки<div class="resize-handle" @mousedown.stop="startResize($event,16)"><div class="resize-bar"></div></div></th>
            <!-- col 17 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(17,180)">Ответ завода<div class="resize-handle" @mousedown.stop="startResize($event,17)"><div class="resize-bar"></div></div></th>
            <!-- col 18 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(18,100)">Дата ответа<div class="resize-handle" @mousedown.stop="startResize($event,18)"><div class="resize-bar"></div></div></th>
            <!-- col 19 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(19,130)">Инвойс испр.<div class="resize-handle" @mousedown.stop="startResize($event,19)"><div class="resize-bar"></div></div></th>
            <!-- col 20 -->
            <th class="p-2.5 relative select-none overflow-hidden" :style="getColStyle(20,110)">Прим. дата улучш.<div class="resize-handle" @mousedown.stop="startResize($event,20)"><div class="resize-bar"></div></div></th>
            <!-- col 21: Действия (sticky right, без resize) -->
            <th class="p-2.5 text-center select-none" :style="getColStyle(21,110)" style="position:sticky;right:0;background:#f1f5f9;z-index:30">Действия</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <template v-for="(log, logIndex) in filtered" :key="log.id">
            <tr :class="['group transition-colors', isRowDone(log) ? 'bg-amber-100 hover:bg-amber-200/60' : isRowPartialDone(log) ? 'row-stripe' : isRowLocked(log) ? 'bg-green-50 hover:bg-green-100/60' : 'bg-white hover:bg-blue-50/30']"
                :style="isRowPartialDone(log) && !isRowDone(log) ? { backgroundPosition: `${(logIndex * 37) % 95}px 0` } : {}">

              <!-- 0: № РА -->
              <td class="p-2 overflow-hidden" :class="rowBgClass(log)" style="position:sticky;left:0;z-index:10">
                <div class="flex items-center gap-1 cursor-pointer" @click="expandedId = expandedId===log.id ? null : log.id">
                  <ChevronDown :class="['w-3 h-3 text-slate-400 flex-shrink-0 transition-transform', expandedId===log.id ? 'rotate-180 text-blue-600' : '']" />
                  <span class="font-black text-blue-700 text-xs hover:text-blue-900 leading-tight">{{ log.act_number }}</span>
                </div>
                <div class="text-[9px] text-slate-400 ml-4">{{ log.created_at }}</div>
              </td>

              <!-- 1: Артикул -->
              <td class="p-2 font-bold text-slate-800 overflow-hidden truncate" :class="rowBgClass(log)" style="position:sticky;left:90px;z-index:10">{{ log.sku }}</td>

              <!-- 2: Наименование -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ log.product_name || '—' }}</span>
                <input v-else v-model="log.product_name" @blur="saveField(log,'product_name')" :class="inp" placeholder="—" />
              </td>

              <!-- 3: Дата РА — формат ДД.ММ.ГГГГ -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ formatRuDate(log.ra_date) }}</span>
                <input v-else type="date" v-model="log.ra_date" @blur="saveField(log,'ra_date')" :class="inp" />
              </td>

              <!-- 4: Завод -->
              <td class="p-2 text-slate-600 font-medium text-[11px] overflow-hidden truncate">{{ log.supplier }}</td>

              <!-- 5: Кол-во -->
              <td class="p-2 text-center font-black text-red-500">{{ log.qty || log.defects_count }}</td>

              <!-- 6: Описание отклонения -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked + ' text-[11px] line-clamp-3 block'">{{ log.deviation_desc || '—' }}</span>
                <textarea v-else v-model="log.deviation_desc" @blur="saveField(log,'deviation_desc')" rows="2" :class="inp + ' resize-none focus:bg-white'" placeholder="—"></textarea>
              </td>

              <!-- 7: Комментарии -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked + ' text-[11px] line-clamp-2 block'">{{ log.comments || '—' }}</span>
                <textarea v-else v-model="log.comments" @blur="saveField(log,'comments')" rows="2" :class="inp + ' resize-none focus:bg-white'" placeholder="—"></textarea>
              </td>

              <!-- 8: Отч. месяц — текстовый input "Июнь 2026" -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ log.report_month || '—' }}</span>
                <input v-else v-model="log.report_month" @blur="saveField(log,'report_month')" :class="inp" placeholder="Июнь 2026" />
              </td>

              <!-- 9: Инициатор -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ log.initiator || '—' }}</span>
                <select v-else v-model="log.initiator" @change="saveField(log,'initiator')" :class="inp">
                  <option value="">—</option><option v-for="o in INITIATOR_OPTIONS" :key="o">{{ o }}</option>
                </select>
              </td>

              <!-- 10: Инвойсы -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked + ' whitespace-pre-wrap text-[11px]'">{{ (log.invoice_ref || '—').replace(/,\s*/g, '\n') }}</span>
                <input v-else v-model="log.invoice_ref" @blur="saveField(log,'invoice_ref')" :class="inp" placeholder="—" />
              </td>

              <!-- 11: Контр. параметры -->
              <td class="p-2 relative overflow-hidden" @click.stop>
                <div v-if="isRowLocked(log)" class="text-slate-600 text-[11px] leading-tight">
                  <span v-if="!parseMulti(log.controlled_params).length" class="text-slate-400">—</span>
                  <span v-for="v in parseMulti(log.controlled_params)" :key="v" class="block bg-slate-100 rounded px-1 mb-0.5">{{ v }}</span>
                </div>
                <div v-else>
                  <button @click="toggleDropdown(`params_${log.id}`)"
                          class="w-full text-left text-[11px] p-1 border border-transparent hover:border-slate-300 rounded-md flex items-center justify-between">
                    <span class="text-slate-600 whitespace-pre-wrap leading-tight text-left">{{ parseMulti(log.controlled_params).join('\n') || '—' }}</span>
                    <ChevronDown class="w-3 h-3 text-slate-400 flex-shrink-0 ml-1" />
                  </button>
                  <div v-if="openDropdowns[`params_${log.id}`]" class="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-50 py-1 w-72" v-click-outside="() => closeDropdown(`params_${log.id}`)">
                    <label v-for="o in PARAMS_OPTIONS" :key="o" class="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-xs">
                      <input type="checkbox" :checked="isSelected(log,'controlled_params',o)" @change="toggleMulti(log,'controlled_params',o); saveField(log,'controlled_params')" class="rounded accent-blue-600" />{{ o }}
                    </label>
                  </div>
                </div>
              </td>

              <!-- 12: Отклонение -->
              <td class="p-2 relative overflow-hidden" @click.stop>
                <div v-if="isRowLocked(log)" class="text-slate-600 text-[11px] leading-tight">
                  <span v-if="!parseMulti(log.deviation).length" class="text-slate-400">—</span>
                  <span v-for="v in parseMulti(log.deviation)" :key="v" class="block bg-red-50 text-red-700 rounded px-1 mb-0.5">{{ v }}</span>
                </div>
                <div v-else>
                  <button @click="toggleDropdown(`dev_${log.id}`)"
                          class="w-full text-left text-[11px] p-1 border border-transparent hover:border-slate-300 rounded-md flex items-center justify-between">
                    <span class="text-slate-600 whitespace-pre-wrap leading-tight text-left">{{ parseMulti(log.deviation).join('\n') || '—' }}</span>
                    <ChevronDown class="w-3 h-3 text-slate-400 flex-shrink-0 ml-1" />
                  </button>
                  <div v-if="openDropdowns[`dev_${log.id}`]" class="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-50 py-1 w-64 max-h-72 overflow-y-auto custom-scroll" v-click-outside="() => closeDropdown(`dev_${log.id}`)">
                    <label v-for="o in DEVIATION_OPTIONS" :key="o" class="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-xs">
                      <input type="checkbox" :checked="isSelected(log,'deviation',o)" @change="toggleMulti(log,'deviation',o); saveField(log,'deviation')" class="rounded accent-blue-600" />{{ o }}
                    </label>
                  </div>
                </div>
              </td>

              <!-- 13: Повторяемость -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ log.repeatability || '—' }}</span>
                <select v-else v-model="log.repeatability" @change="saveField(log,'repeatability')" :class="inp">
                  <option value="">—</option><option v-for="o in REPEATABILITY_OPTIONS" :key="o">{{ o }}</option>
                </select>
              </td>

              <!-- 14: Кто отправил -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked">{{ log.who_sent || '—' }}</span>
                <input v-else v-model="log.who_sent" @blur="saveField(log,'who_sent')" :class="inp" placeholder="—" />
              </td>

              <!-- 15: Статус отправки -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="['px-2 py-0.5 rounded text-[9px] font-black', sendCls(log.send_status)]">{{ log.send_status }}</span>
                <select v-else v-model="log.send_status" @change="handleSendStatusChange(log)" :class="inp">
                  <option value="">—</option><option v-for="o in SEND_STATUS_OPTIONS" :key="o">{{ o }}</option>
                </select>
              </td>

              <!-- 16: Дата отправки -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowLocked(log)" :class="inpLocked + ' font-bold text-green-700'">{{ formatRuDate(log.send_date) }}</span>
                <input v-else type="date" v-model="log.send_date" @blur="saveField(log,'send_date')" :class="inp" />
              </td>

              <!-- 17: Ответ завода -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowFullyLocked(log)" :class="inpLocked + ' text-[11px] line-clamp-3 block'">{{ log.factory_reply || '—' }}</span>
                <textarea v-else v-model="log.factory_reply" @blur="saveField(log,'factory_reply')" rows="2" :class="inp + ' resize-none focus:bg-white'" placeholder="Ответ завода..."></textarea>
              </td>

              <!-- 18: Дата ответа -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowFullyLocked(log)" :class="inpLocked">{{ formatRuDate(log.factory_reply_date) }}</span>
                <input v-else type="date" v-model="log.factory_reply_date" @blur="saveField(log,'factory_reply_date')" :class="inp" />
              </td>

              <!-- 19: Примерный инвойс -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowFullyLocked(log)" :class="inpLocked">{{ log.correction_invoice || '—' }}</span>
                <input v-else v-model="log.correction_invoice" @blur="saveField(log,'correction_invoice')" :class="inp" placeholder="Номер инвойса..." />
              </td>

              <!-- 20: Примерная дата улучшения (последнее поле — заполнение закрывает строку) -->
              <td class="p-2 overflow-hidden">
                <span v-if="isRowFullyLocked(log)" :class="inpLocked + ' font-bold'">{{ formatRuDate(log.estimated_improvement_date) }}</span>
                <input v-else type="date" v-model="log.estimated_improvement_date" @blur="saveField(log,'estimated_improvement_date')" :class="inp" />
              </td>

              <!-- 21: Действия (sticky right) -->
              <td class="p-2 overflow-hidden" :class="rowBgClass(log)" style="position:sticky;right:0;z-index:10">
                <div class="flex flex-col gap-1">
                  <button @click="redownload(log)" :disabled="!log.pdf_payload"
                          class="justify-center text-blue-600 hover:bg-blue-600 hover:text-white border border-blue-200 bg-blue-50 p-1 rounded-lg flex items-center gap-1 text-[10px] font-bold disabled:opacity-30 transition-colors">
                    <Download class="w-3 h-3" /> PDF
                  </button>
                  <button v-if="log.status==='Активен' && !isRowLocked(log)" @click="cancelLog(log.id)"
                          class="justify-center text-orange-500 hover:bg-orange-500 hover:text-white border border-orange-200 bg-orange-50 p-1 rounded-lg flex items-center gap-1 text-[10px] font-bold transition-colors">
                    <Ban class="w-3 h-3" /> Отмена
                  </button>
                  <div v-else-if="isRowLocked(log) && !isRowFullyLocked(log)" class="text-[9px] text-green-700 font-bold text-center bg-green-100 rounded p-1">🔒 Отправлен</div>
                  <div v-else-if="isRowFullyLocked(log)" class="text-[9px] text-amber-700 font-bold text-center bg-amber-100 rounded p-1">🏁 Завершён</div>
                  <button v-if="currentUserRole==='admin' && isRowLocked(log)" @click="unlockLog(log.id)"
                          class="justify-center text-yellow-700 hover:bg-yellow-500 hover:text-white border border-yellow-200 bg-yellow-50 p-1 rounded-lg flex items-center gap-1 text-[10px] font-bold transition-colors">
                    🔓 Разблок.
                  </button>
                  <button v-if="currentUserRole==='admin'" @click="deleteLog(log.id)"
                          class="justify-center text-red-500 hover:bg-red-500 hover:text-white border border-red-200 bg-red-50 p-1 rounded-lg flex items-center gap-1 text-[10px] font-bold transition-colors">
                    <X class="w-3 h-3" /> Удалить
                  </button>
                </div>
              </td>
            </tr>

            <!-- ── Раскрытая строка ──────────────────────────────────────────── -->
            <tr v-if="expandedId===log.id" class="bg-indigo-50/40 border-b border-indigo-100">
              <td colspan="22" class="px-5 py-4">
                <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3 text-xs">

                  <!-- Статус акта (п.1: перенесён из основной строки) -->
                  <div>
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Статус акта</div>
                    <span :class="['px-2 py-0.5 rounded text-[9px] font-black', statusCls(log.status)]">{{ log.status }}</span>
                  </div>

                  <!-- Статус претензии -->
                  <div>
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Статус претензии</div>
                    <span v-if="isRowLocked(log)" :class="['px-2 py-0.5 rounded text-[9px] font-black', claimCls(log.claim_status)]">{{ log.claim_status || '—' }}</span>
                    <select v-else v-model="log.claim_status" @change="saveField(log,'claim_status')"
                      class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 text-xs max-w-[160px] w-full">
                      <option value="">—</option><option v-for="o in CLAIM_STATUS_OPTIONS" :key="o">{{ o }}</option>
                    </select>
                  </div>

                  <!-- Объект (мультиселект) -->
                  <div class="relative" @click.stop>
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Объект</div>
                    <div v-if="isRowLocked(log)" class="text-slate-600 text-[11px] leading-tight">
                      <span v-for="v in parseMulti(log.object_type)" :key="v" class="block bg-slate-100 rounded px-1 mb-0.5">{{ v }}</span>
                      <span v-if="!parseMulti(log.object_type).length" class="text-slate-400">—</span>
                    </div>
                    <div v-else>
                      <button @click="toggleDropdown(`obj_${log.id}`)"
                              class="w-full max-w-[160px] text-left text-[11px] p-1 border border-slate-200 rounded-lg bg-white flex items-center justify-between">
                        <span class="truncate text-slate-600">{{ parseMulti(log.object_type).join(', ') || '—' }}</span>
                        <ChevronDown class="w-3 h-3 text-slate-400 flex-shrink-0" />
                      </button>
                      <div v-if="openDropdowns[`obj_${log.id}`]" class="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-50 py-1 w-48" v-click-outside="() => closeDropdown(`obj_${log.id}`)">
                        <label v-for="o in OBJECT_OPTIONS" :key="o" class="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-xs">
                          <input type="checkbox" :checked="isSelected(log,'object_type',o)" @change="toggleMulti(log,'object_type',o); saveField(log,'object_type')" class="rounded accent-blue-600" />{{ o }}
                        </label>
                      </div>
                    </div>
                  </div>

                  <!-- Стадия освоения -->
                  <div>
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Стадия освоения</div>
                    <span v-if="isRowLocked(log)" class="text-slate-600 text-[11px]">{{ log.stage || '—' }}</span>
                    <select v-else v-model="log.stage" @change="saveField(log,'stage')" class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 text-xs max-w-[160px] w-full">
                      <option value="">—</option><option v-for="o in STAGE_OPTIONS" :key="o">{{ o }}</option>
                    </select>
                  </div>

                  <!-- Прочие поля -->
                  <div v-for="[field, label, type] in [
                    ['batch_num','№ партии','text'],['production_date','Дата производства','date'],
                    ['sale_date','Дата продажи','date'],['chat_name','Название чата','text'],
                    ['abc_group','Группа ABC','text'],['factory_type','Завод/не завод','text']
                  ]" :key="field">
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">{{ label }}</div>
                    <span v-if="isRowLocked(log)" class="text-slate-700 text-xs font-medium">{{ type === 'date' ? formatRuDate(log[field]) : (log[field] || '—') }}</span>
                    <input v-else :type="type" v-model="log[field]" @blur="saveField(log, field)"
                           class="p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 text-xs max-w-[160px] w-full" placeholder="—" />
                  </div>

                  <div class="col-span-2">
                    <div class="text-[9px] font-black text-slate-400 uppercase mb-1">Причина отклонения</div>
                    <span v-if="isRowLocked(log)" class="text-slate-700 text-xs">{{ log.deviation_cause || '—' }}</span>
                    <textarea v-else v-model="log.deviation_cause" @blur="saveField(log,'deviation_cause')" rows="2"
                              class="w-full p-1 border border-slate-200 rounded-lg bg-white outline-none focus:ring-1 focus:ring-blue-400 text-xs resize-none" placeholder="—"></textarea>
                  </div>
                </div>
              </td>
            </tr>
          </template>

          <tr v-if="filtered.length===0">
            <td colspan="22" class="text-center py-16 text-slate-400 font-bold">Нет записей</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ── Ручные обращения ───────────────────────────────────────────────────── -->
    <div v-else-if="activeTab==='manual'" class="flex-1 overflow-auto flex flex-col">
      <!-- Статистика -->
      <div class="flex-shrink-0 bg-white border-b border-slate-100 px-5 py-2 flex items-center gap-4 text-[11px] font-bold">
        <span class="text-slate-500">Всего: <span class="text-slate-800">{{ manualStats.total }}</span></span>
        <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-amber-400 inline-block"></span>Ожидают ответа: <span class="text-amber-600">{{ manualStats.sent }}</span></span>
        <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-green-400 inline-block"></span>Отвечено: <span class="text-green-600">{{ manualStats.replied }}</span></span>
      </div>
      <div v-if="manualClaims.length===0" class="text-center py-16 text-slate-400 font-bold">Ручные претензии ещё не добавлялись</div>
      <div v-else class="flex-1 overflow-auto">
        <table class="w-full text-left border-collapse text-xs" style="min-width:1300px">
          <thead style="position:sticky;top:0;z-index:20;background:#f1f5f9" class="border-b-2 border-slate-300">
            <tr class="text-slate-500 uppercase font-black text-[9px] tracking-wider">
              <th class="p-2.5 w-32">Номер</th>
              <th class="p-2.5 w-28">Дата отпр.</th>
              <th class="p-2.5 w-28">Кто отправил</th>
              <th class="p-2.5 w-28">Завод</th>
              <th class="p-2.5 w-32">№ инвойса</th>
              <th class="p-2.5 w-32">№ контейнера</th>
              <th class="p-2.5 w-28">Статус</th>
              <th class="p-2.5">Текст отправления</th>
              <th class="p-2.5 w-28">Дата ответа</th>
              <th class="p-2.5">Текст ответа</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            <tr v-for="claim in manualClaims" :key="claim.id"
                :class="['transition-colors', (claim.reply_text || claim.reply_date) ? 'bg-green-50 hover:bg-green-100/60' : claim.send_date ? 'bg-amber-50 hover:bg-amber-100/50' : 'hover:bg-slate-50']">
              <td class="p-2.5 font-black text-amber-600 whitespace-nowrap">{{ claim.ticket_number }}</td>
              <td class="p-2.5 text-slate-700 text-[11px]">{{ claim.send_date }}</td>
              <td class="p-2.5 text-[11px] text-slate-600 font-medium">{{ claim.who_sent || '—' }}</td>
              <!-- Завод с datalist -->
              <td class="p-2.5">
                <input v-model="claim.factory_name" @blur="saveManual(claim)" :list="`r-factory-${claim.id}`"
                       class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300" placeholder="—" />
                <datalist :id="`r-factory-${claim.id}`"><option v-for="v in claimsMetadata.factories" :key="v" :value="v"/></datalist>
              </td>
              <!-- Инвойс с datalist -->
              <td class="p-2.5">
                <input v-model="claim.invoice_ref" @blur="saveManual(claim)" :list="`r-invoice-${claim.id}`"
                       class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300 font-mono" placeholder="—" />
                <datalist :id="`r-invoice-${claim.id}`"><option v-for="v in claimsMetadata.invoices" :key="v" :value="v"/></datalist>
              </td>
              <!-- Контейнер с datalist -->
              <td class="p-2.5">
                <input v-model="claim.container_num" @blur="saveManual(claim)" :list="`r-container-${claim.id}`"
                       class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white focus:border-blue-300 font-mono" placeholder="—" />
                <datalist :id="`r-container-${claim.id}`"><option v-for="v in claimsMetadata.containers" :key="v" :value="v"/></datalist>
              </td>
              <td class="p-2.5">
                <select v-model="claim.manual_status" @change="saveManual(claim)" class="w-full p-1 text-xs border border-transparent hover:border-slate-200 rounded bg-transparent outline-none focus:bg-white">
                  <option value="">—</option><option v-for="o in MANUAL_STATUS_OPTIONS" :key="o">{{ o }}</option>
                </select>
              </td>
              <td class="p-2.5 text-slate-500 text-[11px] whitespace-pre-wrap max-w-xs">{{ claim.send_text }}</td>
              <td class="p-2.5"><input type="date" v-model="claim.reply_date" @blur="saveManual(claim)" class="w-full p-1.5 text-xs border border-slate-200 rounded bg-slate-50 focus:bg-white outline-none" /></td>
              <td class="p-2.5"><textarea v-model="claim.reply_text" @blur="saveManual(claim)" rows="2" placeholder="Ответ..." class="w-full text-xs p-2 border border-slate-200 rounded-lg bg-slate-50 focus:bg-white outline-none resize-none"></textarea></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<style scoped>
.row-stripe {
  background: repeating-linear-gradient(-45deg, #fff 0px, #fff 15px, #fef9c3 15px, #fef9c3 95px) !important;
}
.custom-scroll::-webkit-scrollbar { width: 4px; height: 4px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }

/* п.2: Видимые ручки для изменения ширины колонок */
.resize-handle {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 12px;
  cursor: col-resize;
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
  z-index: 1;
}
.resize-bar {
  width: 2px;
  background: #cbd5e1;
  transition: background 0.15s, width 0.15s;
  border-radius: 1px;
  margin: 4px 0;
}
.resize-handle:hover .resize-bar {
  background: #3b82f6;
  width: 3px;
}
</style>
