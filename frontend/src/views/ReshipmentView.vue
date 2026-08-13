<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import {
  PackageCheck, RefreshCw, X, CheckCircle2, XCircle,
  Truck, ExternalLink, Star, Loader2, Search,
  Info, AlertTriangle, Phone, Mail, MapPin, CheckSquare, Square,
  MessageCircle, FileDown, Printer, LinkIcon, History, ShoppingCart, RotateCcw, Settings2, TrendingUp, Tag
} from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const route = useRoute()
const router = useRouter()
const userRole = ref(localStorage.getItem('role') || '')
const username = ref(localStorage.getItem('username') || '')
const platformStore = usePlatformStore()
const currentMp = computed(() => String(platformStore.platform || 'all').toLowerCase())

const requests        = ref([])
const stats           = ref([])
const statsByCarrier  = ref([])
const loading         = ref(false)
const selected        = ref(null)
const filterStatus    = ref('all')
const filterCarrier   = ref('all')  // 'all' | 'cdek' | 'yandex'
const searchQuery     = ref('')

// ── Роль-зависимые флаги ─────────────────────────────────────────────────────
const isCS           = computed(() => ['cs_manager', 'admin', 'moderator'].includes(userRole.value))
const isWarehouse    = computed(() => ['warehouse_manager', 'admin', 'moderator'].includes(userRole.value))
const isAdmin        = computed(() => ['admin', 'moderator'].includes(userRole.value))
const isWarehouseOnly = computed(() => userRole.value === 'warehouse_manager')
const canManageYandexActs = computed(() => ['warehouse_manager', 'admin'].includes(userRole.value))
const canDeleteYandexActs = computed(() => userRole.value === 'admin')

const defaultStatus = computed(() => {
  if (userRole.value === 'cs_manager')        return 'new'
  if (userRole.value === 'warehouse_manager') return 'approved'
  return 'all'
})

const visibleFilters = computed(() => {
  if (userRole.value === 'cs_manager') return [
    { value: 'all',              label: 'Все' },
    { value: 'new',              label: 'Новые' },
    { value: 'approved',         label: 'На отправку' },
    { value: 'packed',           label: 'Собраны' },
    { value: 'shipped',          label: 'Отправлены' },
    { value: 'in_transit',       label: 'В пути' },
    { value: 'ready_for_pickup', label: 'Готов к выдаче' },
    { value: 'delivered',        label: 'Получены' },
    { value: 'rejected',         label: 'Отклонены' },
  ]
  if (userRole.value === 'warehouse_manager') return [
    { value: 'all',              label: 'Все' },
    { value: 'approved',         label: 'На отправку' },
    { value: 'packed',           label: 'Собраны' },
    { value: 'shipped',          label: 'Отправлены' },
    { value: 'in_transit',       label: 'В пути' },
    { value: 'ready_for_pickup', label: 'Готов к выдаче' },
    { value: 'delivered',        label: 'Получены' },
  ]
  return [
    { value: 'all',              label: 'Все' },
    { value: 'new',              label: 'Новые' },
    { value: 'approved',         label: 'На отправку' },
    { value: 'packed',           label: 'Собраны' },
    { value: 'shipped',          label: 'Отправлены' },
    { value: 'in_transit',       label: 'В пути' },
    { value: 'ready_for_pickup', label: 'Готов к выдаче' },
    { value: 'delivered',        label: 'Получены' },
    { value: 'rejected',         label: 'Отклонены' },
    { value: 'returned',         label: 'Вернулись' },
  ]
})

const statusMeta = {
  new:              { label: 'Новая',            color: 'bg-blue-100 text-blue-700' },
  matched:          { label: 'Сопоставлена',     color: 'bg-amber-100 text-amber-700' },
  approved:         { label: 'На отправку',      color: 'bg-violet-100 text-violet-700' },
  packed:           { label: 'Собран',           color: 'bg-teal-100 text-teal-700' },
  shipped:          { label: 'Отправлен',        color: 'bg-indigo-100 text-indigo-700' },
  in_transit:       { label: 'В пути',           color: 'bg-sky-100 text-sky-700' },
  ready_for_pickup: { label: 'Готов к выдаче',  color: 'bg-emerald-100 text-emerald-700' },
  delivered:        { label: 'Получен',          color: 'bg-slate-100 text-slate-600' },
  rejected:         { label: 'Отклонён',         color: 'bg-rose-100 text-rose-700' },
  returned:         { label: 'Вернулся',         color: 'bg-orange-100 text-orange-700' },
}

// ── История статусов ─────────────────────────────────────────────────────────
const statusHistory        = ref([])
const statusHistoryLoading = ref(false)
const showStatusHistory    = ref(false)

const loadStatusHistory = async (reqId) => {
  statusHistory.value = []
  statusHistoryLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${reqId}/status-history`)
    const data = await res.json()
    statusHistory.value = data || []
  } catch { /* ignore */ }
  finally { statusHistoryLoading.value = false }
}

const fmtMsk = (iso) => {
  if (!iso) return '—'
  // ISO from backend is already MSK (AT TIME ZONE applied on server)
  const d = new Date(iso)
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ── WB Context ────────────────────────────────────────────────────────────────
const wbCtx             = ref(null)
const wbCtxLoading      = ref(false)
const showFbHistory     = ref(false)

// WB чат: отправка сообщений
const wbMsgModal        = ref(false)
const wbMsgTarget       = ref(null)
const wbMsgText         = ref('')
const wbMsgSending      = ref(false)

const WB_MSG_TEMPLATES = [
  { label: 'Заявка принята', text: 'Здравствуйте! Ваша заявка на отправку детали принята и находится в обработке. Мы свяжемся с вами, как только посылка будет готова к отправке.' },
  { label: 'Ожидаем возврат', text: 'Здравствуйте! Мы ожидаем поступления детали на склад. Как только она будет получена и проверена, сразу подготовим отправку.' },
  { label: 'Отправлено', text: 'Здравствуйте! Ваша посылка отправлена. Трек-номер для отслеживания вы получите отдельным сообщением.' },
  { label: 'Нужна информация', text: 'Здравствуйте! Для обработки вашей заявки нам необходима дополнительная информация. Пожалуйста, уточните детали.' },
]

const openWbMsgModal = (req) => {
  wbMsgTarget.value = req
  wbMsgText.value = ''
  wbMsgModal.value = true
}

const sendWbMessage = async () => {
  if (!wbMsgText.value.trim() || wbMsgSending.value) return
  wbMsgSending.value = true
  try {
    const res = await apiFetch(`/api/v1/reshipment/requests/${wbMsgTarget.value.id}/send-wb-message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: wbMsgText.value.trim() })
    })
    if (res.status === 'ok') {
      wbMsgModal.value = false
      wbMsgText.value = ''
    }
  } catch (e) {
    alert('Ошибка отправки: ' + e.message)
  } finally {
    wbMsgSending.value = false
  }
}
const showProductClaims = ref(false)
const showEconomics     = ref(false)

const loadWbContext = async (reqId) => {
  wbCtx.value = null
  showFbHistory.value = false
  showProductClaims.value = false
  showEconomics.value = false
  economicsData.value = null
  if (!reqId) return
  wbCtxLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${reqId}/wb-context`)
    const data = await res.json()
    wbCtx.value = data.data || null
  } catch {}
  finally { wbCtxLoading.value = false }
}

const starLabel = (n) => '★'.repeat(n || 0) + '☆'.repeat(5 - (n || 0))

// ── Лайтбокс для фото ────────────────────────────────────────────────────────
const lightboxPhotos = ref([])
const lightboxIndex  = ref(0)
const lightboxOpen   = computed(() => lightboxPhotos.value.length > 0)

const openLightbox = (urls, idx = 0) => {
  lightboxPhotos.value = urls
  lightboxIndex.value  = idx
}
const closeLightbox = () => { lightboxPhotos.value = [] }
const lightboxPrev  = () => { lightboxIndex.value = (lightboxIndex.value - 1 + lightboxPhotos.value.length) % lightboxPhotos.value.length }
const lightboxNext  = () => { lightboxIndex.value = (lightboxIndex.value + 1) % lightboxPhotos.value.length }

const parsePhotos = (photosStr) => (photosStr || '').split(' ').map(u => u.trim()).filter(Boolean)

// ── WB / ЯМ Chat URL ───────────────────────────────────────────────────────────
const wbChatUrl = (chatId) => {
  const uuid = chatId?.split(':').slice(1).join(':') || chatId || ''
  return `https://seller.wildberries.ru/chat-with-clients?chatId=${uuid}`
}

const ymChatUrl = (req) => {
  const biz = '203987989'
  const camp = '146194774'
  const group = String(req?.ym_context_type || 'DIRECT').toUpperCase()
  const q = new URLSearchParams({ campaignId: camp, chatGroup: group })
  const cab = String(req?.ym_cabinet_chat_id || '').trim()
  if (cab.includes('/')) q.set('chatId', cab)
  return `https://partner.market.yandex.ru/business/${biz}/arbiter?${q}`
}

const marketplaceLabel = (req) => {
  const mp = String(req?.marketplace || 'wb').toLowerCase()
  if (mp === 'ym') return 'Яндекс Маркет'
  if (mp === 'ozon') return 'Ozon'
  return 'WB'
}

// ── Экономика заявки ─────────────────────────────────────────────────────────
const economicsData    = ref(null)
const economicsLoading = ref(false)

const loadEconomics = async (reqId) => {
  economicsData.value    = null
  economicsLoading.value = true
  try {
    const carrierParam = selectedCarrier.value ? `?carrier=${selectedCarrier.value}` : ''
    const res  = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${reqId}/economics${carrierParam}`)
    const data = await res.json()
    economicsData.value = data.data || null
  } catch { /* ignore */ }
  finally { economicsLoading.value = false }
}

// Для обратной совместимости с существующим UI-кодом, который проверяет economics
const economics = computed(() => {
  if (!economicsData.value) return null
  const v3 = economicsData.value.variants?.v3
  if (!v3) return null
  return { exists: true }
})

// ── Риск: доставка дороже себестоимости товара ────────────────────────────────
const showCostRiskModal = ref(false)
const selectedTariffCost = computed(() => {
  const t = tariffOptions.value.find(x => x.tariff_code === selectedTariff.value)
  return t ? Number(t.cost_total || t.cost || 0) : 0
})
const cogValue = computed(() => Number(economicsData.value?.cog || 0))
const COST_LIMIT_NO_COG = 650  // порог доставки, если себестоимость неизвестна
const costRisk = computed(() => {
  const delivery = selectedTariffCost.value
  const cog = cogValue.value
  if (!delivery) return null
  // Себестоимость известна — сравниваем с ней
  if (cog > 0) {
    if (delivery > cog) return { mode: 'cog', delivery, cog, diff: delivery - cog }
    return null
  }
  // Себестоимость неизвестна — порог 650 ₽
  if (delivery > COST_LIMIT_NO_COG) {
    return { mode: 'limit', delivery, limit: COST_LIMIT_NO_COG, diff: delivery - COST_LIMIT_NO_COG }
  }
  return null
})

// ── Состояние панели ──────────────────────────────────────────────────────────
const panel        = ref(null)
const panelLoading = ref(false)
const confirmUrl   = ref('')
const copied       = ref(false)
const costLoading  = ref(false)
const estimatedCost = ref(null)
const costFailed    = ref(false)
const lastShipResult = ref(null)

const copiedTemplate = ref(false)
const templateLoading = ref(false)
const copyTemplate = async () => {
  templateLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/generate-token`, { method: 'POST' })
    const data = await res.json()
    const url = data.url || FORM_URL
    const msg = `Для оформления заявки на отправку детали, пожалуйста, заполните форму — это займёт пару минут: ${url}\n\nПосле проверки мы свяжемся с вами и организуем отправку.`
    await navigator.clipboard.writeText(msg)
    copiedTemplate.value = true
    setTimeout(() => { copiedTemplate.value = false }, 2000)
  } catch {
    // fallback — общая ссылка без токена
    const msg = `Для оформления заявки на отправку детали, пожалуйста, заполните форму — это займёт пару минут: ${FORM_URL}\n\nПосле проверки мы свяжемся с вами и организуем отправку.`
    await navigator.clipboard.writeText(msg)
    copiedTemplate.value = true
    setTimeout(() => { copiedTemplate.value = false }, 2000)
  } finally {
    templateLoading.value = false
  }
}

// ── Модал выбора тарифа СДЭК ─────────────────────────────────────────────────
const tariffModal       = ref(false)
const tariffOptions     = ref([])
const tariffLoading     = ref(false)
const selectedTariff    = ref(null)
const tariffPendingIds  = ref([])  // ids заявок ожидающих подтверждения

const openTariffModal = async (ids) => {
  tariffPendingIds.value = ids
  tariffOptions.value = []
  selectedTariff.value = null
  tariffModal.value = true
  tariffLoading.value = true
  try {
    // Загружаем тарифы по первой заявке (для всех одинаково — тот же тариф)
    const firstId = ids[0]
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${firstId}/tariff-options`)
    const data = await res.json()
    tariffOptions.value = data.options || []
    if (tariffOptions.value.length) selectedTariff.value = tariffOptions.value[0].tariff_code
  } catch (e) {
    tariffOptions.value = []
  } finally {
    tariffLoading.value = false
  }
}

const doConfirmWithTariff = async () => {
  if (!tariffPendingIds.value.length) return
  bulkLoading.value = true
  tariffModal.value = false
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/warehouse-confirm-bulk`, {
      method: 'POST',
      body: JSON.stringify({
        ids: tariffPendingIds.value,
        processed_by: username.value,
        tariff_code: selectedTariff.value,
      }),
    })
    const data = await res.json()
    bulkResults.value = data.results || []
    panel.value = 'bulk_done'
    await reload()
  } catch (e) { alert(e.message) }
  finally { bulkLoading.value = false }
}

const doConfirmSingleWithTariff = async () => {
  if (!tariffPendingIds.value.length) return
  panelLoading.value = true
  tariffModal.value = false
  const id = tariffPendingIds.value[0]
  try {
    await apiFetch(
      `${API_BASE}/api/v1/reshipment/requests/${id}/warehouse-confirm?processed_by=${encodeURIComponent(username.value)}&tariff_code=${selectedTariff.value || ''}`,
      { method: 'POST' }
    )
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Формы ─────────────────────────────────────────────────────────────────────
const approveForm         = ref({ matched_srid: '', match_notes: '', moderator_comment: '' })
const rejectForm          = ref({ moderator_comment: '' })
const currentPromoCode    = ref('')
const promoCodeInput      = ref('')
const promoExpiresInput   = ref('')
const promoCodeUpdatedAt  = ref(null)
const promoCodeUpdatedBy  = ref('')
const promoExpiresAt      = ref(null)
const promoCodeSaving     = ref(false)
const showPromoSettings   = ref(false)

const loadPromoCode = async () => {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/promo-code`)
    const data = await res.json()
    currentPromoCode.value   = data.code || ''
    promoCodeInput.value     = data.code || ''
    promoCodeUpdatedAt.value = data.updated_at || null
    promoCodeUpdatedBy.value = data.updated_by || ''
    promoExpiresAt.value     = data.expires_at || null
    promoExpiresInput.value  = data.expires_at || ''
  } catch (e) { /* тихо игнорируем */ }
}

const savePromoCode = async () => {
  if (!promoCodeInput.value.trim()) { alert('Укажите код'); return }
  promoCodeSaving.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/promo-code`, {
      method: 'POST',
      body: JSON.stringify({
        code: promoCodeInput.value.trim().toUpperCase(),
        expires_at: promoExpiresInput.value || null,
        processed_by: username.value,
      }),
    })
    await loadPromoCode()
    showPromoSettings.value = false
  } catch (e) { alert(e.message) }
  finally { promoCodeSaving.value = false }
}

