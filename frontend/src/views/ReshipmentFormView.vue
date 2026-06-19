<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import IMask from 'imask'
import { PackageCheck, CheckCircle2, AlertCircle, Loader2, Upload, X, Info, Send } from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const FORM_BASE = 'http://box.vidovito.com'

const state = ref('form')   // form | loading | success | error
const errorMsg = ref('')
const submittedReqId = ref(null)

const wbChatId = ref('')
const salebotClientId = ref('')

const categories = ref([])

// Если открыто на поддомене vidovito — меняем заголовок и иконку
const isVidovitoDomain = window.location.hostname.includes('vidovito.com')
let prevTitle = ''
let prevFavicon = ''

onUnmounted(() => {
  if (isVidovitoDomain) {
    document.title = prevTitle
    const favicon = document.querySelector("link[rel='icon']")
    if (favicon) favicon.href = prevFavicon
  }
})

onMounted(async () => {
  if (isVidovitoDomain) {
    prevTitle = document.title
    const favicon = document.querySelector("link[rel='icon']")
    prevFavicon = favicon?.href || ''
    document.title = 'Сервисная служба | Vidovito – легкая мебель для дома'
    if (favicon) favicon.href = '/favicon-box.png'
    let metaDesc = document.querySelector("meta[name='description']")
    if (!metaDesc) {
      metaDesc = document.createElement('meta')
      metaDesc.name = 'description'
      document.head.appendChild(metaDesc)
    }
    metaDesc.content = 'Сервисная служба | Vidovito – легкая мебель для дома'
  }
  const params = new URLSearchParams(window.location.search)
  wbChatId.value = params.get('cid') || ''
  salebotClientId.value = params.get('sbc') || ''
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

const form = reactive({
  customer_name:    '',
  customer_phone:   '',
  customer_email:   '',
  product_category: '',
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
  fd.append('product_category',  form.product_category || '')
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
  if (salebotClientId.value) fd.append('salebot_client_id', salebotClientId.value)

  try {
    const res = await fetch(`${API_BASE}/api/v1/reshipment/submit`, { method: 'POST', body: fd })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка сервера')
    submittedReqId.value = data.id || null
    state.value = 'success'
  } catch (e) {
    errorMsg.value = e.message
    state.value = 'error'
  }
}
</script>

<template>
  <div class="min-h-screen flex flex-col" style="background:#F7F7F7; font-family:'Montserrat',sans-serif;">

    <!-- Шапка -->
    <header class="bg-white border-b border-gray-100 px-6 py-5">
      <a href="https://vidovito.com/" target="_blank" rel="noopener">
        <img src="/logo-dark.svg" alt="Видовито" class="h-10" />
      </a>
    </header>

    <main class="flex-1 flex justify-center px-4 py-8">
      <div class="w-full max-w-xl">

        <!-- Успех -->
        <div v-if="state === 'success'" class="bg-white rounded-3xl p-10 text-center" style="box-shadow:0 4px 24px rgba(0,0,0,0.07);">
          <div class="w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-6" style="background:#FFF8D6;">
            <CheckCircle2 class="w-10 h-10" style="color:#F5C000;" />
          </div>
          <h2 class="text-2xl font-black mb-2" style="color:#1E2235;">Заявка принята!</h2>
          <p class="text-sm leading-relaxed mb-6" style="color:#6B7280;">
            Мы рассмотрим её и свяжемся с вами в ближайшее время.
          </p>

          <!-- Уведомления через бот -->
          <div v-if="!salebotClientId"
            class="rounded-2xl p-5 text-left"
            style="background:#F9FAFB; border:1.5px solid #E5E7EB;">
            <p class="text-sm font-bold mb-1" style="color:#1E2235;">Получайте статусы заявки в мессенджере</p>
            <p class="text-xs leading-relaxed mb-4" style="color:#6B7280;">
              Выберите удобный мессенджер — бот пришлёт уведомление, когда статус изменится.
            </p>
            <div class="flex gap-3">
              <a :href="`https://t.me/vidovito_bot?start=box${submittedReqId}`" target="_blank" rel="noopener"
                class="flex items-center justify-center gap-2 flex-1 py-3 text-sm font-bold transition-opacity hover:opacity-90"
                style="background:#229ED9; color:#fff; border-radius:50px;">
                <Send class="w-4 h-4" />
                Telegram
              </a>
              <a :href="`https://max.ru/id1685010312_bot?start=box${submittedReqId}`" target="_blank" rel="noopener"
                class="flex items-center justify-center gap-2 flex-1 py-3 text-sm font-bold transition-opacity hover:opacity-90"
                style="background:#6E3EDD; color:#fff; border-radius:50px;">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"/>
                </svg>
                MAX
              </a>
            </div>
          </div>

          <div v-else
            class="flex items-center gap-2 px-4 py-3 rounded-2xl text-sm font-medium"
            style="background:#FFF8D6; color:#92700A;">
            <CheckCircle2 class="w-4 h-4 flex-shrink-0" style="color:#F5C000;" />
            Уведомления о статусе придут в мессенджер
          </div>
        </div>

        <!-- Ошибка -->
        <div v-else-if="state === 'error'" class="bg-white rounded-3xl p-10 text-center" style="box-shadow:0 4px 24px rgba(0,0,0,0.07);">
          <div class="w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-6" style="background:#FEF2F2;">
            <AlertCircle class="w-10 h-10" style="color:#EF4444;" />
          </div>
          <h2 class="text-2xl font-black mb-2" style="color:#1E2235;">Что-то пошло не так</h2>
          <p class="text-sm mb-6" style="color:#6B7280;">{{ errorMsg }}</p>
          <button @click="state = 'form'"
            class="px-8 py-3 font-bold text-sm transition-opacity hover:opacity-90"
            style="background:#1E2235; color:#fff; border-radius:50px;">
            Попробовать снова
          </button>
        </div>

        <!-- Форма -->
        <div v-else class="bg-white rounded-3xl overflow-hidden" style="box-shadow:0 4px 24px rgba(0,0,0,0.07);">

          <!-- Заголовок формы -->
          <div class="px-8 py-6" style="background:#1E2235;">
            <h1 class="text-xl font-black text-white mb-1">Заявка на отправку детали</h1>
            <p class="text-sm" style="color:#94A3B8;">
              Заполните форму — мы проверим и организуем бесплатную отправку
            </p>
          </div>

          <!-- Telegram подключён (если пришёл через бот) -->
          <div v-if="salebotClientId" class="mx-8 mt-6 flex items-center gap-2 px-4 py-3 rounded-2xl text-sm font-medium"
            style="background:#FFF8D6; color:#92700A;">
            <CheckCircle2 class="w-4 h-4 flex-shrink-0" style="color:#F5C000;" />
            Telegram-бот подключён — статусы придут автоматически
          </div>

          <form @submit.prevent="submit" class="px-8 py-6 space-y-6">

            <!-- Ваши данные -->
            <div>
              <div class="flex items-center gap-2 mb-4">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">01</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Ваши данные</span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>
              <div class="space-y-3">

                <div>
                  <label class="block text-xs font-bold uppercase tracking-wide mb-1.5" style="color:#1E2235;">
                    Имя <span style="color:#EF4444;">*</span>
                  </label>
                  <input v-model="form.customer_name" type="text" placeholder="Иван Иванов"
                    :class="['form-input w-full px-4 py-3 rounded-2xl border text-sm outline-none transition-colors',
                      errors.customer_name ? 'border-red-300 bg-red-50' : '']"
                    :style="errors.customer_name ? '' : 'border-color:#E5E7EB;'" />
                  <p v-if="errors.customer_name" class="text-xs mt-1" style="color:#EF4444;">{{ errors.customer_name }}</p>
                </div>

                <div>
                  <label class="block text-xs font-bold uppercase tracking-wide mb-1.5" style="color:#1E2235;">
                    Телефон <span style="color:#EF4444;">*</span>
                  </label>
                  <input id="phone-input" type="tel" placeholder="+7 (___) ___-__-__"
                    :class="['form-input w-full px-4 py-3 rounded-2xl border text-sm outline-none transition-colors font-mono',
                      errors.customer_phone ? 'border-red-300 bg-red-50' : '']"
                    :style="errors.customer_phone ? '' : 'border-color:#E5E7EB;'" />
                  <p v-if="errors.customer_phone" class="text-xs mt-1" style="color:#EF4444;">{{ errors.customer_phone }}</p>
                </div>

                <div>
                  <label class="block text-xs font-bold uppercase tracking-wide mb-1.5" style="color:#1E2235;">
                    Email <span class="font-normal normal-case" style="color:#9CA3AF;">(необязательно)</span>
                  </label>
                  <input v-model="form.customer_email" type="email" placeholder="mail@example.com"
                    class="form-input w-full px-4 py-3 rounded-2xl border text-sm outline-none transition-colors"
                    style="border-color:#E5E7EB;" />
                </div>

              </div>
            </div>

            <!-- Категория товара -->
            <div v-if="categories.length">
              <div class="flex items-center gap-2 mb-4">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">02</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Категория товара</span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>
              <select v-model="form.product_category"
                class="form-input w-full px-4 py-3 rounded-2xl border text-sm outline-none transition-colors"
                style="border-color:#E5E7EB; color:#1E2235;">
                <option value="">— Выберите категорию —</option>
                <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
              </select>
            </div>

            <!-- Проблема -->
            <div>
              <div class="flex items-center gap-2 mb-4">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">{{ categories.length ? '03' : '02' }}</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Тип проблемы</span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>
              <div class="flex flex-wrap gap-2 mb-1">
                <button v-for="pt in PROBLEM_TYPES" :key="pt" type="button" @click="form.problem_type = pt"
                  class="px-5 py-2.5 text-sm font-bold transition-all"
                  :style="form.problem_type === pt
                    ? 'background:#F5C000; color:#1E2235; border-radius:50px; border:2px solid #F5C000;'
                    : 'background:#fff; color:#6B7280; border-radius:50px; border:2px solid #E5E7EB;'">
                  {{ pt }}
                </button>
              </div>
              <p v-if="errors.problem_type" class="text-xs mt-1" style="color:#EF4444;">{{ errors.problem_type }}</p>
            </div>

            <!-- Что отправить -->
            <div>
              <div class="flex items-center gap-2 mb-4">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">{{ categories.length ? '04' : '03' }}</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Что нужно отправить</span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>
              <textarea v-model="form.items_to_send" rows="3"
                placeholder="Например: болт М4 × 2 шт., гайка М4 × 2 шт."
                :class="['form-input w-full px-4 py-3 rounded-2xl border text-sm resize-none outline-none transition-colors',
                  errors.items_to_send ? 'border-red-300 bg-red-50' : '']"
                :style="errors.items_to_send ? '' : 'border-color:#E5E7EB;'" />
              <div class="flex items-start gap-1.5 mt-2 text-xs" style="color:#9CA3AF;">
                <Info class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>Укажите название детали как в инструкции по сборке (раздел «Комплектация»)</span>
              </div>
              <p v-if="errors.items_to_send" class="text-xs mt-1" style="color:#EF4444;">{{ errors.items_to_send }}</p>
            </div>

            <!-- Фото -->
            <div>
              <div class="flex items-center gap-2 mb-2">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">{{ categories.length ? '05' : '04' }}</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Фото <span style="color:#EF4444;">*</span></span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>
              <p class="text-xs mb-3" style="color:#9CA3AF;">Сфотографируйте содержимое упаковки и инструкцию с комплектацией</p>

              <div v-if="uploadedFiles.length" class="space-y-2 mb-3">
                <div v-for="(f, i) in uploadedFiles" :key="i"
                  class="flex items-center gap-2 px-4 py-2.5 rounded-2xl border text-xs"
                  :style="f.error ? 'border-color:#FECACA; background:#FEF2F2; color:#EF4444;'
                    : f.uploading ? 'border-color:#E5E7EB; background:#F9FAFB; color:#6B7280;'
                    : 'border-color:#FDE68A; background:#FFF8D6; color:#92700A;'">
                  <Loader2 v-if="f.uploading" class="w-3.5 h-3.5 animate-spin flex-shrink-0" />
                  <CheckCircle2 v-else-if="f.url" class="w-3.5 h-3.5 flex-shrink-0" style="color:#F5C000;" />
                  <AlertCircle v-else class="w-3.5 h-3.5 flex-shrink-0" />
                  <span class="flex-1 truncate font-medium">{{ f.name }}</span>
                  <button type="button" @click="removeFile(i)" class="flex-shrink-0 opacity-50 hover:opacity-100">
                    <X class="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              <input ref="fileInputRef" type="file" accept=".jpg,.jpeg,.png,.webp,.heic,.heif" multiple class="hidden"
                @change="onFilesSelected" />
              <button type="button" @click="fileInputRef.click()"
                class="w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl border-2 border-dashed text-sm font-bold transition-colors"
                :style="errors.photos
                  ? 'border-color:#FCA5A5; color:#EF4444; background:#FEF2F2;'
                  : 'border-color:#E5E7EB; color:#9CA3AF; background:#FAFAFA;'"
                @mouseenter="$event.target.style.borderColor='#F5C000'; $event.target.style.color='#92700A';"
                @mouseleave="errors.photos ? '' : ($event.target.style.borderColor='#E5E7EB', $event.target.style.color='#9CA3AF');">
                <Upload class="w-4 h-4" />
                Выбрать фото (JPG, PNG, WEBP)
              </button>
              <p v-if="errors.photos" class="text-xs mt-1" style="color:#EF4444;">{{ errors.photos }}</p>
            </div>

            <!-- Адрес -->
            <div>
              <div class="flex items-center gap-2 mb-4">
                <span class="text-xs font-bold uppercase tracking-widest" style="color:#F5C000;">{{ categories.length ? '06' : '05' }}</span>
                <span class="text-sm font-bold" style="color:#1E2235;">Адрес доставки</span>
                <div class="flex-1 h-px" style="background:#F0F0F0;"></div>
              </div>

              <div class="relative">
                <input v-model="addressQuery" @input="onAddressInput" type="text"
                  placeholder="Начните вводить город, улицу, дом..."
                  class="form-input w-full px-4 py-3 rounded-2xl border text-sm outline-none transition-colors"
                  :style="errors.address ? 'border-color:#FCA5A5; background:#FEF2F2;' : 'border-color:#E5E7EB;'" />

                <div v-if="addressSuggestions.length"
                  class="absolute z-10 w-full mt-1 bg-white rounded-2xl overflow-hidden"
                  style="box-shadow:0 8px 24px rgba(0,0,0,0.12); border:1px solid #E5E7EB;">
                  <button v-for="s in addressSuggestions" :key="s.value" type="button"
                    @click="selectSuggestion(s)"
                    class="w-full text-left px-4 py-3 text-sm border-b last:border-0 transition-colors"
                    style="border-color:#F3F4F6; color:#374151;"
                    @mouseenter="$event.target.style.background='#FFF8D6'"
                    @mouseleave="$event.target.style.background=''">
                    {{ s.value }}
                  </button>
                </div>
              </div>

              <div v-if="form.address_city" class="mt-2 px-4 py-2.5 rounded-2xl flex items-center gap-2 text-xs font-medium"
                style="background:#FFF8D6; color:#92700A;">
                <CheckCircle2 class="w-3.5 h-3.5 flex-shrink-0" style="color:#F5C000;" />
                {{ [form.address_postal, form.address_region, form.address_city, form.address_street, form.address_house].filter(Boolean).join(', ') }}
              </div>
              <p v-if="errors.address" class="text-xs mt-1" style="color:#EF4444;">{{ errors.address }}</p>
            </div>

            <!-- Согласие -->
            <div class="rounded-2xl p-4" style="background:#F9FAFB; border:1px solid #F0F0F0;">
              <div class="flex gap-3 items-start">
                <button type="button" @click="form.personal_data_consent = !form.personal_data_consent"
                  class="flex-shrink-0 mt-0.5 w-5 h-5 rounded-md border-2 flex items-center justify-center transition-all"
                  :style="form.personal_data_consent
                    ? 'background:#F5C000; border-color:#F5C000;'
                    : errors.personal_data_consent
                      ? 'background:#fff; border-color:#EF4444;'
                      : 'background:#fff; border-color:#D1D5DB;'">
                  <svg v-if="form.personal_data_consent" class="w-3 h-3" fill="none" viewBox="0 0 12 12">
                    <path d="M2 6l3 3 5-5" stroke="#1E2235" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </button>
                <span class="text-xs leading-relaxed cursor-pointer select-none" style="color:#6B7280;"
                  @click="form.personal_data_consent = !form.personal_data_consent">
                  Я принимаю условия
                  <a href="https://vidovito.com/uploads/personal_data_policy.pdf" target="_blank" rel="noopener"
                    @click.stop
                    class="underline underline-offset-2 hover:opacity-70 transition-opacity" style="color:#1E2235; font-weight:600;">
                    Политики конфиденциальности
                  </a>
                  и даю
                  <a href="https://vidovito.com/uploads/personal_data_agreement.pdf" target="_blank" rel="noopener"
                    @click.stop
                    class="underline underline-offset-2 hover:opacity-70 transition-opacity" style="color:#1E2235; font-weight:600;">
                    Согласие на обработку персональных данных
                  </a>
                </span>
              </div>
              <p v-if="errors.personal_data_consent" class="text-xs mt-2 ml-8" style="color:#EF4444;">{{ errors.personal_data_consent }}</p>
            </div>

            <input name="honeypot" type="text" style="display:none" tabindex="-1" autocomplete="off" />

            <button type="submit" :disabled="state === 'loading' || !form.personal_data_consent"
              class="w-full flex items-center justify-center gap-2 py-4 font-black text-base transition-all"
              :style="form.personal_data_consent
                ? 'background:#F5C000; color:#1E2235; border-radius:50px; opacity:1; cursor:pointer;'
                : 'background:#E5E7EB; color:#9CA3AF; border-radius:50px; opacity:1; cursor:not-allowed;'">
              <Loader2 v-if="state === 'loading'" class="w-5 h-5 animate-spin" />
              <PackageCheck v-else class="w-5 h-5" />
              {{ state === 'loading' ? 'Отправляем...' : 'Отправить заявку' }}
            </button>

          </form>
        </div>

      </div>
    </main>

    <footer class="py-6 text-center text-xs" style="color:#9CA3AF;">
      © {{ new Date().getFullYear() }} Видовито. Данные передаются по защищённому соединению HTTPS.
    </footer>

  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap');

.form-input {
  font-family: 'Montserrat', sans-serif;
  color: #1E2235;
}
.form-input::placeholder {
  color: #9CA3AF;
}
.form-input:focus {
  outline: none;
  border-color: #F5C000 !important;
  box-shadow: 0 0 0 3px rgba(245, 192, 0, 0.15);
}
</style>
