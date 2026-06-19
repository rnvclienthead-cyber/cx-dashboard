<script setup>
import { ref, onMounted, computed } from 'vue'
import { apiFetch } from '../api'
import {
  ShieldAlert, Users, MailPlus, Trash2, Check, UserCog,
  Plus, Settings2, X, Save, Globe, ChevronDown, ChevronRight, KeyRound
} from 'lucide-vue-next'

// ── Пользователи и инвайты ────────────────────────────────────────────────────
const users        = ref([])
const invites      = ref([])
const newInviteEmail = ref('')
const newInviteRole  = ref('sales_manager')
const isLoading    = ref(true)

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
const mpLabel = { all: 'Все', wb: 'WB', ym: 'ЯМ' }
const mpColor = { all: 'bg-slate-100 text-slate-600', wb: 'bg-purple-100 text-purple-700', ym: 'bg-yellow-100 text-yellow-700' }

// ── Инвайты: список ролей из БД ───────────────────────────────────────────────
const roleOptions = computed(() =>
  dbRoles.value.map(r => ({ id: r.name, name: r.display_name }))
)

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
  for (const p of role.permissions || []) permsMap[p.module] = p.marketplace
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
const resetTarget = ref(null)   // { id, email }
const resetNewPw  = ref('')
const resetError  = ref('')
const resetOk     = ref(false)

const openReset = (user) => {
  resetTarget.value = user
  resetNewPw.value  = ''
  resetError.value  = ''
  resetOk.value     = false
}

const submitReset = async () => {
  resetError.value = ''
  if (resetNewPw.value.length < 6) { resetError.value = 'Минимум 6 символов'; return }
  try {
    const res = await apiFetch('/api/auth/admin/reset-password', {
      method: 'POST',
      body: JSON.stringify({ user_id: resetTarget.value.id, new_password: resetNewPw.value })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка')
    resetOk.value = true
    setTimeout(() => { resetTarget.value = null }, 1500)
  } catch (e) { resetError.value = e.message }
}

const deleteRole = async (roleName) => {
  if (!confirm(`Удалить роль «${roleName}»?`)) return
  try {
    await apiFetch(`/api/v1/admin/roles/${roleName}`, { method: 'DELETE' })
    await loadData()
  } catch (e) { alert(e.message) }
}

onMounted(loadData)
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
                  <th class="pb-3 font-bold">Роль</th>
                  <th class="pb-3 font-bold text-right">Пароль</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="user in users" :key="user.id" class="hover:bg-slate-50">
                  <td class="py-3 font-black text-slate-400">#{{ user.id }}</td>
                  <td class="py-3 font-bold text-slate-800">{{ user.email }}</td>
                  <td class="py-3">
                    <select v-model="user.role" @change="changeUserRole(user.id, user.role)"
                      class="bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 text-xs font-bold text-slate-700 focus:outline-none cursor-pointer">
                      <option v-for="r in roleOptions" :key="r.id" :value="r.id">{{ r.name }}</option>
                    </select>
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

            <!-- Модал сброса пароля -->
            <div v-if="resetTarget" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
              <div class="bg-white rounded-2xl shadow-xl p-6 w-full max-w-sm">
                <div class="flex items-center justify-between mb-4">
                  <h3 class="font-black text-slate-800">Сброс пароля</h3>
                  <button @click="resetTarget = null" class="p-1 rounded-lg hover:bg-slate-100 text-slate-400"><X class="w-4 h-4" /></button>
                </div>
                <p class="text-sm text-slate-500 mb-4">Новый пароль для <span class="font-bold text-slate-700">{{ resetTarget.email }}</span></p>
                <div v-if="resetOk" class="p-3 bg-green-50 text-green-700 text-sm font-bold rounded-xl text-center">Пароль сброшен!</div>
                <template v-else>
                  <div v-if="resetError" class="mb-3 p-3 bg-red-50 text-red-600 text-sm font-bold rounded-xl">{{ resetError }}</div>
                  <input v-model="resetNewPw" type="text" placeholder="Новый пароль"
                    class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-amber-400 mb-3" />
                  <button @click="submitReset"
                    class="w-full bg-amber-500 hover:bg-amber-600 text-white font-black py-3 rounded-xl transition-colors">
                    Сохранить новый пароль
                  </button>
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
                    {{ p.module }}
                    <span v-if="p.marketplace !== 'all'" :class="['font-bold', mpColor[p.marketplace]?.replace('bg-','text-').replace(' text-','')]">·{{ p.marketplace }}</span>
                  </span>
                  <span v-if="(role.permissions || []).length > 4" class="text-[10px] text-slate-400">+{{ role.permissions.length - 4 }}</span>
                </div>
                <component :is="editingRole?.name === role.name ? ChevronDown : ChevronRight" class="w-4 h-4 text-slate-400" />
              </div>
            </div>

            <!-- Редактор прав -->
            <div v-if="editingRole?.name === role.name" class="border-t border-slate-100 p-5">
              <p class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">Настройка доступов</p>

              <div class="space-y-2">
                <div v-for="mod in modules" :key="mod.key"
                  class="flex items-center gap-3 py-2 px-3 rounded-xl hover:bg-slate-50 transition-colors">

                  <!-- Toggle -->
                  <button type="button" @click="toggleModule(mod.key)"
                    :class="['relative flex-shrink-0 w-10 h-5 rounded-full transition-colors',
                      hasModule(mod.key) ? 'bg-emerald-500' : 'bg-slate-200']">
                    <span :class="['absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform',
                      hasModule(mod.key) ? 'translate-x-5' : 'translate-x-0.5']"></span>
                  </button>

                  <!-- Название модуля -->
                  <span :class="['text-sm flex-1', hasModule(mod.key) ? 'text-slate-800 font-medium' : 'text-slate-400']">
                    {{ mod.label }}
                  </span>

                  <!-- Маркетплейс (только если модуль включён) -->
                  <div v-if="hasModule(mod.key)" class="flex gap-1">
                    <button v-for="mp in marketplaces" :key="mp"
                      type="button" @click="setMarketplace(mod.key, mp)"
                      :class="['px-2.5 py-1 text-xs font-semibold rounded-lg transition-colors',
                        editingRole.permsMap[mod.key] === mp
                          ? (mp === 'all' ? 'bg-slate-700 text-white' : mp === 'wb' ? 'bg-purple-600 text-white' : 'bg-yellow-500 text-white')
                          : 'bg-slate-100 text-slate-500 hover:bg-slate-200']">
                      {{ mpLabel[mp] }}
                    </button>
                  </div>
                  <div v-else class="w-28"></div>
                </div>
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