// ── Товары, по которым разрешена доотправка деталей ──────────────────────────
const showSkuSettings = ref(false)
const skuItems        = ref([])
const skuLoading      = ref(false)
const skuSaving       = ref(false)
const skuSearch       = ref('')
const skuOnlyEnabled  = ref(false)

const skuEnabledCount = computed(() => skuItems.value.filter(s => s.enabled).length)

const skuFiltered = computed(() => {
  const q = skuSearch.value.trim().toLowerCase()
  return skuItems.value.filter(s => {
    if (skuOnlyEnabled.value && !s.enabled) return false
    if (!q) return true
    return (s.supplier_article || '').toLowerCase().includes(q)
        || (s.group_name || '').toLowerCase().includes(q)
  })
})

const loadSkus = async () => {
  skuLoading.value = true
  try {
    const r = await apiFetch(`${API_BASE}/api/v1/reshipment-skus`)
    const d = await r.json()
    skuItems.value = d.items || []
  } catch (e) { alert(e.message) }
  finally { skuLoading.value = false }
}

const openSkuSettings = () => { showSkuSettings.value = true; loadSkus() }

const saveSkus = async () => {
  skuSaving.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment-skus/bulk`, {
      method: 'POST',
      body: JSON.stringify({
        items: skuItems.value.map(s => ({
          supplier_article: s.supplier_article,
          enabled: !!s.enabled,
        })),
      }),
    })
    showSkuSettings.value = false
  } catch (e) { alert(e.message) }
  finally { skuSaving.value = false }
}

const warehouseRejectForm = ref({ rejection_reason: '' })
const yandexForm          = ref({ track_number: '', shipping_cost: '' })
const yandexActs          = ref([])
const yandexActsLoading   = ref(false)
const yandexActGenerating = ref(false)
const yandexActPending    = ref(0)
const yandexActHasStamp   = ref(false)
const showYandexActsRegistry = ref(false)
const yandexActDeletingId = ref(null)

const loadYandexActs = async () => {
  if (!canManageYandexActs.value) return
  yandexActsLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/warehouse/yandex-acts`)
    const data = await res.json()
    yandexActs.value = data.data || []
    yandexActPending.value = data.pending_count || 0
    yandexActHasStamp.value = !!data.has_auto_stamp
  } catch (e) {
    console.error(e)
  } finally {
    yandexActsLoading.value = false
  }
}

const downloadYandexAct = async (actId, format = 'pdf') => {
  const token = localStorage.getItem('token')
  const res = await fetch(`${API_BASE}/api/v1/reshipment/warehouse/yandex-acts/${actId}/download?format=${format}`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.click()
  URL.revokeObjectURL(url)
}

const generateYandexAct = async () => {
  if (!yandexActPending.value) {
    alert('Нет собранных Яндекс-заявок без акта')
    return
  }
  yandexActGenerating.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/warehouse/yandex-acts/generate?processed_by=${encodeURIComponent(username.value)}`, {
      method: 'POST'
    })
    const data = await res.json()
    const actId = data?.data?.id
    await Promise.all([reload(), loadYandexActs()])
    if (actId) await downloadYandexAct(actId, 'pdf')
  } catch (e) {
    alert(e.message)
  } finally {
    yandexActGenerating.value = false
  }
}

const openYandexActsRegistry = async () => {
  showYandexActsRegistry.value = true
  await loadYandexActs()
}

const deleteYandexAct = async (actId) => {
  if (!canDeleteYandexActs.value) return
  if (!confirm('Удалить акт из системы? Это действие необратимо, а заявки снова станут без акта.')) return
  yandexActDeletingId.value = actId
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/warehouse/yandex-acts/${actId}`, { method: 'DELETE' })
    await Promise.all([loadYandexActs(), load()])
  } catch (e) {
    alert(e.message)
  } finally {
    yandexActDeletingId.value = null
  }
}

// ── Ручная смена статуса (только admin/moderator) ────────────────────────────
const statusOptions = [
  { value: 'new',              label: 'Новая' },
  { value: 'matched',          label: 'Сопоставлена' },
  { value: 'approved',         label: 'На отправку' },
  { value: 'packed',           label: 'Собран' },
  { value: 'shipped',          label: 'Отправлен' },
  { value: 'in_transit',       label: 'В пути' },
  { value: 'ready_for_pickup', label: 'Готов к выдаче' },
  { value: 'delivered',        label: 'Получен' },
  { value: 'rejected',         label: 'Отклонён' },
  { value: 'returned',         label: 'Вернулся' },
]
const setStatusForm    = ref({ status: '', comment: '' })
const setStatusLoading = ref(false)

const doSetStatus = async () => {
  if (!setStatusForm.value.status) return
  setStatusLoading.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/set-status`, {
      method: 'POST',
      body: JSON.stringify({
        status:       setStatusForm.value.status,
        comment:      setStatusForm.value.comment || null,
        processed_by: username.value,
      }),
    })
    panel.value = null
    setStatusForm.value = { status: '', comment: '' }
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
    if (selected.value) loadStatusHistory(selected.value.id)
  } catch (e) { alert(e.message) }
  finally { setStatusLoading.value = false }
}

const approvedCount = computed(() =>
  filteredRequests.value.filter(r => r.status === 'approved').length
)

// Базовый список: роль + площадка из сайдбара (без статуса/ТК/поиска) — для цифр в фильтрах
const requestsForMp = computed(() => {
  let list = requests.value
  if (userRole.value === 'cs_manager') {
    list = list.filter(r => ['new', 'matched', 'approved', 'packed', 'rejected', 'shipped', 'in_transit', 'ready_for_pickup', 'delivered'].includes(r.status))
  } else if (userRole.value === 'warehouse_manager') {
    list = list.filter(r => ['approved', 'packed', 'shipped', 'in_transit', 'ready_for_pickup', 'delivered', 'rejected'].includes(r.status))
  }
  if (currentMp.value && currentMp.value !== 'all') {
    list = list.filter(r => String(r.marketplace || 'wb').toLowerCase() === currentMp.value)
  }
  return list
})

// ── Вычисляемые ──────────────────────────────────────────────────────────────
const filteredRequests = computed(() => {
  let list = requestsForMp.value
  if (filterStatus.value !== 'all') list = list.filter(r => r.status === filterStatus.value)
  if (filterCarrier.value !== 'all') {
    list = list.filter(r => {
      if (r.status === 'rejected') return false  // отклонённые — вне перевозчиков
      if (filterCarrier.value === 'cdek') {
        // Явно СДЭК ИЛИ одобренная/старая заявка без delivery_method, но с cdek и без яндекса
        return r.delivery_method === 'cdek' || (
          !r.delivery_method
          && !['new', 'matched'].includes(r.status)
          && !r.yandex_cost && !r.yandex_pvz_code
        )
      }
      return r.delivery_method === filterCarrier.value
    })
  }
  if (searchQuery.value.trim()) {
    const raw = searchQuery.value.trim()
    // "№123" или просто "123" — точный поиск по номеру заявки
    const idMatch = raw.match(/^№?\s*(\d+)$/)
    if (idMatch) {
      const targetId = parseInt(idMatch[1], 10)
      list = list.filter(r => r.id === targetId)
    } else {
      const q = raw.toLowerCase()
      list = list.filter(r =>
        String(r.id).includes(q) ||
        (r.customer_name       || '').toLowerCase().includes(q) ||
        (r.order_number        || '').toLowerCase().includes(q) ||
        (r.items_to_send       || '').toLowerCase().includes(q) ||
        (r.customer_phone      || '').includes(q) ||
        (r.wb_chat_id          || '').toLowerCase().includes(q) ||
        (r.ym_chat_id          || '').toLowerCase().includes(q) ||
        (r.chat_token          || '').toLowerCase().includes(q) ||
        (r.confirmation_token  || '').toLowerCase().includes(q)
      )
    }
  }
  return list
})

const statsByStatus = computed(() => {
  const map = {}
  for (const r of requestsForMp.value) {
    const st = r.status || 'new'
    if (!map[st]) map[st] = { cnt: 0 }
    map[st].cnt += 1
  }
  return map
})

// Соотношение СДЭК/Яндекс среди заявок текущей площадки, где перевозчик уже определён
const carrierRatio = computed(() => {
  const map = { cdek: 0, yandex: 0 }
  for (const r of requestsForMp.value) {
    if (r.status === 'rejected') continue
    if (r.delivery_method === 'yandex') map.yandex += 1
    else if (r.delivery_method === 'cdek') map.cdek += 1
    else if (!r.delivery_method && !['new', 'matched'].includes(r.status) && !r.yandex_cost && !r.yandex_pvz_code) {
      map.cdek += 1
    }
  }
  const total = map.cdek + map.yandex
  return {
    cdek: map.cdek, yandex: map.yandex, total,
    cdekPct:   total ? Math.round((map.cdek / total) * 100) : 0,
    yandexPct: total ? Math.round((map.yandex / total) * 100) : 0,
  }
})

const mpStripeClass = (req) => {
  const mp = String(req?.marketplace || 'wb').toLowerCase()
  if (mp === 'ym') return 'bg-orange-400'
  if (mp === 'ozon') return 'bg-blue-500'
  return 'bg-violet-500'
}

const carrierLabel = (req) => {
  if (req?.delivery_method === 'yandex') return 'Яндекс'
  if (req?.delivery_method === 'cdek') return 'СДЭК'
  return ''
}

const emptyListHint = computed(() => {
  if (filterStatus.value === 'new') return 'Новых заявок нет'
  if (filterStatus.value === 'approved') return 'Нет заявок к отправке'
  if (currentMp.value === 'ym') return 'Заявок с Яндекс Маркета пока нет — ссылки в чатах ЯМ уже работают, ждут заполнения формы'
  if (currentMp.value === 'ozon') return 'Заявок с Ozon пока нет'
  if (currentMp.value === 'wb') return 'Нет заявок WB'
  return 'Нет заявок'
})

// cdek_cost — теперь справочная цена СДЭК-кандидата у ЛЮБОЙ заявки (даже если выбран
// Яндекс), поэтому нельзя брать "cdek_cost если есть иначе shipping_cost" — смотрим на
// фактический delivery_method. Старые СДЭК-заявки (до dual-carrier) имеют delivery_method=null,
// но у них cdek_cost — реальная цена доставки, а не оценка кандидата.
const realCost = (r) => {
  if (r.delivery_method === 'yandex') return parseFloat(r.shipping_cost || 0)
  if (r.delivery_method === 'cdek')   return parseFloat(r.cdek_cost || 0)
  // Устаревшие СДЭК-заявки без delivery_method: если прошли одобрение — реальные расходы есть
  if (!['new', 'matched'].includes(r.status)) return parseFloat(r.cdek_cost || 0)
  return 0  // new/matched — перевозчик ещё не выбран, cdek_cost — лишь оценка кандидата
}
const totalShippingCost = computed(() =>
  filteredRequests.value.reduce((acc, r) => acc + realCost(r), 0)
)

// ── Данные ────────────────────────────────────────────────────────────────────
const load = async () => {
  loading.value = true
  try {
    const [r1, r2] = await Promise.all([
      apiFetch(`${API_BASE}/api/v1/reshipment/requests`),
      apiFetch(`${API_BASE}/api/v1/reshipment/stats`),
    ])
    const d1 = await r1.json()
    const d2 = await r2.json()
    requests.value   = d1.data || []
    stats.value      = d2.data || []
    statsByCarrier.value = d2.by_carrier || []
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

async function openFromQuery() {
  const raw = route.query.id || route.query.req
  const id = Number(raw || 0)
  if (!id) return
  // Сбрасываем фильтр статуса — иначе заявка может быть скрыта
  if (filterStatus.value !== 'all') filterStatus.value = 'all'
  if (!requests.value.length) await load()
  let req = requests.value.find(r => Number(r.id) === id)
  if (!req) {
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${id}`)
      const d = await res.json()
      req = d.data || d
      if (req?.id && !requests.value.some(r => Number(r.id) === Number(req.id))) {
        requests.value = [req, ...requests.value]
      }
    } catch { /* ignore */ }
  }
  if (req?.id) await select(req)
}

const select = async (req) => {
  selected.value = req
  panel.value    = null
  confirmUrl.value = ''
  estimatedCost.value = null
  costFailed.value = false
  lastShipResult.value = null
  approveForm.value         = { matched_srid: req.matched_srid || '', match_notes: req.match_notes || '', moderator_comment: '' }
  rejectForm.value          = { moderator_comment: '' }
  warehouseRejectForm.value = { rejection_reason: '' }
  yandexForm.value          = { track_number: '', shipping_cost: '' }
  // Сразу подгружаем оба варианта доставки (СДЭК + Яндекс) для КС, чтобы видеть
  // стоимость и согласовать перевозчика до одобрения
  tariffOptions.value = []
  selectedTariff.value = null
  candidatesData.value = null
  if (isCS.value && ['new', 'matched'].includes(req.status)) {
    await loadCandidates()
    if (selectedCarrier.value === 'cdek') loadTariffs()
  } else {
    // Уже одобрена (или на складе) — перевозчик зафиксирован, экономика должна
    // считаться именно по нему, а не по значению, оставшемуся от прошлой заявки
    selectedCarrier.value = req.delivery_method || 'cdek'
  }
  if (req.wb_chat_id || req.wb_nm_id) loadWbContext(req.id)
  if (!isWarehouseOnly.value) loadEconomics(req.id)
  showStatusHistory.value = false
  loadStatusHistory(req.id)
}

const reload = async () => {
  await load()
  if (canManageYandexActs.value) await loadYandexActs()
  if (selected.value) {
    selected.value = requests.value.find(r => r.id === selected.value.id) || null
  }
}

// ── Оба кандидата доставки (СДЭК + Яндекс), КС сам согласовывает выгодный ────
const candidatesData = ref(null)   // { client, cdek, yandex }
const candidatesLoading = ref(false)
const selectedCarrier = ref('cdek')  // 'cdek' | 'yandex' — выбор КС на этапе одобрения
const pvzMapEl = ref(null)
const pvzMapError = ref('')
let pvzMap = null
let ymapsLoadPromise = null

const YANDEX_MAPS_KEY = import.meta.env.VITE_YANDEX_MAPS_KEY || ''

const fmtDistance = (km) => {
  if (km == null || Number.isNaN(Number(km))) return ''
  const n = Number(km)
  const s = Number.isInteger(n) ? String(n) : n.toFixed(1).replace('.', ',')
  return `~${s} км от адреса клиента`
}

const hasPvzMapPoints = computed(() => {
  const d = candidatesData.value
  if (!d) return false
  const clientOk = d.client?.lat != null && d.client?.lon != null
  const cdekOk = d.cdek?.available && d.cdek?.lat != null && d.cdek?.lng != null
  const ydOk = d.yandex?.available && d.yandex?.lat != null && d.yandex?.lng != null
  return clientOk && (cdekOk || ydOk)
})

const loadYmaps = () => {
  if (typeof window !== 'undefined' && window.ymaps) {
    return new Promise((resolve) => window.ymaps.ready(() => resolve(window.ymaps)))
  }
  if (!YANDEX_MAPS_KEY) {
    return Promise.reject(new Error('Нет VITE_YANDEX_MAPS_KEY'))
  }
  if (!ymapsLoadPromise) {
    ymapsLoadPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script')
      s.src = `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(YANDEX_MAPS_KEY)}&lang=ru_RU`
      s.async = true
      s.onload = () => {
        if (!window.ymaps) {
          reject(new Error('ymaps не загрузился'))
          return
        }
        window.ymaps.ready(() => resolve(window.ymaps))
      }
      s.onerror = () => reject(new Error('Не удалось загрузить Яндекс.Карты'))
      document.head.appendChild(s)
    })
  }
  return ymapsLoadPromise
}

