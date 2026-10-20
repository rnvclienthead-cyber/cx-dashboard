<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { apiFetch } from '../api'
import {
  ShieldAlert, Users, MailPlus, Trash2, Check, UserCog,
  Plus, Settings2, X, Save, Globe, ChevronDown, ChevronRight, KeyRound, Send
} from 'lucide-vue-next'

// ── Пользователи и инвайты ────────────────────────────────────────────────────
const users        = ref([])
const invites      = ref([])
const newInviteEmail = ref('')
const newInviteRole  = ref('sales_manager')
const isLoading    = ref(true)
const presence     = ref({})
let presenceTimer  = null

// ── Роли и права ─────────────────────────────────────────────────────────────
const dbRoles      = ref([])    // роли из БД
const modules      = ref([])    // список модулей
const marketplaces = ref([])    // wb, ym, all
const activeTab    = ref('users')  // users | roles

const editingRole  = ref(null)   // роль, открытая на редактирование
const newRole      = ref({ name: '', display_name: '', description: '' })
const showNewRole  = ref(false)
const saving       = ref(false)

// Маркетплейсы: читаемые названия
const mpLabel = { all: 'Все', wb: 'WB', ym: 'ЯМ', ozon: 'OZON' }
const mpColor = { all: 'bg-slate-100 text-slate-600', wb: 'bg-purple-100 text-purple-700', ym: 'bg-yellow-100 text-yellow-700', ozon: 'bg-blue-100 text-blue-700' }

// ── Инвайты: список ролей из БД ───────────────────────────────────────────────
const roleOptions = computed(() =>
  dbRoles.value.map(r => ({ id: r.name, name: r.display_name }))
)

const moduleByKey = computed(() =>
  Object.fromEntries(modules.value.map(module => [module.key, module]))
)

const moduleGroups = computed(() => {
  const groups = []
  for (const module of modules.value) {
    let group = groups.find(item => item.name === module.group)
    if (!group) {
      group = { name: module.group || 'Другие разделы', modules: [] }
      groups.push(group)
    }
    group.modules.push(module)
  }
  return groups
})

// ── Загрузка ─────────────────────────────────────────────────────────────────
const loadData = async () => {
  isLoading.value = true
  try {
    const [usersRes, invitesRes, rolesRes, modulesRes] = await Promise.all([
      apiFetch('/api/auth/users'),
      apiFetch('/api/auth/invites'),
      apiFetch('/api/v1/admin/roles'),
      apiFetch('/api/v1/admin/roles/modules'),
    ])
    users.value   = await usersRes.json()
    invites.value = await invitesRes.json()
    const rolesData   = await rolesRes.json()
    const modulesData = await modulesRes.json()
    dbRoles.value   = rolesData.data || []
    modules.value   = modulesData.modules || []
    marketplaces.value = modulesData.marketplaces || []
  } catch (e) {
    console.error('Ошибка загрузки данных', e)
  } finally {
    isLoading.value = false
  }
}

// ── Пользователи ─────────────────────────────────────────────────────────────
const changeUserRole = async (userId, newRole) => {
  try {
    await apiFetch(`/api/auth/users/${userId}/role`, { method: 'PUT', body: JSON.stringify({ role: newRole }) })
  } catch { alert('Ошибка при изменении роли') }
}

// ── Telegram: привязка чата + флаг системных алертов ──────────────────────────
const savingTg = ref(null)
const savedTg  = ref(null)

const _putTg = (user) => apiFetch(`/api/auth/users/${user.id}/tg`, {
  method: 'PUT',
  body: JSON.stringify({ tg_chat_id: user.tg_chat_id || '', tg_alerts: !!user.tg_alerts })
})

// Тихое сохранение при клике на тумблер (без смены иконки кнопки)
const toggleUserTgAlerts = async (user) => {
  user.tg_alerts = !user.tg_alerts
  try { await _putTg(user) }
  catch { user.tg_alerts = !user.tg_alerts; alert('Ошибка сохранения') }
}

// Сохранение через кнопку (chat_id + текущий флаг) — показывает галочку
const saveUserTg = async (user) => {
  savingTg.value = user.id
  try {
    await _putTg(user)
    savedTg.value = user.id
    setTimeout(() => { if (savedTg.value === user.id) savedTg.value = null }, 2000)
  } catch { alert('Ошибка сохранения Telegram') }
  finally { savingTg.value = null }
}

