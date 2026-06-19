<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import {
  Database, Bot, ClipboardCheck, BrainCircuit, ScrollText, MessageSquare,
  LineChart, AlertTriangle, Star, ShieldAlert, BadgeDollarSign, LayoutGrid,
  ChevronLeft, ChevronRight, Menu, PackageCheck, LogOut, KeyRound, X, Eye, EyeOff
} from 'lucide-vue-next'
import { usePlatformStore } from './stores/platform'
import { usePermissionsStore } from './stores/permissions'

const platformStore    = usePlatformStore()
const permissionsStore = usePermissionsStore()

const isCollapsed  = ref(false)
const isMobileOpen = ref(false)

// Доступные площадки (из прав текущего пользователя)
const availablePlatforms = computed(() => permissionsStore.allowedMarketplaces)

onMounted(() => {
  const saved = localStorage.getItem('sidebar_collapsed')
  if (saved !== null) isCollapsed.value = saved === 'true'
})

watch(isCollapsed, (val) => {
  localStorage.setItem('sidebar_collapsed', String(val))
})

// Авто-выбор площадки, если у пользователя только одна
watch(() => permissionsStore.loaded, (loaded) => {
  if (loaded && availablePlatforms.value.length === 1) {
    platformStore.setPlatform(availablePlatforms.value[0])
  }
})

const toggleCollapse = () => { isCollapsed.value = !isCollapsed.value }
const closeMobile = () => { isMobileOpen.value = false }

const currentUser = localStorage.getItem('username') || ''

const logout = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  localStorage.removeItem('username')
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
    const token = localStorage.getItem('token')
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
  <div class="flex h-screen bg-slate-50 overflow-hidden">

    <!-- Мобильный backdrop -->
    <div
      v-if="isMobileOpen"
      class="fixed inset-0 bg-black/40 z-30 lg:hidden"
      @click="closeMobile"
    />

    <!-- Сайдбар -->
    <aside
      v-if="!$route.meta.public && $route.name !== 'login'"
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
      <div v-if="availablePlatforms.length > 1"
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
                'flex items-center justify-center p-1.5 rounded-xl border-2 transition-all duration-200',
                platformStore.platform === p
                  ? [platformBorder[p], 'bg-white shadow-md scale-105']
                  : 'border-transparent bg-slate-50 hover:bg-white hover:border-slate-200 hover:shadow-sm'
              ]"
            >
              <img v-if="p !== 'all'"
                   :src="platformImgSrc[p]"
                   :alt="platformLabel[p]"
                   :class="['w-9 h-9 rounded-lg object-cover transition-all duration-300',
                            platformStore.platform !== p ? 'grayscale opacity-35' : '']" />
              <div v-else
                   :class="['grid grid-cols-2 gap-0.5 w-9 h-9 p-1 rounded-lg bg-slate-100 transition-all duration-300',
                            platformStore.platform !== 'all' ? 'grayscale opacity-35' : '']">
                <img src="/icons/wb.png"   class="w-full h-full rounded-sm object-cover" />
                <img src="/icons/ym.png"   class="w-full h-full rounded-sm object-cover" />
                <img src="/icons/ozon.png" class="w-full h-full rounded-sm object-cover" />
                <div class="w-full h-full rounded-sm bg-slate-300" />
              </div>
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
          <div v-else class="grid grid-cols-2 gap-0.5 w-8 h-8 p-0.5 rounded-lg bg-slate-100">
            <img src="/icons/wb.png"   class="w-full h-full rounded-sm object-cover" />
            <img src="/icons/ym.png"   class="w-full h-full rounded-sm object-cover" />
            <img src="/icons/ozon.png" class="w-full h-full rounded-sm object-cover" />
            <div class="w-full h-full rounded-sm bg-slate-300" />
          </div>
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

        <div v-if="!isCollapsed && permissionsStore.can('sync')" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-2 px-2">
          Технический блок
        </div>
        <div v-else-if="isCollapsed && permissionsStore.can('sync')" class="border-t border-slate-100 my-2"></div>

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

        <div v-if="!isCollapsed && permissionsStore.can('production')" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Операционный блок
        </div>
        <div v-else-if="isCollapsed && permissionsStore.can('production')" class="border-t border-slate-100 my-2"></div>

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

        <div v-if="!isCollapsed && permissionsStore.can('reshipment')" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Функциональный блок
        </div>
        <div v-else-if="isCollapsed && permissionsStore.can('reshipment')" class="border-t border-slate-100 my-2"></div>

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
          <PackageCheck class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Отправка деталей</span>
        </RouterLink>

        <div v-if="!isCollapsed && (permissionsStore.can('admin_panel') || permissionsStore.can('logs'))" class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Административный блок
        </div>
        <div v-else-if="isCollapsed && (permissionsStore.can('admin_panel') || permissionsStore.can('logs'))" class="border-t border-slate-100 my-2"></div>

        <RouterLink
          v-if="permissionsStore.can('admin_panel')"
          to="/admin"
          :class="[
            'flex items-center rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors',
            isCollapsed ? 'justify-center p-3' : 'gap-3 px-3 py-2.5'
          ]"
          active-class="bg-red-50 text-red-700 font-semibold"
          :title="isCollapsed ? 'Панель управления' : ''"
        >
          <ShieldAlert class="w-4 h-4 flex-shrink-0" />
          <span v-if="!isCollapsed">Панель управления</span>
        </RouterLink>

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
      <div v-if="!$route.meta.public && $route.name !== 'login'" class="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-slate-200 flex-shrink-0 z-20">
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

      <main class="flex-1 overflow-y-auto overflow-x-hidden">
        <RouterView />
      </main>
    </div>

  </div>
</template>
