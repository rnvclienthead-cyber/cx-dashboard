<template>
  <div class="min-h-screen bg-gray-50 flex flex-col">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-full px-5 py-3 flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-violet-50 flex items-center justify-center text-violet-600 shrink-0">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div>
            <h1 class="text-sm font-medium text-gray-900 leading-tight">Чаты</h1>
            <p class="text-xs text-gray-400 leading-tight mt-0.5">Wildberries</p>
          </div>

          <!-- Переключатель: переписка / настройка автоответов -->
          <div class="flex items-center gap-1 ml-3 bg-gray-100 rounded-lg p-0.5">
            <button @click="tab = 'chats'"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'chats' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Переписка
            </button>
            <button @click="openTriage"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'triage' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Разбор
            </button>
            <button @click="openArchive"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'archive' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Архив
            </button>
            <button @click="openPlaybook"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'playbook' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Автоответы
            </button>
          </div>
        </div>

        <!-- Счётчики + кнопки -->
        <div v-if="tab === 'chats'" class="flex items-center gap-2 flex-wrap justify-end">
          <span v-if="pendingChatCount > 0"
            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-orange-50 text-orange-700 border border-orange-200">
            Ждёт ответа: {{ pendingChatCount }}
          </span>
          <span v-if="unreadChatCount > 0"
            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
            <span class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span>
            Новых: {{ unreadChatCount }}
          </span>
          <button @click="markAllRead"
            class="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
            Все прочитано
          </button>
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Обновить
          </button>
        </div>
      </div>
    </div>

    <!-- ══ SPLIT ПАНЕЛЬ ════════════════════════════════════════════════════ -->
    <div v-if="tab === 'chats'" class="flex border-t border-gray-200 overflow-hidden" style="height: calc(100vh - 110px)">

      <!-- ── Левая панель: список чатов ─────────────────────────────────── -->
      <div class="w-80 shrink-0 flex flex-col border-r border-gray-200 bg-white">

        <!-- контролы -->
        <div class="px-3 py-2 border-b border-gray-100 flex items-center gap-1.5 shrink-0 bg-gray-50/50 flex-wrap">
          <button @click="pendingOnly = !pendingOnly; unreadOnly = false"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              pendingOnly ? 'bg-orange-500 text-white border-orange-500' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Ждёт ответа
            <span :class="['px-1 rounded-full font-bold text-xs',
              pendingOnly ? 'bg-white/20 text-white' : 'bg-orange-100 text-orange-700']">{{ pendingChatCount }}</span>
          </button>
          <button @click="unreadOnly = !unreadOnly; pendingOnly = false"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              unreadOnly ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Новые
            <span v-if="unreadChatCount" :class="['px-1 rounded-full font-bold text-xs',
              unreadOnly ? 'bg-white/20 text-white' : 'bg-blue-100 text-blue-700']">{{ unreadChatCount }}</span>
          </button>
        </div>

        <!-- список -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="queueLoading" class="flex justify-center py-10">
            <div class="w-6 h-6 border-4 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
          </div>
          <div v-else-if="filteredItems.length === 0"
            class="flex flex-col items-center justify-center py-12 px-4 text-center">
            <div class="text-3xl mb-2">🎉</div>
            <p class="text-sm text-gray-500 font-medium">{{ unreadOnly ? 'Нет непрочитанных' : 'Чатов нет' }}</p>
          </div>
          <div v-else>
            <div v-for="item in pagedItems" :key="item.id"
              @click="selectChat(item)"
              :class="['flex gap-2.5 px-3 py-3 cursor-pointer border-b border-gray-100 transition-colors',
                chatItem?.id === item.id
                  ? 'bg-violet-50 border-l-[3px] border-l-violet-500'
                  : item.needs_answer
                    ? 'hover:bg-orange-50/40 border-l-[3px] border-l-orange-400'
                    : 'hover:bg-gray-50 border-l-[3px] border-l-transparent opacity-60']">

              <div class="shrink-0 w-[60px] h-[84px] rounded-lg overflow-hidden bg-gray-100 border border-gray-200 flex items-center justify-center">
                <img v-if="item.product_photo || item.nm_id"
                  :src="item.product_photo || wbPhotoUrl(item.nm_id)"
                  class="w-full h-full object-cover"
                  @error="e => e.target.style.display='none'" />
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>

              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-1 mb-0.5">
                  <span :class="['text-xs font-semibold truncate', item.needs_answer ? 'text-gray-900' : 'text-gray-500']">
                    {{ item.contact || 'Покупатель' }}
                  </span>
                  <div class="flex items-center gap-1 shrink-0">
                    <span v-if="hasNewMsg(item)" class="w-2 h-2 rounded-full bg-blue-500 animate-pulse block"></span>
                    <span v-if="item.needs_answer" class="text-xs text-orange-600 font-semibold">Ждёт</span>
                    <span class="text-xs text-gray-400">{{ fmtAge(item.age_sec) }}</span>
                  </div>
                </div>
                <p class="text-xs text-gray-400 truncate leading-tight">{{ item.sku || item.nm_id || '—' }}</p>
                <p :class="['text-xs truncate mt-0.5 leading-tight', item.needs_answer ? 'text-gray-700' : 'text-gray-400']">
                  {{ item.body || '—' }}
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- пагинация левой панели -->
        <div v-if="totalPages > 1" class="border-t border-gray-100 px-3 py-2 flex items-center justify-between shrink-0 bg-white">
          <button @click="currentPage = Math.max(1, currentPage - 1)" :disabled="currentPage === 1"
            class="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-40 px-1.5 py-1">← Пред.</button>
          <div class="flex items-center gap-1">
            <input type="number" :min="1" :max="totalPages" :value="currentPage"
              @change="e => currentPage = Math.min(totalPages, Math.max(1, parseInt(e.target.value)||1))"
              @keydown.enter="e => e.target.blur()"
              class="w-10 text-center border border-gray-200 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
            <span class="text-xs text-gray-400">/ {{ totalPages }}</span>
          </div>
          <button @click="currentPage = Math.min(totalPages, currentPage + 1)" :disabled="currentPage === totalPages"
            class="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-40 px-1.5 py-1">След. →</button>
        </div>
      </div>

      <!-- ── Правая панель: переписка ──────────────────────────────────── -->
      <div class="flex-1 flex flex-col overflow-hidden">

        <!-- Placeholder -->
        <div v-if="!chatItem" class="flex-1 flex items-center justify-center bg-gray-50">
          <div class="text-center text-gray-400">
            <svg class="w-14 h-14 mx-auto mb-3 opacity-20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <p class="text-sm">Выберите чат из списка</p>
          </div>
        </div>

        <template v-else>
          <!-- Шапка правой панели -->
          <div class="bg-white border-b border-gray-200 px-5 py-3 shrink-0">
            <div class="flex items-center gap-3">
              <div class="w-[72px] h-24 rounded-xl overflow-hidden bg-gray-100 border border-gray-200 shrink-0 flex items-center justify-center">
                <img v-if="chatItem.product_photo || chatItem.nm_id"
                  :src="chatItem.product_photo || wbPhotoUrl(chatItem.nm_id)"
                  class="w-full h-full object-cover"
                  @error="e => e.target.style.display='none'" />
                <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <a v-if="chatItem.nm_id"
                    :href="`https://www.wildberries.ru/catalog/${chatItem.nm_id}/detail.aspx`" target="_blank"
                    class="text-sm font-semibold text-blue-600 hover:text-blue-700 truncate max-w-xs">
                    {{ chatItem.sku || `Арт. ${chatItem.nm_id}` }}
                  </a>
                  <span v-else class="text-sm font-semibold text-gray-700">Товар без артикула</span>
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(chatItem.age_sec)]">
                    чат {{ fmtAge(chatItem.age_sec) }}
                  </span>
                </div>
                <p class="text-xs text-gray-400 mt-0.5">{{ chatItem.contact || 'Покупатель' }} · нм {{ chatItem.nm_id || '—' }}</p>
              </div>
              <button @click="dismissChatItem" title="В архив без ответа"
                class="text-gray-300 hover:text-red-400 transition-colors shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>

            <!-- Контекст клиента -->
            <div v-if="chatContextLoading" class="mt-2 text-xs text-gray-400">Загружаем…</div>
            <template v-else-if="chatContext">
              <div v-if="chatContext.specific?.claim || chatContext.specific?.review"
                class="mt-2 flex flex-wrap gap-1.5">

                <button v-if="chatContext.specific.claim"
                  @click="expandedClaimId = expandedClaimId === chatContext.specific.claim.claim_id ? '' : chatContext.specific.claim.claim_id"
                  :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors',
                    expandedClaimId === chatContext.specific.claim.claim_id
                      ? 'bg-orange-100 border-orange-400 text-orange-800'
                      : 'bg-orange-50 border-orange-300 text-orange-700 hover:bg-orange-100']">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.93"/></svg>
                  Возврат от {{ chatContext.specific.claim.claim_date }}
                </button>

                <button v-if="chatContext.specific.review" @click="openReviewCard(chatContext.specific.review.id)"
                  class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-amber-300 bg-amber-50 text-amber-800 text-xs font-medium hover:bg-amber-100 transition-colors">
                  <svg width="12" height="12" viewBox="0 0 24 24" :fill="(chatContext.specific.review.rating||0)>=4?'#f59e0b':'#ef4444'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                  Отзыв от {{ chatContext.specific.review.created_date }}
                  <span v-if="chatContext.specific.review.state==='active'" class="text-rose-600">· ждёт ответа</span>
                </button>
              </div>

              <!-- Детали возврата -->
              <div v-if="expandedClaimId && expandedClaimId === chatContext.specific?.claim?.claim_id"
                class="mt-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2.5">
                <div class="flex items-center justify-between mb-1.5">
                  <div class="flex items-center gap-2 text-xs font-semibold text-orange-800">
                    <span>{{ chatContext.specific.claim.claim_type_label || 'Возврат' }}</span>
                    <span class="text-orange-300">·</span>
                    <span :class="chatContext.specific.claim.status_label==='Одобрено' ? 'text-emerald-600' : 'text-orange-700'">
                      {{ chatContext.specific.claim.status_label }}
                    </span>
                  </div>
                  <button @click="expandedClaimId=''" class="text-gray-300 hover:text-gray-500 text-lg leading-none">×</button>
                </div>
                <p v-if="chatContext.specific.claim.body && chatContext.specific.claim.body !== '—'"
                  class="text-xs text-slate-700 leading-relaxed">{{ chatContext.specific.claim.body }}</p>
                <p v-else class="text-xs text-gray-400 italic">Комментарий не указан</p>
              </div>
            </template>
          </div>

          <!-- Лента сообщений -->
          <div ref="chatScroll" class="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-gray-50">
            <div v-if="chatLoading" class="flex justify-center py-8">
              <div class="w-7 h-7 border-4 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
            <div v-else-if="!chatMsgs.length" class="text-center text-gray-400 text-sm py-8">Сообщений нет</div>
            <template v-else>
              <template v-for="(msg, idx) in chatMsgs" :key="msg.id || idx">
                <!-- Разделитель дня -->
                <div v-if="isNewDay(idx)" class="flex justify-center my-2">
                  <span class="text-xs font-medium text-gray-500 bg-gray-200/70 rounded-full px-3 py-1">
                    {{ fmtDayLabel(msg.createdAt) }}
                  </span>
                </div>
                <div :class="['flex', isSeller(msg) ? 'justify-end' : 'justify-start']">
                  <div :class="['max-w-[75%] rounded-2xl px-3.5 py-2',
                  isSeller(msg) ? 'bg-violet-600 text-white rounded-br-md' : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md']">
                  <div v-if="msgImages(msg).length" class="mb-1.5 space-y-1.5">
                    <a v-for="(im, i) in msgImages(msg)" :key="i" :href="im" target="_blank" class="block">
                      <img :src="im" class="rounded-lg max-w-[220px] max-h-[260px] object-cover" />
                    </a>
                  </div>
                  <p v-if="msg.text" class="text-sm whitespace-pre-wrap break-words">{{ msg.text }}</p>
                    <p :class="['text-xs mt-0.5', isSeller(msg) ? 'text-white/60' : 'text-gray-400']">
                      {{ fmtTs(msg.createdAt) }}
                    </p>
                  </div>
                </div>
              </template>
            </template>
          </div>

          <!-- Поле ввода -->
          <div class="border-t border-gray-200 bg-white px-4 py-3 shrink-0">
            <div class="flex items-end gap-2">
              <textarea v-model="chatReply"
                @keydown.enter.exact.prevent="sendChatMessage"
                placeholder="Сообщение…" rows="1"
                class="flex-1 border border-gray-200 rounded-xl px-3.5 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-violet-400 max-h-32"></textarea>
              <button @click="sendChatMessage" :disabled="!chatReply.trim() || chatSending"
                :class="['w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all',
                  chatReply.trim() && !chatSending ? 'bg-violet-600 hover:bg-violet-700 text-white' : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- ══ РАЗБОР: корзины, фильтр, безопасное закрытие ═══════════════════ -->
    <div v-else-if="tab === 'triage'" class="flex-1 overflow-y-auto bg-gray-50" style="height: calc(100vh - 110px)">
      <div class="max-w-5xl mx-auto px-5 py-6 space-y-5">

        <div class="bg-white rounded-2xl border border-gray-200 p-5">
          <h2 class="text-sm font-semibold text-gray-900 mb-1">Что лежит в очереди</h2>
          <p class="text-xs text-gray-500 leading-relaxed mb-3">
            Чаты, где последним писал покупатель. Разложены по смыслу последнего сообщения —
            чтобы видеть, где реальная проблема, а где просто «спасибо».
          </p>
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xs text-gray-500">Молчат дольше</span>
            <select v-model.number="trStaleDays" @change="loadTriage"
              class="text-xs border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-violet-300">
              <option :value="0">любой срок</option>
              <option :value="3">3 дней</option>
              <option :value="5">5 дней</option>
              <option :value="14">14 дней</option>
            </select>
            <button @click="loadTriage" class="text-xs text-violet-600 hover:text-violet-800">Обновить</button>
          </div>

          <div v-if="trLoading" class="flex justify-center py-8">
            <div class="w-6 h-6 border-4 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
          </div>
          <div v-else class="grid sm:grid-cols-3 gap-2">
            <button v-for="c in trSummary" :key="c.category"
              @click="selectCategory(c.category)"
              :class="['text-left rounded-xl border px-3 py-2.5 transition-colors',
                trCategory === c.category ? 'border-violet-400 bg-violet-50' : 'border-gray-200 hover:border-gray-300 bg-white']">
              <div class="flex items-center justify-between gap-2">
                <span class="text-xs font-semibold" :class="catColor(c.category)">{{ c.label }}</span>
                <span class="text-sm font-bold tabular-nums text-gray-800">{{ c.count }}</span>
              </div>
              <span v-if="c.safe_to_close" class="text-[10px] text-emerald-600">можно закрыть</span>
            </button>
          </div>
        </div>

        <!-- Безопасное закрытие -->
        <div v-if="!trLoading && trSafeTotal > 0" class="bg-emerald-50 border border-emerald-200 rounded-2xl p-4 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <p class="text-sm font-semibold text-emerald-900">Можно закрыть без разбора: {{ trSafeTotal }}</p>
            <p class="text-xs text-emerald-700 mt-0.5">Благодарности и чаты без содержимого. Уедут в архив, откуда их можно вернуть.</p>
          </div>
          <button @click="closeSafe" :disabled="trClosing"
            class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-xs font-semibold rounded-xl transition-colors shrink-0">
            {{ trClosing ? 'Закрываем…' : 'Закрыть и убрать в архив' }}
          </button>
        </div>

        <!-- Список выбранной корзины -->
        <div v-if="trCategory" class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
          <div class="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-gray-900">
              {{ trSummary.find(c => c.category === trCategory)?.label }}
              <span class="text-gray-400 font-normal">— {{ trChats.length }}</span>
            </h3>
            <button @click="trCategory = ''; trChats = []" class="text-xs text-gray-400 hover:text-gray-600">Скрыть</button>
          </div>
          <div class="divide-y divide-gray-50 max-h-[520px] overflow-y-auto">
            <div v-for="ch in trChats" :key="ch.chat_id" class="px-5 py-3 hover:bg-gray-50">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0 flex-1">
                  <p class="text-xs font-semibold text-gray-800">{{ ch.client_name || 'Покупатель' }}</p>
                  <p class="text-[11px] text-gray-400 truncate">{{ ch.good_name || '—' }}</p>
                  <p class="text-xs text-gray-600 mt-1 leading-relaxed">{{ ch.last_message || '—' }}</p>
                </div>
                <div class="flex flex-col items-end gap-1.5 shrink-0">
                  <span class="text-[11px] text-gray-400 tabular-nums">{{ ch.days_silent }} дн</span>
                  <div class="flex items-center gap-1.5">
                    <button @click="openChatFromTriage(ch)"
                      class="text-[11px] px-2 py-1 rounded-lg border border-gray-200 text-gray-600 hover:border-violet-300 hover:text-violet-700 transition-colors">
                      Открыть
                    </button>
                    <button @click="closeOneChat(ch)" :disabled="ch._closing"
                      class="text-[11px] px-2 py-1 rounded-lg border border-gray-200 text-gray-600 hover:border-rose-300 hover:text-rose-700 disabled:opacity-50 transition-colors">
                      {{ ch._closing ? '…' : 'Закрыть' }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="!trChats.length" class="px-5 py-8 text-center text-sm text-gray-400">Пусто</div>
          </div>
        </div>

      </div>
    </div>

    <!-- ══ АРХИВ ══════════════════════════════════════════════════════════ -->
    <div v-else-if="tab === 'archive'" class="flex-1 overflow-y-auto bg-gray-50" style="height: calc(100vh - 110px)">
      <div class="max-w-5xl mx-auto px-5 py-6">
        <div class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
          <div class="px-5 py-3 border-b border-gray-100">
            <h2 class="text-sm font-semibold text-gray-900">Архив чатов <span class="text-gray-400 font-normal">— {{ arTotal }}</span></h2>
            <p class="text-[11px] text-gray-400 mt-0.5">Закрытые обращения. Любое можно вернуть в работу.</p>
          </div>
          <div v-if="arLoading" class="flex justify-center py-12">
            <div class="w-6 h-6 border-4 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
          </div>
          <div v-else-if="!arItems.length" class="px-5 py-12 text-center text-sm text-gray-400">
            Архив пуст — сюда попадают закрытые чаты
          </div>
          <div v-else class="divide-y divide-gray-50">
            <div v-for="a in arItems" :key="a.chat_id" class="px-5 py-3 flex items-start justify-between gap-3 hover:bg-gray-50">
              <div class="min-w-0 flex-1">
                <p class="text-xs font-semibold text-gray-800">{{ a.client_name || 'Покупатель' }}</p>
                <p class="text-[11px] text-gray-400 truncate">{{ a.good_name || '—' }}</p>
                <p class="text-xs text-gray-600 mt-1">{{ a.last_message || '—' }}</p>
                <p class="text-[10px] text-gray-400 mt-1">
                  Закрыл {{ a.dismissed_by || '—' }}{{ a.note ? ' · ' + a.note : '' }}
                </p>
              </div>
              <button @click="restoreChat(a)"
                class="text-[11px] text-violet-600 hover:text-violet-800 font-medium shrink-0">Вернуть</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ АВТООТВЕТЫ: темы, режимы, тексты ═══════════════════════════════ -->
    <div v-else class="flex-1 overflow-y-auto bg-gray-50" style="height: calc(100vh - 110px)">
      <div class="max-w-4xl mx-auto px-5 py-6 space-y-6">

        <div v-if="pbLoading" class="flex justify-center py-16">
          <div class="w-7 h-7 border-4 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
        </div>

        <template v-else>
          <!-- Как это работает -->
          <div class="bg-white rounded-2xl border border-gray-200 p-5">
            <h2 class="text-sm font-semibold text-gray-900 mb-2">Режим по каждой теме</h2>
            <p class="text-xs text-gray-500 leading-relaxed mb-3">
              Выберите, что бот делает с каждой темой. Режим можно менять в любой момент —
              изменения действуют сразу, без перезапуска.
            </p>
            <div class="grid sm:grid-cols-3 gap-2">
              <div class="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2">
                <p class="text-xs font-semibold text-emerald-800">Авто</p>
                <p class="text-[11px] text-emerald-700 leading-snug">Бот отвечает сам, сразу.</p>
              </div>
              <div class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
                <p class="text-xs font-semibold text-amber-800">На согласование</p>
                <p class="text-[11px] text-amber-700 leading-snug">Готовит ответ, отправляет менеджер.</p>
              </div>
              <div class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2">
                <p class="text-xs font-semibold text-rose-800">Менеджер</p>
                <p class="text-[11px] text-rose-700 leading-snug">Бот не отвечает, передаёт человеку.</p>
              </div>
            </div>
          </div>

          <!-- Статус бота + журнал теневых ответов -->
          <div class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
            <div class="px-5 py-3 border-b border-gray-100 flex items-center justify-between gap-3">
              <div>
                <h2 class="text-sm font-semibold text-gray-900">Работа бота</h2>
                <p class="text-[11px] text-gray-400 mt-0.5">
                  {{ botStatus?.enabled ? 'Автозапуск включён' : 'Только по кнопке' }}
                  · {{ botStatus?.shadow ? 'теневой режим (клиенту не пишет)' : 'боевой режим' }}
                  · свежие {{ botStatus?.only_days ?? 2 }} дн.
                  · подходит {{ botStatus?.pending_chats ?? '—' }} чатов
                </p>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <button @click="runBotOnce" :disabled="botRunning"
                  class="text-[11px] px-3 py-1.5 rounded-lg border border-violet-200 text-violet-700 hover:bg-violet-50 disabled:opacity-50">
                  {{ botRunning ? 'Считает…' : 'Прогнать сейчас' }}
                </button>
                <button @click="loadBotLog" class="text-[11px] px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">
                  Обновить журнал
                </button>
              </div>
            </div>
            <div class="px-5 py-3 grid grid-cols-2 sm:grid-cols-4 gap-2 border-b border-gray-50">
              <div class="rounded-xl bg-gray-50 px-3 py-2">
                <p class="text-[10px] text-gray-400">Сегодня</p>
                <p class="text-sm font-semibold text-gray-800">{{ botStatus?.today?.today_total ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-amber-50 px-3 py-2">
                <p class="text-[10px] text-amber-600">В тени</p>
                <p class="text-sm font-semibold text-amber-800">{{ botStatus?.today?.today_shadow ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-emerald-50 px-3 py-2">
                <p class="text-[10px] text-emerald-600">Отправлено</p>
                <p class="text-sm font-semibold text-emerald-800">{{ botStatus?.today?.today_sent ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-rose-50 px-3 py-2">
                <p class="text-[10px] text-rose-600">Менеджеру</p>
                <p class="text-sm font-semibold text-rose-800">{{ botStatus?.today?.today_human ?? 0 }}</p>
              </div>
            </div>
            <div class="divide-y divide-gray-50 max-h-[360px] overflow-y-auto">
              <div v-if="!botLog.length" class="px-5 py-8 text-center text-sm text-gray-400">
                Журнал пуст — нажмите «Прогнать сейчас» или подождите цикл (~1.5 мин)
              </div>
              <div v-for="row in botLog" :key="row.id" class="px-5 py-3">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <p class="text-[11px] text-gray-400">
                      {{ row.theme || '—' }}
                      <span v-if="row.zone==='auto'" class="text-emerald-600">· авто</span>
                      <span v-else-if="row.zone==='draft'" class="text-amber-600">· черновик</span>
                      <span v-else class="text-rose-600">· менеджер</span>
                      <span v-if="row.shadow && !row.sent" class="text-amber-500">· тень</span>
                      <span v-if="row.sent" class="text-emerald-600">· отправлено</span>
                      · {{ Math.round((row.confidence || 0) * 100) }}%
                    </p>
                    <p class="text-xs text-gray-700 mt-0.5"><span class="text-gray-400">Клиент:</span> {{ row.client_message }}</p>
                    <p class="text-xs text-violet-800 mt-1 whitespace-pre-line"><span class="text-gray-400">Бот:</span> {{ row.reply_text }}</p>
                    <p v-if="row.reason" class="text-[10px] text-gray-400 mt-1">{{ row.reason }}</p>
                  </div>
                  <button @click="openChatFromTriage({ chat_id: row.chat_id, client_name: '', good_name: '', last_message: row.client_message })"
                    class="text-[11px] text-violet-600 hover:text-violet-800 shrink-0">Открыть</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Общие настройки -->
          <div class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
            <div class="px-5 py-3 border-b border-gray-100">
              <h2 class="text-sm font-semibold text-gray-900">Общие настройки</h2>
              <p class="text-[11px] text-gray-400 mt-0.5">Подпись, передача менеджеру, закрытие диалога</p>
            </div>
            <div class="divide-y divide-gray-50">
              <div v-for="s in pbSettings" :key="s.key" class="px-5 py-3">
                <label class="text-xs font-medium text-gray-700 block">{{ settingLabel(s.key) }}</label>
                <p class="text-[11px] text-gray-400 mb-1.5">{{ s.descr }}</p>
                <textarea v-model="s.value" rows="2"
                  @blur="saveSetting(s)"
                  class="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:ring-2 focus:ring-violet-300"></textarea>
              </div>
            </div>
          </div>

          <!-- Темы -->
          <div v-for="t in pbThemes" :key="t.theme_key"
            :class="['bg-white rounded-2xl border overflow-hidden',
              t.zone==='auto' ? 'border-l-4 border-l-emerald-400 border-gray-200'
              : t.zone==='draft' ? 'border-l-4 border-l-amber-400 border-gray-200'
              : 'border-l-4 border-l-rose-400 border-gray-200']">

            <div class="px-5 py-4">
              <div class="flex items-start justify-between gap-3 flex-wrap">
                <div class="min-w-0 flex-1">
                  <h3 class="text-sm font-semibold text-gray-900">{{ t.name }}</h3>
                  <p class="text-xs text-gray-500 mt-0.5 leading-relaxed">{{ t.when_text }}</p>
                  <p v-if="t.note" class="text-[11px] text-violet-700 bg-violet-50 rounded-lg px-2.5 py-1.5 mt-2 leading-snug">
                    {{ t.note }}
                  </p>
                </div>
                <div class="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5 shrink-0">
                  <button v-for="z in [['auto','Авто'],['draft','Согласование'],['human','Менеджер']]" :key="z[0]"
                    @click="setZone(t, z[0])"
                    :class="['px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors',
                      t.zone === z[0]
                        ? (z[0]==='auto' ? 'bg-emerald-600 text-white' : z[0]==='draft' ? 'bg-amber-500 text-white' : 'bg-rose-600 text-white')
                        : 'text-gray-500 hover:text-gray-700']">
                    {{ z[1] }}
                  </button>
                </div>
              </div>
            </div>

            <!-- Варианты по веткам -->
            <div class="border-t border-gray-100 bg-gray-50/60 px-5 py-3 space-y-4">
              <div v-for="(vars, bkey) in groupedVariants(t)" :key="bkey">
                <p v-if="bkey" class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                  {{ vars[0].branch_label || bkey }}
                </p>
                <div class="space-y-2">
                  <div v-for="v in vars" :key="v.id"
                    :class="['rounded-xl border bg-white p-2.5', v.active ? 'border-gray-200' : 'border-gray-200 opacity-50']">
                    <textarea v-model="v.body" rows="3"
                      @blur="saveVariant(v)"
                      class="w-full border-0 p-0 text-xs text-gray-700 resize-y focus:outline-none bg-transparent leading-relaxed"></textarea>
                    <div class="flex items-center justify-between gap-2 pt-1.5 mt-1 border-t border-gray-50">
                      <label class="flex items-center gap-1.5 text-[11px] text-gray-500 cursor-pointer">
                        <input type="checkbox" v-model="v.active" @change="saveVariant(v)"
                          class="w-3.5 h-3.5 rounded accent-violet-600" />
                        Используется
                      </label>
                      <button @click="deleteVariant(t, v)"
                        class="text-[11px] text-gray-400 hover:text-rose-600 transition-colors">Удалить</button>
                    </div>
                  </div>
                  <button @click="addVariant(t, bkey, vars[0]?.branch_label || '')"
                    class="text-[11px] text-violet-600 hover:text-violet-800 font-medium transition-colors">
                    + Добавить вариант
                  </button>
                </div>
              </div>
            </div>
          </div>
        </template>

      </div>
    </div>

    <!-- Toast -->
    <transition name="toast">
      <div v-if="toast" :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white max-w-sm',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">
        {{ toast.msg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Centrifuge } from 'centrifuge'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const router = useRouter()
function openReviewCard(reviewId) {
  if (!reviewId) return
  router.push({ path: '/reviews', query: { review: 'wb:' + reviewId } })
}

const BASE = '/api/v1/cx-responses'
const PB   = '/api/v1/chat-playbook'
const platformStore = usePlatformStore()
const currentMp = computed(() => platformStore.platform || 'all')

// ── Вкладки: переписка / настройка автоответов ──────────────────────────────
const tab        = ref('chats')
const pbThemes   = ref([])
const pbSettings = ref([])
const pbLoading  = ref(false)

const SETTING_LABELS = {
  bot_enabled:                  'Автозапуск бота (true / false). false = только по кнопке',
  bot_shadow:                   'Теневой режим — не писать клиенту (true / false)',
  bot_only_days:                'Брать чаты только за последние N дней',
  greeting_text:                'Приветствие (раз в день по Москве)',
  bot_confidence_min:           'Мин. уверенность (0–1), ниже — менеджеру',
  signature:                    'Подпись бота',
  red_zone_client_text:         'Что бот пишет клиенту при передаче менеджеру',
  red_zone_notify_tg:           'Уведомлять менеджера в Telegram (true / false)',
  red_zone_manager_text:        'Текст уведомления менеджеру',
  closing_question_text:        'Уточняющий вопрос перед закрытием диалога',
  closing_question_delay_hours: 'Через сколько часов молчания спросить (часы)',
  closing_auto_close_hours:     'Через сколько часов закрыть без ответа (часы)',
}
const settingLabel = k => SETTING_LABELS[k] || k

const BOT = '/api/v1/chat-bot'
const botStatus  = ref(null)
const botLog     = ref([])
const botRunning = ref(false)

async function loadBotStatus() {
  try {
    const r = await apiFetch(`${BOT}/status`)
    botStatus.value = await r.json()
  } catch { /* тихо */ }
}

async function loadBotLog() {
  try {
    const r = await apiFetch(`${BOT}/log?limit=40`)
    const d = await r.json()
    botLog.value = d.items || []
  } catch { showToast('Не удалось загрузить журнал бота', 'error') }
  await loadBotStatus()
}

async function runBotOnce() {
  botRunning.value = true
  try {
    const r = await apiFetch(`${BOT}/run-once?limit=15`, { method: 'POST' })
    const d = await r.json()
    showToast(d.ok ? `Обработано: ${d.processed}` : (d.reason || 'Бот выключен'), d.ok ? 'ok' : 'error')
    await loadBotLog()
  } catch { showToast('Ошибка прогона бота', 'error') }
  finally { botRunning.value = false }
}

async function loadPlaybook() {
  pbLoading.value = true
  try {
    const r = await apiFetch(PB)
    const d = await r.json()
    pbThemes.value   = d.themes   || []
    pbSettings.value = d.settings || []
    await loadBotLog()
  } catch { showToast('Не удалось загрузить автоответы', 'error') }
  finally { pbLoading.value = false }
}

function openPlaybook() {
  tab.value = 'playbook'
  if (!pbThemes.value.length) loadPlaybook()
}

// ── Разбор очереди по корзинам ──────────────────────────────────────────────
const TR = '/api/v1/chat-triage'
const trSummary   = ref([])
const trChats     = ref([])
const trCategory  = ref('')
const trStaleDays = ref(5)
const trLoading   = ref(false)
const trClosing   = ref(false)
const trSafeTotal = ref(0)

const CAT_COLORS = {
  red: 'text-rose-700', question: 'text-blue-700', problem: 'text-amber-700',
  other: 'text-gray-700', thanks: 'text-emerald-700', empty: 'text-gray-400',
}
const catColor = c => CAT_COLORS[c] || 'text-gray-700'

async function loadTriage() {
  trLoading.value = true
  try {
    const r = await apiFetch(`${TR}/summary?stale_days=${trStaleDays.value}`)
    const d = await r.json()
    trSummary.value   = d.items || []
    trSafeTotal.value = d.safe_to_close_total || 0
    if (trCategory.value) await loadTriageChats()
  } catch { showToast('Не удалось загрузить разбор', 'error') }
  finally { trLoading.value = false }
}

async function loadTriageChats() {
  try {
    const r = await apiFetch(`${TR}/chats?category=${encodeURIComponent(trCategory.value)}&stale_days=${trStaleDays.value}`)
    const d = await r.json()
    trChats.value = d.items || []
  } catch { trChats.value = [] }
}

function selectCategory(cat) {
  if (trCategory.value === cat) { trCategory.value = ''; trChats.value = []; return }
  trCategory.value = cat
  loadTriageChats()
}

function openTriage() {
  tab.value = 'triage'
  if (!trSummary.value.length) loadTriage()
}

async function closeSafe() {
  if (!confirm(`Закрыть ${trSafeTotal.value} чатов и убрать их в архив?\n\nЭто благодарности и чаты без содержимого. Вернуть можно в любой момент.`)) return
  trClosing.value = true
  try {
    const r = await apiFetch(`${TR}/close`, {
      method: 'POST',
      body: JSON.stringify({ categories: ['thanks', 'empty'], stale_days: trStaleDays.value, dry_run: false }),
    })
    const d = await r.json()
    showToast(`Закрыто и убрано в архив: ${d.closed}`, 'ok')
    trCategory.value = ''; trChats.value = []
    await loadTriage()
    await loadQueue()
  } catch { showToast('Не удалось закрыть', 'error') }
  finally { trClosing.value = false }
}

async function closeOneChat(ch) {
  if (!confirm(`Закрыть чат и убрать в архив?\n\n${ch.client_name || 'Покупатель'}: ${(ch.last_message || '—').slice(0, 120)}`)) return
  ch._closing = true
  try {
    await apiFetch(`${TR}/close-chat`, {
      method: 'POST', body: JSON.stringify({ chat_id: ch.chat_id }),
    })
    trChats.value = trChats.value.filter(x => x.chat_id !== ch.chat_id)
    queueItems.value = queueItems.value.filter(i => i.id !== ch.chat_id)
    const cat = trSummary.value.find(c => c.category === ch.category)
    if (cat && cat.count > 0) cat.count--
    showToast('Чат закрыт и убран в архив', 'ok')
  } catch { showToast('Не удалось закрыть', 'error') }
  finally { ch._closing = false }
}

// Посмотреть переписку перед закрытием: переключаемся на вкладку и открываем чат
async function openChatFromTriage(ch) {
  tab.value = 'chats'
  if (!queueItems.value.length) await loadQueue()
  const found = queueItems.value.find(i => i.id === ch.chat_id)
  if (found) { selectChat(found); return }
  // чата нет в очереди (например, старше 30 дней) — открываем по минимальным данным
  selectChat({
    id: ch.chat_id, item_type: 'chat', mp: 'wb',
    contact: ch.client_name, sku: ch.good_name, nm_id: ch.nm_id,
    body: ch.last_message, last_ts: ch.last_ts, needs_answer: ch.needs_answer,
  })
}

// ── Архив ───────────────────────────────────────────────────────────────────
const arItems   = ref([])
const arTotal   = ref(0)
const arLoading = ref(false)

async function loadArchive() {
  arLoading.value = true
  try {
    const r = await apiFetch(`${TR}/archive`)
    const d = await r.json()
    arItems.value = d.items || []
    arTotal.value = d.total || 0
  } catch { showToast('Не удалось загрузить архив', 'error') }
  finally { arLoading.value = false }
}

function openArchive() { tab.value = 'archive'; loadArchive() }

async function restoreChat(a) {
  try {
    await apiFetch(`${TR}/restore`, { method: 'POST', body: JSON.stringify({ chat_id: a.chat_id }) })
    arItems.value = arItems.value.filter(x => x.chat_id !== a.chat_id)
    arTotal.value = Math.max(0, arTotal.value - 1)
    showToast('Чат вернулся в работу', 'ok')
  } catch { showToast('Не удалось вернуть', 'error') }
}

function groupedVariants(theme) {
  const out = {}
  for (const v of (theme.variants || [])) {
    (out[v.branch_key || ''] ||= []).push(v)
  }
  return out
}

async function setZone(theme, zone) {
  if (theme.zone === zone) return
  const prev = theme.zone
  theme.zone = zone
  try {
    await apiFetch(`${PB}/themes/${encodeURIComponent(theme.theme_key)}`, {
      method: 'PUT', body: JSON.stringify({ zone }),
    })
    showToast(`«${theme.name}» — ${zone === 'auto' ? 'отвечает бот' : zone === 'draft' ? 'на согласование' : 'менеджеру'}`, 'ok')
  } catch {
    theme.zone = prev
    showToast('Не удалось сменить режим', 'error')
  }
}

async function saveVariant(v) {
  try {
    await apiFetch(`${PB}/variants/${v.id}`, {
      method: 'PUT', body: JSON.stringify({ body: v.body, active: v.active }),
    })
  } catch { showToast('Не удалось сохранить текст', 'error') }
}

async function addVariant(theme, branchKey, branchLabel) {
  try {
    const r = await apiFetch(`${PB}/variants`, {
      method: 'POST',
      body: JSON.stringify({
        theme_key: theme.theme_key, branch_key: branchKey || '',
        branch_label: branchLabel || '', body: 'Новый вариант ответа — впишите текст',
      }),
    })
    const d = await r.json()
    theme.variants.push({
      id: d.id, theme_key: theme.theme_key, branch_key: branchKey || '',
      branch_label: branchLabel || '', body: 'Новый вариант ответа — впишите текст', active: true,
    })
  } catch { showToast('Не удалось добавить вариант', 'error') }
}

async function deleteVariant(theme, v) {
  if (!confirm('Удалить этот вариант ответа?')) return
  try {
    await apiFetch(`${PB}/variants/${v.id}`, { method: 'DELETE' })
    theme.variants = theme.variants.filter(x => x.id !== v.id)
  } catch { showToast('Не удалось удалить', 'error') }
}

async function saveSetting(s) {
  try {
    await apiFetch(`${PB}/settings/${encodeURIComponent(s.key)}`, {
      method: 'PUT', body: JSON.stringify({ value: s.value }),
    })
  } catch { showToast('Не удалось сохранить настройку', 'error') }
}

// ── «Непрочитанные» чаты ────────────────────────────────────────────────────
const VIEWED_KEY = 'cx_chat_viewed_ts'
const chatLastViewed = ref({})

function _loadViewed() {
  try { chatLastViewed.value = JSON.parse(localStorage.getItem(VIEWED_KEY) || '{}') } catch { chatLastViewed.value = {} }
}
function _saveViewed() {
  try { localStorage.setItem(VIEWED_KEY, JSON.stringify(chatLastViewed.value)) } catch {}
}
function hasNewMsg(item) {
  if (item.item_type !== 'chat') return false
  const lastView = chatLastViewed.value[item.id] || 0
  return (item.last_ts || 0) > lastView
}

function markAllRead() {
  const chats = queueItems.value.filter(i => i.item_type === 'chat')
  if (!chats.length) return
  chats.forEach(i => { chatLastViewed.value[i.id] = (i.last_ts || Date.now()) })
  _saveViewed()
  showToast(`Отмечено прочитанными: ${chats.length} чатов`, 'ok')
}

function wbPhotoUrl(nmId) {
  if (!nmId) return ''
  return `/api/v1/cx-responses/wb-photo/${parseInt(nmId)}`
}

// ── Контекст клиента ─────────────────────────────────────────────────────────
const chatContext        = ref(null)
const chatContextLoading = ref(false)

async function loadChatContext(item) {
  chatContext.value = null
  if (!item?.id) return
  chatContextLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/chat-context/wb/${encodeURIComponent(item.id)}`)
    const d = await r.json()
    if (d.specific?.claim || d.specific?.review || d.reviews?.length || d.questions?.length || d.claims?.length)
      chatContext.value = d
  } catch { /* silent */ } finally {
    chatContextLoading.value = false
  }
}

// ── Фильтры ──────────────────────────────────────────────────────────────────
const unreadOnly      = ref(false)
const pendingOnly     = ref(false)
const expandedClaimId = ref('')

const unreadChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && hasNewMsg(i)).length
)
const pendingChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && i.needs_answer).length
)

// ── Очередь ──────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const perPage      = ref(50)
const currentPage  = ref(1)

const filteredItems = computed(() => {
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'chat')
  if (pendingOnly.value) list = list.filter(i => i.needs_answer)
  else if (unreadOnly.value) list = list.filter(i => hasNewMsg(i))
  return [...list].sort((a, b) => {
    const aPend = a.needs_answer ? 1 : 0
    const bPend = b.needs_answer ? 1 : 0
    if (aPend !== bPend) return bPend - aPend
    return (b.last_ts || 0) - (a.last_ts || 0)
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))
const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

async function loadQueue() {
  queueLoading.value = true
  currentPage.value  = 1
  try {
    const res = await apiFetch(`${BASE}/queue?mp=${currentMp.value}`)
    const data = await res.json()
    queueItems.value = (data.items || [])
  } catch {
    showToast('Ошибка загрузки', 'error')
  } finally {
    queueLoading.value = false
  }
}

watch(currentMp, () => loadQueue())

watch([unreadOnly, pendingOnly], () => { currentPage.value = 1 })

// ── Чат: выбор и сообщения ───────────────────────────────────────────────────
const chatItem    = ref(null)
const chatMsgs    = ref([])
const chatLoading = ref(false)
const chatReply   = ref('')
const chatSending = ref(false)
const chatScroll  = ref(null)
let chatPollTimer = null

function isSeller(msg) {
  const t = (msg.senderType || msg.sender || '').toLowerCase()
  return t === 'seller' || t === 'client_seller' || msg.isMine === true
}

function msgImages(msg) {
  return Array.isArray(msg.images) ? msg.images : []
}

function scrollChatBottom() {
  requestAnimationFrame(() => {
    if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
  })
}

async function fetchChatMsgs(scroll = false) {
  if (!chatItem.value) return
  try {
    const r = await apiFetch(`${BASE}/chat-messages/wb/${chatItem.value.id}`)
    const d = await r.json()
    const msgs = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    if (msgs.length !== chatMsgs.value.length) {
      chatMsgs.value = msgs
      if (scroll) scrollChatBottom()
    }
  } catch { /* тихо */ }
}

async function selectChat(item) {
  chatLastViewed.value[item.id] = item.last_ts || Date.now()
  _saveViewed()
  expandedClaimId.value = ''
  loadChatContext(item)
  chatItem.value    = item
  chatMsgs.value    = []
  chatLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/chat-messages/wb/${encodeURIComponent(item.id)}`)
    const d = await r.json()
    chatMsgs.value = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    scrollChatBottom()
  } catch { chatMsgs.value = [] } finally { chatLoading.value = false }
  clearInterval(chatPollTimer)
  chatPollTimer = setInterval(() => fetchChatMsgs(true), 8000)
}

async function dismissChatItem() {
  if (!chatItem.value) return
  if (!confirm('Закрыть без ответа и перенести в архив?')) return
  const item = chatItem.value
  try {
    await apiFetch(`${BASE}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ mp: item.mp, item_type: item.item_type, item_id: String(item.id), note: '' }),
    })
    queueItems.value = queueItems.value.filter(i => i !== item)
    chatItem.value   = null
    chatContext.value = null
    showToast('Перенесено в архив', 'ok')
  } catch { showToast('Ошибка', 'error') }
}

async function sendChatMessage() {
  const txt = chatReply.value.trim()
  if (!txt || chatSending.value || !chatItem.value) return
  chatSending.value = true
  try {
    await apiFetch(`${BASE}/wb/chats/${encodeURIComponent(chatItem.value.id)}/message`, {
      method: 'POST',
      body: JSON.stringify({ text: txt }),
    })
    chatMsgs.value.push({ text: txt, senderType: 'seller', createdAt: Date.now() })
    chatReply.value = ''
    scrollChatBottom()
    queueItems.value = queueItems.value.filter(i => i !== chatItem.value)
    showToast('Сообщение отправлено', 'ok')
  } catch (e) {
    showToast('Ошибка отправки: ' + (e?.message || ''), 'error')
  } finally {
    chatSending.value = false
  }
}

// ── Форматирование ─────────────────────────────────────────────────────────
function fmtAge(sec) {
  if (sec == null) return '?'
  if (sec < 3600)  return `${Math.round(sec / 60)} мин`
  if (sec < 86400) return `${Math.round(sec / 3600)} ч`
  return `${Math.round(sec / 86400)} дн`
}

function ageCls(sec) {
  if (sec == null)       return 'bg-gray-100 text-gray-500'
  if (sec > 86400 * 2)   return 'bg-red-100 text-red-700'
  if (sec > 86400)       return 'bg-orange-100 text-orange-700'
  if (sec > 3600 * 4)    return 'bg-yellow-100 text-yellow-700'
  return 'bg-green-100 text-green-700'
}

function fmtTs(s) {
  if (!s) return ''
  return new Date(s).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

function fmtDayLabel(s) {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date()
  const yest = new Date(); yest.setDate(today.getDate() - 1)
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, today)) return 'Сегодня'
  if (sameDay(d, yest))  return 'Вчера'
  const opts = { day: 'numeric', month: 'long' }
  if (d.getFullYear() !== today.getFullYear()) opts.year = 'numeric'
  return d.toLocaleDateString('ru', opts)
}

function isNewDay(idx) {
  if (idx === 0) return true
  const cur  = chatMsgs.value[idx]?.createdAt
  const prev = chatMsgs.value[idx - 1]?.createdAt
  if (!cur || !prev) return false
  const a = new Date(cur), b = new Date(prev)
  return a.getFullYear() !== b.getFullYear() || a.getMonth() !== b.getMonth() || a.getDate() !== b.getDate()
}

// ── Toast ──────────────────────────────────────────────────────────────────
const toast = ref(null)
function showToast(msg, type = 'ok') {
  toast.value = { msg, type }
  setTimeout(() => { toast.value = null }, 3000)
}

// ── Centrifugo real-time ──────────────────────────────────────────────────
let centrifuge = null

function _applyRealtimeChat(chatData) {
  const idx = queueItems.value.findIndex(i => i.id === chatData.chat_id)
  if (idx !== -1) {
    // Обновляем существующий чат точечно
    const item = queueItems.value[idx]
    queueItems.value[idx] = {
      ...item,
      body:         chatData.last_message ?? item.body,
      last_ts:      chatData.last_ts      ?? item.last_ts,
      needs_answer: chatData.needs_answer ?? item.needs_answer,
      age_sec:      Math.floor((Date.now() - (chatData.last_ts || Date.now())) / 1000),
    }
  }
  // Если открыт именно этот чат — подгружаем новые сообщения
  if (chatItem.value?.id === chatData.chat_id) {
    fetchChatMsgs(true)
  }
}

async function initCentrifugo() {
  try {
    const res  = await apiFetch('/api/auth/centrifugo-token')
    const data = await res.json()
    const token = data.token
    if (!token) return

    centrifuge = new Centrifuge('wss://cxvo.ru/connection/websocket', { token })

    const sub = centrifuge.newSubscription('chats')
    sub.on('publication', ctx => {
      const { type, chat } = ctx.data || {}
      if (type === 'chat_updated' && chat) _applyRealtimeChat(chat)
    })
    sub.subscribe()
    centrifuge.connect()
  } catch (e) {
    console.warn('[Centrifugo] connect failed:', e)
  }
}

// ── Init / Cleanup ─────────────────────────────────────────────────────────
onMounted(async () => {
  _loadViewed()
  await loadQueue()
  initCentrifugo()
})

onUnmounted(() => {
  clearInterval(chatPollTimer)
  centrifuge?.disconnect()
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
</style>
