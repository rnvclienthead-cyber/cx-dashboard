<script setup>
import '../assets/main.css'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { apiFetch } from '../api'
import {
  Database, Bot, ClipboardCheck, BrainCircuit, ScrollText, MessageSquare,
  LineChart, AlertTriangle, Star, BadgeDollarSign, LayoutGrid,
  ChevronLeft, ChevronRight, Menu, PackageCheck, LogOut, KeyRound, X, Eye, EyeOff,
  BookOpen, HelpCircle, BarChart3, Gauge, MessagesSquare, PackageX, ClipboardPenLine, ShoppingBag, Store, ScanLine
} from 'lucide-vue-next'
import { usePlatformStore } from '../stores/platform'
import { usePermissionsStore } from '../stores/permissions'
import { isTelegramMiniApp } from '../telegramWebApp'
import { getAuthToken, getAuthRole, getAuthUsername, clearAuthSession, markSessionUse } from '../authStorage'

const platformStore    = usePlatformStore()
const permissionsStore = usePermissionsStore()
const route = useRoute()
const canViewQualityQuestions = computed(() =>
  permissionsStore.isAdmin ||
  permissionsStore.role === 'qa_manager' ||
  permissionsStore.can('quality_questions')
)

const hideChrome = computed(() =>
  permissionsStore.isTshoppingPartner || isTelegramMiniApp()
)

const isCollapsed  = ref(false)
const isMobileOpen = ref(false)

// Доступные площадки (из прав текущего пользователя)
const availablePlatforms = computed(() => permissionsStore.allowedMarketplaces)
const specificPlatforms = computed(() =>
  availablePlatforms.value.filter(p => p !== 'all')
)
const firstAvailablePlatform = computed(() =>
  specificPlatforms.value[0] || 'wb'
)
const prefersAllPlatform = computed(() =>
  route.path === '/reshipment' || route.path === '/questions/quality'
)
const keepsChosenAll = computed(() => route.path === '/ratings')

watch(isCollapsed, (val) => {
  localStorage.setItem('sidebar_collapsed', String(val))
})

function syncPlatformSelection() {
  if (!permissionsStore.loaded) return
  const available = availablePlatforms.value
  const current = platformStore.platform
  if (prefersAllPlatform.value) {
    if (current !== 'all') platformStore.setPlatform('all')
    return
  }
  if (keepsChosenAll.value && current === 'all' && available.includes('all')) return
  if (current === 'all' || !specificPlatforms.value.includes(current)) {
    platformStore.setPlatform(firstAvailablePlatform.value)
  } else if (!available.includes(current)) {
    platformStore.setPlatform(firstAvailablePlatform.value)
  }
}

watch(
  [() => permissionsStore.loaded, availablePlatforms, () => route.path],
  () => syncPlatformSelection(),
  { immediate: true },
)

const toggleCollapse = () => { isCollapsed.value = !isCollapsed.value }
const closeMobile = () => { isMobileOpen.value = false }

// Переключатель площадки на всех экранах, где >1 МП (в т.ч. Возвраты — WB/ЯМ/Ozon).
const showPlatformSwitcher = computed(() =>
  permissionsStore.loaded &&
  !hideChrome.value &&
  availablePlatforms.value.length > 1 &&
  route.path !== '/tshopping'
)

function pingActivity () {
  if (!permissionsStore.loaded || hideChrome.value) return
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
  apiFetch('/api/v1/cx-control/activity/ping', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: route.path }),
  }).catch(() => {})
}

let activityDirty = false
let activityFlushTimer = null

function markActivity () {
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
  markSessionUse()
  activityDirty = true
}

function flushActivity () {
  if (!activityDirty) return
  activityDirty = false
  pingActivity()
}

const newReshipmentCount = ref(0)
let reshipmentTimer = null
const qualityQuestionCount = ref(0)
let qualityQuestionTimer = null
const tshoppingCount = ref(0)
let tshoppingTimer = null
const shopOrderCount = ref(0)
let shopOrderTimer = null

