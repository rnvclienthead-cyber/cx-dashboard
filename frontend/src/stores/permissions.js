import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../api'

export const usePermissionsStore = defineStore('permissions', () => {
  const role        = ref('')
  const permissions = ref({})   // { module: 'all' | 'wb' | 'ym' }
  const loaded      = ref(false)

  const can = (module) => !!permissions.value[module]
  const marketplace = (module) => permissions.value[module] || null

  // Какие площадки доступны пользователю для переключения
  const allowedMarketplaces = computed(() => {
    if (['admin', 'moderator'].includes(role.value)) return ['wb', 'ym', 'ozon', 'all']
    if (!loaded.value) return ['wb', 'ym', 'ozon', 'all']
    const vals = new Set(Object.values(permissions.value).filter(Boolean))
    if (vals.size === 0 || vals.has('all')) return ['wb', 'ym', 'ozon', 'all']
    const specific = [...vals]
    if (specific.length === 1) return specific
    return ['wb', 'ym', 'ozon', 'all'].filter(p => specific.includes(p) || p === 'all')
  })

  const isAdmin     = computed(() => role.value === 'admin')
  const isCS        = computed(() => ['cs_manager', 'admin', 'moderator'].includes(role.value))
  const isWarehouse = computed(() => ['warehouse_manager', 'admin', 'moderator'].includes(role.value))

  async function load() {
    const token = localStorage.getItem('token')
    if (!token) { loaded.value = true; return }
    try {
      const res  = await apiFetch('/api/v1/admin/roles/my-permissions')
      const data = await res.json()
      role.value        = data.role || localStorage.getItem('role') || ''
      permissions.value = data.permissions || {}
    } catch {
      role.value = localStorage.getItem('role') || ''
    } finally {
      loaded.value = true
    }
  }

  function clear() {
    role.value = ''; permissions.value = {}; loaded.value = false
  }

  return { role, permissions, loaded, can, marketplace, allowedMarketplaces, isAdmin, isCS, isWarehouse, load, clear }
})