const destroyPvzMap = () => {
  if (pvzMap) {
    try { pvzMap.destroy() } catch { /* ignore */ }
    pvzMap = null
  }
}

const renderPvzMap = async () => {
  await nextTick()
  if (!hasPvzMapPoints.value || !pvzMapEl.value) {
    destroyPvzMap()
    pvzMapError.value = ''
    return
  }
  const d = candidatesData.value
  try {
    const ymaps = await loadYmaps()
    pvzMapError.value = ''
    destroyPvzMap()

    const points = [[d.client.lat, d.client.lon]]
    if (d.cdek?.available && d.cdek.lat != null && d.cdek.lng != null) {
      points.push([d.cdek.lat, d.cdek.lng])
    }
    if (d.yandex?.available && d.yandex.lat != null && d.yandex.lng != null) {
      points.push([d.yandex.lat, d.yandex.lng])
    }

    pvzMap = new ymaps.Map(pvzMapEl.value, {
      center: points[0],
      zoom: 14,
      controls: ['zoomControl'],
    }, {
      suppressMapOpenBlock: true,
    })

    const addMark = (coords, color, label, balloon) => {
      const mark = new ymaps.Placemark(coords, {
        balloonContent: balloon,
        iconCaption: label,
      }, {
        preset: 'islands#circleIcon',
        iconColor: color,
      })
      pvzMap.geoObjects.add(mark)
    }

    addMark(
      [d.client.lat, d.client.lon],
      '#334155',
      'Адрес',
      d.client.address || 'Адрес клиента',
    )

    if (d.cdek?.available && d.cdek.lat != null && d.cdek.lng != null) {
      const sel = selectedCarrier.value === 'cdek'
      addMark(
        [d.cdek.lat, d.cdek.lng],
        sel ? '#047857' : '#059669',
        sel ? 'СДЭК ✓' : 'СДЭК',
        `СДЭК${d.cdek.distance_km != null ? ` · ${fmtDistance(d.cdek.distance_km)}` : ''}<br>${d.cdek.pvz_address || ''}`,
      )
    }
    if (d.yandex?.available && d.yandex.lat != null && d.yandex.lng != null) {
      const sel = selectedCarrier.value === 'yandex'
      addMark(
        [d.yandex.lat, d.yandex.lng],
        sel ? '#B45309' : '#D97706',
        sel ? 'Яндекс ✓' : 'Яндекс',
        `Яндекс${d.yandex.distance_km != null ? ` · ${fmtDistance(d.yandex.distance_km)}` : ''}<br>${d.yandex.pvz_address || ''}`,
      )
    }

    if (points.length > 1) {
      pvzMap.setBounds(pvzMap.geoObjects.getBounds(), {
        checkZoomRange: true,
        zoomMargin: 36,
      })
    }
  } catch (e) {
    destroyPvzMap()
    pvzMapError.value = e?.message || 'Карта недоступна'
  }
}

watch([candidatesData, selectedCarrier, hasPvzMapPoints], () => { renderPvzMap() })

const loadCandidates = async () => {
  if (!selected.value) return
  candidatesLoading.value = true
  candidatesData.value = null
  destroyPvzMap()
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/cost`)
    const data = await res.json()
    candidatesData.value = data
    // Уже одобрена — фиксируем реальный перевозчик; иначе по умолчанию берём доступный
    if (data.delivery_method) {
      selectedCarrier.value = data.delivery_method
    } else if (data.cdek?.available && !data.yandex?.available) {
      selectedCarrier.value = 'cdek'
    } else if (data.yandex?.available && !data.cdek?.available) {
      selectedCarrier.value = 'yandex'
    } else {
      selectedCarrier.value = 'cdek'
    }
  } catch { /* ignore */ }
  finally { candidatesLoading.value = false }
}

const refreshingCandidates = ref(false)
const refreshCandidates = async () => {
  if (!selected.value) return
  refreshingCandidates.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/refresh-candidates`, { method: 'POST' })
    const data = await res.json()
    // Обновляем candidatesData и объект заявки без перезагрузки
    candidatesData.value = {
      ...(candidatesData.value || {}),
      client: data.client || candidatesData.value?.client,
      cdek: data.cdek?.available
        ? { ...data.cdek, available: true }
        : (candidatesData.value?.cdek || { available: false }),
      yandex: data.yandex?.available
        ? { ...data.yandex, available: true }
        : (candidatesData.value?.yandex || { available: false }),
    }
    if (data.cdek?.available) {
      selected.value.cdek_cost = data.cdek.cost
      selected.value.client_pvz_address = data.cdek.pvz_address
    }
    if (data.yandex?.available) {
      selected.value.yandex_cost = data.yandex.cost
      selected.value.yandex_pvz_address = data.yandex.pvz_address
    }
    // Обновляем и список заявок
    const idx = requests.value.findIndex(r => r.id === selected.value.id)
    if (idx !== -1) requests.value[idx] = { ...requests.value[idx], ...selected.value }
  } catch (e) {
    alert(e.message || 'Не удалось найти перевозчиков')
  } finally {
    refreshingCandidates.value = false
  }
}

const pickCarrier = (carrier) => {
  selectedCarrier.value = carrier
  loadEconomics(selected.value.id)
}

