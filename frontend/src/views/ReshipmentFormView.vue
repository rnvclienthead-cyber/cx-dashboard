<script setup>
import { ref, reactive, onMounted } from 'vue'
import IMask from 'imask'
import { PackageCheck, CheckCircle2, AlertCircle, Loader2, Upload, X, Info, Send, Bot } from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''

const state = ref('form')   // form | loading | success | error
const errorMsg = ref('')

// WB Chat ID — если форма открыта по ссылке из чата продавца
const wbChatId = ref('')

// ── Категории из БД ──────────────────────────────────────────────────────────
const categories = ref([])
onMounted(async () => {
  // Читаем ?cid= из URL (ставится автоматически когда ссылку отправляет WB воркер)
  const params = new URLSearchParams(window.location.search)
  wbChatId.value = params.get('cid') || ''
  try {
    const res = await fetch(`${API_BASE}/api/v1/reshipment/categories`)
    const data = await res.json()
    categories.value = data.categories || []
  } catch {}

  const phoneInput = document.getElementById('phone-input')
  if (phoneInput) {
    IMask(phoneInput, { mask: '+{7} (000) 000-00-00', lazy: false, placeholderChar: '_' })
    phoneInput.addEventListener('input', () => { form.customer_phone = phoneInput.value })
  }
})

// ── Форма ────────────────────────────────────────────────────────────────────
const form = reactive({
  customer_name:    '',
  customer_phone:   '',
  customer_email:   '',
  problem_type:     '',
  items_to_send:    '',
  address_postal:   '',
  address_region:   '',
  address_city:     '',
  address_street:   '',
  address_house:    '',
  personal_data_consent: false,
})

const PROBLEM_TYPES = ['Некомплект', 'Брак', 'Нужна другая деталь']

// Выбранный чат-бот для уведомлений (заглушки, реализация позже)
const selectedBot = ref(null) // null | 'telegram' | 'max'

// ── Файлы (фото) ─────────────────────────────────────────────────────────────
const uploadedFiles = ref([])
const fileInputRef = ref(null)

const onFilesSelected = async (event) => {
  const files = Array.from(event.target.files)
  for (const file of files) {
    const entry = reactive({ name: file.name, url: null, uploading: true, error: null })
    uploadedFiles.value.push(entry)
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/api/v1/reshipment/upload-photo`, { method: 'POST', body: fd })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Ошибка загрузки')
      entry.url = data.url
      entry.uploading = false
    } catch (e) {
      entry.error = e.message
      entry.uploading = false
    }
  }
  event.target.value = ''
}

const removeFile = (idx) => uploadedFiles.value.splice(idx, 1)

// ── Адрес: DaData autocomplete ────────────────────────────────────────────────
const DADATA_KEY = import.meta.env.VITE_DADATA_KEY || ''
const addressSuggestions = ref([])
const addressQuery = ref('')
let addressDebounce = null

const onAddressInput = () => {
  if (!DADATA_KEY || addressQuery.value.length < 3) { addressSuggestions.value = []; return }
  clearTimeout(addressDebounce)
  addressDebounce = setTimeout(async () => {
    try {
      const res = await fetch('https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Token ${DADATA_KEY}` },
        body: JSON.stringify({ query: addressQuery.value, count: 5, from_bound: { value: 'city' }, to_bound: { value: 'house' } }),
      })
      const data = await res.json()
      addressSuggestions.value = data.suggestions || []
    } catch {}
  }, 300)
}

const selectSuggestion = (s) => {
  const d = s.data
  form.address_postal = d.postal_code || ''
  form.address_region = d.region_with_type || ''
  form.address_city   = d.city_with_type || d.settlement_with_type || ''
  form.address_street = d.street_with_type || ''
  form.address_house  = [d.house_type, d.house, d.block_type, d.block].filter(Boolean).join(' ')
  addressQuery.value = s.value
  addressSuggestions.value = []
}

// ── Валидация и отправка ──────────────────────────────────────────────────────
const errors = reactive({})

const validate = () => {
  Object.keys(errors).forEach(k => delete errors[k])
  if (!form.customer_name.trim())   errors.customer_name = 'Укажите имя'
  const phoneDigits = form.customer_phone.replace(/\D/g, '')
  if (phoneDigits.length < 11)      errors.customer_phone = 'Введите полный номер'
  if (!form.problem_type)           errors.problem_type = 'Выберите тип проблемы'
  if (!form.items_to_send.trim())   errors.items_to_send = 'Укажите, что нужно отправить'
  if (!form.address_city.trim() && !form.address_street.trim())
                                    errors.address = 'Укажите хотя бы город и улицу'
  if (uploadedFiles.value.filter(f => f.url).length === 0)
                                    errors.photos = 'Прикрепите хотя бы одну фотографию'
  if (!form.personal_data_consent)  errors.personal_data_consent = 'Необходимо согласие'
  return Object.keys(errors).length === 0
}