const createInvite = async () => {
  if (!newInviteEmail.value) return
  try {
    await apiFetch('/api/auth/invites', {
      method: 'POST',
      body: JSON.stringify({ email: newInviteEmail.value, role: newInviteRole.value })
    })
    newInviteEmail.value = ''
    await loadData()
  } catch { alert('Ошибка: email уже в списке') }
}

const removeInvite = async (email) => {
  if (!confirm(`Удалить ${email}?`)) return
  try {
    await apiFetch(`/api/auth/invites/${email}`, { method: 'DELETE' })
    await loadData()
  } catch { alert('Ошибка при удалении') }
}

// ── Роли: редактирование прав ─────────────────────────────────────────────────
const openRole = (role) => {
  // Создаём локальную копию прав для редактирования
  const permsMap = {}
  for (const p of role.permissions || []) {
    const module = moduleByKey.value[p.module]
    if (module?.assignable !== false) permsMap[p.module] = p.marketplace
  }
  editingRole.value = { ...role, permsMap }
}

const hasModule = (module) => !!editingRole.value?.permsMap[module]

const toggleModule = (module) => {
  if (!editingRole.value) return
  if (editingRole.value.permsMap[module]) {
    delete editingRole.value.permsMap[module]
  } else {
    editingRole.value.permsMap[module] = 'all'
  }
}

const setMarketplace = (module, mp) => {
  if (editingRole.value) editingRole.value.permsMap[module] = mp
}

const savePermissions = async () => {
  if (!editingRole.value) return
  saving.value = true
  try {
    const permissions = Object.entries(editingRole.value.permsMap)
      .map(([module, marketplace]) => ({ module, marketplace }))
    await apiFetch(`/api/v1/admin/roles/${editingRole.value.name}/permissions`, {
      method: 'PUT',
      body: JSON.stringify({ permissions }),
    })
    await loadData()
    editingRole.value = null
  } catch (e) { alert(e.message) }
  finally { saving.value = false }
}

const createRole = async () => {
  if (!newRole.value.name || !newRole.value.display_name) { alert('Заполните имя и название'); return }
  try {
    await apiFetch('/api/v1/admin/roles', {
      method: 'POST',
      body: JSON.stringify(newRole.value),
    })
    newRole.value = { name: '', display_name: '', description: '' }
    showNewRole.value = false
    await loadData()
  } catch (e) { alert(e.message) }
}

// ── Сброс пароля пользователя (admin) ────────────────────────────────────────
const resetTarget  = ref(null)   // { id, email }
const resetError   = ref('')
const resetOk      = ref(false)
const resetLoading = ref(false)

const openReset = (user) => {
  resetTarget.value  = user
  resetError.value   = ''
  resetOk.value      = false
  resetLoading.value = false
}