/** Для КС — очередь «новые»; для склада — «на отправку»; иначе тоже новые. */
function reshipmentBadgeStatus () {
  const role = permissionsStore.role || getAuthRole() || ''
  if (role === 'warehouse_manager') return 'approved'
  return 'new'
}

async function fetchReshipmentBadge () {
  if (!permissionsStore.can('reshipment')) return
  try {
    const res = await apiFetch('/api/v1/reshipment/stats')
    const data = await res.json()
    const target = reshipmentBadgeStatus()
    const row = (data?.data ?? []).find((s) => s.status === target)
    newReshipmentCount.value = parseInt(row?.cnt ?? 0, 10)
  } catch { /* silent */ }
}

async function fetchQualityQuestionBadge () {
  if (!canViewQualityQuestions.value) {
    qualityQuestionCount.value = 0
    return
  }
  try {
    const res = await apiFetch('/api/v1/quality-questions/count')
    const data = await res.json()
    qualityQuestionCount.value = parseInt(data?.count ?? 0, 10)
  } catch { /* silent */ }
}

async function fetchShopOrderBadge () {
  if (!permissionsStore.can('shop_orders')) {
    shopOrderCount.value = 0
    return
  }
  try {
    const res = await apiFetch('/api/v1/shop/orders/stats')
    const data = await res.json()
    shopOrderCount.value = parseInt(data?.data?.open ?? 0, 10)
  } catch { /* silent */ }
}

async function fetchTshoppingBadge () {
  if (!(permissionsStore.isAdmin || permissionsStore.can('tshopping'))) {
    tshoppingCount.value = 0
    return
  }
  try {
    const res = await apiFetch('/api/v1/tshopping/stats')
    const data = await res.json()
    tshoppingCount.value = permissionsStore.isTshoppingPartner
      ? parseInt(data?.unread_partner ?? 0, 10)
      : parseInt(data?.pending ?? 0, 10)
  } catch { /* silent */ }
}

watch(() => permissionsStore.loaded, (loaded) => {
  if (loaded && permissionsStore.can('reshipment')) {
    fetchReshipmentBadge()
    if (!reshipmentTimer) reshipmentTimer = setInterval(fetchReshipmentBadge, 60000)
  }
}, { immediate: true })

// После смены роли/прав пересчитать сразу
watch(() => permissionsStore.role, () => {
  if (permissionsStore.loaded && permissionsStore.can('reshipment')) fetchReshipmentBadge()
})

watch([() => permissionsStore.loaded, canViewQualityQuestions], ([loaded, canView]) => {
  if (!loaded) return
  if (!canView) {
    qualityQuestionCount.value = 0
    if (qualityQuestionTimer) clearInterval(qualityQuestionTimer)
    qualityQuestionTimer = null
    return
  }
  fetchQualityQuestionBadge()
  if (!qualityQuestionTimer) qualityQuestionTimer = setInterval(fetchQualityQuestionBadge, 60000)
}, { immediate: true })

watch([() => permissionsStore.loaded, () => permissionsStore.can('tshopping'), () => permissionsStore.isAdmin], ([loaded]) => {
  if (!loaded) return
  if (!(permissionsStore.isAdmin || permissionsStore.can('tshopping'))) {
    tshoppingCount.value = 0
    if (tshoppingTimer) clearInterval(tshoppingTimer)
    tshoppingTimer = null
    return
  }
  fetchTshoppingBadge()
  if (!tshoppingTimer) tshoppingTimer = setInterval(fetchTshoppingBadge, 30000)
}, { immediate: true })

watch([() => permissionsStore.loaded, () => permissionsStore.can('shop_orders')], ([loaded, canView]) => {
  if (!loaded) return
  if (!canView) {
    shopOrderCount.value = 0
    if (shopOrderTimer) clearInterval(shopOrderTimer)
    shopOrderTimer = null
    return
  }
  fetchShopOrderBadge()
  if (!shopOrderTimer) shopOrderTimer = setInterval(fetchShopOrderBadge, 60000)
}, { immediate: true })