const submit = async () => {
  if (!validate()) return
  state.value = 'loading'

  const photoFiles = JSON.stringify(uploadedFiles.value.filter(f => f.url).map(f => f.url))

  const fd = new FormData()
  fd.append('customer_name',     form.customer_name.trim())
  fd.append('customer_phone',    form.customer_phone)
  fd.append('customer_email',    form.customer_email || '')
  fd.append('problem_type',      form.problem_type)
  fd.append('items_to_send',     form.items_to_send.trim())
  fd.append('address_postal',    form.address_postal || '')
  fd.append('address_region',    form.address_region || '')
  fd.append('address_city',      form.address_city || '')
  fd.append('address_street',    form.address_street || '')
  fd.append('address_house',     form.address_house || '')
  fd.append('personal_data_consent', 'true')
  fd.append('photo_files',       photoFiles)
  fd.append('honeypot',          '')
  if (wbChatId.value) fd.append('wb_chat_id', wbChatId.value)

  try {
    const res = await fetch(`${API_BASE}/api/v1/reshipment/submit`, { method: 'POST', body: fd })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка сервера')
    state.value = 'success'
  } catch (e) {
    errorMsg.value = e.message
    state.value = 'error'
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-50 flex flex-col">

    <header class="bg-white border-b border-slate-200 px-6 py-4 flex items-center gap-3">
      <PackageCheck class="w-6 h-6 text-emerald-600 flex-shrink-0" />
      <div>
        <h1 class="text-lg font-bold text-slate-800" style="font-family: 'Montserrat', sans-serif;">
          Заявка на отправку детали
        </h1>
        <p class="text-xs text-slate-500">Видовито — служба поддержки покупателей</p>
      </div>
    </header>

    <main class="flex-1 flex justify-center px-4 py-8">
      <div class="w-full max-w-xl">

        <!-- Успех -->
        <div v-if="state === 'success'" class="bg-white rounded-2xl border border-slate-200 p-10 text-center shadow-sm">
          <CheckCircle2 class="w-16 h-16 text-emerald-500 mx-auto mb-4" />
          <h2 class="text-xl font-bold text-slate-800 mb-2">Заявка принята!</h2>
          <p class="text-slate-500 text-sm leading-relaxed">
            Мы рассмотрим вашу заявку и свяжемся с вами в ближайшее время.
          </p>
        </div>

        <!-- Ошибка -->
        <div v-else-if="state === 'error'" class="bg-white rounded-2xl border border-rose-200 p-10 text-center shadow-sm">
          <AlertCircle class="w-16 h-16 text-rose-400 mx-auto mb-4" />
          <h2 class="text-xl font-bold text-slate-800 mb-2">Что-то пошло не так</h2>
          <p class="text-slate-500 text-sm mb-6">{{ errorMsg }}</p>
          <button @click="state = 'form'" class="px-5 py-2.5 bg-slate-800 text-white text-sm font-semibold rounded-lg hover:bg-slate-700 transition-colors">
            Попробовать снова
          </button>
        </div>

        <!-- Форма -->
        <div v-else class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div class="bg-emerald-50 border-b border-emerald-100 px-6 py-4">
            <p class="text-sm text-emerald-800 leading-relaxed">
              Если в вашем заказе отсутствует деталь или комплектующая — заполните форму. Мы проверим и организуем отправку.
            </p>
          </div>

          <form @submit.prevent="submit" class="p-6 space-y-5">

            <!-- Ваши данные -->
            <fieldset>
              <legend class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Ваши данные</legend>
              <div class="space-y-3">

                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-1">Имя <span class="text-rose-500">*</span></label>
                  <input v-model="form.customer_name" type="text" placeholder="Иван Иванов"
                    :class="['w-full px-3 py-2.5 rounded-lg border text-sm outline-none transition-colors',
                      errors.customer_name ? 'border-rose-300 bg-rose-50' : 'border-slate-200 focus:border-emerald-400']" />
                  <p v-if="errors.customer_name" class="text-xs text-rose-500 mt-1">{{ errors.customer_name }}</p>
                </div>

                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-1">Телефон <span class="text-rose-500">*</span></label>
                  <input id="phone-input" type="tel" placeholder="+7 (___) ___-__-__"
                    :class="['w-full px-3 py-2.5 rounded-lg border text-sm outline-none transition-colors font-mono',
                      errors.customer_phone ? 'border-rose-300 bg-rose-50' : 'border-slate-200 focus:border-emerald-400']" />
                  <p v-if="errors.customer_phone" class="text-xs text-rose-500 mt-1">{{ errors.customer_phone }}</p>
                </div>

                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-1">Email <span class="text-slate-400 font-normal">(для уведомлений)</span></label>
                  <input v-model="form.customer_email" type="email" placeholder="mail@example.com"
                    class="w-full px-3 py-2.5 rounded-lg border border-slate-200 focus:border-emerald-400 text-sm outline-none transition-colors" />
                </div>

                <!-- Чат-бот для уведомлений -->
                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-2">
                    Чат-бот <span class="text-slate-400 font-normal">(для уведомлений)</span>
                  </label>
                  <div class="flex gap-2">
                    <button type="button" @click="selectedBot = selectedBot === 'telegram' ? null : 'telegram'"
                      :class="['flex items-center gap-2 px-4 py-2.5 rounded-xl border text-sm font-medium transition-colors flex-1 justify-center',
                        selectedBot === 'telegram'
                          ? 'bg-sky-500 border-sky-500 text-white'
                          : 'border-slate-200 text-slate-600 hover:border-sky-400 hover:text-sky-600']">
                      <Send class="w-4 h-4" />
                      Telegram
                    </button>
                    <button type="button" @click="selectedBot = selectedBot === 'max' ? null : 'max'"
                      :class="['flex items-center gap-2 px-4 py-2.5 rounded-xl border text-sm font-medium transition-colors flex-1 justify-center',
                        selectedBot === 'max'
                          ? 'bg-violet-500 border-violet-500 text-white'
                          : 'border-slate-200 text-slate-600 hover:border-violet-400 hover:text-violet-600']">
                      <Bot class="w-4 h-4" />
                      MAX
                    </button>
                  </div>
                  <div v-if="selectedBot" class="mt-2 px-3 py-2.5 rounded-lg border border-dashed text-sm flex items-center gap-2"
                    :class="selectedBot === 'telegram' ? 'border-sky-300 bg-sky-50 text-sky-700' : 'border-violet-300 bg-violet-50 text-violet-700'">
                    <CheckCircle2 class="w-4 h-4 flex-shrink-0" />
                    {{ selectedBot === 'telegram' ? 'Уведомления придут в Telegram-бот' : 'Уведомления придут в MAX' }}
                    <span class="text-xs opacity-70 ml-auto">скоро будет доступно</span>
                  </div>
                </div>

              </div>
            </fieldset>

            <hr class="border-slate-100" />

            <!-- Проблема -->
            <fieldset>
              <legend class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Проблема</legend>
              <div class="space-y-3">

                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-2">Тип проблемы <span class="text-rose-500">*</span></label>
                  <div class="flex flex-wrap gap-2">
                    <button v-for="pt in PROBLEM_TYPES" :key="pt" type="button" @click="form.problem_type = pt"
                      :class="['px-4 py-2 rounded-xl border text-sm font-medium transition-colors',
                        form.problem_type === pt
                          ? 'bg-emerald-600 border-emerald-600 text-white'
                          : 'border-slate-200 text-slate-600 hover:border-emerald-400 hover:text-emerald-700']">
                      {{ pt }}
                    </button>
                  </div>
                  <p v-if="errors.problem_type" class="text-xs text-rose-500 mt-1">{{ errors.problem_type }}</p>
                </div>

                <div>
                  <label class="block text-sm font-medium text-slate-700 mb-1">
                    Что нужно отправить <span class="text-rose-500">*</span>
                  </label>
                  <textarea v-model="form.items_to_send" rows="2"
                    placeholder="Например: болт М4 × 2 шт., гайка М4 × 2 шт."
                    :class="['w-full px-3 py-2.5 rounded-lg border text-sm resize-none outline-none transition-colors',
                      errors.items_to_send ? 'border-rose-300 bg-rose-50' : 'border-slate-200 focus:border-emerald-400']" />
                  <div class="flex items-start gap-1.5 mt-1.5 text-xs text-slate-400">
                    <Info class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                    <span>Указывайте название детали точно так, как в инструкции по сборке (раздел «Комплектация»)</span>
                  </div>
                  <p v-if="errors.items_to_send" class="text-xs text-rose-500 mt-1">{{ errors.items_to_send }}</p>
                </div>

              </div>
            </fieldset>

            <hr class="border-slate-100" />

            <!-- Фото -->
            <fieldset>
              <legend class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Фото <span class="text-rose-500">*</span></legend>
              <p class="text-xs text-slate-400 mb-3">Сфотографируйте содержимое упаковки и инструкцию с комплектацией</p>

              <div v-if="uploadedFiles.length" class="space-y-2 mb-3">
                <div v-for="(f, i) in uploadedFiles" :key="i"
                  :class="['flex items-center gap-2 px-3 py-2 rounded-lg border text-xs',
                    f.error ? 'border-rose-200 bg-rose-50 text-rose-600'
                    : f.uploading ? 'border-slate-200 bg-slate-50 text-slate-500'
                    : 'border-emerald-200 bg-emerald-50 text-emerald-700']">
                  <Loader2 v-if="f.uploading" class="w-3.5 h-3.5 animate-spin flex-shrink-0" />
                  <CheckCircle2 v-else-if="f.url" class="w-3.5 h-3.5 flex-shrink-0" />
                  <AlertCircle v-else class="w-3.5 h-3.5 flex-shrink-0" />
                  <span class="flex-1 truncate">{{ f.name }}</span>
                  <span v-if="f.error" class="text-rose-500">{{ f.error }}</span>
                  <button type="button" @click="removeFile(i)" class="flex-shrink-0 opacity-60 hover:opacity-100">
                    <X class="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              <input ref="fileInputRef" type="file" accept=".jpg,.jpeg,.png,.webp,.heic,.heif" multiple class="hidden"
                @change="onFilesSelected" />
              <button type="button" @click="fileInputRef.click()"
                :class="['w-full flex items-center justify-center gap-2 px-4 py-3 border-2 border-dashed rounded-xl text-sm transition-colors',
                  errors.photos ? 'border-rose-300 text-rose-500 bg-rose-50' : 'border-slate-200 text-slate-500 hover:border-emerald-400 hover:text-emerald-600']">
                <Upload class="w-4 h-4" />
                Выбрать фото (JPG, PNG, WEBP)
              </button>
              <p v-if="errors.photos" class="text-xs text-rose-500 mt-1">{{ errors.photos }}</p>
            </fieldset>

            <hr class="border-slate-100" />

            <!-- Адрес доставки -->
            <fieldset>
              <legend class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Адрес доставки</legend>

              <div class="relative">
                <input v-model="addressQuery" @input="onAddressInput" type="text"
                  placeholder="Начните вводить адрес — город, улицу, дом..."
                  :class="['w-full px-3 py-2.5 rounded-lg border text-sm outline-none transition-colors',
                    errors.address ? 'border-rose-300 bg-rose-50' : 'border-slate-200 focus:border-emerald-400']" />

                <!-- Подсказки DaData -->
                <div v-if="addressSuggestions.length"
                  class="absolute z-10 w-full mt-1 bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden">
                  <button v-for="s in addressSuggestions" :key="s.value" type="button"
                    @click="selectSuggestion(s)"
                    class="w-full text-left px-3 py-2.5 text-sm text-slate-700 hover:bg-emerald-50 border-b border-slate-100 last:border-0">
                    {{ s.value }}
                  </button>
                </div>
              </div>

              <!-- Выбранный адрес -->
              <div v-if="form.address_city" class="mt-2 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700 flex items-center gap-2">
                <CheckCircle2 class="w-3.5 h-3.5 flex-shrink-0" />
                {{ [form.address_postal, form.address_region, form.address_city, form.address_street, form.address_house].filter(Boolean).join(', ') }}
              </div>

              <p v-if="errors.address" class="text-xs text-rose-500 mt-1">{{ errors.address }}</p>
            </fieldset>

            <hr class="border-slate-100" />

            <!-- Согласие -->
            <div>
              <label :class="['flex gap-3 cursor-pointer select-none', errors.personal_data_consent ? 'text-rose-600' : 'text-slate-600']">
                <input v-model="form.personal_data_consent" type="checkbox"
                  class="mt-0.5 w-4 h-4 rounded accent-emerald-600 flex-shrink-0 cursor-pointer" />
                <span class="text-sm leading-relaxed">
                  Я даю согласие на обработку моих персональных данных (имя, контактные данные, адрес) в целях обработки настоящей заявки на отправку детали.
                </span>
              </label>
              <p v-if="errors.personal_data_consent" class="text-xs text-rose-500 mt-1 ml-7">{{ errors.personal_data_consent }}</p>
            </div>

            <input name="honeypot" type="text" style="display:none" tabindex="-1" autocomplete="off" />

            <button type="submit" :disabled="state === 'loading'"
              class="w-full flex items-center justify-center gap-2 px-6 py-3 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 disabled:cursor-not-allowed text-white font-semibold rounded-xl transition-colors text-sm">
              <Loader2 v-if="state === 'loading'" class="w-4 h-4 animate-spin" />
              <PackageCheck v-else class="w-4 h-4" />
              {{ state === 'loading' ? 'Отправляем...' : 'Отправить заявку' }}
            </button>

          </form>
        </div>
      </div>
    </main>

    <footer class="text-center text-xs text-slate-400 py-4">
      © {{ new Date().getFullYear() }} Видовит. Данные передаются по защищённому соединению HTTPS.
    </footer>
  </div>
</template>

<style scoped>
input:focus, textarea:focus, select:focus { outline: none; }
</style>
