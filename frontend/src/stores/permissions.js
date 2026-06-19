import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../api'

export const usePermissionsStore = defineStore('permissions', () => {
  const role        = ref('')
  const permissions = ref({})   // { module: 'all' | 'wb' | 'ym' }
  const loaded      = ref(false)

  const can = (module) => !!permissions.value[module]
  const marketplace = (module) => permissions.value[module] || null

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

  return { role, permissions, loaded, can, marketplace, isAdmin, isCS, isWarehouse, load, clear }
})