onMounted(() => {
  const saved = localStorage.getItem('sidebar_collapsed')
  if (saved !== null) isCollapsed.value = saved === 'true'
  window.addEventListener('pointerdown', markActivity, { passive: true })
  window.addEventListener('keydown', markActivity)
  window.addEventListener('scroll', markActivity, { passive: true, capture: true })
  activityFlushTimer = setInterval(flushActivity, 2000)
  window.addEventListener('quality-questions-updated', fetchQualityQuestionBadge)
  pingLive()
  liveTimer = setInterval(pingLive, 12000)
})

onUnmounted(() => {
  clearInterval(activityFlushTimer)
  window.removeEventListener('pointerdown', markActivity)
  window.removeEventListener('keydown', markActivity)
  window.removeEventListener('scroll', markActivity, true)
  if (reshipmentTimer) clearInterval(reshipmentTimer)
  if (qualityQuestionTimer) clearInterval(qualityQuestionTimer)
  if (tshoppingTimer) clearInterval(tshoppingTimer)
  if (shopOrderTimer) clearInterval(shopOrderTimer)
  if (liveTimer) clearInterval(liveTimer)
  window.removeEventListener('quality-questions-updated', fetchQualityQuestionBadge)
})

watch(() => route.path, markActivity)

const serverUnreachable = ref(false)
let liveFails = 0
let liveTimer = null

async function pingLive () {
  if (route.name === 'login' || route.meta.public) return
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 4000)
  try {
    const res = await fetch('/api/v1/health/live', { signal: ctrl.signal, cache: 'no-store' })
    if (res.ok) {
      liveFails = 0
      serverUnreachable.value = false
      return
    }
  } catch { /* сеть или таймаут */ }
  finally {
    clearTimeout(timer)
  }
  liveFails += 1
  if (liveFails >= 2) serverUnreachable.value = true
}

const currentUser = getAuthUsername() || ''

const logout = () => {
  clearAuthSession()
  window.location.href = '/login'
}

// --- Смена пароля ---
const showChangePw   = ref(false)
const changePwCurrent = ref('')
const changePwNew     = ref('')
const changePwConfirm = ref('')
const changePwError   = ref('')
const changePwOk      = ref(false)
const changePwLoading = ref(false)

const openChangePw = () => {
  showChangePw.value   = true
  changePwCurrent.value = ''
  changePwNew.value     = ''
  changePwConfirm.value = ''
  changePwError.value   = ''
  changePwOk.value      = false
}

const submitChangePw = async () => {
  changePwError.value = ''
  if (changePwNew.value.length < 6) { changePwError.value = 'Минимум 6 символов'; return }
  if (changePwNew.value !== changePwConfirm.value) { changePwError.value = 'Пароли не совпадают'; return }
  changePwLoading.value = true
  try {
    const token = getAuthToken()
    const res = await fetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ current_password: changePwCurrent.value, new_password: changePwNew.value })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка')
    changePwOk.value = true
    setTimeout(() => { showChangePw.value = false }, 1500)
  } catch (e) {
    changePwError.value = e.message
  } finally {
    changePwLoading.value = false
  }
}

const cyclePlatform = () => {
  const order = availablePlatforms.value
  const next = order[(order.indexOf(platformStore.platform) + 1) % order.length]
  platformStore.setPlatform(next)
}

const platformLabel = { wb: 'WB', ym: 'ЯМ', ozon: 'OZON', all: 'Все' }

const platformBorder = {
  wb:   'border-purple-400',
  ym:   'border-orange-400',
  ozon: 'border-blue-500',
  all:  'border-slate-300',
}
const platformTextColor = {
  wb:   'text-purple-700',
  ym:   'text-orange-600',
  ozon: 'text-blue-600',
  all:  'text-slate-600',
}

// PNG-логотипы брендов
const platformImgSrc = { wb: '/icons/wb.png', ym: '/icons/ym.png', ozon: '/icons/ozon.png' }
</script>