// ── КС: загрузка тарифов СДЭК (показываем сразу при выборе заявки) ────────────
const loadTariffs = async () => {
  if (!selected.value) return
  tariffOptions.value = []
  selectedTariff.value = null
  tariffLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/tariff-options`)
    const data = await res.json()
    tariffOptions.value = data.options || []
    if (tariffOptions.value.length) pickTariff(tariffOptions.value[0])
  } catch (e) { /* ignore */ }
  finally { tariffLoading.value = false }
}

// Выбор тарифа → обновляем и расчётную стоимость (для блока «выгода»)
const pickTariff = (t) => {
  selectedTariff.value = t.tariff_code
  estimatedCost.value  = t.cost_total || t.cost || null
}

const doApprove = async (force = false) => {
  if (selectedCarrier.value === 'cdek' && !selectedTariff.value) {
    alert('Сначала выберите тариф СДЭК'); return
  }
  // Риск: стоимость доставки превышает себестоимость товара (только для СДЭК-тарифа)
  if (selectedCarrier.value === 'cdek' && !force && costRisk.value) { showCostRiskModal.value = true; return }
  showCostRiskModal.value = false
  panelLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/approve`, {
      method: 'POST',
      body: JSON.stringify({
        matched_srid:      approveForm.value.matched_srid || null,
        match_notes:       approveForm.value.match_notes || null,
        moderator_comment: approveForm.value.moderator_comment || null,
        processed_by:      username.value,
        tariff_code:       selectedCarrier.value === 'cdek' ? (selectedTariff.value || null) : null,
        delivery_method:   selectedCarrier.value,
      }),
    })
    const data = await res.json()
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
    if (data.cdek_warning) alert(`⚠️ ${data.cdek_warning}`)
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── КС: отклонить ────────────────────────────────────────────────────────────
const doReject = async () => {
  if (!rejectForm.value.moderator_comment.trim()) { alert('Укажите причину'); return }
  panelLoading.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        moderator_comment: rejectForm.value.moderator_comment,
        processed_by:      username.value,
      }),
    })
    panel.value = null
    await reload()
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: отклонить с причиной ───────────────────────────────────────────────
const doWarehouseReject = async () => {
  if (!warehouseRejectForm.value.rejection_reason.trim()) { alert('Укажите причину'); return }
  panelLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/warehouse-reject`, {
      method: 'POST',
      body: JSON.stringify({
        rejection_reason: warehouseRejectForm.value.rejection_reason,
        processed_by:     username.value,
      }),
    })
    const data = await res.json()
    panel.value = 'warehouse_rejected'
    lastShipResult.value = data
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: отметить как собрано ──────────────────────────────────────────────
const doMarkPacked = async (id) => {
  panelLoading.value = true
  try {
    await apiFetch(
      `${API_BASE}/api/v1/reshipment/requests/${id}/pack?processed_by=${encodeURIComponent(username.value)}`,
      { method: 'POST' }
    )
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── КС: повторить автосоздание заказа Яндекс (если упало при одобрении) ──────
const doYandexRetry = async (id) => {
  panelLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${id}/yandex-create`, {
      method: 'POST',
      body: JSON.stringify({ processed_by: username.value }),
    })
    const data = await res.json()
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
    if (data.warning) alert(`⚠️ ${data.warning}`)
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: подтвердить ручную отправку Яндекс (если автосоздание не удалось) ─
const doShipYandex = async (id) => {
  if (!yandexForm.value.track_number.trim()) { alert('Укажите трек-номер'); return }
  panelLoading.value = true
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${id}/ship-yandex`, {
      method: 'POST',
      body: JSON.stringify({
        track_number:   yandexForm.value.track_number.trim(),
        shipping_cost:  yandexForm.value.shipping_cost ? parseFloat(yandexForm.value.shipping_cost) : (selected.value?.shipping_cost || null),
        processed_by:   username.value,
      }),
    })
    yandexForm.value = { track_number: '', shipping_cost: '' }
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}


// ── Склад: отметить как сдано на ПВЗ ────────────────────────────────────────
const doMarkShipped = async (id) => {
  panelLoading.value = true
  try {
    await apiFetch(
      `${API_BASE}/api/v1/reshipment/requests/${id}/mark-shipped?processed_by=${encodeURIComponent(username.value)}`,
      { method: 'POST' }
    )
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Склад: массовые действия ─────────────────────────────────────────────────
const bulkLoading = ref(false)
const bulkResults = ref([])

// ── Склад: скачать список к упаковке ─────────────────────────────────────────
const downloadPacklist = async () => {
  const token = localStorage.getItem('token')
  const res = await fetch(`${API_BASE}/api/v1/reshipment/warehouse/packlist`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `packlist_${new Date().toISOString().slice(0,10)}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Склад: печать этикеток PDF ────────────────────────────────────────────────
const downloadLabels = async (ids = []) => {
  const token = localStorage.getItem('token')
  const idsParam = ids.length ? `?ids=${ids.join(',')}` : ''
  const res = await fetch(`${API_BASE}/api/v1/reshipment/warehouse/labels${idsParam}`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.click()
  URL.revokeObjectURL(url)
}

const downloadLabelsThermal = async (ids = []) => {
  const token = localStorage.getItem('token')
  const idsParam = ids.length ? `?ids=${ids.join(',')}` : ''
  const res = await fetch(`${API_BASE}/api/v1/reshipment/warehouse/labels/thermal${idsParam}`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.target = '_blank'
  a.click()
  URL.revokeObjectURL(url)
}

// ── Склад: отметить посылку как вернувшуюся ──────────────────────────────────
const doMarkReturned = async () => {
  panelLoading.value = true
  try {
    await apiFetch(
      `${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/mark-returned?processed_by=${encodeURIComponent(username.value)}`,
      { method: 'POST' }
    )
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Повторная отправка после возврата/отмены перевозчиком ───────────────────
const doRecreateShipment = async (id) => {
  if (!confirm('Создать повторный заказ у перевозчика на ту же заявку?\nКарточка возврата (srid) сохранится, статус станет «Одобрена».')) return
  panelLoading.value = true
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${id}/recreate-shipment`, {
      method: 'POST',
      body: JSON.stringify({ processed_by: username.value, notify_client: true }),
    })
    const data = await res.json()
    panel.value = null
    await reload()
    selected.value = requests.value.find(r => r.id === id) || null
    alert(data.message || 'Повторный заказ создан')
  } catch (e) { alert(e.message) }
  finally { panelLoading.value = false }
}

// ── Утилиты ────────────────────────────────────────────────────────────────
const doRequestReview = async () => {
  try {
    await apiFetch(`${API_BASE}/api/v1/reshipment/requests/${selected.value.id}/request-review`, { method: 'POST' })
    await reload()
    selected.value = requests.value.find(r => r.id === selected.value?.id) || null
  } catch (e) { alert(e.message) }
}

const copyUrl = async (url) => {
  await navigator.clipboard.writeText(url)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

const cdekTrackUrl = (num) => num ? `https://www.cdek.ru/track.html?order_id=${num}` : null

const fmt = (d) => {
  if (!d) return '—'
  const date = new Date(d)
  date.setHours(date.getHours() + 3) // UTC → МСК
  return date.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'
  })
}

const fmtCost = (v) => v != null ? `${Number(v).toLocaleString('ru-RU')} ₽` : '—'

const onKeydown = (e) => { if (e.key === 'Escape') closeLightbox() }
onMounted(async () => {
  filterStatus.value = defaultStatus.value
  await load()
  if (isCS.value) loadPromoCode()
  if (canManageYandexActs.value) loadYandexActs()
  await openFromQuery()
  window.addEventListener('keydown', onKeydown)
})
watch(() => route.query.id || route.query.req, () => {
  openFromQuery()
})
watch(currentMp, () => {
  // При смене площадки в сайдбаре — сбросить карточку, если её нет в новом списке
  if (selected.value && currentMp.value !== 'all') {
    const mp = String(selected.value.marketplace || 'wb').toLowerCase()
    if (mp !== currentMp.value) selected.value = null
  }
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  destroyPvzMap()
})
</script>

<template>
  <div class="flex flex-col md:flex-row h-full bg-slate-50">

    <!-- ── Список ──────────────────────────────────────────────────────────── -->
    <div :class="['flex flex-col min-h-0 md:w-80 xl:w-96 md:flex-shrink-0 border-r border-slate-200 bg-white', selected ? 'hidden md:flex' : 'flex w-full']">

      <div class="p-4 border-b border-slate-100">
        <div class="mb-2.5">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <h1 class="flex items-center gap-2 text-base font-bold text-slate-800">
                <PackageCheck class="w-4 h-4 text-emerald-600" /> Отправка деталей
              </h1>
              <p class="mt-0.5 text-[10px] text-slate-400">
                {{ userRole === 'cs_manager' ? 'Менеджер КС' : userRole === 'warehouse_manager' ? 'Менеджер склада' : 'Все заявки' }}
              </p>
            </div>
            <button @click="reload" :class="['flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 transition-colors', loading ? 'animate-spin' : '']">
              <RefreshCw class="w-3.5 h-3.5" />
            </button>
          </div>
          <div class="mt-2 flex max-w-full flex-wrap items-center gap-1">
            <button v-if="isAdmin" @click="openSkuSettings"
              title="Товары, по которым можно предлагать доотправку деталей (только администратор)"
              class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 whitespace-nowrap">
              <PackageCheck class="h-3.5 w-3.5" />
              <span>Товары</span>
            </button>
            <button v-if="isCS" @click="showPromoSettings = true"
              title="Промокод на следующую покупку (для отказов)"
              class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 whitespace-nowrap">
              <Tag class="h-3.5 w-3.5" />
              <span>{{ currentPromoCode || 'Промокод' }}</span>
            </button>
            <button @click="copyTemplate" :disabled="templateLoading"
              :title="copiedTemplate ? 'Скопировано!' : 'Сгенерировать уникальную ссылку для клиента'"
              :class="['inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-50 whitespace-nowrap',
                copiedTemplate ? 'bg-emerald-100 text-emerald-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600']">
              <Loader2 v-if="templateLoading" class="h-3.5 w-3.5 animate-spin" />
              <LinkIcon v-else class="h-3.5 w-3.5" />
              <span>{{ copiedTemplate ? 'Скопировано!' : 'Ссылка для клиента' }}</span>
            </button>
          </div>
        </div>

        <div v-if="canManageYandexActs"
          class="mb-3 rounded-xl border border-amber-200/80 bg-gradient-to-r from-amber-50 via-orange-50 to-amber-50 p-1 shadow-[0_10px_24px_-24px_rgba(217,119,6,0.5)]">
          <div class="grid grid-cols-2 gap-1.5">
            <button @click="generateYandexAct" :disabled="yandexActGenerating || !yandexActPending"
              :title="yandexActPending ? `Без акта: ${yandexActPending}` : 'Нет заявок без акта'"
              class="inline-flex min-h-[36px] w-full items-center justify-center gap-1 rounded-lg bg-amber-500 px-2.5 py-2 text-[12px] font-semibold text-white shadow-sm transition-all hover:bg-amber-600 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-amber-500 whitespace-nowrap">
              <Loader2 v-if="yandexActGenerating" class="h-3 w-3 animate-spin" />
              <FileDown v-else class="h-3 w-3" />
              <span>Сформировать акт</span>
            </button>
            <button @click="openYandexActsRegistry"
              class="inline-flex min-h-[36px] w-full items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[12px] font-semibold text-slate-700 shadow-sm transition-all hover:bg-slate-50 hover:shadow-md whitespace-nowrap">
              <History class="h-3 w-3" />
              <span>Реестр актов Яндекс</span>
            </button>
          </div>
        </div>

        <div class="relative mb-3">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input v-model="searchQuery" type="text" placeholder="ID заявки, имя, номер, телефон, чат, токен..."
            class="w-full pl-8 pr-3 py-2 text-xs rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none" />
        </div>

        <div class="flex flex-wrap gap-1 mb-2">
          <button v-for="f in visibleFilters" :key="f.value" @click="filterStatus = f.value"
            :class="['px-2.5 py-1 text-xs rounded-full font-medium transition-colors',
              filterStatus === f.value ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200']">
            {{ f.label }}
            <span v-if="f.value !== 'all' && statsByStatus[f.value]" class="ml-1 opacity-70">{{ statsByStatus[f.value].cnt }}</span>
          </button>
        </div>

        <!-- Фильтр по перевозчику + соотношение СДЭК/Яндекс -->
        <div class="flex items-center gap-2 flex-wrap">
          <div class="flex gap-1">
            <button v-for="c in [{v:'all',l:'Все ТК'},{v:'cdek',l:'СДЭК'},{v:'yandex',l:'Яндекс'}]" :key="c.v"
              @click="filterCarrier = c.v"
              :class="['px-2 py-0.5 text-[11px] rounded-full font-medium border transition-colors',
                filterCarrier === c.v
                  ? (c.v === 'yandex' ? 'bg-amber-500 border-amber-500 text-white' : c.v === 'cdek' ? 'bg-emerald-600 border-emerald-600 text-white' : 'bg-slate-600 border-slate-600 text-white')
                  : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300']">
              {{ c.l }}
            </button>
          </div>
          <span v-if="carrierRatio.total > 0" class="text-[10px] text-slate-400 ml-auto">
            СДЭК {{ carrierRatio.cdek }} ({{ carrierRatio.cdekPct }}%) · Яндекс {{ carrierRatio.yandex }} ({{ carrierRatio.yandexPct }}%)
          </span>
        </div>
      </div>


      <!-- Сводка -->
      <div class="px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs text-slate-500 flex justify-between">
        <span>{{ filteredRequests.length }} заявок</span>
        <span v-if="totalShippingCost > 0">Расходы: <b class="text-slate-700">{{ fmtCost(totalShippingCost) }}</b></span>
      </div>


      <!-- Список заявок -->
      <div class="flex-1 overflow-y-auto">
        <div v-if="loading && !requests.length" class="flex items-center justify-center h-32 text-slate-400">
          <Loader2 class="w-5 h-5 animate-spin" />
        </div>
        <div v-else-if="!filteredRequests.length" class="text-center text-sm text-slate-400 py-12 px-4">
          {{ emptyListHint }}
        </div>

        <div v-for="req in filteredRequests" :key="req.id"
          :class="['relative flex items-start border-b border-slate-100',
            selected?.id === req.id ? 'bg-emerald-50' : 'hover:bg-slate-50']">
          <!-- Цветовая метка площадки (WB / ЯМ / Ozon) -->
          <span class="absolute left-0 top-0 bottom-0 w-1 rounded-r"
            :class="mpStripeClass(req)"
            :title="marketplaceLabel(req)" />

          <button class="flex-1 text-left pl-4 pr-4 py-3 min-w-0" @click="select(req)">
            <div class="flex items-start justify-between gap-2 mb-1">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="text-sm font-medium text-slate-800 truncate">{{ req.customer_name }}</span>
              </div>
              <span :class="['text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0', statusMeta[req.status]?.color]">
                {{ statusMeta[req.status]?.label || req.status }}
              </span>
            </div>
            <div class="text-xs text-slate-500 truncate mb-1">{{ req.problem_type }} · {{ req.items_to_send }}</div>
            <div class="flex items-center gap-2 text-[10px] text-slate-400 flex-wrap">
              <span>#{{ req.id }}</span>
              <span v-if="req.order_number">· {{ req.order_number }}</span>
              <span v-if="realCost(req) > 0" class="text-emerald-600 font-medium">· {{ fmtCost(realCost(req)) }}</span>
              <span v-if="carrierLabel(req)"
                :class="['text-[9px] font-bold px-1.5 py-0.5 rounded-full',
                  req.delivery_method === 'yandex' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700']">
                {{ carrierLabel(req) }}
              </span>
              <template v-if="req.status === 'delivered' && (req.wb_before_rating || req.wb_after_rating)">
                <span class="text-slate-400">·</span>
                <span v-if="req.wb_before_rating"
                  :class="['font-bold', req.wb_before_rating >= 4 ? 'text-emerald-600' : req.wb_before_rating === 3 ? 'text-amber-500' : 'text-rose-500']">
                  {{ req.wb_before_rating }}★
                </span>
                <span v-if="req.wb_after_rating" class="text-slate-400">→</span>
                <span v-if="req.wb_after_rating"
                  :class="['font-bold', req.wb_after_rating >= 4 ? 'text-emerald-600' : req.wb_after_rating === 3 ? 'text-amber-500' : 'text-rose-500']">
                  {{ req.wb_after_rating }}★
                </span>
              </template>
              <span class="ml-auto">{{ fmt(req.created_at) }}</span>
            </div>
          </button>
        </div>
      </div>
    </div>

    <!-- ── Детали ──────────────────────────────────────────────────────────── -->
    <div :class="['min-h-0 w-full', selected ? 'overflow-y-auto flex-1' : 'hidden md:flex md:flex-1 items-center justify-center']">
      <div v-if="!selected" class="flex flex-col items-center justify-center text-slate-400 gap-3">
        <PackageCheck class="w-12 h-12 opacity-30" />
        <p class="text-sm">Выберите заявку из списка</p>
      </div>

      <div v-else class="p-6 max-w-3xl">

        <!-- Заголовок -->
        <div class="flex items-start justify-between mb-5 gap-4">
          <div class="flex items-start gap-2 min-w-0">
            <!-- Кнопка Назад (только мобильный) -->
            <button @click="selected = null" class="md:hidden mt-0.5 p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 flex-shrink-0">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
            </button>
            <div class="min-w-0">
              <div class="flex items-center gap-2 mb-1 flex-wrap">
                <h2 class="text-xl font-bold text-slate-800">{{ selected.customer_name }}</h2>
                <span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full"
                  :class="String(selected.marketplace || 'wb').toLowerCase() === 'ym'
                    ? 'bg-yellow-100 text-yellow-800'
                    : 'bg-violet-100 text-violet-700'">
                  {{ marketplaceLabel(selected) }}
                </span>
                <!-- Статус + дата + тултип с историей -->
                <div class="relative group">
                  <div class="flex items-center gap-1.5 cursor-default">
                    <span :class="['text-xs font-semibold px-2.5 py-1 rounded-full', statusMeta[selected.status]?.color]">
                      {{ statusMeta[selected.status]?.label }}
                    </span>
                    <span v-if="statusHistory.length" class="text-xs text-slate-400">
                      {{ fmtMsk(statusHistory[statusHistory.length - 1]?.changed_at) }}
                    </span>
                  </div>
                  <!-- Тултип при ховере -->
                  <div v-if="statusHistory.length"
                    class="absolute left-0 top-full mt-1.5 z-50 hidden group-hover:block bg-white border border-slate-200 rounded-xl shadow-xl p-3 min-w-[210px]">
                    <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">История статусов</p>
                    <ol class="relative border-l border-slate-200 ml-1.5 space-y-2">
                      <!-- Синтетическая запись "Новая" если лог начинается не с неё -->
                      <li v-if="statusHistory[0]?.new_status !== 'new'" class="pl-3 relative">
                        <span class="absolute -left-[4px] top-1 w-2 h-2 rounded-full border-2 border-white bg-slate-300" />
                        <span :class="['inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded-full', statusMeta['new']?.color]">Новая</span>
                        <div class="text-[9px] text-slate-400 leading-tight">{{ fmt(selected.created_at) }}</div>
                      </li>
                      <li v-for="(entry, i) in statusHistory" :key="i" class="pl-3 relative">
                        <span :class="['absolute -left-[4px] top-1 w-2 h-2 rounded-full border-2 border-white',
                          i === statusHistory.length - 1 ? 'bg-slate-700' : 'bg-slate-300']" />
                        <span :class="['inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded-full',
                          statusMeta[entry.new_status]?.color || 'bg-slate-100 text-slate-600']">
                          {{ statusMeta[entry.new_status]?.label || entry.new_status }}
                        </span>
                        <div class="text-[9px] text-slate-400 leading-tight">
                          {{ fmtMsk(entry.changed_at) }}
                          <span v-if="entry.changed_by" class="ml-1 text-slate-500">· {{ entry.changed_by }}</span>
                        </div>
                      </li>
                    </ol>
                  </div>
                </div>
              </div>
              <p class="text-sm text-slate-400">Заявка #{{ selected.id }}</p>
            </div>
          </div>
          <button @click="selected = null" class="hidden md:block p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 flex-shrink-0">
            <X class="w-5 h-5" />
          </button>
        </div>

        <!-- Контакты + WB чат / СДЭК стоимость (2 колонки) -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
          <div class="bg-white rounded-xl border border-slate-200 p-3">
            <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">Контакты</p>
            <div class="space-y-1 text-slate-700">
              <div class="font-medium flex items-center gap-1.5 text-xs">
                <Phone class="w-3 h-3 text-slate-400 flex-shrink-0" />{{ selected.customer_phone }}
              </div>
              <div v-if="selected.customer_email" class="text-slate-500 text-xs flex items-center gap-1.5">
                <Mail class="w-3 h-3 text-slate-400 flex-shrink-0" />{{ selected.customer_email }}
              </div>
              <div v-if="selected.order_number" class="text-[10px] text-slate-400 pt-0.5">
                № <span class="font-mono font-bold text-slate-600">{{ selected.order_number }}</span>
              </div>
            </div>
          </div>
          <!-- Для склада: стоимость доставки рядом с контактами -->
          <div v-if="isWarehouseOnly && (realCost(selected) > 0 || selected.cdek_number)"
            class="bg-emerald-50 rounded-xl border border-emerald-200 p-3 flex items-center gap-2">
            <div class="w-7 h-7 bg-emerald-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <Truck class="w-3.5 h-3.5 text-emerald-600" />
            </div>
            <div>
              <p class="text-[10px] text-emerald-600 font-semibold uppercase tracking-wider">
                Доставка {{ selected.delivery_method === 'yandex' ? 'Яндекс' : 'СДЭК' }}
              </p>
              <p class="text-base font-bold text-emerald-800">{{ fmtCost(realCost(selected)) }}</p>
              <p v-if="selected.delivery_method === 'yandex' && selected.track_number" class="text-[10px] font-mono text-emerald-600 mt-0.5">{{ selected.track_number }}</p>
              <p v-else-if="selected.cdek_number" class="text-[10px] font-mono text-emerald-600 mt-0.5">{{ selected.cdek_number }}</p>
            </div>
          </div>
          <!-- Чаты WB / ЯМ (только для КС/админа, не для склада) -->
          <div v-if="!isWarehouseOnly && (selected.wb_chat_id || selected.ym_chat_id)" class="bg-violet-50 rounded-xl border border-violet-200 p-3">
            <p class="text-[10px] font-bold text-violet-500 uppercase tracking-wider mb-2 flex items-center gap-1">
              <MessageCircle class="w-3 h-3" /> Чаты
            </p>
            <!-- Статусы каналов -->
            <div class="flex gap-1.5 mb-2">
              <!-- WB -->
              <div class="flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-1"
                :class="selected.wb_chat_id ? 'border-violet-200 bg-violet-100' : 'border-slate-200 bg-slate-50'">
                <span class="text-[10px] font-bold uppercase tracking-wider"
                  :class="selected.wb_chat_id ? 'text-violet-500' : 'text-slate-400'">WB</span>
                <span class="text-[11px] font-bold" :class="selected.wb_chat_id ? 'text-violet-600' : 'text-slate-300'">
                  {{ selected.wb_chat_id ? '✓' : '✗' }}
                </span>
              </div>
              <!-- ЯМ -->
              <div class="flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-1"
                :class="selected.ym_chat_id ? 'border-yellow-200 bg-yellow-50' : 'border-slate-200 bg-slate-50'">
                <span class="text-[10px] font-bold uppercase tracking-wider"
                  :class="selected.ym_chat_id ? 'text-yellow-700' : 'text-slate-400'">ЯМ</span>
                <span class="text-[11px] font-bold" :class="selected.ym_chat_id ? 'text-yellow-700' : 'text-slate-300'">
                  {{ selected.ym_chat_id ? '✓' : '✗' }}
                </span>
              </div>
              <!-- TG -->
              <div class="flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-1"
                :class="selected.salebot_client_id && selected.salebot_messenger === 'Telegram' && !selected.salebot_blocked ? 'border-sky-200 bg-sky-50' : 'border-slate-200 bg-slate-50'">
                <span class="text-[10px] font-bold uppercase tracking-wider"
                  :class="selected.salebot_client_id && selected.salebot_messenger === 'Telegram' && !selected.salebot_blocked ? 'text-sky-500' : 'text-slate-400'">TG</span>
                <span class="text-[11px] font-bold"
                  :class="selected.salebot_client_id && selected.salebot_messenger === 'Telegram' && !selected.salebot_blocked ? 'text-sky-600' : 'text-slate-300'">
                  {{ selected.salebot_client_id && selected.salebot_messenger === 'Telegram' && !selected.salebot_blocked ? '✓' : '✗' }}
                </span>
              </div>
              <!-- MAX -->
              <div class="flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-1"
                :class="selected.salebot_client_id && selected.salebot_messenger === 'Max' && !selected.salebot_blocked ? 'border-violet-200 bg-violet-100' : 'border-slate-200 bg-slate-50'">
                <span class="text-[10px] font-bold uppercase tracking-wider"
                  :class="selected.salebot_client_id && selected.salebot_messenger === 'Max' && !selected.salebot_blocked ? 'text-violet-500' : 'text-slate-400'">MAX</span>
                <span class="text-[11px] font-bold"
                  :class="selected.salebot_client_id && selected.salebot_messenger === 'Max' && !selected.salebot_blocked ? 'text-violet-600' : 'text-slate-300'">
                  {{ selected.salebot_client_id && selected.salebot_messenger === 'Max' && !selected.salebot_blocked ? '✓' : '✗' }}
                </span>
              </div>
            </div>
            <p v-if="selected.salebot_blocked" class="text-[10px] text-amber-500 italic mb-1.5">⚠ Клиент заблокировал бота</p>
            <p v-if="selected.wb_chat_id" class="text-xs text-violet-400 italic mb-1.5">Если чат не появился — прокрути левую панель WB вниз</p>
            <div class="flex gap-1.5">
              <a v-if="selected.wb_chat_id" :href="wbChatUrl(selected.wb_chat_id)" target="_blank"
                class="flex items-center justify-center gap-1.5 text-[10px] px-2 py-1.5 bg-violet-600 hover:bg-violet-700 text-white font-semibold rounded-lg transition-colors flex-1">
                <ExternalLink class="w-3 h-3" /> Открыть чат WB
              </a>
              <a v-if="selected.ym_chat_id" :href="ymChatUrl(selected)" target="_blank"
                class="flex items-center justify-center gap-1.5 text-[10px] px-2 py-1.5 bg-yellow-500 hover:bg-yellow-600 text-white font-semibold rounded-lg transition-colors flex-1">
                <ExternalLink class="w-3 h-3" /> Открыть чат ЯМ
              </a>
            </div>
            <p v-if="selected.ym_chat_id && selected.ym_context_article" class="text-[10px] text-slate-500 mt-1.5">
              Товар: <span class="font-mono font-semibold text-slate-700">{{ selected.ym_context_article }}</span>
              <span v-if="selected.ym_order_id"> · заказ {{ selected.ym_order_id }}</span>
            </p>
          </div>
          <div v-else-if="!isWarehouseOnly" class="bg-slate-50 rounded-xl border border-slate-200 p-3 flex items-center justify-center">
            <span class="text-[10px] text-slate-400 italic">Чат не привязан</span>
          </div>
        </div>

        <!-- ── WB: заказ + претензия + отзыв ─── -->
        <div v-if="selected.wb_chat_id || selected.wb_nm_id" class="mb-3">
          <div v-if="wbCtxLoading" class="flex items-center gap-2 text-xs text-slate-400 py-2 px-1">
            <Loader2 class="w-3.5 h-3.5 animate-spin" /> Загружаем данные WB...
          </div>
          <template v-else-if="wbCtx">
            <!-- Заказ + Претензия (2 колонки) -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2">
              <div v-if="wbCtx.order" class="bg-slate-50 rounded-xl border border-slate-200 p-3">
                <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center gap-1">
                  <ShoppingCart class="w-3 h-3" /> Заказ WB
                </p>
                <div class="space-y-0.5 text-xs text-slate-600">
                  <div v-if="wbCtx.order.supplier_article">
                    <span class="text-slate-400">Арт:</span>
                    <span class="font-mono font-bold ml-1">{{ wbCtx.order.supplier_article }}</span>
                    <a v-if="selected.wb_nm_id" :href="`https://www.wildberries.ru/catalog/${selected.wb_nm_id}/detail.aspx`"
                      target="_blank" class="font-mono text-violet-500 hover:underline ml-1 text-[10px]">({{ selected.wb_nm_id }})</a>
                  </div>
                  <div v-else-if="selected.wb_nm_id">
                    <span class="text-slate-400">Арт:</span>
                    <a :href="`https://www.wildberries.ru/catalog/${selected.wb_nm_id}/detail.aspx`"
                      target="_blank" class="font-mono font-bold text-violet-500 hover:underline ml-1">{{ selected.wb_nm_id }}</a>
                  </div>
                  <div v-if="wbCtx.order.dt">
                    <span class="text-slate-400">Дата:</span>
                    <span class="ml-1">{{ fmt(wbCtx.order.dt) }}</span>
                  </div>
                  <div v-if="wbCtx.order.finish_price">
                    <span class="text-slate-400">Сумма:</span>
                    <span class="font-bold text-emerald-700 ml-1">{{ fmtCost(wbCtx.order.finish_price) }}</span>
                  </div>
                  <div v-if="wbCtx.order.warehouse_name">
                    <span class="text-slate-400">Склад:</span>
                    <span class="ml-1 text-[10px]">{{ wbCtx.order.warehouse_name }}</span>
                  </div>
                </div>
              </div>
              <div v-else class="bg-slate-50 rounded-xl border border-slate-200 p-3 flex items-center justify-center">
                <span class="text-[10px] text-slate-400 italic">Заказ не найден</span>
              </div>

              <div v-if="wbCtx.claim" class="bg-rose-50 rounded-xl border border-rose-200 p-3">
                <p class="text-[10px] font-bold text-rose-500 uppercase tracking-wider mb-1.5 flex items-center gap-1">
                  <RotateCcw class="w-3 h-3" /> Претензия WB
                </p>
                <div class="space-y-0.5 text-xs text-slate-600">
                  <div v-if="wbCtx.claim.claim_type">
                    <span class="text-rose-400">Тип:</span>
                    <span class="font-medium ml-1">{{ wbCtx.claim.claim_type }}</span>
                  </div>
                  <div>
                    <span class="text-rose-400">Статус:</span>
                    <span class="ml-1">{{ wbCtx.claim.status }}</span>
                  </div>
                  <div v-if="wbCtx.claim.user_comment" class="mt-1 text-[10px] text-slate-600 leading-relaxed bg-white rounded p-1.5 border border-rose-100">
                    {{ wbCtx.claim.user_comment }}
                  </div>
                  <div v-if="wbCtx.claim.photos" class="flex flex-wrap gap-1 mt-1">
                    <button v-for="(url, i) in parsePhotos(wbCtx.claim.photos)" :key="i"
                      @click="openLightbox(parsePhotos(wbCtx.claim.photos), i)"
                      class="text-[10px] px-1.5 py-0.5 bg-rose-100 text-rose-700 rounded hover:bg-rose-200 flex items-center gap-0.5">
                      <RotateCcw class="w-2 h-2" /> {{ i + 1 }}
                    </button>
                  </div>
                </div>
              </div>
              <div v-else class="bg-slate-50 rounded-xl border border-slate-200 p-3 flex items-center justify-center">
                <span class="text-[10px] text-slate-400 italic">Претензий нет</span>
              </div>
            </div>

            <!-- Отзыв клиента: текущий (точная привязка через shk_id) -->
            <div v-if="wbCtx.feedback" class="bg-amber-50 rounded-xl border border-amber-200 p-3">
              <div class="flex items-center gap-1.5 text-[10px] font-semibold text-amber-600 uppercase tracking-wider mb-2">
                <Star class="w-3 h-3" /> Отзыв клиента
              </div>
              <div class="flex items-center gap-1 mb-1">
                <span v-for="n in 5" :key="n">
                  <Star class="w-3 h-3" :class="n <= wbCtx.feedback.valuation ? 'text-amber-400 fill-amber-400' : 'text-slate-300'" />
                </span>
                <span class="text-[10px] text-slate-400 ml-1">{{ fmt(wbCtx.feedback.created_date) }}</span>
              </div>
              <div v-if="wbCtx.feedback.text" class="text-[10px] text-slate-700 leading-relaxed bg-white rounded p-1.5 border border-amber-100 mb-1">{{ wbCtx.feedback.text }}</div>
              <div v-if="wbCtx.feedback.pros" class="text-[10px] text-emerald-700">+ {{ wbCtx.feedback.pros }}</div>
              <div v-if="wbCtx.feedback.cons" class="text-[10px] text-rose-600">− {{ wbCtx.feedback.cons }}</div>
            </div>

            <!-- Динамика отзыва: ДО → ПОСЛЕ переотправки -->
            <div v-if="wbCtx.feedback_before || wbCtx.feedback_after" class="bg-violet-50 rounded-xl border border-violet-200 p-3">
              <div class="text-[10px] font-semibold text-violet-600 uppercase tracking-wider mb-2">Динамика отзыва</div>
              <div class="flex items-center gap-3">
                <!-- ДО -->
                <div class="flex-1 text-center">
                  <div class="text-[9px] text-slate-400 mb-1">До отправки</div>
                  <div v-if="wbCtx.feedback_before" class="flex justify-center gap-0.5">
                    <span v-for="n in 5" :key="n">
                      <Star class="w-3 h-3" :class="n <= wbCtx.feedback_before.valuation ? 'text-amber-400 fill-amber-400' : 'text-slate-200'" />
                    </span>
                  </div>
                  <div v-else class="text-[10px] text-slate-400 italic">нет отзыва</div>
                  <div v-if="wbCtx.feedback_before?.text" class="text-[9px] text-slate-500 mt-1 leading-tight line-clamp-2">{{ wbCtx.feedback_before.text }}</div>
                </div>
                <!-- Стрелка -->
                <div class="text-slate-300 text-lg">→</div>
                <!-- ПОСЛЕ -->
                <div class="flex-1 text-center">
                  <div class="text-[9px] text-slate-400 mb-1">После получения</div>
                  <div v-if="wbCtx.feedback_after" class="flex justify-center gap-0.5">
                    <span v-for="n in 5" :key="n">
                      <Star class="w-3 h-3" :class="n <= wbCtx.feedback_after.valuation ? 'text-emerald-500 fill-emerald-500' : 'text-slate-200'" />
                    </span>
                  </div>
                  <div v-else class="text-[10px] text-slate-400 italic">ещё нет</div>
                  <div v-if="wbCtx.feedback_after?.text" class="text-[9px] text-slate-600 mt-1 leading-tight line-clamp-2">{{ wbCtx.feedback_after.text }}</div>
                </div>
              </div>
            </div>

          </template>
        </div>

        <!-- Варианты доставки: СДЭК vs Яндекс — КС согласовывает до одобрения -->
        <div v-if="!isWarehouseOnly && isCS && ['new','matched'].includes(selected.status)" class="mb-3">
          <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">Варианты доставки — выберите выгодный</p>
          <div v-if="candidatesLoading" class="flex items-center gap-2 text-slate-400 text-xs py-2">
            <Loader2 class="w-3.5 h-3.5 animate-spin" /> Считаем оба варианта...
          </div>
          <div v-else-if="candidatesData" class="space-y-2">
            <div class="grid grid-cols-2 gap-2">
              <button type="button" :disabled="!candidatesData.cdek?.available"
                @click="pickCarrier('cdek')"
                class="text-left p-3 rounded-xl border-2 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                :class="selectedCarrier === 'cdek' && candidatesData.cdek?.available ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 bg-white hover:border-emerald-300'">
                <div class="flex items-center gap-1.5 mb-1">
                  <Truck class="w-3.5 h-3.5 text-emerald-600" />
                  <span class="text-xs font-bold text-slate-700">СДЭК</span>
                  <CheckCircle2 v-if="selectedCarrier === 'cdek' && candidatesData.cdek?.available" class="w-3.5 h-3.5 text-emerald-600 ml-auto" />
                </div>
                <p v-if="candidatesData.cdek?.available" class="text-base font-bold text-emerald-800">{{ fmtCost(candidatesData.cdek.cost) }}</p>
                <p v-else class="text-xs text-slate-400 italic">не подобрано</p>
                <p v-if="candidatesData.cdek?.pvz_address" class="text-[10px] text-slate-500 mt-0.5 leading-snug line-clamp-2">{{ candidatesData.cdek.pvz_address }}</p>
                <p v-if="candidatesData.cdek?.distance_km != null" class="text-[10px] text-emerald-700 mt-1 font-medium flex items-center gap-0.5">
                  <MapPin class="w-2.5 h-2.5 flex-shrink-0" />{{ fmtDistance(candidatesData.cdek.distance_km) }}
                </p>
              </button>
              <button type="button" :disabled="!candidatesData.yandex?.available"
                @click="pickCarrier('yandex')"
                class="text-left p-3 rounded-xl border-2 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                :class="selectedCarrier === 'yandex' && candidatesData.yandex?.available ? 'border-amber-500 bg-amber-50' : 'border-slate-200 bg-white hover:border-amber-300'">
                <div class="flex items-center gap-1.5 mb-1">
                  <Truck class="w-3.5 h-3.5 text-amber-600" />
                  <span class="text-xs font-bold text-slate-700">Яндекс</span>
                  <CheckCircle2 v-if="selectedCarrier === 'yandex' && candidatesData.yandex?.available" class="w-3.5 h-3.5 text-amber-600 ml-auto" />
                </div>
                <p v-if="candidatesData.yandex?.available" class="text-base font-bold text-amber-800">{{ fmtCost(candidatesData.yandex.cost) }}</p>
                <p v-else class="text-xs text-slate-400 italic">не подобрано</p>
                <p v-if="candidatesData.yandex?.pvz_address" class="text-[10px] text-slate-500 mt-0.5 leading-snug line-clamp-2">{{ candidatesData.yandex.pvz_address }}</p>
                <p v-if="candidatesData.yandex?.distance_km != null" class="text-[10px] text-amber-700 mt-1 font-medium flex items-center gap-0.5">
                  <MapPin class="w-2.5 h-2.5 flex-shrink-0" />{{ fmtDistance(candidatesData.yandex.distance_km) }}
                </p>
              </button>
            </div>
            <div v-if="hasPvzMapPoints" class="rounded-xl overflow-hidden border border-slate-200 bg-slate-100">
              <div ref="pvzMapEl" class="w-full h-[180px]"></div>
              <p v-if="pvzMapError" class="px-2.5 py-1.5 text-[10px] text-rose-600 bg-white border-t border-slate-100">{{ pvzMapError }}</p>
              <div v-else class="px-2.5 py-1.5 bg-white border-t border-slate-100 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-slate-500">
                <span class="inline-flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-slate-600"></span>Адрес клиента</span>
                <span class="inline-flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-emerald-600"></span>СДЭК</span>
                <span class="inline-flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-amber-500"></span>Яндекс</span>
                <span class="ml-auto text-slate-400">Яндекс.Карты</span>
              </div>
            </div>
          </div>
          <!-- Кнопка повтора: всегда для new/matched, чтобы можно было пересчитать -->
          <div v-if="candidatesData" class="mt-2">
            <button @click="refreshCandidates" :disabled="refreshingCandidates"
              class="flex items-center gap-1.5 text-xs text-slate-500 hover:text-emerald-700 transition-colors disabled:opacity-50">
              <RefreshCw class="w-3 h-3" :class="refreshingCandidates ? 'animate-spin' : ''" />
              {{ refreshingCandidates ? 'Ищем...' : 'Найти цены повторно' }}
            </button>
          </div>
        </div>

        <!-- После одобрения — только фактически выбранный перевозчик (для КС/склада) -->
        <div v-if="!isWarehouseOnly && !['new','matched'].includes(selected.status) && realCost(selected) > 0"
          class="grid gap-2 mb-3 grid-cols-1">
          <div class="bg-emerald-50 rounded-xl border border-emerald-200 p-3 flex items-center gap-2">
            <div class="w-7 h-7 bg-emerald-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <Truck class="w-3.5 h-3.5 text-emerald-600" />
            </div>
            <div>
              <p class="text-[10px] text-emerald-600 font-semibold uppercase tracking-wider">
                Доставка {{ selected.delivery_method === 'yandex' ? 'Яндекс' : 'СДЭК' }}
              </p>
              <p class="text-base font-bold text-emerald-800">
                {{ fmtCost(realCost(selected)) }}
              </p>
            </div>
          </div>
        </div>

        <!-- Экономика отправки (из финотчёта WB) -->
        <div v-if="!isWarehouseOnly" class="bg-white rounded-xl border border-slate-200 p-4 mb-3">
          <button @click="showEconomics = !showEconomics"
            class="w-full flex items-center justify-between text-xs font-bold text-slate-500 uppercase tracking-wider">
            <span class="flex items-center gap-1.5"><TrendingUp class="w-3.5 h-3.5" /> Экономика отправки</span>
            <span v-if="economicsLoading" class="text-slate-400">
              <Loader2 class="w-3 h-3 animate-spin inline" />
            </span>
            <span v-else class="text-slate-400 font-normal normal-case">{{ showEconomics ? 'Скрыть' : 'Показать' }}</span>
          </button>

          <template v-if="showEconomics && economicsData">
            <div class="mt-2 mb-2 flex items-center gap-1.5 flex-wrap">
              <span v-if="economicsData.has_report_data"
                class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium">данные из финотчёта WB</span>
              <span v-else
                class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium">нет данных финотчёта</span>
              <span class="text-[10px] text-slate-400 italic">* доставка WB→ПВЗ — среднее по артикулу</span>
            </div>

            <div class="space-y-2 text-xs">

              <!-- Отправить деталь -->
              <div class="p-2.5 rounded-lg bg-emerald-50 border border-emerald-200">
                <div class="font-semibold text-emerald-700 mb-1.5">Отправить деталь · продажа сохранена</div>
                <div v-if="economicsData.variants.v3.breakdown.ppvz_for_pay != null" class="flex justify-between text-emerald-600">
                  <span>Выплата WB <span class="text-[10px] font-normal opacity-70">(после вычета комиссий WB)</span></span>
                  <span class="font-medium">+{{ fmtCost(economicsData.variants.v3.breakdown.ppvz_for_pay) }}</span>
                </div>
                <div class="flex justify-between" :class="economicsData.variants.v3.breakdown.cog > 0 ? 'text-rose-500' : 'text-slate-400'">
                  <span>Себестоимость товара <span v-if="economicsData.cog_exact" class="text-[10px] font-normal opacity-60">(точная)</span><span v-else class="text-[10px] font-normal opacity-60">(по артикулу)</span></span>
                  <span class="font-medium">{{ economicsData.variants.v3.breakdown.cog > 0 ? '−' : '' }}{{ fmtCost(economicsData.variants.v3.breakdown.cog) }}</span>
                </div>
                <div class="flex justify-between text-rose-500">
                  <span>Доставка {{ economicsData.carrier === 'yandex' ? 'Яндекс' : 'СДЭК' }} клиенту</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v3.breakdown.delivery_cost ?? economicsData.delivery_cost) }}</span>
                </div>
                <div v-if="economicsData.variants.v3.net_profit != null"
                  :class="['flex justify-between font-bold border-t mt-1.5 pt-1.5',
                    economicsData.variants.v3.net_profit >= 0 ? 'text-emerald-800 border-emerald-200' : 'text-rose-700 border-rose-200']">
                  <span>Чистая прибыль</span>
                  <span>{{ economicsData.variants.v3.net_profit >= 0 ? '+' : '' }}{{ fmtCost(economicsData.variants.v3.net_profit) }}</span>
                </div>
                <div v-if="economicsData.variants.v3.benefit_vs_v1 != null"
                  class="flex justify-between text-[10px] text-slate-500 mt-1 border-t border-slate-100 pt-1">
                  <span>Выгода vs «Возврат денег, остаётся у клиента»</span>
                  <span :class="['font-semibold', economicsData.variants.v3.benefit_vs_v1 >= 0 ? 'text-emerald-600' : 'text-rose-500']">
                    {{ economicsData.variants.v3.benefit_vs_v1 >= 0 ? '+' : '' }}{{ fmtCost(economicsData.variants.v3.benefit_vs_v1) }}
                  </span>
                </div>
              </div>

              <!-- Возврат денег, товар у клиента -->
              <div class="p-2.5 rounded-lg bg-rose-50 border border-rose-100">
                <div class="font-semibold text-rose-700 mb-1.5">Возврат денег · товар остаётся у клиента</div>
                <div class="flex justify-between" :class="economicsData.variants.v1.breakdown.cog > 0 ? 'text-rose-500' : 'text-slate-400'">
                  <span>Себестоимость товара <span class="text-[10px] font-normal opacity-70">(товар потерян)</span></span>
                  <span class="font-medium">{{ economicsData.variants.v1.breakdown.cog > 0 ? '−' : '' }}{{ fmtCost(economicsData.variants.v1.breakdown.cog) }}</span>
                </div>
                <div class="flex justify-between" :class="economicsData.variants.v1.breakdown.acquiring > 0 ? 'text-rose-500' : 'text-slate-400'">
                  <span>Эквайринг при возврате
                    <span class="text-[10px] font-normal opacity-70">(не возмещается)</span>
                    <span v-if="economicsData.acquiring_fee_return_estimated" class="text-[10px] font-normal opacity-60 ml-0.5">расч. ~4%</span>
                  </span>
                  <span class="font-medium">{{ economicsData.variants.v1.breakdown.acquiring > 0 ? '−' : '' }}{{ fmtCost(economicsData.variants.v1.breakdown.acquiring) }}</span>
                </div>
                <div v-if="economicsData.variants.v1.breakdown.rebill > 0" class="flex justify-between text-rose-400">
                  <span>Сбор WB за логистику возврата</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v1.breakdown.rebill) }}</span>
                </div>
                <div v-if="economicsData.variants.v1.breakdown.penalty > 0" class="flex justify-between text-rose-400">
                  <span>Штраф WB</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v1.breakdown.penalty) }}</span>
                </div>
                <div v-if="economicsData.variants.v1.breakdown.delivery > 0" class="flex justify-between text-rose-400">
                  <span>Доставка (WB)</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v1.breakdown.delivery) }}</span>
                </div>
                <div class="flex justify-between text-rose-700 font-bold border-t border-rose-200 mt-1.5 pt-1.5">
                  <span>Итого потери</span>
                  <span>−{{ fmtCost(economicsData.variants.v1.total_loss) }}</span>
                </div>
              </div>

              <!-- Возврат денег, товар к нам на ПВЗ -->
              <div class="p-2.5 rounded-lg bg-amber-50 border border-amber-100">
                <div class="font-semibold text-amber-700 mb-1.5">Возврат денег · товар возвращается к нам на ПВЗ</div>
                <div class="flex justify-between" :class="economicsData.variants.v2.breakdown.cog > 0 ? 'text-rose-500' : 'text-slate-400'">
                  <span>Себестоимость товара <span class="text-[10px] font-normal opacity-60" v-if="economicsData.cog_exact">(точная)</span><span class="text-[10px] font-normal opacity-60" v-else>(по артикулу)</span></span>
                  <span class="font-medium">{{ economicsData.variants.v2.breakdown.cog > 0 ? '−' : '' }}{{ fmtCost(economicsData.variants.v2.breakdown.cog) }}</span>
                </div>
                <div class="flex justify-between" :class="economicsData.variants.v2.breakdown.acquiring > 0 ? 'text-amber-600' : 'text-slate-400'">
                  <span>Эквайринг при возврате
                    <span class="text-[10px] font-normal opacity-70">(не возмещается)</span>
                    <span v-if="economicsData.acquiring_fee_return_estimated" class="text-[10px] font-normal opacity-60 ml-0.5">расч. ~4%</span>
                  </span>
                  <span class="font-medium">{{ economicsData.variants.v2.breakdown.acquiring > 0 ? '−' : '' }}{{ fmtCost(economicsData.variants.v2.breakdown.acquiring) }}</span>
                </div>
                <div class="flex justify-between text-amber-600">
                  <span>Доставка WB → наш ПВЗ <span class="text-[10px] font-normal opacity-70">(~среднее)</span></span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v2.breakdown.return_delivery) }}</span>
                </div>
                <div v-if="economicsData.variants.v2.breakdown.rebill > 0" class="flex justify-between text-amber-500">
                  <span>Сбор WB за логистику</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v2.breakdown.rebill) }}</span>
                </div>
                <div v-if="economicsData.variants.v2.breakdown.penalty > 0" class="flex justify-between text-amber-500">
                  <span>Штраф WB</span>
                  <span class="font-medium">−{{ fmtCost(economicsData.variants.v2.breakdown.penalty) }}</span>
                </div>
                <div class="flex justify-between text-amber-700 font-bold border-t border-amber-200 mt-1.5 pt-1.5">
                  <span>Итого потери</span>
                  <span>−{{ fmtCost(economicsData.variants.v2.total_loss) }}</span>
                </div>
              </div>

            </div>
          </template>

          <!-- Коллапс: краткая строка -->
          <div v-else-if="!showEconomics && economicsData && economicsData.variants" class="mt-1.5 text-xs flex justify-between items-center">
            <span class="text-slate-500">Отправить деталь:</span>
            <span v-if="economicsData.variants.v3.net_profit != null"
              :class="['font-bold', economicsData.variants.v3.net_profit >= 0 ? 'text-emerald-600' : 'text-rose-500']">
              {{ economicsData.variants.v3.net_profit >= 0 ? '+' : '' }}{{ fmtCost(economicsData.variants.v3.net_profit) }}
            </span>
            <span v-else class="text-slate-400 italic">нет данных о выплате</span>
          </div>
          <div v-else-if="economicsLoading" class="mt-2 text-[10px] text-slate-400 italic">Загружаем данные...</div>
        </div>

        <!-- Проблема + ПВЗ (2 колонки) -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
          <div class="bg-white rounded-xl border border-slate-200 p-3">
            <div class="flex items-center gap-1.5 mb-1.5 flex-wrap">
              <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Проблема</p>
              <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700">{{ selected.problem_type }}</span>
            </div>
            <div class="flex items-center gap-1 mb-1.5">
              <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Категория:</p>
              <span class="text-[10px] font-semibold" :class="selected.product_category ? 'text-slate-600' : 'text-slate-400 italic'">{{ selected.product_category || 'Не указана' }}</span>
            </div>
            <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Что отправить</p>
            <p class="text-xs font-medium text-slate-800 leading-snug">{{ selected.items_to_send }}</p>
          </div>
          <!-- Адрес клиента (что ввёл клиент) -->
          <div class="bg-white rounded-xl border border-slate-200 p-3">
            <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">Адрес клиента</p>
            <div class="flex items-start gap-1.5">
              <MapPin class="w-3.5 h-3.5 text-blue-400 flex-shrink-0 mt-0.5" />
              <p class="text-xs text-slate-700 leading-snug">{{ selected.shipping_address || selected.client_pvz_address || '—' }}</p>
            </div>
          </div>
          <!-- ПВЗ назначен (после одобрения) -->
          <div v-if="selected.delivery_method" class="bg-white rounded-xl border border-slate-200 p-3">
            <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
              ПВЗ клиента ({{ selected.delivery_method === 'yandex' ? 'Яндекс' : 'СДЭК' }})
            </p>
            <div class="flex items-start gap-1.5">
              <MapPin class="w-3.5 h-3.5 text-emerald-500 flex-shrink-0 mt-0.5" />
              <div>
                <p class="text-xs font-medium text-slate-700 leading-snug">{{ selected.client_pvz_city }}</p>
                <p class="text-[10px] text-slate-500 mt-0.5">{{ selected.client_pvz_address || '—' }}</p>
                <p v-if="selected.client_pvz_code" class="text-[10px] text-slate-400 font-mono">{{ selected.client_pvz_code }}</p>
              </div>
            </div>
          </div>
        </div>

        <!-- Фото из заявки -->
        <div v-if="selected.photo_files" class="bg-white rounded-xl border border-slate-200 p-4 mb-3">
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Фото</p>
          <div class="flex flex-wrap gap-2">
            <button v-for="(url, i) in JSON.parse(selected.photo_files || '[]')" :key="i"
              @click="openLightbox(JSON.parse(selected.photo_files || '[]'), i)"
              class="flex items-center gap-1.5 text-xs text-emerald-600 hover:text-emerald-700 bg-emerald-50 hover:bg-emerald-100 px-2.5 py-1.5 rounded-lg border border-emerald-100 transition-colors">
              <span class="text-emerald-400">⊞</span> Фото {{ i + 1 }}
            </button>
          </div>
        </div>

        <!-- Подтверждение получения -->
        <div v-if="selected.confirmed_at" class="bg-emerald-50 rounded-xl border border-emerald-200 p-4 mb-4">
          <div class="flex items-center justify-between mb-2">
            <div>
              <p class="text-xs font-bold text-emerald-600 uppercase tracking-wider mb-0.5">Детали получены</p>
              <p class="text-sm text-emerald-700">{{ fmt(selected.confirmed_at) }}</p>
            </div>
          </div>
          <template v-if="!isWarehouseOnly && selected.review_step >= 1">
            <div class="border-t border-emerald-200 pt-2 mt-1 space-y-1">
              <div v-if="selected.review_msg1_at" class="flex items-center justify-between text-xs">
                <span class="text-emerald-700">Запрос отзыва</span>
                <span class="text-emerald-500 text-[10px]">{{ fmt(selected.review_msg1_at) }}</span>
              </div>
              <div v-if="selected.review_msg2_at" class="flex items-center justify-between text-xs">
                <span class="text-emerald-700">1-е напоминание</span>
                <span class="text-emerald-500 text-[10px]">{{ fmt(selected.review_msg2_at) }}</span>
              </div>
              <div v-if="selected.review_msg3_at" class="flex items-center justify-between text-xs">
                <span class="text-emerald-700">Финальное напоминание</span>
                <span class="text-emerald-500 text-[10px]">{{ fmt(selected.review_msg3_at) }}</span>
              </div>
              <div v-if="selected.review_step >= 3" class="text-[10px] text-emerald-500 italic">Цепочка завершена</div>
            </div>
          </template>
        </div>

        <!-- Причина отклонения склада -->
        <div v-if="selected.rejected_by === 'warehouse' && selected.rejection_reason"
          class="bg-rose-50 rounded-xl border border-rose-200 p-4 mb-4">
          <p class="text-xs font-bold text-rose-500 uppercase tracking-wider mb-1">Причина отклонения (склад)</p>
          <p class="text-sm text-rose-700">{{ selected.rejection_reason }}</p>
          <p v-if="selected.processed_by" class="text-xs text-rose-400 mt-1">— {{ selected.processed_by }}, {{ fmt(selected.processed_at) }}</p>
        </div>

        <!-- Комментарий КС -->
        <div v-if="selected.moderator_comment" class="bg-slate-50 rounded-xl border border-slate-200 p-4 mb-4">
          <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Комментарий КС</p>
          <p class="text-sm text-slate-600">{{ selected.moderator_comment }}</p>
          <p v-if="selected.processed_by" class="text-xs text-slate-400 mt-1">— {{ selected.processed_by }}, {{ fmt(selected.processed_at) }}</p>
        </div>

        <!-- Промокод -->
        <div v-if="selected.promo_code" class="bg-violet-50 rounded-xl border border-violet-200 p-4 mb-4">
          <p class="text-xs font-bold text-violet-500 uppercase tracking-wider mb-1">Промокод</p>
          <div class="flex items-center gap-2">
            <span class="font-mono font-bold text-violet-700 text-sm">{{ selected.promo_code }}</span>
            <span class="text-xs text-violet-500">— скидка 15%</span>
          </div>
          <p class="text-[10px] text-violet-400 mt-1">
            {{ selected.promo_sent_at ? `Отправлен ${fmt(selected.promo_sent_at)}` : 'Ожидает отправки (через 10 сек после отказа)' }}
          </p>
        </div>

        <!-- Ссылка подтверждения (после создания заказа) -->
        <div v-if="confirmUrl" class="bg-violet-50 rounded-xl border border-violet-200 p-4 mb-4">
          <p class="text-xs font-bold text-violet-500 uppercase tracking-wider mb-1">Ссылка для клиента</p>
          <p class="text-xs text-violet-600 mb-3">Отправьте клиенту — по ней он может подтвердить получение</p>
          <div class="flex items-center gap-2">
            <code class="flex-1 text-xs bg-white border border-violet-200 rounded-lg px-3 py-2 text-violet-700 truncate">{{ confirmUrl }}</code>
            <button @click="copyUrl(confirmUrl)"
              class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-2 bg-violet-600 hover:bg-violet-700 text-white font-semibold rounded-lg transition-colors">
              <Copy class="w-3.5 h-3.5" />{{ copied ? 'Скопировано!' : 'Копировать' }}
            </button>
          </div>
        </div>

        <!-- Результат отклонения складом -->
        <div v-if="panel === 'warehouse_rejected' && lastShipResult" class="bg-amber-50 rounded-xl border border-amber-200 p-4 mb-4">
          <p class="text-xs font-bold text-amber-600 uppercase tracking-wider mb-2">Заявка отклонена</p>
          <div v-if="lastShipResult.email_sent" class="text-xs text-emerald-700 flex items-center gap-1.5 mb-2">
            <CheckCircle2 class="w-3.5 h-3.5" /> Email уведомление отправлено клиенту
          </div>
          <div v-if="!lastShipResult.email_sent" class="bg-amber-100 rounded-lg p-3">
            <p class="text-xs text-amber-700 font-semibold mb-1">Email не отправлен — свяжитесь вручную:</p>
            <p class="text-sm font-bold text-amber-800">{{ lastShipResult.customer_name }}</p>
            <p class="text-xs text-amber-700 flex items-center gap-1 mt-1">
              <Phone class="w-3 h-3" /> {{ lastShipResult.customer_phone }}
            </p>
            <div class="mt-2 p-2 bg-white rounded border border-amber-200 text-xs text-slate-600 leading-relaxed">
              «К сожалению, в данный момент нет возможности отправить деталь. Предлагаем оформить возврат.»
            </div>
          </div>
        </div>

        <!-- Расчётная стоимость показана выше в общем блоке -->

        <!-- Кнопка "Посылка вернулась" -->
        <div v-if="selected.status === 'shipped' && (isCS || isWarehouse) && !panel" class="mb-4">
          <button @click="doMarkReturned" :disabled="panelLoading"
            class="flex items-center gap-2 px-4 py-2.5 bg-orange-50 hover:bg-orange-100 disabled:opacity-60 text-orange-700 text-sm font-semibold rounded-xl border border-orange-200 transition-colors">
            <Loader2 v-if="panelLoading" class="w-4 h-4 animate-spin" />
            <PackageCheck v-else class="w-4 h-4" />
            Посылка вернулась
          </button>
        </div>

        <!-- Повторная отправка после возврата / сбоя перевозчика -->
        <div v-if="['returned','shipped','in_transit','ready_for_pickup'].includes(selected.status) && isCS && !panel" class="mb-4">
          <button @click="doRecreateShipment(selected.id)" :disabled="panelLoading"
            class="flex items-center gap-2 px-4 py-2.5 bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
            <Loader2 v-if="panelLoading" class="w-4 h-4 animate-spin" />
            <RefreshCw v-else class="w-4 h-4" />
            Повторная отправка (новый заказ)
          </button>
          <p class="text-[11px] text-slate-500 mt-1.5">Та же заявка и привязка к возврату WB. Статус → «Одобрена», склад собирает заново.</p>
        </div>

        <!-- ── Действия КС: выбор тарифа + одобрение (без промежуточного окна) ─── -->
        <div v-if="isCS && ['new', 'matched'].includes(selected.status) && panel !== 'reject'"
          class="bg-emerald-50 rounded-xl border border-emerald-200 p-5 mt-4">

          <div class="flex gap-3">
            <button @click="doApprove()" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <CheckCircle2 v-else class="w-3.5 h-3.5" /> Одобрить {{ selectedCarrier === 'cdek' ? 'СДЭК' : 'Яндекс' }}
            </button>
            <button @click="panel = 'reject'" :disabled="panelLoading"
              class="flex items-center gap-2 px-4 py-2.5 bg-white hover:bg-rose-50 text-rose-600 text-sm font-semibold rounded-xl border border-rose-200 transition-colors">
              <XCircle class="w-4 h-4" /> Отклонить
            </button>
          </div>
        </div>

        <!-- Панель отклонения (КС) -->
        <div v-if="panel === 'reject'" class="bg-rose-50 rounded-xl border border-rose-200 p-5 mt-4">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold text-rose-700 flex items-center gap-2"><XCircle class="w-4 h-4" /> Отклонение</h3>
            <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">Причина <span class="text-rose-500">*</span></label>
              <textarea v-model="rejectForm.moderator_comment" rows="3" placeholder="Возврат не подтверждён / дубль заявки..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-rose-400 focus:outline-none resize-none" />
            </div>
            <div class="border-t border-rose-200 pt-3 text-xs">
              <template v-if="currentPromoCode">
                <span class="text-slate-500">Клиенту через 10 сек придёт промокод </span>
                <span class="font-mono font-bold text-violet-700">{{ currentPromoCode }}</span>
                <span class="text-slate-500"> (скидка 15%)</span>
              </template>
              <span v-else class="text-amber-600">Промокод не задан — сообщение с промокодом не будет отправлено. Задать можно в настройках раздела.</span>
            </div>
            <button @click="doReject" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <XCircle v-else class="w-3.5 h-3.5" /> Отклонить
            </button>
          </div>
        </div>

        <!-- ── Панель склада (только warehouse_manager) ─────────────────────── -->
        <template v-if="isWarehouse && ['approved','packed'].includes(selected.status)">
          <div :class="['rounded-xl border p-4', selected.status === 'packed' ? 'bg-teal-50 border-teal-200' : 'bg-violet-50 border-violet-200']">
            <p :class="['text-xs font-bold uppercase tracking-wider mb-2', selected.status === 'packed' ? 'text-teal-600' : 'text-violet-500']">
              {{ selected.status === 'packed'
                ? (selected.delivery_method === 'yandex' ? '✓ Собрано — ждёт забор курьера' : '✓ Собрано — готово к сдаче')
                : 'К отправке' }}
            </p>
            <p v-if="selected.moderator_comment" class="text-sm text-slate-700 mb-3 italic">«{{ selected.moderator_comment }}»</p>

            <!-- Яндекс: заказ уже создан автоматически при одобрении; копятся до вторника -->
            <div v-if="selected.delivery_method === 'yandex' && selected.track_number" class="mb-3 text-xs bg-white/70 rounded-lg px-3 py-2 text-slate-600 font-mono">
              Яндекс заказ создан ✓ ({{ selected.track_number }})<br class="font-sans">
              <span class="font-sans">Забор курьера со склада <strong>Техническая 17Б</strong> — по <strong>вторникам</strong> (10:00–13:00).<br>
              Получатель: <strong>{{ selected.client_pvz_address || '—' }}</strong></span>
            </div>
            <!-- Яндекс: автосоздание не удалось — кнопка повтора -->
            <div v-else-if="selected.delivery_method === 'yandex'" class="mb-3 text-xs bg-amber-50 rounded-lg px-3 py-2 text-amber-700 border border-amber-200">
              ⚠️ Заказ Яндекс не создался автоматически.<br>
              После создания посылка останется на складе до забора курьера (вторник).<br>
              Получатель: <strong>{{ selected.client_pvz_address || '—' }}</strong>
              <button @click="doYandexRetry(selected.id)" :disabled="panelLoading"
                class="mt-2 flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors">
                <Loader2 v-if="panelLoading" class="w-3 h-3 animate-spin" />
                <RefreshCw v-else class="w-3 h-3" /> Создать заказ Яндекс автоматически
              </button>
            </div>
            <div v-else-if="selected.cdek_uuid" class="mb-3 text-xs bg-white/70 rounded-lg px-3 py-2 text-slate-600 font-mono">
              СДЭК {{ selected.cdek_number ? `#${selected.cdek_number}` : 'заказ создан ✓' }}
            </div>

            <div class="flex flex-wrap gap-2">
              <!-- Статус "На отправку": кнопка "Отметить собранной" + этикетка (СДЭК) -->
              <template v-if="selected.status === 'approved'">
                <button @click="doMarkPacked(selected.id)" :disabled="panelLoading"
                  class="flex items-center gap-2 px-3 py-2.5 bg-teal-600 hover:bg-teal-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Loader2 v-if="panelLoading" class="w-4 h-4 animate-spin" />
                  <CheckSquare v-else class="w-4 h-4" /> Отметить собранной
                </button>
                <!-- Этикетка: СДЭК — по cdek_number, Яндекс — по track_number -->
                <button v-if="selected.delivery_method !== 'yandex' && selected.cdek_number" @click="downloadLabels([selected.id])"
                  class="flex items-center gap-2 px-3 py-2.5 bg-slate-500 hover:bg-slate-600 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Printer class="w-4 h-4" /> Этикетка PDF
                </button>
                <button v-else-if="selected.delivery_method === 'yandex' && selected.track_number" @click="downloadLabels([selected.id])"
                  class="flex items-center gap-2 px-3 py-2.5 bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Printer class="w-4 h-4" /> Этикетка Яндекс A5
                </button>
              </template>

              <!-- Статус "Собраны", Яндекс: копятся до вторника, статус сам после забора курьера -->
              <template v-else-if="selected.status === 'packed' && selected.delivery_method === 'yandex' && selected.track_number">
                <p class="text-xs text-slate-500 italic self-center">Не возить в ПВЗ. Ждёт забор курьера во вторник — статус обновится сам</p>
                <button @click="downloadLabels([selected.id])"
                  class="flex items-center gap-2 px-3 py-2.5 bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Printer class="w-4 h-4" /> Этикетка Яндекс A5
                </button>
              </template>

              <!-- Статус "Собраны", Яндекс, автосоздание не удалось: ручной ввод трека -->
              <template v-else-if="selected.status === 'packed' && selected.delivery_method === 'yandex'">
                <div class="w-full space-y-2">
                  <input v-model="yandexForm.track_number" type="text" placeholder="Трек-номер Яндекс"
                    class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-amber-400 focus:outline-none" />
                  <input v-model="yandexForm.shipping_cost" type="number" step="0.01"
                    :placeholder="selected.shipping_cost ? `Стоимость (по умолчанию ${selected.shipping_cost} ₽)` : 'Стоимость доставки, ₽'"
                    class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-amber-400 focus:outline-none" />
                  <button @click="doShipYandex(selected.id)" :disabled="panelLoading || !yandexForm.track_number"
                    class="flex items-center gap-2 px-3 py-2.5 bg-amber-500 hover:bg-amber-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors">
                    <Loader2 v-if="panelLoading" class="w-4 h-4 animate-spin" />
                    <PackageCheck v-else class="w-4 h-4" /> Подтвердить отправку Яндекс
                  </button>
                </div>
              </template>

              <!-- Статус "Собраны", СДЭК: только этикетка (сдача на ПВЗ — автоматически через вебхук СДЭК) -->
              <template v-else-if="selected.status === 'packed'">
                <button v-if="selected.cdek_number" @click="downloadLabels([selected.id])"
                  class="flex items-center gap-2 px-3 py-2.5 bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Printer class="w-4 h-4" /> Этикетка PDF
                </button>
                <span v-else class="text-xs text-slate-400 italic self-center">Этикетка недоступна — нет номера СДЭК</span>
                <span class="text-xs text-teal-600 italic self-center">Сдача на ПВЗ фиксируется автоматически</span>
              </template>
            </div>
          </div>
        </template>

        <!-- Панель отклонения склада (скрыта — решения принимает менеджер КС) -->
        <div v-if="false && panel === 'warehouse_reject'" class="bg-rose-50 rounded-xl border border-rose-200 p-5 mt-4">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold text-rose-700 flex items-center gap-2">
              <AlertTriangle class="w-4 h-4" /> Отклонение (склад)
            </h3>
            <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="text-xs font-medium text-slate-600 block mb-1">
                Причина <span class="text-rose-500">*</span>
                <span class="text-slate-400 font-normal">(видна только нам, клиенту придёт общее уведомление)</span>
              </label>
              <textarea v-model="warehouseRejectForm.rejection_reason" rows="3"
                placeholder="Например: Отсутствует деталь на складе / Деталь снята с производства..."
                class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-rose-400 focus:outline-none resize-none" />
            </div>
            <div class="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
              <Info class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              Клиенту {{ selected.customer_email ? 'автоматически придёт email' : 'нет email — потребуется позвонить' }} с предложением оформить возврат
            </div>
            <button @click="doWarehouseReject" :disabled="panelLoading"
              class="flex items-center gap-2 px-5 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
              <Loader2 v-if="panelLoading" class="w-3.5 h-3.5 animate-spin" />
              <XCircle v-else class="w-3.5 h-3.5" /> Отклонить и уведомить клиента
            </button>
          </div>
        </div>

        <!-- ── Ручная смена статуса (только admin/moderator) ──────────────── -->
        <div v-if="isAdmin && selected" class="mt-6 pt-4 border-t border-slate-200">
          <template v-if="panel !== 'set_status'">
            <button @click="() => { panel = 'set_status'; setStatusForm.status = selected.status; setStatusForm.comment = '' }"
              class="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-1.5">
              <Settings2 class="w-3.5 h-3.5" /> Изменить статус вручную
            </button>
          </template>
          <div v-else class="bg-slate-50 rounded-xl border border-slate-200 p-4">
            <div class="flex items-center justify-between mb-3">
              <p class="text-xs font-bold text-slate-500 uppercase tracking-wider">Изменить статус</p>
              <button @click="panel = null" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
            </div>
            <div class="space-y-3">
              <div class="flex flex-wrap gap-1.5">
                <button v-for="opt in statusOptions" :key="opt.value"
                  @click="setStatusForm.status = opt.value"
                  :class="['px-3 py-1.5 text-xs rounded-lg font-medium transition-colors border',
                    setStatusForm.status === opt.value
                      ? 'bg-slate-700 text-white border-slate-700'
                      : 'bg-white text-slate-500 border-slate-200 hover:border-slate-400']">
                  {{ opt.label }}
                </button>
              </div>
              <textarea v-model="setStatusForm.comment" rows="2"
                placeholder="Комментарий (необязательно)"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:border-slate-400 focus:outline-none resize-none" />
              <button @click="doSetStatus" :disabled="!setStatusForm.status || setStatusLoading"
                class="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-800 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors">
                <Loader2 v-if="setStatusLoading" class="w-3.5 h-3.5 animate-spin" />
                Применить
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- ── Лайтбокс ──────────────────────────────────────────────────────────── -->
  <Teleport to="body">
    <div v-if="lightboxOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      @click.self="closeLightbox">
      <button @click="closeLightbox"
        class="absolute top-4 right-4 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10">
        <X class="w-6 h-6" />
      </button>
      <!-- Навигация назад -->
      <button v-if="lightboxPhotos.length > 1" @click="lightboxPrev"
        class="absolute left-4 text-white/80 hover:text-white p-3 rounded-full hover:bg-white/10">
        <span class="text-2xl">‹</span>
      </button>
      <!-- Фото -->
      <div class="max-w-4xl max-h-[90vh] w-full px-16 flex items-center justify-center">
        <img :src="lightboxPhotos[lightboxIndex]" :key="lightboxIndex"
          class="max-h-[85vh] max-w-full object-contain rounded-lg shadow-2xl"
          @error="$event.target.src=''" />
      </div>
      <!-- Навигация вперёд -->
      <button v-if="lightboxPhotos.length > 1" @click="lightboxNext"
        class="absolute right-4 text-white/80 hover:text-white p-3 rounded-full hover:bg-white/10">
        <span class="text-2xl">›</span>
      </button>
      <!-- Счётчик -->
      <div v-if="lightboxPhotos.length > 1"
        class="absolute bottom-4 text-white/60 text-sm font-medium">
        {{ lightboxIndex + 1 }} / {{ lightboxPhotos.length }}
      </div>
    </div>
  </Teleport>

  <!-- Модалка: написать в WB чат -->
  <Teleport to="body">
    <div v-if="wbMsgModal" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" @click="wbMsgModal = false"></div>
      <div class="relative bg-white rounded-3xl shadow-2xl w-full max-w-md p-6">
        <div class="flex items-center gap-2 mb-4">
          <div class="p-2 bg-sky-100 text-sky-600 rounded-xl"><MessageCircle class="w-4 h-4" /></div>
          <div>
            <p class="font-black text-slate-800 text-sm">Написать клиенту в WB</p>
            <p class="text-[10px] text-slate-400">{{ wbMsgTarget?.customer_name }}</p>
          </div>
        </div>

        <!-- Шаблоны -->
        <div class="mb-3">
          <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">Шаблоны</p>
          <div class="flex flex-wrap gap-1.5">
            <button v-for="tpl in WB_MSG_TEMPLATES" :key="tpl.label"
              @click="wbMsgText = tpl.text"
              class="text-[10px] px-2 py-1 rounded-full border border-slate-200 hover:border-sky-400 hover:bg-sky-50 hover:text-sky-700 text-slate-600 font-semibold transition-colors">
              {{ tpl.label }}
            </button>
          </div>
        </div>

        <!-- Текст -->
        <textarea v-model="wbMsgText" rows="5"
          placeholder="Текст сообщения..."
          class="w-full text-xs border border-slate-200 rounded-xl p-3 resize-none focus:outline-none focus:border-sky-400 text-slate-700 mb-4"></textarea>

        <div class="flex gap-2">
          <button @click="wbMsgModal = false"
            class="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 text-slate-600 text-xs font-semibold hover:bg-slate-50 transition-colors">
            Отмена
          </button>
          <button @click="sendWbMessage" :disabled="!wbMsgText.trim() || wbMsgSending"
            class="flex-1 px-4 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white text-xs font-bold transition-colors flex items-center justify-center gap-1.5">
            <Loader2 v-if="wbMsgSending" class="w-3 h-3 animate-spin" />
            <MessageCircle v-else class="w-3 h-3" />
            {{ wbMsgSending ? 'Отправка...' : 'Отправить' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- ── Модал выбора тарифа СДЭК ──────────────────────────────────────────── -->
  <Teleport to="body">
    <div v-if="tariffModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 p-6">
        <div class="flex items-center justify-between mb-5">
          <h3 class="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Truck class="w-4 h-4 text-violet-600" /> Выберите тариф СДЭК
          </h3>
          <button @click="tariffModal = false" class="text-slate-400 hover:text-slate-600">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div v-if="tariffLoading" class="flex items-center justify-center py-8 text-slate-400 gap-2">
          <Loader2 class="w-5 h-5 animate-spin" /> Запрашиваем тарифы...
        </div>

        <div v-else-if="!tariffOptions.length" class="py-6 text-center">
          <p class="text-sm text-slate-500 mb-1">Не удалось получить тарифы от СДЭК</p>
          <p class="text-xs text-slate-400">Проверьте ПВЗ клиента и повторите</p>
        </div>

        <div v-else class="space-y-3 mb-5">
          <button
            v-for="t in tariffOptions" :key="t.tariff_code"
            @click="selectedTariff = t.tariff_code"
            :class="[
              'w-full flex items-center justify-between px-4 py-3.5 rounded-xl border-2 transition-all text-left',
              selectedTariff === t.tariff_code
                ? 'border-violet-500 bg-violet-50'
                : 'border-slate-200 hover:border-violet-300 bg-white'
            ]"
          >
            <div>
              <p class="text-sm font-semibold" :class="selectedTariff === t.tariff_code ? 'text-violet-800' : 'text-slate-700'">
                {{ t.name }}
              </p>
              <p class="text-xs text-slate-400 mt-0.5">{{ t.period_min }}–{{ t.period_max }} дней</p>
            </div>
            <div class="text-right ml-4">
              <p class="text-lg font-bold" :class="selectedTariff === t.tariff_code ? 'text-violet-700' : 'text-slate-800'">
                {{ t.cost.toFixed(0) }} ₽
              </p>
              <div v-if="tariffOptions.indexOf(t) === 0" class="text-[10px] text-emerald-600 font-semibold">Выгоднее</div>
            </div>
          </button>
        </div>

        <p class="text-xs text-slate-400 mb-4">
          Заявок к отправке: <b class="text-slate-600">{{ tariffPendingIds.length }}</b>.
          После подтверждения заказы СДЭК создадутся автоматически.
        </p>

        <div class="flex gap-2">
          <button @click="tariffModal = false"
            class="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 text-slate-600 text-sm font-semibold hover:bg-slate-50 transition-colors">
            Отмена
          </button>
          <button
            @click="tariffPendingIds.length === 1 ? doConfirmSingleWithTariff() : doConfirmWithTariff()"
            :disabled="!selectedTariff || tariffLoading || bulkLoading || panelLoading"
            class="flex-1 px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-bold transition-colors flex items-center justify-center gap-1.5">
            <Loader2 v-if="bulkLoading || panelLoading" class="w-3.5 h-3.5 animate-spin" />
            <CheckCircle2 v-else class="w-3.5 h-3.5" />
            Создать заказ{{ tariffPendingIds.length > 1 ? `ы (${tariffPendingIds.length})` : '' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Модалка: предупреждение о риске (доставка дороже себестоимости) -->
  <Teleport to="body">
    <div v-if="showCostRiskModal && costRisk" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden border-2 border-rose-300">
        <div class="bg-rose-50 px-5 py-4 border-b border-rose-200 flex items-center gap-3">
          <div class="w-10 h-10 rounded-full bg-rose-100 flex items-center justify-center flex-shrink-0">
            <AlertTriangle class="w-5 h-5 text-rose-600" />
          </div>
          <div>
            <p class="text-base font-bold text-rose-700">
              {{ costRisk.mode === 'cog' ? 'Внимание: доставка дороже товара' : 'Внимание: дорогая доставка' }}
            </p>
            <p class="text-xs text-rose-500">Отправка может быть убыточной</p>
          </div>
        </div>
        <div class="px-5 py-4">
          <p v-if="costRisk.mode === 'cog'" class="text-sm text-slate-600 mb-3">
            Стоимость доставки СДЭК <strong class="text-rose-600">превышает себестоимость товара</strong>.
            Проверьте, оправдана ли отправка экономически.
          </p>
          <p v-else class="text-sm text-slate-600 mb-3">
            Себестоимость товара неизвестна, а доставка СДЭК <strong class="text-rose-600">превышает {{ fmtCost(COST_LIMIT_NO_COG) }}</strong>.
            Проверьте, оправдана ли отправка экономически.
          </p>
          <div class="bg-slate-50 rounded-xl p-3 space-y-1.5 text-sm">
            <div class="flex justify-between">
              <span class="text-slate-500">Доставка СДЭК</span>
              <span class="font-bold text-rose-600">{{ fmtCost(costRisk.delivery) }}</span>
            </div>
            <div v-if="costRisk.mode === 'cog'" class="flex justify-between">
              <span class="text-slate-500">Себестоимость товара</span>
              <span class="font-bold text-slate-700">{{ fmtCost(costRisk.cog) }}</span>
            </div>
            <div v-else class="flex justify-between">
              <span class="text-slate-500">Порог (СБ неизвестна)</span>
              <span class="font-bold text-slate-700">{{ fmtCost(costRisk.limit) }}</span>
            </div>
            <div class="flex justify-between pt-1.5 border-t border-slate-200">
              <span class="text-slate-600 font-medium">Превышение</span>
              <span class="font-bold text-rose-700">+{{ fmtCost(costRisk.diff) }}</span>
            </div>
          </div>
        </div>
        <div class="px-5 py-4 bg-slate-50 border-t border-slate-200 flex gap-2">
          <button @click="showCostRiskModal = false"
            class="flex-1 px-4 py-2.5 bg-white hover:bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl border border-slate-300 transition-colors">
            Отмена
          </button>
          <button @click="doApprove(true)"
            class="flex-1 px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white text-sm font-semibold rounded-xl transition-colors">
            Всё равно одобрить
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Реестр актов Яндекс -->
  <Teleport to="body">
    <div v-if="showYandexActsRegistry" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-2xl shadow-2xl max-w-2xl w-full overflow-hidden flex flex-col" style="max-height: 85vh">
        <div class="px-5 py-4 border-b border-slate-200 flex items-center justify-between shrink-0">
          <div>
            <h3 class="text-sm font-bold text-slate-700 flex items-center gap-2">
              <History class="w-4 h-4 text-amber-600" /> Реестр актов Яндекс
            </h3>
            <p class="text-[11px] text-slate-400 mt-0.5">
              Всего актов: {{ yandexActs.length }} · без акта сейчас: {{ yandexActPending }}
            </p>
          </div>
          <div class="flex items-center gap-2">
            <button @click="loadYandexActs" :disabled="yandexActsLoading"
              class="p-2 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50 transition-colors">
              <RefreshCw :class="['w-4 h-4', yandexActsLoading ? 'animate-spin' : '']" />
            </button>
            <button @click="showYandexActsRegistry = false" class="text-slate-400 hover:text-slate-600">
              <X class="w-4 h-4" />
            </button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto px-5 py-3">
          <div v-if="yandexActsLoading && !yandexActs.length" class="flex justify-center py-10">
            <Loader2 class="w-6 h-6 animate-spin text-amber-600" />
          </div>
          <div v-else-if="!yandexActs.length" class="text-center py-10 text-sm text-slate-400">
            Актов пока нет
          </div>
          <div v-else class="space-y-2">
            <div v-for="act in yandexActs" :key="act.id" class="rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-800">{{ act.act_number }}</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">
                    {{ fmt(act.created_at) }} · {{ act.requests_count }} шт. · {{ fmtCost(act.total_shipping_cost) }}
                  </p>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                  <button @click="downloadYandexAct(act.id, 'pdf')"
                    class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white hover:bg-slate-100 text-slate-700 text-xs font-semibold border border-slate-200 transition-colors">
                    <FileDown class="w-3.5 h-3.5" /> PDF
                  </button>
                  <button v-if="canDeleteYandexActs" @click="deleteYandexAct(act.id)"
                    :disabled="yandexActDeletingId === act.id"
                    class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-100 disabled:opacity-50 text-rose-700 text-xs font-semibold border border-rose-200 transition-colors">
                    <Loader2 v-if="yandexActDeletingId === act.id" class="w-3.5 h-3.5 animate-spin" />
                    <X v-else class="w-3.5 h-3.5" /> Удалить
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="px-5 py-4 bg-slate-50 border-t border-slate-200 flex justify-between items-center gap-3 shrink-0">
          <p class="text-[11px] text-slate-400">
            Удаление акта доступно только администратору.
          </p>
          <button @click="showYandexActsRegistry = false"
            class="px-4 py-2.5 bg-white hover:bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl border border-slate-300 transition-colors">
            Закрыть
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Модалка: настройка промокода на следующую покупку -->
  <Teleport to="body">
    <div v-if="showPromoSettings" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-2xl shadow-2xl max-w-sm w-full overflow-hidden">
        <div class="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
          <h3 class="text-sm font-bold text-slate-700 flex items-center gap-2"><Tag class="w-4 h-4" /> Промокод (отказы)</h3>
          <button @click="showPromoSettings = false" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
        </div>
        <div class="px-5 py-4 space-y-3">
          <p class="text-xs text-slate-500">
            Отправляется клиенту через 10 сек после отказа в заявке. Скидка фиксированная — 15%.
            Код меняйте вручную раз в неделю.
          </p>
          <div>
            <label class="text-xs font-medium text-slate-600 block mb-1">Текущий код</label>
            <input v-model="promoCodeInput" type="text" placeholder="Например VIDOVITO15"
              class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none font-mono uppercase" />
          </div>
          <div>
            <label class="text-xs font-medium text-slate-600 block mb-1">Действителен до</label>
            <input v-model="promoExpiresInput" type="date"
              class="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none" />
          </div>
          <p v-if="promoCodeUpdatedAt" class="text-[10px] text-slate-400">
            Обновлён {{ fmt(promoCodeUpdatedAt) }}{{ promoCodeUpdatedBy ? ` — ${promoCodeUpdatedBy}` : '' }}
          </p>
        </div>
        <div class="px-5 py-4 bg-slate-50 border-t border-slate-200 flex gap-2">
          <button @click="showPromoSettings = false"
            class="flex-1 px-4 py-2.5 bg-white hover:bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl border border-slate-300 transition-colors">
            Отмена
          </button>
          <button @click="savePromoCode" :disabled="promoCodeSaving"
            class="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
            <Loader2 v-if="promoCodeSaving" class="w-3.5 h-3.5 animate-spin" />
            Сохранить
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Модалка: товары, по которым разрешена доотправка деталей -->
  <Teleport to="body">
    <div v-if="showSkuSettings" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-2xl shadow-2xl max-w-2xl w-full overflow-hidden flex flex-col" style="max-height: 85vh">

        <div class="px-5 py-4 border-b border-slate-200 flex items-center justify-between shrink-0">
          <div>
            <h3 class="text-sm font-bold text-slate-700 flex items-center gap-2">
              <PackageCheck class="w-4 h-4 text-emerald-600" /> Товары для доотправки
            </h3>
            <p class="text-[11px] text-slate-400 mt-0.5">
              Разрешено сейчас: <span class="font-semibold text-emerald-700">{{ skuEnabledCount }}</span> из {{ skuItems.length }}
            </p>
          </div>
          <button @click="showSkuSettings = false" class="text-slate-400 hover:text-slate-600"><X class="w-4 h-4" /></button>
        </div>

        <div class="px-5 py-3 border-b border-slate-100 space-y-2 shrink-0">
          <p class="text-xs text-slate-500">
            По отмеченным товарам бот и менеджер могут предлагать клиенту отправку недостающих деталей.
            По остальным доотправка не предлагается — оформляется возврат.
          </p>
          <div class="flex items-center gap-2">
            <div class="relative flex-1">
              <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <input v-model="skuSearch" type="text" placeholder="Артикул или группа товаров..."
                class="w-full pl-8 pr-3 py-2 text-xs rounded-lg border border-slate-200 focus:border-emerald-400 focus:outline-none" />
            </div>
            <button @click="skuOnlyEnabled = !skuOnlyEnabled"
              :class="['px-3 py-2 text-xs font-medium rounded-lg border transition-colors whitespace-nowrap',
                skuOnlyEnabled ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300']">
              Только разрешённые
            </button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto px-5 py-2">
          <div v-if="skuLoading" class="flex justify-center py-10">
            <Loader2 class="w-6 h-6 animate-spin text-emerald-600" />
          </div>
          <div v-else-if="skuFiltered.length === 0" class="text-center py-10 text-sm text-slate-400">
            Ничего не найдено
          </div>
          <label v-else v-for="s in skuFiltered" :key="s.supplier_article"
            class="flex items-center gap-3 py-2 border-b border-slate-50 cursor-pointer hover:bg-slate-50 -mx-2 px-2 rounded">
            <input type="checkbox" v-model="s.enabled" class="w-4 h-4 rounded accent-emerald-600 shrink-0" />
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium text-slate-700 truncate">{{ s.supplier_article }}</p>
              <p v-if="s.group_name" class="text-[11px] text-slate-400 truncate">{{ s.group_name }}</p>
            </div>
            <span class="text-[11px] text-slate-400 tabular-nums shrink-0">{{ s.reviews }} отз.</span>
          </label>
        </div>

        <div class="px-5 py-4 bg-slate-50 border-t border-slate-200 flex gap-2 shrink-0">
          <button @click="showSkuSettings = false"
            class="flex-1 px-4 py-2.5 bg-white hover:bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl border border-slate-300 transition-colors">
            Отмена
          </button>
          <button @click="saveSkus" :disabled="skuSaving"
            class="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
            <Loader2 v-if="skuSaving" class="w-3.5 h-3.5 animate-spin" />
            Сохранить
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
