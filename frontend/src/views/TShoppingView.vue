<template>
  <div class="h-full min-h-0 bg-gray-50 flex flex-col">
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-full px-5 py-3 flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 bg-teal-50 text-teal-700">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div>
            <h1 class="text-sm font-medium text-gray-900 leading-tight">Запросы «Т-Шопинг»</h1>
            <p class="text-xs text-gray-400 leading-tight mt-0.5">
              {{ isPartner ? 'Общие чаты команды. Закрыть заявку можете только вы.' : 'Письма с почты Т-Шопинга. Ответ уходит с help@vidovito.team' }}
            </p>
          </div>
        </div>
        <div class="flex items-center gap-2 flex-wrap justify-end">
          <button v-if="isPartner" type="button" @click="showNew = true"
            class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-700">
            Новая заявка
          </button>
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            Обновить
          </button>
          <button v-if="isPartner" type="button" @click="logout"
            class="text-xs text-gray-400 hover:text-gray-700">
            Выйти
          </button>
        </div>
      </div>
    </div>

    <div class="flex border-t border-gray-200 overflow-hidden flex-1 min-h-0">
      <div class="w-80 shrink-0 flex flex-col border-r border-gray-200 bg-white">
        <div class="px-3 py-2 border-b border-gray-100 shrink-0 bg-white">
          <input v-model="search" type="search" placeholder="Поиск по заказу или тексту"
            class="w-full text-sm border border-gray-200 rounded-xl px-3 py-2 focus:outline-none focus:ring-1 focus:ring-teal-400" />
          <div class="flex items-center justify-between mt-2">
            <p class="text-[11px] text-gray-400">
              {{ tab === 'archive' ? 'Архив' : 'Заявки' }}
              <span v-if="badgeCount > 0 && tab !== 'archive'"> · {{ badgeCount }} новых</span>
            </p>
            <button type="button" @click="tab = tab === 'archive' ? 'open' : 'archive'"
              class="text-[11px] text-gray-400 hover:text-gray-700">
              {{ tab === 'archive' ? 'К заявкам' : 'Архив' }}
            </button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto">
          <div v-if="queueLoading" class="flex justify-center py-10">
            <div class="w-6 h-6 border-4 border-teal-200 border-t-teal-600 rounded-full animate-spin"></div>
          </div>
          <div v-else-if="filteredItems.length === 0"
            class="flex flex-col items-center justify-center py-12 px-4 text-center">
            <p class="text-sm text-gray-500 font-medium">
              {{ tab === 'archive' ? 'Архив пуст' : (search.trim() ? 'Ничего не найдено' : 'Пока нет заявок') }}
            </p>
            <p class="text-xs text-gray-400 mt-1">
              {{ isPartner ? 'Нажмите «Новая заявка»: номер заказа и проблема' : 'Заявки приходят на help@vidovito.team' }}
            </p>
          </div>
          <div v-else>
            <div v-for="item in filteredItems" :key="item.id"
              @click="selectChat(item)"
              :class="['flex gap-2.5 px-3 py-3 cursor-pointer border-b border-gray-100 transition-colors border-l-[3px]',
                chatItem?.id === item.id
                  ? 'bg-teal-50 border-l-teal-500'
                  : 'hover:bg-gray-50 border-l-transparent']">
              <div class="shrink-0 w-10 h-10 rounded-full bg-teal-100 text-teal-800 flex items-center justify-center text-sm font-semibold">
                {{ initials(item.order_number || item.contact) }}
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-1 mb-0.5">
                  <span :class="['text-xs truncate', itemUnread(item) ? 'font-semibold text-gray-900' : 'font-medium text-gray-700']">
                    {{ item.order_number ? ('Заказ ' + item.order_number) : (item.contact || 'Заявка') }}
                  </span>
                  <span class="text-[11px] text-gray-400 shrink-0">{{ fmtAge(item.last_ts) }}</span>
                </div>
                  <p class="text-[11px] text-gray-500 truncate">{{ item.contact }}</p>
                  <p v-if="ozonLine(item)" class="text-[11px] text-teal-800 truncate">{{ ozonLine(item) }}</p>
                <div class="flex items-center gap-1.5 mt-0.5">
                  <span v-if="itemUnread(item)" class="w-2 h-2 rounded-full bg-teal-500 shrink-0"></span>
                  <span v-if="item.close_requested" class="text-[10px] text-amber-700 font-medium shrink-0">закрыть</span>
                  <span v-else-if="waitingReply(item)" class="text-[10px] text-orange-600 font-semibold shrink-0">ждёт ответа</span>
                  <p :class="['text-xs truncate leading-tight min-w-0', itemUnread(item) ? 'text-gray-800' : 'text-gray-400']">
                    {{ previewText(item.last_message) }}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="flex-1 flex flex-col overflow-hidden">
        <div v-if="!chatItem" class="flex-1 flex items-center justify-center bg-gray-50">
          <div class="text-center text-gray-400">
            <svg class="w-14 h-14 mx-auto mb-3 opacity-20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <p class="text-sm">Выберите заявку из списка</p>
          </div>
        </div>

        <template v-else>
          <div class="bg-white border-b border-gray-200 px-5 py-3 shrink-0">
            <div class="flex items-center gap-3">
              <div class="w-11 h-11 rounded-full bg-teal-100 text-teal-800 flex items-center justify-center text-sm font-semibold shrink-0">
                {{ initials(chatItem.order_number || chatItem.contact) }}
              </div>
              <div class="flex-1 min-w-0">
                <p class="text-sm font-semibold text-gray-800 truncate">
                  {{ chatItem.order_number ? ('Заказ ' + chatItem.order_number) : (chatItem.contact || 'Заявка') }}
                </p>
                <p class="text-xs text-gray-500 mt-0.5 truncate">{{ chatItem.contact }}</p>
                <p v-if="ozonTitle" class="text-xs text-gray-700 mt-0.5 truncate">{{ ozonTitle }}</p>
                <p v-if="chatItem.problem" class="text-xs text-gray-500 mt-0.5 line-clamp-2">{{ chatItem.problem }}</p>
              </div>
              <button v-if="(isPartner || isEmailChat(chatItem)) && tab === 'archive'" @click="reopenChat"
                class="shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg border border-teal-200 bg-teal-50 text-teal-800 hover:bg-teal-100 font-medium transition-colors">
                Вернуть в работу
              </button>
              <button v-else-if="canCloseChat" @click="closeChat"
                class="shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-600 hover:border-rose-300 hover:text-rose-700 hover:bg-rose-50 font-medium transition-colors">
                Закрыть заявку
              </button>
              <button v-else-if="tab !== 'archive' && !chatItem.close_requested" @click="requestClose"
                class="shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100 font-medium transition-colors">
                Попросить закрыть
              </button>
              <span v-else-if="chatItem.close_requested"
                class="shrink-0 text-[11px] px-2.5 py-1.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 font-medium">
                Просьба закрыть отправлена
              </span>
            </div>
          </div>
          <div v-if="ozonCard" class="px-5 pb-3 bg-white border-b border-gray-100">
            <div class="rounded-xl border border-teal-100 bg-teal-50/70 px-3 py-2 text-xs text-gray-700 space-y-1">
              <p class="font-medium text-teal-900">{{ ozonCard.status_label || 'Заказ Ozon' }}<span v-if="ozonCard.schema"> · {{ ozonCard.schema }}</span></p>
              <p v-for="(line, i) in ozonCard.products" :key="i" class="truncate">
                {{ line.name || line.article || 'Товар' }}
                <span v-if="line.quantity > 1">× {{ line.quantity }}</span>
                <span v-if="line.sum != null" class="text-gray-500"> · {{ formatMoney(line.sum) }}</span>
              </p>
              <p v-if="ozonCard.total != null" class="font-medium">Сумма: {{ formatMoney(ozonCard.total) }}</p>
              <p v-if="ozonMeta" class="text-gray-500 truncate">{{ ozonMeta }}</p>
            </div>
          </div>

          <div v-if="chatItem.close_requested && tab !== 'archive'"
            class="px-5 py-2 bg-amber-50 border-b border-amber-100 text-xs text-amber-800">
            {{ isPartner ? 'Видовито просит закрыть эту заявку, если вопрос решён.' : 'Ждём, пока Т-Шопинг закроет заявку.' }}
          </div>

          <div ref="chatScroll" class="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-gray-50">
            <div v-if="chatLoading" class="flex justify-center py-8">
              <div class="w-7 h-7 border-4 border-teal-200 border-t-teal-600 rounded-full animate-spin"></div>
            </div>
            <div v-else-if="!chatMsgs.length" class="text-center text-gray-400 text-sm py-8">Сообщений нет</div>
            <template v-else-if="isEmailThread">
              <article v-for="(msg, idx) in chatMsgs" :key="msg.id || idx"
                class="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
                <header class="flex items-start justify-between gap-3 px-4 py-2.5 border-b border-gray-100 bg-gray-50">
                  <div class="min-w-0">
                    <p class="text-xs font-semibold text-gray-800">
                      {{ isManager(msg) ? 'Видовито' : 'Т-Шопинг' }}
                      <span class="font-normal text-gray-400"> · почта</span>
                    </p>
                    <p class="text-[11px] text-gray-500 truncate">{{ letterFrom(msg) }}</p>
                    <p v-if="letterSubject(msg)" class="text-[11px] text-gray-600 mt-0.5 truncate">{{ letterSubject(msg) }}</p>
                  </div>
                  <time class="text-[11px] text-gray-400 shrink-0">{{ fmtLetterDate(msg.createdAt) }}</time>
                </header>
                <div class="px-4 py-3 space-y-2">
                  <div v-if="msgImages(msg).length" class="flex flex-wrap gap-2">
                    <a v-for="(attachment, i) in msgImages(msg)" :key="i" :href="attachment.url" target="_blank" rel="noopener">
                      <img :src="attachment.url" :alt="attachment.name || 'Фото'" class="rounded-lg max-w-[240px] max-h-[280px] object-cover border border-gray-100" />
                    </a>
                  </div>
                  <div v-if="msgDocuments(msg).length" class="space-y-1.5">
                    <a v-for="(attachment, i) in msgDocuments(msg)" :key="i"
                      :href="attachment.url || undefined" target="_blank" rel="noopener"
                      class="flex min-h-10 items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-2 text-xs font-medium text-gray-700">
                      {{ attachment.pending ? (attachment.name || 'Файл ещё загружается') : (attachment.name || 'Документ') }}
                    </a>
                  </div>
                  <p v-if="msg.text" class="text-sm whitespace-pre-wrap break-words text-gray-800">{{ msg.text }}</p>
                  <p v-if="msg.manager_login" class="text-[11px] text-gray-400">{{ msg.manager_login }}</p>
                </div>
              </article>
            </template>
            <template v-else>
              <template v-for="(msg, idx) in chatMsgs" :key="msg.id || idx">
                <div v-if="isNewDay(idx)" class="flex justify-center my-2">
                  <span class="text-xs font-medium text-gray-500 bg-gray-200/70 rounded-full px-3 py-1">
                    {{ fmtDayLabel(msg.createdAt) }}
                  </span>
                </div>
                <div v-if="isSystem(msg)" class="flex justify-center my-1">
                  <span class="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-full px-3 py-1 max-w-[80%] text-center">
                    {{ msg.text }}
                  </span>
                </div>
                <div v-else :class="['flex', isMine(msg) ? 'justify-end' : 'justify-start']">
                  <div :class="['max-w-[75%] rounded-2xl px-3.5 py-2',
                    isMine(msg) ? 'bg-teal-600 text-white rounded-br-md' : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md']">
                    <p :class="['text-[10px] font-medium mb-0.5', isMine(msg) ? 'text-white/70' : 'text-gray-400']">
                      {{ isManager(msg) ? 'Видовито' : 'Т-Шопинг' }}{{ channelSuffix }}
                    </p>
                    <div v-if="msgImages(msg).length" class="mb-1.5 space-y-1.5">
                      <a v-for="(attachment, i) in msgImages(msg)" :key="i" :href="attachment.url" target="_blank" rel="noopener" class="block">
                        <img :src="attachment.url" :alt="attachment.name || 'Фото'" class="rounded-lg max-w-[220px] max-h-[260px] object-cover" />
                      </a>
                    </div>
                    <div v-if="msgDocuments(msg).length" class="mb-1.5 space-y-1.5">
                      <a v-for="(attachment, i) in msgDocuments(msg)" :key="i"
                        :href="attachment.url || undefined" target="_blank" rel="noopener"
                        class="flex min-h-11 items-center gap-2 rounded-lg border border-current/20 bg-white/10 px-2.5 py-2 text-xs font-medium">
                        {{ attachment.pending ? (attachment.name || 'Файл ещё загружается') : (attachment.name || 'Документ') }}
                      </a>
                    </div>
                    <p v-if="msg.text" class="text-sm whitespace-pre-wrap break-words">{{ msg.text }}</p>
                    <p :class="['text-xs mt-0.5', isMine(msg) ? 'text-white/60' : 'text-gray-400']">
                      {{ fmtTs(msg.createdAt) }}
                      <span v-if="msg.manager_login"> · {{ msg.manager_login }}</span>
                    </p>
                  </div>
                </div>
              </template>
            </template>
          </div>

          <div class="border-t border-gray-200 bg-white px-4 py-3 shrink-0">
            <p v-if="sendError" class="mb-2 text-xs text-rose-600">{{ sendError }}</p>
            <div v-if="!isPartner && suggestedTemplate && !reply" class="mb-2 rounded-xl border border-teal-100 bg-teal-50 px-3 py-2">
              <p class="text-[11px] text-teal-900">Черновик по теме «{{ suggestedTemplate.theme_name }}» — проверьте и отправьте.</p>
              <button type="button" @click="applyTemplate(suggestedTemplate)"
                class="mt-1 text-[11px] font-medium text-teal-800 hover:underline">Подставить шаблон</button>
            </div>
            <div v-if="!isPartner && (returnTemplates.length || chatTemplateGroups.length)" class="mb-2">
              <p class="text-[10px] font-medium text-gray-400 mb-1">Шаблоны</p>
              <div class="flex flex-wrap gap-1.5">
                <button v-for="tpl in returnTemplates" :key="tpl.id" type="button"
                  @click="applyTemplate(tpl)"
                  class="rounded-full border border-teal-200 bg-teal-50 px-2.5 py-1 text-[11px] font-medium text-teal-800 hover:bg-teal-100">
                  {{ tpl.theme_name }}
                </button>
                <button v-if="chatTemplateGroups.length" type="button" @click="chatThemesOpen = !chatThemesOpen"
                  class="rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-600 hover:bg-gray-50">
                  {{ chatThemesOpen ? 'Свернуть темы' : `Темы из чатов · ${chatTemplateGroups.length}` }}
                </button>
              </div>
              <div v-if="chatThemesOpen" class="mt-2 max-h-48 overflow-y-auto rounded-xl border border-gray-200 bg-white p-2 space-y-1">
                <button v-for="group in chatTemplateGroups" :key="group.theme_key" type="button"
                  @click="applyChatGroup(group)"
                  class="w-full rounded-lg border border-gray-100 px-2.5 py-1.5 text-left text-[11px] text-gray-700 hover:bg-gray-50">
                  {{ group.theme_name }}
                </button>
              </div>
            </div>
            <div v-if="attachments.length" class="mb-2 flex flex-wrap gap-2">
              <div v-for="attachment in attachments" :key="attachment.id"
                class="flex min-w-0 max-w-64 items-center gap-2 rounded-xl border border-gray-200 bg-gray-50 p-1.5 pr-1">
                <img v-if="attachment.previewUrl" :src="attachment.previewUrl" alt=""
                  class="h-10 w-10 shrink-0 rounded-lg border border-gray-200 object-cover" />
                <div class="min-w-0 flex-1">
                  <p class="truncate text-xs font-medium text-gray-700">{{ attachment.file.name }}</p>
                </div>
                <button type="button" @click="removeAttachment(attachment.id)"
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-gray-400 hover:text-rose-600">×</button>
              </div>
            </div>
            <div class="flex items-end gap-2">
              <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" multiple class="hidden" @change="onFiles" />
              <button type="button" @click="fileInput?.click()" :disabled="sending || tab === 'archive'"
                title="Прикрепить JPEG, PNG или PDF до 5 МиБ"
                class="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 border border-gray-200 bg-white text-gray-500 hover:bg-gray-50 disabled:opacity-40">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
              </button>
              <textarea v-model="reply"
                :disabled="tab === 'archive'"
                @keydown.enter.exact.prevent="sendMessage"
                :placeholder="isEmailThread ? 'Ответ на письмо… (Enter — отправить)' : 'Сообщение… (Enter — отправить, Shift+Enter — новая строка)'"
                rows="1"
                class="flex-1 border border-gray-200 rounded-xl px-3.5 py-2.5 text-sm leading-5 text-gray-900 bg-white resize-none overflow-y-auto focus:outline-none focus:ring-2 focus:ring-teal-400 min-h-[44px] max-h-40 disabled:bg-gray-50"></textarea>
              <button @click="sendMessage" :disabled="!canSend"
                class="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-all"
                :class="canSend ? 'bg-teal-600 text-white hover:bg-teal-700' : 'bg-gray-100 text-gray-400 cursor-not-allowed'">
                <svg v-if="sending" class="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M21 12a9 9 0 1 1-6.22-8.56"/>
                </svg>
                <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                </svg>
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <div v-if="showNew" class="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" @click.self="showNew = false">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <h2 class="text-sm font-semibold text-gray-900">Новая заявка</h2>
        <p class="text-xs text-gray-400 mt-1 mb-4">Номер заказа и в чём проблема. Если такой заказ уже открыт — сообщение попадёт в тот же чат.</p>
        <label class="block text-xs font-medium text-gray-600 mb-1">Номер заказа</label>
        <input v-model="newOrder" type="text"
          class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-teal-400" />
        <label class="block text-xs font-medium text-gray-600 mb-1">Проблема</label>
        <textarea v-model="newProblem" rows="4"
          class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm mb-3 resize-none focus:outline-none focus:ring-2 focus:ring-teal-400"></textarea>
        <p v-if="newError" class="text-xs text-rose-600 mb-3">{{ newError }}</p>
        <div class="flex justify-end gap-2">
          <button type="button" @click="showNew = false" class="text-xs px-3 py-2 rounded-lg text-gray-500 hover:bg-gray-50">Отмена</button>
          <button type="button" @click="createTicket" :disabled="creating"
            class="text-xs px-3 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50">
            {{ creating ? 'Создаём…' : 'Отправить' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Centrifuge } from 'centrifuge'
import { apiFetch } from '../api'
import { centrifugoUrl } from '../realtime'
import { usePermissionsStore } from '../stores/permissions'
import { groupTemplatesByTheme } from '../utils/templateGroups'
import { validateChatAttachment, validateChatAttachmentAddition, chatAttachmentKey } from '../utils/chatAttachments'
import { clearAuthSession } from '../authStorage'

const BASE = '/api/v1/tshopping'
const route = useRoute()
const router = useRouter()
const permissionsStore = usePermissionsStore()
const isPartner = computed(() => permissionsStore.isTshoppingPartner)

const tab = ref('open')
const search = ref('')
const queueLoading = ref(false)
const items = ref([])
const counts = ref({ pending: 0, unread: 0, unread_partner: 0, waiting_partner: 0, close_requested: 0, open: 0, closed: 0 })
const chatItem = ref(null)
const chatMsgs = ref([])
const chatLoading = ref(false)
const reply = ref('')
const sending = ref(false)
const sendError = ref('')
const attachments = ref([])
const fileInput = ref(null)
const chatScroll = ref(null)
const showNew = ref(false)
const newOrder = ref('')
const newProblem = ref('')
const newError = ref('')
const creating = ref(false)
const returnTemplates = ref([])
const chatTemplateGroups = ref([])
const chatThemesOpen = ref(false)
const suggestedKey = ref('')
let pollTimer = null
let centrifuge = null

const badgeCount = computed(() =>
  isPartner.value ? (counts.value.unread_partner || 0) : (counts.value.pending || 0)
)

const filteredItems = computed(() => {
  const list = [...items.value]
  list.sort((a, b) => {
    const au = itemUnread(a) ? 1 : 0
    const bu = itemUnread(b) ? 1 : 0
    if (au !== bu) return bu - au
    return new Date(b.last_ts || 0) - new Date(a.last_ts || 0)
  })
  return list
})

const canSend = computed(() =>
  tab.value !== 'archive' && !sending.value && (Boolean(reply.value.trim()) || attachments.value.length > 0)
)

const canCloseChat = computed(() => {
  if (!chatItem.value || tab.value === 'archive') return false
  return isPartner.value || isEmailChat(chatItem.value)
})

const ozonCard = computed(() => chatItem.value?.ozon || null)
const ozonTitle = computed(() => {
  const card = ozonCard.value
  if (!card?.found) return ''
  const first = (card.products || []).find((p) => p.name) || {}
  const bits = [first.name, card.total != null ? formatMoney(card.total) : ''].filter(Boolean)
  return bits.join(' · ')
})
const ozonMeta = computed(() => {
  const card = ozonCard.value
  if (!card) return ''
  return [card.city, card.warehouse, card.tracking_number].filter(Boolean).join(' · ')
})

function isEmailChat(item) {
  return (item?.source || '') === 'email' || String(item?.id || item?.chat_id || '').startsWith('mail:')
}

const isEmailThread = computed(() => isEmailChat(chatItem.value))
const channelSuffix = computed(() => {
  const src = chatItem.value?.source || ''
  if (src === 'telegram') return ' · Telegram'
  if (src === 'web') return ' · сайт'
  if (src === 'email') return ' · почта'
  return ''
})
const suggestedTemplate = computed(() =>
  returnTemplates.value.find((t) => t.id === suggestedKey.value) || null
)

function ozonLine(item) {
  const card = item?.ozon
  if (!card?.found) return item?.product || ''
  const first = (card.products || []).find((p) => p.name) || {}
  const bits = [first.name || item.product, card.total != null ? formatMoney(card.total) : '', card.status_label].filter(Boolean)
  return bits.join(' · ')
}

function formatMoney(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(n)
}

function itemUnread(item) {
  return isPartner.value ? item.unread_partner : item.unread
}

function waitingReply(item) {
  const sender = item?.last_sender || ''
  if (!sender || sender === 'system') return sender === 'system' && isPartner.value
  if (isPartner.value) return sender !== 'client'
  return sender !== 'manager'
}

function logout() {
  clearAuthSession()
  window.location.href = '/login'
}

function previewText(text) {
  const raw = String(text || '').replace(/^Заявка\s*/i, '').replace(/\s+/g, ' ').trim()
  return raw || '—'
}

function initials(name) {
  const s = String(name || 'С').trim()
  return (s[0] || 'С').toUpperCase()
}

function isManager(msg) {
  return msg?.sender === 'manager'
}

function isSystem(msg) {
  return msg?.sender === 'system'
}

function isMine(msg) {
  if (isPartner.value) return msg?.sender === 'client'
  return msg?.sender === 'manager'
}

function msgImages(msg) {
  return (msg?.attachments || []).filter((a) => a.kind === 'image' && a.url)
}

function msgDocuments(msg) {
  return (msg?.attachments || []).filter((a) => a.kind !== 'image' || !a.url)
}

function fmtTs(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

function fmtDayLabel(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

function isNewDay(idx) {
  if (idx === 0) return true
  const a = new Date(chatMsgs.value[idx - 1]?.createdAt || 0).toDateString()
  const b = new Date(chatMsgs.value[idx]?.createdAt || 0).toDateString()
  return a !== b
}

function fmtAge(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const min = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000))
  if (min < 60) return `${min} мин`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} ч`
  return `${Math.floor(h / 24)} дн`
}

function letterFrom(msg) {
  if (isManager(msg)) return 'help@vidovito.team'
  return chatItem.value?.email_from || 'lifestyle_service@tbank.ru'
}

function letterSubject(msg) {
  const subject = String(chatItem.value?.email_subject || '').trim()
  if (!subject) return ''
  if (isManager(msg) && !/^re:/i.test(subject)) return 'Re: ' + subject
  return subject
}

function fmtLetterDate(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function isNearBottom(el) {
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 96
}

function keepScroll(el, prevHeight, prevTop) {
  if (!el) return
  el.scrollTop = el.scrollHeight - prevHeight + prevTop
}

function applyTemplate(tpl) {
  const body = String(tpl?.body || tpl?.preview_body || '').trim()
  if (!body) return
  reply.value = body
  chatThemesOpen.value = false
}

function applyChatGroup(group) {
  const first = (group?.templates || [])[0]
  if (first) applyTemplate(first)
}

async function loadTemplates(chatId) {
  if (isPartner.value) {
    returnTemplates.value = []
    chatTemplateGroups.value = []
    return
  }
  try {
    const res = await apiFetch(`${BASE}/templates?chat_id=${encodeURIComponent(chatId || '')}`)
    const data = await res.json()
    returnTemplates.value = data.templates || []
  } catch {
    returnTemplates.value = []
  }
  try {
    const res = await apiFetch(`/api/v1/chat-bot/chat-templates?mp=ozon&chat_id=${encodeURIComponent(chatId || '')}`)
    if (!res.ok) {
      chatTemplateGroups.value = []
      return
    }
    const data = await res.json()
    chatTemplateGroups.value = groupTemplatesByTheme(data.templates || [])
  } catch {
    chatTemplateGroups.value = []
  }
}

async function loadQueue(silent = false) {
  if (!silent) queueLoading.value = true
  try {
    const closed = tab.value === 'archive'
    const q = search.value.trim()
    const res = await apiFetch(`${BASE}/chats?closed=${closed}&q=${encodeURIComponent(q)}`)
    const data = await res.json()
    items.value = data.items || []
    counts.value = { ...counts.value, ...(data.counts || {}) }
    if (!silent) maybeOpenFirst()
  } catch {
    /* silent */
  } finally {
    queueLoading.value = false
  }
}

async function maybeOpenFirst() {
  if (chatItem.value || route.query.chat) return
  const first = filteredItems.value[0]
  if (first) await selectChat(first)
}

async function selectChat(item) {
  chatItem.value = item
  sendError.value = ''
  chatThemesOpen.value = false
  router.replace({ path: '/tshopping', query: { chat: item.id } })
  await Promise.all([loadMessages(item.id), loadTemplates(item.id)])
}

async function loadMessages(chatId, silent = false) {
  const el = chatScroll.value
  const stick = !silent || isNearBottom(el)
  const prevHeight = el?.scrollHeight || 0
  const prevTop = el?.scrollTop || 0
  if (!silent) chatLoading.value = true
  try {
    const res = await apiFetch(`${BASE}/chats/${encodeURIComponent(chatId)}`)
    const data = await res.json()
    chatItem.value = data.chat || chatItem.value
    if (data.ozon && chatItem.value) chatItem.value.ozon = data.ozon
    chatMsgs.value = data.messages || []
    suggestedKey.value = data.suggested_template || ''
    await nextTick()
    if (chatScroll.value) {
      if (stick) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
      else keepScroll(chatScroll.value, prevHeight, prevTop)
    }
    const row = items.value.find((i) => i.id === chatId)
    if (row) {
      if (isPartner.value) row.unread_partner = false
      else row.unread = false
    }
  } catch {
    /* silent */
  } finally {
    chatLoading.value = false
  }
}

async function closeChat() {
  if (!chatItem.value) return
  await apiFetch(`${BASE}/chats/${encodeURIComponent(chatItem.value.id)}/close`, { method: 'POST' })
  chatItem.value = null
  chatMsgs.value = []
  router.replace({ path: '/tshopping' })
  await loadQueue()
}

async function reopenChat() {
  if (!chatItem.value) return
  await apiFetch(`${BASE}/chats/${encodeURIComponent(chatItem.value.id)}/reopen`, { method: 'POST' })
  tab.value = 'open'
  await loadQueue()
}

async function requestClose() {
  if (!chatItem.value) return
  const res = await apiFetch(`${BASE}/chats/${encodeURIComponent(chatItem.value.id)}/request-close`, { method: 'POST' })
  const data = await res.json()
  if (data.chat) chatItem.value = data.chat
  await loadMessages(chatItem.value.id, true)
  await loadQueue(true)
}

async function createTicket() {
  newError.value = ''
  creating.value = true
  try {
    const res = await apiFetch(`${BASE}/chats`, {
      method: 'POST',
      body: JSON.stringify({ order: newOrder.value, problem: newProblem.value }),
    })
    const data = await res.json()
    if (!res.ok) {
      newError.value = data.detail || 'Не удалось создать заявку'
      return
    }
    showNew.value = false
    newOrder.value = ''
    newProblem.value = ''
    tab.value = 'open'
    await loadQueue()
    if (data.chat) await selectChat(data.chat)
  } catch (e) {
    newError.value = e.message || 'Не удалось создать заявку'
  } finally {
    creating.value = false
  }
}

function onFiles(ev) {
  const files = [...(ev.target.files || [])]
  ev.target.value = ''
  for (const file of files) {
    const err = validateChatAttachment(file) || validateChatAttachmentAddition(attachments.value.map((a) => a.file), file)
    if (err) {
      sendError.value = err
      continue
    }
    const id = chatAttachmentKey(file) + Math.random()
    const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : ''
    attachments.value.push({ id, file, previewUrl })
  }
}

function removeAttachment(id) {
  const found = attachments.value.find((a) => a.id === id)
  if (found?.previewUrl) URL.revokeObjectURL(found.previewUrl)
  attachments.value = attachments.value.filter((a) => a.id !== id)
}

function clearAttachments() {
  attachments.value.forEach((a) => a.previewUrl && URL.revokeObjectURL(a.previewUrl))
  attachments.value = []
}

async function sendMessage() {
  if (!canSend.value || !chatItem.value) return
  sending.value = true
  sendError.value = ''
  try {
    const chatId = encodeURIComponent(chatItem.value.id)
    let res
    if (attachments.value.length) {
      const form = new FormData()
      form.append('text', reply.value)
      attachments.value.forEach((a) => form.append('files', a.file))
      res = await apiFetch(`${BASE}/chats/${chatId}/message-with-files`, { method: 'POST', body: form })
    } else {
      res = await apiFetch(`${BASE}/chats/${chatId}/message`, {
        method: 'POST',
        body: JSON.stringify({ text: reply.value }),
      })
    }
    const data = await res.json()
    if (!res.ok) {
      sendError.value = data.detail || 'Не удалось отправить'
      return
    }
    if (data.message) chatMsgs.value.push(data.message)
    reply.value = ''
    clearAttachments()
    await nextTick()
    if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
    await loadQueue(true)
  } catch (e) {
    sendError.value = e.message || 'Не удалось отправить'
  } finally {
    sending.value = false
  }
}

async function initCentrifugo() {
  try {
    const res = await apiFetch('/api/auth/centrifugo-token')
    const data = await res.json()
    if (!data.token) return
    centrifuge = new Centrifuge(centrifugoUrl(), { token: data.token })
    const sub = centrifuge.newSubscription('tshopping')
    sub.on('publication', () => {
      loadQueue(true)
      if (chatItem.value) loadMessages(chatItem.value.id, true)
    })
    sub.subscribe()
    centrifuge.on('connected', () => {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = setInterval(liveTick, 15000)
      }
    })
    centrifuge.connect()
  } catch {
    /* polling fallback */
  }
}

function liveTick() {
  loadQueue(true)
  if (chatItem.value) loadMessages(chatItem.value.id, true)
}

watch(tab, () => {
  chatItem.value = null
  router.replace({ path: '/tshopping' })
  loadQueue()
})

let searchTimer = null
watch(search, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadQueue(true), 300)
})

onMounted(async () => {
  await loadQueue()
  initCentrifugo()
  pollTimer = setInterval(liveTick, 2500)
  const qChat = route.query.chat
  if (qChat) {
    const found = items.value.find((i) => i.id === String(qChat))
    if (found) await selectChat(found)
    else await loadMessages(String(qChat))
  }
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  centrifuge?.disconnect()
  clearAttachments()
})
</script>