<template>
  <div class="flex h-full min-h-full overflow-hidden bg-slate-50">

    <!-- Мобильный backdrop -->
    <div
      v-if="isMobileOpen"
      class="fixed inset-0 bg-black/40 z-30 lg:hidden"
      @click="closeMobile"
    />

    <!-- Сайдбар -->
    <aside
      v-if="!$route.meta.public && $route.name !== 'login' && !hideChrome"
      :class="[
        'flex flex-col bg-white border-r border-slate-200 shadow-sm z-40 transition-all duration-300 flex-shrink-0',
        isCollapsed ? 'w-[52px]' : 'w-64',
        'fixed lg:relative h-full',
        isMobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
      ]"
    >
      <!-- Шапка -->
      <div class="border-b border-slate-200 flex-shrink-0" :class="isCollapsed ? 'px-2 py-3' : 'p-5'">
        <!-- Логотип развёрнутый -->
        <div v-if="!isCollapsed" class="flex items-center justify-between">
          <h2 class="text-2xl tracking-tight flex items-baseline text-[#222D3D] whitespace-nowrap" style="font-family: 'Montserrat', sans-serif; font-weight: 800;">
            CX Видовит<span class="relative inline-block">
              о
              <svg class="absolute bottom-[-2px] left-1/2 -translate-x-1/2 w-4 h-2.5 text-[#FFC107]" viewBox="0 0 24 12" fill="none">
                <path d="M2 2 Q12 14 22 2" stroke="currentColor" stroke-width="5" stroke-linecap="round"/>
              </svg>
            </span>
          </h2>
          <!-- Кнопка свернуть (desktop) -->
          <button @click="toggleCollapse" class="hidden lg:flex items-center justify-center w-6 h-6 rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors ml-2 flex-shrink-0">
            <ChevronLeft class="w-4 h-4" />
          </button>
        </div>
        <!-- Логотип свёрнутый -->
        <div v-else class="flex flex-col items-center gap-2">
          <span class="text-[#222D3D] font-black text-lg leading-none whitespace-nowrap" style="font-family: 'Montserrat', sans-serif;">
            В<span class="relative inline-block">о<svg class="absolute bottom-[-3px] left-1/2 -translate-x-1/2 w-2.5 h-1.5 text-[#FFC107]" viewBox="0 0 24 12" fill="none"><path d="M2 2 Q12 14 22 2" stroke="currentColor" stroke-width="5" stroke-linecap="round"/></svg></span>
          </span>
          <button @click="toggleCollapse" class="hidden lg:flex items-center justify-center w-6 h-6 rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors">
            <ChevronRight class="w-4 h-4" />
          </button>
        </div>
      </div>

      <!-- Переключатель платформ (скрыт, если доступна только одна) -->
      <div v-if="showPlatformSwitcher"
           class="border-b border-slate-200 flex-shrink-0"
           :class="isCollapsed ? 'px-2 py-2' : 'px-3 py-3'">

        <!-- Развёрнутый: карточки-кнопки платформ -->
        <template v-if="!isCollapsed">
          <p class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2 px-0.5">Площадка</p>
          <div class="grid gap-1.5"
               :class="availablePlatforms.length === 2 ? 'grid-cols-2'
                     : availablePlatforms.length === 3 ? 'grid-cols-3'
                     : 'grid-cols-2'">
            <button
              v-for="p in availablePlatforms"
              :key="p"
              @click="platformStore.setPlatform(p)"
              :title="platformLabel[p]"
              :class="[
                'flex flex-col items-center justify-center gap-1 px-1 py-1.5 rounded-xl border-2 transition-all duration-200',
                platformStore.platform === p
                  ? [platformBorder[p], 'bg-white shadow-md']
                  : 'border-transparent bg-slate-50 hover:bg-white hover:border-slate-200 hover:shadow-sm'
              ]"
            >
              <img v-if="p !== 'all'"
                   :src="platformImgSrc[p]"
                   :alt="platformLabel[p]"
                   :class="['w-8 h-8 rounded-lg object-cover transition-all duration-300',
                            platformStore.platform !== p ? 'grayscale opacity-35' : '']" />
              <div v-else
                   :class="['grid grid-cols-2 gap-0.5 w-8 h-8 p-1 rounded-lg bg-slate-100',
                            platformStore.platform !== 'all' ? 'opacity-40' : '']">
                <span class="rounded-sm bg-purple-400"></span>
                <span class="rounded-sm bg-orange-400"></span>
                <span class="rounded-sm bg-blue-500"></span>
                <span class="rounded-sm bg-slate-300"></span>
              </div>
              <span :class="['text-[10px] font-semibold leading-none',
                platformStore.platform === p ? platformTextColor[p] : 'text-slate-400']">
                {{ platformLabel[p] }}
              </span>
            </button>
          </div>
        </template>

        <!-- Свёрнутый: одна кнопка-цикл -->
        <button
          v-else
          @click="cyclePlatform"
          class="w-full py-1.5 rounded-xl border border-slate-200 bg-white flex items-center justify-center hover:bg-slate-50 transition-colors shadow-sm"
          :title="`${platformLabel[platformStore.platform]} — нажмите для смены`"
        >
          <img v-if="platformStore.platform !== 'all'"
               :src="platformImgSrc[platformStore.platform]"
               class="w-8 h-8 rounded-lg object-cover" />
          <span v-else class="text-[10px] font-bold text-slate-600 leading-none">Все</span>
        </button>
      </div>

      <!-- Навигация -->
      <nav class="flex-1 overflow-y-auto" :class="isCollapsed ? 'p-2 space-y-1' : 'p-4 space-y-1'">

        <RouterLink
          v-if="permissionsStore.can('dashboard')"
          to="/"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Главный дашборд' : ''"
        >
          <LineChart class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Главный дашборд</span>
        </RouterLink>

        <div v-if="!isCollapsed && ['sync', 'ai_tagging', 'moderation', 'ai_training'].some(permissionsStore.can)" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-2 px-2">
          Технический блок
        </div>
        <div v-else-if="isCollapsed && ['sync', 'ai_tagging', 'moderation', 'ai_training'].some(permissionsStore.can)" class="border-t border-slate-100 my-2"></div>

        <RouterLink
          v-if="permissionsStore.can('sync')"
          to="/sync"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-blue-50 text-blue-700 font-semibold"
          :title="isCollapsed ? 'Статус автоматизации' : ''"
        >
          <Database class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Статус автоматизации</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('ai_tagging')"
          to="/ai-tagging"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-blue-50 text-blue-700 font-semibold"
          :title="isCollapsed ? 'ИИ Тегирование' : ''"
        >
          <Bot class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">ИИ Тегирование</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('moderation')"
          to="/moderation"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-blue-50 text-blue-700 font-semibold"
          :title="isCollapsed ? 'Модерация тегов' : ''"
        >
          <ClipboardCheck class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Модерация тегов</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('ai_training')"
          to="/ai-training"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-blue-50 text-blue-700 font-semibold"
          :title="isCollapsed ? 'Обучение ИИ' : ''"
        >
          <BrainCircuit class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Обучение ИИ</span>
        </RouterLink>

        <div v-if="!isCollapsed && ['production', 'ppm', 'voc', 'ratings', 'finances', 'pnl', 'cx_control'].some(permissionsStore.can)" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Операционный блок
        </div>
        <div v-else-if="isCollapsed && ['production', 'ppm', 'voc', 'ratings', 'finances', 'pnl', 'cx_control'].some(permissionsStore.can)" class="border-t border-slate-100 my-2"></div>

        <RouterLink
          v-if="permissionsStore.can('production')"
          to="/production"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Карта проблем' : ''"
        >
          <LayoutGrid class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Карта проблем</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('ppm')"
          to="/ppm"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'PPM и Акты' : ''"
        >
          <AlertTriangle class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">PPM и Акты</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('voc')"
          to="/voc"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Отзывы и аналитика' : ''"
        >
          <MessageSquare class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Отзывы и аналитика</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('ratings')"
          to="/ratings"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Рейтинг товаров' : ''"
        >
          <Star class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Рейтинг товаров</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('finances')"
          to="/finances"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-rose-50 text-rose-700 font-semibold"
          :title="isCollapsed ? 'Финансовые потери' : ''"
        >
          <BadgeDollarSign class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Финансовые потери</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('pnl')"
          to="/pnl"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-indigo-50 text-indigo-700 font-semibold"
          :title="isCollapsed ? 'P&L по артикулу' : ''"
        >
          <BarChart3 class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">P&amp;L по артикулу</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.isAdmin"
          to="/cx-control"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Контроль отдела' : ''"
        >
          <Gauge class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Контроль отдела</span>
        </RouterLink>

        <div v-if="!isCollapsed && ['cx_responses', 'tshopping', 'quality_questions', 'reshipment', 'shop_orders', 'faq'].some(permissionsStore.can)" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Клиентский сервис
        </div>
        <div v-else-if="isCollapsed && ['cx_responses', 'tshopping', 'quality_questions', 'reshipment', 'shop_orders', 'faq'].some(permissionsStore.can)" class="border-t border-slate-100 my-2"></div>

        <template v-if="permissionsStore.isAdmin || permissionsStore.can('cx_responses')">
          <RouterLink
            to="/returns"
            :class="[
              'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
              isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
            ]"
            active-class="bg-emerald-50 text-emerald-700 font-semibold"
            :title="isCollapsed ? 'Возвраты' : ''"
          >
            <PackageX class="w-4 h-4 flex-shrink-0" />
            <span v-if="!isCollapsed">Возвраты</span>
          </RouterLink>
          <RouterLink
            to="/chats"
            :class="[
              'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
              isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
            ]"
            active-class="bg-emerald-50 text-emerald-700 font-semibold"
            :title="isCollapsed ? 'Чаты' : ''"
          >
            <MessagesSquare class="w-4 h-4 flex-shrink-0" />
            <span v-if="!isCollapsed">Чаты</span>
          </RouterLink>
        </template>

        <RouterLink
          v-if="permissionsStore.isAdmin || permissionsStore.can('tshopping')"
          to="/tshopping"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-teal-50 text-teal-700 font-semibold"
          :title="isCollapsed ? 'Запросы «Т-Шопинг»' : ''"
        >
          <span class="relative flex-shrink-0">
            <ShoppingBag class="w-4 h-4" />
            <span
              v-if="isCollapsed && tshoppingCount > 0"
              class="absolute -top-1.5 -right-1.5 flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
              style="min-width:14px;height:14px;font-size:9px;padding:0 2px"
            >{{ tshoppingCount }}</span>
          </span>
          <span v-if="!isCollapsed" class="flex-1">Запросы «Т-Шопинг»</span>
          <span
            v-if="!isCollapsed && tshoppingCount > 0"
            class="flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
            style="min-width:18px;height:18px;font-size:10px;padding:0 4px"
          >{{ tshoppingCount }}</span>
        </RouterLink>

        <template v-if="permissionsStore.isAdmin || permissionsStore.can('cx_responses')">
          <RouterLink
            to="/reviews"
            :class="[
              'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
              isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
            ]"
            active-class="bg-emerald-50 text-emerald-700 font-semibold"
            :title="isCollapsed ? 'Отзывы' : ''"
          >
            <Star class="w-4 h-4 flex-shrink-0" />
            <span v-if="!isCollapsed">Отзывы</span>
          </RouterLink>
          <RouterLink
            to="/questions"
            :class="[
              'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
              isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
            ]"
            active-class="bg-emerald-50 text-emerald-700 font-semibold"
            :title="isCollapsed ? 'Вопросы' : ''"
          >
            <HelpCircle class="w-4 h-4 flex-shrink-0" />
            <span v-if="!isCollapsed">Вопросы</span>
          </RouterLink>
        </template>

        <RouterLink
          v-if="canViewQualityQuestions"
          to="/questions/quality"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-violet-50 text-violet-700 font-semibold"
          :title="isCollapsed ? 'Уточнения по вопросам' : ''"
        >
          <span class="relative flex-shrink-0">
            <ClipboardPenLine class="w-4 h-4" />
            <span
              v-if="isCollapsed && qualityQuestionCount > 0"
              class="absolute -top-1.5 -right-1.5 flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
              style="min-width:14px;height:14px;font-size:9px;padding:0 2px"
            >{{ qualityQuestionCount }}</span>
          </span>
          <span v-if="!isCollapsed" class="flex-1">Уточнения по вопросам</span>
          <span
            v-if="!isCollapsed && qualityQuestionCount > 0"
            class="flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
            style="min-width:18px;height:18px;font-size:10px;padding:0 4px"
          >{{ qualityQuestionCount }}</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('reshipment')"
          to="/reshipment"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Отправка деталей' : ''"
        >
          <span class="relative flex-shrink-0">
            <PackageCheck class="w-4 h-4" />
            <span
              v-if="isCollapsed && newReshipmentCount > 0"
              class="absolute -top-1.5 -right-1.5 flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
              style="min-width:14px;height:14px;font-size:9px;padding:0 2px"
            >{{ newReshipmentCount }}</span>
          </span>
          <span v-if="!isCollapsed" class="flex-1">Отправка деталей</span>
          <span
            v-if="!isCollapsed && newReshipmentCount > 0"
            class="flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
            style="min-width:18px;height:18px;font-size:10px;padding:0 4px"
          >{{ newReshipmentCount }}</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.isAdmin"
          to="/intake"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'Приёмка' : ''"
        >
          <ScanLine class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Приёмка</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('shop_orders')"
          to="/shop-orders"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-violet-50 text-violet-700 font-semibold"
          :title="isCollapsed ? 'Заказы сайта' : ''"
        >
          <span class="relative flex-shrink-0">
            <Store class="w-4 h-4" />
            <span
              v-if="isCollapsed && shopOrderCount > 0"
              class="absolute -top-1.5 -right-1.5 flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
              style="min-width:14px;height:14px;font-size:9px;padding:0 2px"
            >{{ shopOrderCount }}</span>
          </span>
          <span v-if="!isCollapsed" class="flex-1">Заказы сайта</span>
          <span
            v-if="!isCollapsed && shopOrderCount > 0"
            class="flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none"
            style="min-width:18px;height:18px;font-size:10px;padding:0 4px"
          >{{ shopOrderCount }}</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('faq')"
          to="/faq"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-emerald-50 text-emerald-700 font-semibold"
          :title="isCollapsed ? 'База знаний FAQ' : ''"
        >
          <HelpCircle class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">База знаний FAQ</span>
        </RouterLink>

        <div v-if="!isCollapsed && (permissionsStore.isAdmin || permissionsStore.can('logs') || permissionsStore.can('docs'))" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Административный блок
        </div>
        <div v-else-if="isCollapsed && (permissionsStore.isAdmin || permissionsStore.can('logs') || permissionsStore.can('docs'))" class="border-t border-slate-100 my-2"></div>

        <RouterLink
          v-if="permissionsStore.can('logs')"
          to="/logs"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-blue-50 text-blue-700 font-semibold"
          :title="isCollapsed ? 'Журнал событий' : ''"
        >
          <ScrollText class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Журнал событий</span>
        </RouterLink>

        <RouterLink
          v-if="permissionsStore.can('docs')"
          to="/docs"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-indigo-50 text-indigo-700 font-semibold"
          :title="isCollapsed ? 'Документация системы' : ''"
        >
          <BookOpen class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Документация системы</span>
        </RouterLink>

      </nav>

      <!-- Профиль + выход -->
      <div class="border-t border-slate-200 flex-shrink-0" :class="isCollapsed ? 'p-2' : 'p-3'">
        <div v-if="!isCollapsed" class="flex items-center gap-2 px-2 py-1.5 rounded-lg">
          <div class="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
            <span class="text-xs font-black text-blue-600">{{ currentUser.charAt(0).toUpperCase() }}</span>
          </div>
          <span class="text-xs font-semibold text-slate-500 truncate flex-1 min-w-0">{{ currentUser }}</span>
          <button @click="openChangePw" title="Сменить пароль" class="p-1.5 rounded-lg text-slate-400 hover:bg-blue-50 hover:text-blue-500 transition-colors flex-shrink-0">
            <KeyRound class="w-4 h-4" />
          </button>
          <button @click="logout" title="Выйти" class="p-1.5 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-500 transition-colors flex-shrink-0">
            <LogOut class="w-4 h-4" />
          </button>
        </div>
        <div v-else class="flex flex-col gap-1">
          <button @click="openChangePw" title="Сменить пароль" class="w-full flex justify-center p-2.5 rounded-lg text-slate-400 hover:bg-blue-50 hover:text-blue-500 transition-colors">
            <KeyRound class="w-4 h-4" />
          </button>
          <button @click="logout" title="Выйти" class="w-full flex justify-center p-2.5 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-500 transition-colors">
            <LogOut class="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>

    <!-- Модал смены пароля -->
    <div v-if="showChangePw" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div class="bg-white rounded-2xl shadow-xl p-6 w-full max-w-sm">
        <div class="flex items-center justify-between mb-5">
          <h3 class="font-black text-slate-800">Смена пароля</h3>
          <button @click="showChangePw = false" class="p-1 rounded-lg hover:bg-slate-100 text-slate-400"><X class="w-4 h-4" /></button>
        </div>
        <div v-if="changePwOk" class="p-4 bg-green-50 text-green-700 text-sm font-bold rounded-xl text-center">Пароль успешно изменён!</div>
        <template v-else>
          <div v-if="changePwError" class="mb-3 p-3 bg-red-50 text-red-600 text-sm font-bold rounded-xl">{{ changePwError }}</div>
          <div class="space-y-3">
            <input v-model="changePwCurrent" type="password" placeholder="Текущий пароль"
              class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-blue-400" />
            <input v-model="changePwNew" type="password" placeholder="Новый пароль (мин. 6 символов)"
              class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-blue-400" />
            <input v-model="changePwConfirm" type="password" placeholder="Подтвердите новый пароль"
              class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-blue-400" />
          </div>
          <button @click="submitChangePw" :disabled="changePwLoading"
            class="w-full mt-4 bg-blue-600 hover:bg-blue-700 text-white font-black py-3 rounded-xl transition-colors">
            {{ changePwLoading ? 'Сохраняем...' : 'Сменить пароль' }}
          </button>
        </template>
      </div>
    </div>

    <!-- Основной контент -->
    <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
      <!-- Мобильная шапка с гамбургером -->
      <div v-if="!$route.meta.public && $route.name !== 'login' && !hideChrome" class="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-slate-200 flex-shrink-0 z-20">
        <button @click="isMobileOpen = true" class="p-2 rounded-lg text-slate-500 hover:bg-slate-100 transition-colors">
          <Menu class="w-5 h-5" />
        </button>
        <h2 class="text-lg tracking-tight flex items-baseline text-[#222D3D]" style="font-family: 'Montserrat', sans-serif; font-weight: 800;">
          CX Видовит<span class="relative inline-block">
            о
            <svg class="absolute bottom-[-2px] left-1/2 -translate-x-1/2 w-3 h-2 text-[#FFC107]" viewBox="0 0 24 12" fill="none">
              <path d="M2 2 Q12 14 22 2" stroke="currentColor" stroke-width="5" stroke-linecap="round"/>
            </svg>
          </span>
        </h2>
      </div>

      <div
        v-if="serverUnreachable && $route.name !== 'login' && !$route.meta.public"
        class="flex items-center gap-2 px-4 py-2.5 bg-amber-50 border-b border-amber-200 text-amber-900 text-sm font-medium flex-shrink-0"
      >
        <AlertTriangle class="w-4 h-4 flex-shrink-0" />
        Нет связи с сервером. Страница откроется сама, как только ответ появится — можно подождать или обновить.
      </div>
      <main :class="['flex-1 overflow-x-hidden', hideChrome ? 'overflow-hidden' : 'overflow-y-auto']">
        <RouterView v-slot="{ Component }">
          <KeepAlive :max="12">
            <component :is="Component" />
          </KeepAlive>
        </RouterView>
      </main>
    </div>

  </div>
</template>