const submitReset = async () => {
  resetError.value   = ''
  resetLoading.value = true
  try {
    const res = await apiFetch('/api/auth/admin/reset-to-activation', {
      method: 'POST',
      body: JSON.stringify({ user_id: resetTarget.value.id })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка')
    resetOk.value = true
    setTimeout(() => { resetTarget.value = null; loadData() }, 1800)
  } catch (e) {
    resetError.value = e.message
  } finally {
    resetLoading.value = false
  }
}

const deleteRole = async (roleName) => {
  if (!confirm(`Удалить роль «${roleName}»?`)) return
  try {
    await apiFetch(`/api/v1/admin/roles/${roleName}`, { method: 'DELETE' })
    await loadData()
  } catch (e) { alert(e.message) }
}

onMounted(() => {
  loadData()
  loadPresence()
  presenceTimer = setInterval(loadPresence, 2000)
})

onUnmounted(() => {
  if (presenceTimer) clearInterval(presenceTimer)
})

const onlineNow = computed(() =>
  Object.values(presence.value).filter((s) => s.status === 'online')
)

function presenceOf (user) {
  return presence.value[user.id] || null
}

function presenceLabel (user) {
  const s = presenceOf(user)
  if (!s || s.status === 'offline') return 'не в сети'
  if (s.status === 'online') return 'онлайн'
  const sec = s.last_seen_sec
  if (sec != null && sec < 60) return `${sec} сек назад`
  if (s.last_seen_min != null && s.last_seen_min < 60) return `${s.last_seen_min} мин назад`
  return 'недавно'
}

async function loadPresence () {
  try {
    const r = await apiFetch('/api/v1/cx-control/staff')
    const d = await r.json()
    const map = {}
    for (const s of d.staff || []) map[s.id] = s
    presence.value = map
  } catch { /* тихий опрос */ }
}
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto pb-24 font-sans text-slate-800">

    <!-- Заголовок -->
    <div class="flex items-center gap-4 mb-6 border-b border-slate-200 pb-6">
      <div class="p-3 bg-red-100 text-red-600 rounded-xl shadow-sm">
        <ShieldAlert class="w-7 h-7" />
      </div>
      <div>
        <h1 class="text-2xl font-black tracking-tight">Панель управления</h1>
        <p class="text-sm text-slate-500 font-medium">Безопасность, доступы и настройка ролей</p>
      </div>
    </div>

    <!-- Вкладки -->
    <div class="flex gap-2 mb-6">
      <button v-for="tab in [{id:'users', label:'Пользователи', icon: Users}, {id:'roles', label:'Роли и доступы', icon: Settings2}]"
        :key="tab.id" @click="activeTab = tab.id"
        :class="['flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors',
          activeTab === tab.id ? 'bg-slate-800 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50']">
        <component :is="tab.icon" class="w-4 h-4" />{{ tab.label }}
      </button>
    </div>

    <div v-if="isLoading" class="text-center py-20 text-slate-400 font-bold animate-pulse">Загрузка...</div>

    <div v-else>

      <!-- ── Вкладка: Пользователи ────────────────────────────────────────── -->
      <div v-if="activeTab === 'users'" class="space-y-6">

        <div class="rounded-2xl border px-5 py-4 flex items-start gap-3"
          :class="onlineNow.length ? 'bg-emerald-50 border-emerald-100' : 'bg-slate-50 border-slate-200'">
          <span class="mt-1.5 w-2.5 h-2.5 rounded-full flex-shrink-0"
            :class="onlineNow.length ? 'bg-emerald-500' : 'bg-slate-300'"></span>
          <div class="min-w-0">
            <div class="text-sm font-bold" :class="onlineNow.length ? 'text-emerald-800' : 'text-slate-600'">
              {{ onlineNow.length ? `Сейчас на сайте: ${onlineNow.map(s => s.name).join(', ')}` : 'На сайте сейчас никого нет' }}
            </div>
            <div class="text-xs mt-1" :class="onlineNow.length ? 'text-emerald-700/80' : 'text-slate-400'">
              {{ onlineNow.length ? 'Есть живая активность (клик, набор, прокрутка).' : 'Можно перезапускать службу — вкладок без действий не считаем.' }}
            </div>
          </div>
        </div>

        <!-- Белый список -->
        <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
          <div class="p-5 bg-slate-50 border-b border-slate-100 flex items-center gap-2">
            <MailPlus class="w-5 h-5 text-indigo-500" />
            <h2 class="font-bold text-slate-700">Белый список — выдача доступов</h2>
          </div>
          <div class="p-6">
            <form @submit.prevent="createInvite" class="flex flex-col md:flex-row gap-3 mb-6">
              <input v-model="newInviteEmail" type="email" placeholder="email@company.com" required
                class="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none focus:border-indigo-500" />
              <select v-model="newInviteRole"
                class="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none cursor-pointer">
                <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.name }}</option>
              </select>
              <button type="submit" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold px-6 py-3 rounded-xl transition-colors flex items-center gap-2">
                <Check class="w-4 h-4" /> Добавить
              </button>
            </form>
            <table class="w-full text-left text-sm">
              <thead>
                <tr class="text-slate-400 uppercase tracking-wider text-[10px] border-b">
                  <th class="pb-3 font-bold">Email</th>
                  <th class="pb-3 font-bold">Роль</th>
                  <th class="pb-3 font-bold text-right">Удалить</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="inv in invites" :key="inv.email" class="hover:bg-slate-50">
                  <td class="py-3 font-semibold text-slate-700">{{ inv.email }}</td>
                  <td class="py-3"><span class="px-2.5 py-1 bg-slate-100 text-slate-600 rounded-lg text-xs font-bold">{{ inv.role }}</span></td>
                  <td class="py-3 text-right">
                    <button @click="removeInvite(inv.email)" class="text-red-400 hover:text-red-600 p-2 rounded-lg hover:bg-red-50">
                      <Trash2 class="w-4 h-4" />
                    </button>
                  </td>
                </tr>
                <tr v-if="!invites.length"><td colspan="3" class="py-6 text-center text-slate-400">Список пуст</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Пользователи -->
        <div class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
          <div class="p-5 bg-slate-50 border-b border-slate-100 flex items-center gap-2">
            <Users class="w-5 h-5 text-blue-500" />
            <h2 class="font-bold text-slate-700">Зарегистрированные пользователи</h2>
          </div>
          <div class="p-6 overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead>
                <tr class="text-slate-400 uppercase tracking-wider text-[10px] border-b">
                  <th class="pb-3 font-bold">ID</th>
                  <th class="pb-3 font-bold">Email</th>
                  <th class="pb-3 font-bold">На сайте</th>
                  <th class="pb-3 font-bold">Роль</th>
                  <th class="pb-3 font-bold">Telegram-алерты</th>
                  <th class="pb-3 font-bold text-right">Пароль</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="user in users" :key="user.id" class="hover:bg-slate-50">
                  <td class="py-3 font-black text-slate-400">#{{ user.id }}</td>
                  <td class="py-3 font-bold text-slate-800">{{ user.email }}</td>
                  <td class="py-3">
                    <div class="flex items-center gap-2">
                      <span class="w-2 h-2 rounded-full flex-shrink-0"
                        :class="presenceOf(user)?.status === 'online' ? 'bg-emerald-500' : presenceOf(user)?.status === 'recent' ? 'bg-amber-400' : 'bg-slate-300'"></span>
                      <div>
                        <div class="text-xs font-bold"
                          :class="presenceOf(user)?.status === 'online' ? 'text-emerald-700' : presenceOf(user)?.status === 'recent' ? 'text-amber-700' : 'text-slate-400'">
                          {{ presenceLabel(user) }}
                        </div>
                        <div v-if="presenceOf(user)?.last_path && presenceOf(user)?.status !== 'offline'"
                          class="font-mono text-[10px] text-slate-400 truncate max-w-[140px]">
                          {{ presenceOf(user).last_path }}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td class="py-3">
                    <select v-model="user.role" @change="changeUserRole(user.id, user.role)"
                      class="bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 text-xs font-bold text-slate-700 focus:outline-none cursor-pointer">
                      <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.name }}</option>
                    </select>
                  </td>
                  <td class="py-3">
                    <div class="flex items-center gap-2">
                      <input v-model="user.tg_chat_id" type="text" placeholder="chat_id" inputmode="numeric"
                        class="w-28 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-mono text-slate-700 focus:outline-none focus:border-sky-400" />
                      <!-- Тумблер: получать алерты -->
                      <button type="button" @click="toggleUserTgAlerts(user)"
                        :title="user.tg_alerts ? 'Получает системные алерты' : 'Алерты выключены'"
                        :class="['relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
                                 user.tg_alerts ? 'bg-emerald-500' : 'bg-slate-300']">
                        <span :class="['pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition duration-200 ease-in-out',
                                       user.tg_alerts ? 'translate-x-4' : 'translate-x-0']"></span>
                      </button>
                      <Check v-if="user.tg_alerts" class="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
                      <button @click="saveUserTg(user)" :disabled="savingTg === user.id"
                        :class="['p-1.5 rounded-lg transition-colors', savedTg === user.id ? 'text-emerald-600 bg-emerald-50' : 'text-sky-500 hover:bg-sky-50']"
                        :title="savedTg === user.id ? 'Сохранено' : 'Сохранить'">
                        <Check v-if="savedTg === user.id" class="w-4 h-4" />
                        <Send v-else class="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                  <td class="py-3 text-right">
                    <button @click="openReset(user)" title="Сбросить пароль"
                      class="p-2 rounded-lg text-amber-500 hover:bg-amber-50 hover:text-amber-600 transition-colors">
                      <KeyRound class="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>

            <!-- Модал сброса пароля → активация -->
            <div v-if="resetTarget" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
              <div class="bg-white rounded-2xl shadow-xl p-6 w-full max-w-sm">
                <div class="flex items-center justify-between mb-4">
                  <h3 class="font-black text-slate-800">Сброс пароля</h3>
                  <button @click="resetTarget = null" class="p-1 rounded-lg hover:bg-slate-100 text-slate-400"><X class="w-4 h-4" /></button>
                </div>

                <div v-if="resetOk" class="p-4 bg-green-50 text-green-700 text-sm font-bold rounded-xl text-center">
                  ✓ Аккаунт сброшен — сотрудник может войти через активацию
                </div>
                <template v-else>
                  <p class="text-sm text-slate-600 mb-1">Аккаунт будет деактивирован:</p>
                  <p class="font-bold text-slate-800 mb-4">{{ resetTarget.email }}</p>
                  <p class="text-sm text-slate-500 mb-5">Сотрудник увидит ошибку при попытке войти и сможет самостоятельно задать новый пароль через кнопку <span class="font-semibold text-slate-700">«Активировать»</span> на странице входа.</p>
                  <div v-if="resetError" class="mb-3 p-3 bg-red-50 text-red-600 text-sm font-bold rounded-xl">{{ resetError }}</div>
                  <div class="flex gap-2">
                    <button @click="resetTarget = null"
                      class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-3 rounded-xl transition-colors">
                      Отмена
                    </button>
                    <button @click="submitReset" :disabled="resetLoading"
                      class="flex-1 bg-amber-500 hover:bg-amber-600 disabled:opacity-60 text-white font-black py-3 rounded-xl transition-colors">
                      {{ resetLoading ? 'Сброс...' : 'Сбросить' }}
                    </button>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Вкладка: Роли и доступы ──────────────────────────────────────── -->
      <div v-else-if="activeTab === 'roles'">

        <!-- Создать новую роль -->
        <div class="mb-4">
          <button @click="showNewRole = !showNewRole"
            class="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-xl transition-colors">
            <Plus class="w-4 h-4" /> Создать новую роль
          </button>
          <div v-if="showNewRole" class="mt-3 bg-white border border-emerald-200 rounded-2xl p-5">
            <div class="grid grid-cols-3 gap-3 mb-3">
              <div>
                <label class="text-xs font-bold text-slate-500 block mb-1">Системное имя <span class="text-slate-400">(только латиница и _)</span></label>
                <input v-model="newRole.name" type="text" placeholder="logistics_manager"
                  class="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-emerald-400 font-mono" />
              </div>
              <div>
                <label class="text-xs font-bold text-slate-500 block mb-1">Название</label>
                <input v-model="newRole.display_name" type="text" placeholder="Менеджер логистики"
                  class="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-emerald-400" />
              </div>
              <div>
                <label class="text-xs font-bold text-slate-500 block mb-1">Описание</label>
                <input v-model="newRole.description" type="text" placeholder="Краткое описание роли"
                  class="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-emerald-400" />
              </div>
            </div>
            <div class="flex gap-2">
              <button @click="createRole" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg transition-colors">
                Создать роль
              </button>
              <button @click="showNewRole = false" class="px-4 py-2 text-slate-500 hover:bg-slate-100 text-sm rounded-lg transition-colors">
                Отмена
              </button>
            </div>
          </div>
        </div>

        <!-- Список ролей -->
        <div class="space-y-3">
          <div v-for="role in dbRoles" :key="role.name"
            class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">

            <!-- Заголовок роли -->
            <div class="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-slate-50 transition-colors"
              @click="editingRole?.name === role.name ? editingRole = null : openRole(role)">
              <div class="flex items-center gap-3">
                <div :class="['w-2 h-2 rounded-full', role.is_system ? 'bg-red-400' : 'bg-emerald-400']"></div>
                <div>
                  <div class="flex items-center gap-2">
                    <span class="font-bold text-slate-800">{{ role.display_name }}</span>
                    <code class="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{{ role.name }}</code>
                    <span v-if="role.is_system" class="text-[10px] bg-red-50 text-red-500 px-1.5 py-0.5 rounded font-semibold">системная</span>
                  </div>
                  <p v-if="role.description" class="text-xs text-slate-400 mt-0.5">{{ role.description }}</p>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <!-- Краткий список доступов -->
                <div class="hidden md:flex flex-wrap gap-1 max-w-xs justify-end">
                  <span v-for="p in (role.permissions || []).slice(0, 4)" :key="p.module"
                    class="text-[10px] px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded">
                    {{ moduleByKey[p.module]?.label || p.module }}
                    <span v-if="p.marketplace !== 'all'" :class="['font-bold', mpColor[p.marketplace]?.replace('bg-','text-').replace(' text-','')]">·{{ p.marketplace }}</span>
                  </span>
                  <span v-if="(role.permissions || []).length > 4" class="text-[10px] text-slate-400">+{{ role.permissions.length - 4 }}</span>
                </div>
                <component :is="editingRole?.name === role.name ? ChevronDown : ChevronRight" class="w-4 h-4 text-slate-400" />
              </div>
            </div>

            <!-- Редактор прав -->
            <div v-if="editingRole?.name === role.name" class="border-t border-slate-100 p-5">
              <div class="mb-5">
                <p class="text-xs font-bold text-slate-500 uppercase tracking-wider">Настройка доступов</p>
                <p class="text-xs text-slate-400 mt-1">Включите нужные разделы. «Все» открывает данные всех маркетплейсов.</p>
              </div>

              <div class="space-y-5">
                <section v-for="group in moduleGroups" :key="group.name">
                  <h3 class="px-3 mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">{{ group.name }}</h3>
                  <div class="space-y-1">
                    <div v-for="mod in group.modules" :key="mod.key"
                      class="flex flex-col md:flex-row md:items-center gap-2 md:gap-3 py-2.5 px-3 rounded-xl hover:bg-slate-50 transition-colors">

                      <!-- Toggle -->
                      <button type="button" @click="toggleModule(mod.key)"
                        :disabled="mod.assignable === false"
                        :aria-label="`${hasModule(mod.key) ? 'Закрыть' : 'Открыть'} раздел ${mod.label}`"
                        :aria-pressed="hasModule(mod.key)"
                        :class="['relative flex-shrink-0 w-10 h-5 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-400 focus:ring-offset-2',
                          mod.assignable === false ? 'bg-slate-100 cursor-not-allowed' : hasModule(mod.key) ? 'bg-emerald-500' : 'bg-slate-200']">
                        <span :class="['absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform',
                          hasModule(mod.key) ? 'translate-x-5' : 'translate-x-0.5']"></span>
                      </button>

                      <!-- Название модуля -->
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                          <span :class="['text-sm', hasModule(mod.key) ? 'text-slate-800 font-semibold' : 'text-slate-500']">{{ mod.label }}</span>
                          <span v-if="mod.assignable === false" class="text-[10px] font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">Только администратор</span>
                        </div>
                        <p class="text-xs text-slate-400 mt-0.5">{{ mod.description }}</p>
                      </div>

                      <!-- Маркетплейс (только если модуль включён) -->
                      <div v-if="hasModule(mod.key)" class="flex flex-wrap gap-1 md:justify-end">
                        <button v-for="mp in marketplaces" :key="mp"
                          type="button" @click="setMarketplace(mod.key, mp)"
                          :aria-label="`${mod.label}: ${mpLabel[mp]}`"
                          :class="['px-2.5 py-1.5 text-xs font-semibold rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-1',
                            editingRole.permsMap[mod.key] === mp
                              ? (mp === 'all' ? 'bg-slate-700 text-white' : mp === 'wb' ? 'bg-purple-600 text-white' : mp === 'ym' ? 'bg-yellow-500 text-white' : 'bg-blue-600 text-white')
                              : 'bg-slate-100 text-slate-500 hover:bg-slate-200']">
                          {{ mpLabel[mp] }}
                        </button>
                      </div>
                      <div v-else-if="mod.assignable !== false" class="hidden md:block w-40"></div>
                    </div>
                  </div>
                </section>
              </div>

              <div class="flex items-center gap-3 mt-4 pt-4 border-t border-slate-100">
                <button @click="savePermissions" :disabled="saving"
                  class="flex items-center gap-2 px-5 py-2.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-60 text-white text-sm font-semibold rounded-xl transition-colors">
                  <Save class="w-4 h-4" />{{ saving ? 'Сохраняем...' : 'Сохранить права' }}
                </button>
                <button @click="editingRole = null" class="px-4 py-2.5 text-sm text-slate-500 hover:bg-slate-100 rounded-xl transition-colors">
                  Отмена
                </button>
                <button v-if="!role.is_system" @click="deleteRole(role.name)"
                  class="ml-auto flex items-center gap-1.5 px-3 py-2 text-xs text-red-500 hover:bg-red-50 rounded-xl transition-colors">
                  <Trash2 class="w-3.5 h-3.5" /> Удалить роль
                </button>
              </div>
            </div>

          </div>
        </div>

        <p class="text-xs text-slate-400 mt-4 text-center">
          Системные роли (admin, moderator) нельзя удалить. Изменение прав вступает в силу при следующем входе пользователя.
        </p>
      </div>

    </div>
  </div>
</template>
