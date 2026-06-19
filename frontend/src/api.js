// frontend/src/api.js
import router from './router'

export const apiFetch = async (url, options = {}) => {
  const token = localStorage.getItem('token')
  
  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`

  if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const config = { ...options, headers }
  const response = await fetch(url, config)

  // 1. Если токен протух — выкидываем на логин
  if (response.status === 401) {
    console.warn("Токен недействителен. Выполняем выход.")
    localStorage.removeItem('token')
    localStorage.removeItem('role')
    localStorage.removeItem('username')
    router.push('/login')
    throw new Error('Сессия истекла. Пожалуйста, войдите снова.')
  }

  // 2. УМНАЯ ЗАЩИТА: Если бэкенд ответил любой другой ошибкой (400, 403, 500)
  if (!response.ok) {
    let errorMessage = 'Ошибка сервера'
    try {
      const errData = await response.json()
      errorMessage = errData.detail || errorMessage
    } catch (e) {
      // Если сервер вернул не JSON (например, упал Nginx)
    }
    throw new Error(errorMessage) // Вызываем ошибку, чтобы сработал блок catch в компонентах
  }

  return response
}