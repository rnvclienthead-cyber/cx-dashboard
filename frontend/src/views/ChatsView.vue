<template>
  <div class="min-h-screen bg-gray-50 flex flex-col">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-full px-5 py-3 flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div :class="['w-9 h-9 rounded-xl flex items-center justify-center shrink-0', mpUi.iconBg]">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div>
            <h1 class="text-sm font-medium text-gray-900 leading-tight">Чаты</h1>
            <p class="text-xs text-gray-400 leading-tight mt-0.5">{{ chatsMpLabel }}</p>
          </div>

          <!-- Переключатель: переписка / настройка автоответов -->
          <div class="flex items-center gap-1 ml-3 bg-gray-100 rounded-lg p-0.5">
            <button @click="tab = 'chats'"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'chats' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Переписка
            </button>
            <button v-if="isAdmin" @click="openTriage"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'triage' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Разбор
            </button>
            <button @click="openArchive"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'archive' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              Архив
            </button>
            <button v-if="canViewChatTemplates" @click="openPlaybook"
              :class="['px-3 py-1 text-xs font-medium rounded-md transition-colors',
                tab === 'playbook' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              {{ canEditChatTemplates ? 'Автоответы' : 'Шаблоны' }}
            </button>
          </div>

          <!-- Подразделы автоответов: менеджеру КС — только просмотр шаблонов -->
          <div v-if="tab === 'playbook' && canEditChatTemplates" class="flex items-center gap-1 ml-1 bg-gray-100 rounded-lg p-0.5">
            <button v-for="s in pbSections" :key="s.id" @click="pbSection = s.id"
              :class="['px-2.5 py-1 text-xs font-medium rounded-md transition-colors',
                pbSection === s.id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700']">
              {{ s.label }}
            </button>
          </div>
          <div v-else-if="tab === 'playbook' && canViewChatTemplates" class="ml-2 text-[11px] text-gray-400">
            Только просмотр
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
          <button @click="setChatFilter('pending')"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              chatFilter === 'pending' ? 'bg-orange-500 text-white border-orange-500' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Ждёт ответа
            <span :class="['px-1 rounded-full font-bold text-xs',
              chatFilter === 'pending' ? 'bg-white/20 text-white' : 'bg-orange-100 text-orange-700']">{{ pendingChatCount }}</span>
          </button>
          <button v-if="isAdmin" @click="setChatFilter('review')"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              chatFilter === 'review' ? 'bg-rose-600 text-white border-rose-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            На проверку
            <span v-if="reviewCount" :class="['px-1 rounded-full font-bold text-xs',
              chatFilter === 'review' ? 'bg-white/20 text-white' : 'bg-rose-100 text-rose-700']">{{ reviewCount }}</span>
          </button>
          <button @click="setChatFilter('new')"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              chatFilter === 'new' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Новые
            <span v-if="unreadChatCount" :class="['px-1 rounded-full font-bold text-xs',
              chatFilter === 'new' ? 'bg-white/20 text-white' : 'bg-blue-100 text-blue-700']">{{ unreadChatCount }}</span>
          </button>
          <p class="text-[10px] text-gray-400 w-full basis-full pl-0.5 leading-snug">
            <template v-if="chatFilter === 'review'">Бот оставил черновик или передал менеджеру</template>
            <template v-else-if="chatFilter === 'new'">Только новые сообщения клиента</template>
            <template v-else-if="chatFilter === 'pending'">
              <template v-if="currentMp === 'ozon'">
                Ждёт ответа: {{ pendingCustomerCount }} от клиента
                <span v-if="pendingSystemCount"> · {{ pendingSystemCount }} оповещение Ozon</span>
                <span v-if="pendingUnknownCount"> · {{ pendingUnknownCount }} без отправителя</span>
              </template>
              <template v-else>Клиент ждёт ответа: {{ pendingChatCount }}</template>
            </template>
            <template v-else>Все чаты · нажмите фильтр выше</template>
          </p>
          <div class="flex gap-1.5 w-full basis-full">
            <input
              v-model="chatFindQ"
              @keydown.enter.prevent="findChatByPaste"
              placeholder="chatId или ссылка WB…"
              class="flex-1 min-w-0 text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-violet-400"
            />
            <button type="button" @click="findChatByPaste"
              class="shrink-0 text-xs px-2.5 py-1.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 font-medium">
              Найти
            </button>
          </div>
          <div v-if="chatFilter === 'pending' && currentMp === 'ozon'" class="flex items-center gap-1 w-full basis-full">
            <button v-for="f in senderFilters" :key="f.key" @click="senderFilter = f.key"
              :class="['text-[10px] px-2 py-0.5 rounded-full border transition-colors',
                senderFilter === f.key ? 'bg-gray-800 text-white border-gray-800' : 'bg-white text-gray-500 border-gray-200']">
              {{ f.label }}
              <span v-if="f.count != null" class="opacity-80">{{ f.count }}</span>
            </button>
          </div>
        </div>

        <!-- список -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="queueLoading" class="flex justify-center py-10">
            <div :class="['w-6 h-6 border-4 border-t-transparent rounded-full animate-spin', mpUi.spin]"></div>
          </div>
          <div v-else-if="filteredItems.length === 0"
            class="flex flex-col items-center justify-center py-12 px-4 text-center">
            <p class="text-sm text-gray-500 font-medium">
              {{ chatFilter === 'new' ? 'Нет новых от клиентов'
                : chatFilter === 'review' ? 'Нет чатов на проверку'
                : chatFilter === 'pending' ? 'Нет ожидающих ответа'
                : 'Чатов нет' }}
            </p>
          </div>
          <div v-else>
            <div v-for="item in pagedItems" :key="item.id"
              @click="selectChat(item)"
              :class="['flex gap-2.5 px-3 py-3 cursor-pointer border-b border-gray-100 transition-colors',
                chatItem?.id === item.id
                  ? mpUi.selected
                  : item.needs_answer
                    ? 'hover:bg-orange-50/40 border-l-[3px] border-l-orange-400'
                    : 'hover:bg-gray-50 border-l-[3px] border-l-transparent opacity-60']">

              <div class="shrink-0 w-[60px] h-[84px] rounded-lg overflow-hidden bg-gray-100 border border-gray-200 flex items-center justify-center">
                <img v-if="item.product_photo || (chatMp(item) === 'wb' && item.nm_id)"
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
                    <span v-if="ymChatType(item)"
                      class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-100">
                      {{ ymChatTypeShort(item) }}
                    </span>
                    <span v-else-if="item.mp && item.mp !== 'wb'"
                      class="text-[10px] uppercase font-semibold px-1 py-0.5 rounded bg-gray-100 text-gray-500">
                      {{ item.mp }}
                    </span>
                    <span v-if="hasNewMsg(item)" class="w-2 h-2 rounded-full bg-blue-500 animate-pulse block"></span>
                    <span v-if="item.needs_answer" class="text-xs text-orange-600 font-semibold">Ждёт</span>
                    <template v-if="item.needs_answer && chatMp(item) === 'ozon'">
                      <span v-if="senderKind(item) === 'system'"
                        class="text-[10px] font-medium px-1 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200"
                        title="Последнее сообщение — оповещение Ozon, не клиент">Ozon</span>
                      <span v-else-if="senderKind(item) === 'customer'"
                        class="text-[10px] font-medium px-1 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-100"
                        title="Последнее сообщение от покупателя">клиент</span>
                      <span v-else-if="senderKind(item) === 'unknown'"
                        class="text-[10px] font-medium px-1 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100"
                        title="Отправитель ещё не определён">?</span>
                    </template>
                    <span class="text-xs text-gray-400">{{ fmtAge(chatAgeSec(item)) }}</span>
                  </div>
                </div>
                <p class="text-xs text-gray-400 truncate leading-tight">
                  {{ listSubtitle(item) }}
                </p>
                <p :class="['text-xs truncate mt-0.5 leading-tight', item.needs_answer ? 'text-gray-700' : 'text-gray-400']">
                  {{ previewBody(item.body) }}
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
              :class="['w-10 text-center border border-gray-200 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-2 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none', mpUi.ring]" />
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
                <img v-if="chatItem.product_photo || (chatMp(chatItem) === 'wb' && chatItem.nm_id)"
                  :src="chatItem.product_photo || wbPhotoUrl(chatItem.nm_id)"
                  class="w-full h-full object-cover"
                  @error="e => e.target.style.display='none'" />
                <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <a v-if="chatMp(chatItem) === 'wb' && chatItem.nm_id"
                    :href="`https://www.wildberries.ru/catalog/${chatItem.nm_id}/detail.aspx`" target="_blank"
                    class="text-sm font-semibold text-blue-600 hover:text-blue-700 truncate max-w-xs">
                    {{ chatItem.sku || `Арт. ${chatItem.nm_id}` }}
                  </a>
                  <span v-else class="text-sm font-semibold text-gray-700 truncate max-w-md">
                    {{ chatHeaderTitle(chatItem) }}
                  </span>
                  <span v-if="ymChatType(chatItem)"
                    class="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-200">
                    {{ ymChatTypeLabel(chatItem) }}
                  </span>
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(chatAgeSec(chatItem))]">
                    чат {{ fmtAge(chatAgeSec(chatItem)) }}
                  </span>
                </div>
                <p class="text-xs text-gray-400 mt-0.5">
                  <template v-if="chatMp(chatItem) === 'ym'">
                    {{ chatItem.contact || 'Покупатель' }}
                    <template v-if="ymChatType(chatItem)"> · {{ ymChatTypeLabel(chatItem) }}</template>
                    <template v-if="chatItem.order_number"> · заказ {{ chatItem.order_number }}</template>
                    <template v-if="chatItem.sku"> · {{ chatItem.sku }}</template>
                  </template>
                  <template v-else-if="chatMp(chatItem) === 'ozon'">
                    <template v-if="chatItem.order_number">Заказ {{ chatItem.order_number }}</template>
                    <template v-else>{{ chatItem.contact || 'Покупатель' }}</template>
                    <template v-if="chatItem.sku && chatItem.contact !== chatItem.sku"> · {{ chatItem.sku }}</template>
                  </template>
                  <template v-else>
                    {{ chatItem.contact || 'Покупатель' }}
                    <template v-if="chatMp(chatItem) === 'wb'"> · нм {{ chatItem.nm_id || '—' }}</template>
                  </template>
                </p>
              </div>
              <a v-if="marketplaceChatUrl(chatItem)" :href="marketplaceChatUrl(chatItem)" target="_blank" rel="noopener"
                :class="['shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg font-medium transition-colors', mpUi.btn]">
                {{ marketplaceChatLabel(chatItem) }}
              </a>
              <button v-if="productCardRequest" type="button" @click="toggleProductCard"
                :aria-expanded="productCardOpen"
                :class="[
                  'shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-lg border bg-white transition-colors',
                  productCardOpen
                    ? 'border-amber-300 bg-amber-50 text-amber-700'
                    : 'border-gray-200 text-gray-500 hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700'
                ]"
                title="Карточка товара">
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5A2.5 2.5 0 0 0 17.5 16H4z" />
                  <path d="M6.5 3A2.5 2.5 0 0 0 4 5.5V19a2 2 0 0 0 2 2h11.5A2.5 2.5 0 0 0 20 18.5V16" />
                  <path d="M8 7h8M8 10h8M8 13h5" />
                </svg>
              </button>
              <button type="button" @click="copyCxChatLink"
                class="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 hover:border-violet-300 hover:text-violet-700 hover:bg-violet-50 transition-colors"
                title="Ссылка для коллеги в CX">
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L10 4" />
                  <path d="M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 0 0 7.07 7.07L14 20" />
                </svg>
              </button>
              <button @click="dismissChatItem"
                class="shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-600 hover:border-rose-300 hover:text-rose-700 hover:bg-rose-50 font-medium transition-colors">
                Закрыть чат
              </button>
            </div>

            <div v-if="productCardRequest && productCardOpen" class="mt-2 rounded-xl border border-gray-200 bg-white px-3 py-2.5">
              <div class="flex items-center justify-between gap-3 border-b border-gray-100 pb-2">
                <div>
                  <p class="text-xs font-semibold text-slate-900">Карточка товара</p>
                  <p class="mt-0.5 text-[11px] text-slate-500">
                    <span v-if="productCardRequest.article">арт. {{ productCardRequest.article }}</span>
                    <span v-else-if="productCardRequest.nmId">WB nm_id {{ productCardRequest.nmId }}</span>
                  </p>
                </div>
                <button type="button" @click="toggleProductCard"
                  class="inline-flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
                  title="Свернуть карточку">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <path d="m6 15 6-6 6 6" />
                  </svg>
                </button>
              </div>
              <div class="pt-2 text-xs text-slate-700">
                <p v-if="productCardLoading" class="text-gray-400">Загружаем карточку товара…</p>
                <template v-else-if="productCard?.status === 'ok'">
                  <p v-if="productDimensionsLine(productCard.product.dimensions)" class="mt-1">
                    Габариты упаковки: <span class="font-medium">{{ productDimensionsLine(productCard.product.dimensions) }} см</span>
                  </p>
                  <p v-if="productCard.product.dimensions?.weight != null" class="mt-1">
                    Вес: <span class="font-medium">{{ productCard.product.dimensions.weight }} кг</span>
                  </p>
                  <dl v-if="productCard.product.params?.length" class="mt-2 grid gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(140px,200px)_1fr]">
                    <template v-for="param in productCard.product.params" :key="`${param.name}:${param.value}`">
                      <dt class="text-slate-500">{{ param.name }}</dt>
                      <dd class="font-medium break-words">{{ param.value }}</dd>
                    </template>
                  </dl>
                  <p v-if="productCard.cached" class="mt-2 text-[10px] text-slate-400">Загружено из локальной базы</p>
                </template>
                <p v-else-if="productCard?.status === 'not_found'" class="text-slate-500">
                  Для этого товара карточка пока не найдена.
                </p>
                <p v-else class="text-slate-500">Карточка товара сейчас недоступна. Попробуйте позже.</p>
              </div>
            </div>

            <!-- Контекст клиента -->
            <div v-if="chatContextLoading" class="mt-2 text-xs text-gray-400">Загружаем…</div>
            <template v-else-if="chatContext">
              <div v-if="chatContext.specific?.claim || chatContext.specific?.review || chatContext.specific?.reshipment"
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

                <button v-if="chatContext.specific.reshipment"
                  @click="expandedReshipId = expandedReshipId === chatContext.specific.reshipment.id ? null : chatContext.specific.reshipment.id"
                  :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors',
                    expandedReshipId === chatContext.specific.reshipment.id
                      ? 'bg-emerald-100 border-emerald-400 text-emerald-900'
                      : 'bg-emerald-50 border-emerald-300 text-emerald-800 hover:bg-emerald-100']">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
                  Отправка #{{ chatContext.specific.reshipment.id }}
                  <span v-if="chatContext.specific.reshipment.created_date">от {{ chatContext.specific.reshipment.created_date }}</span>
                  <span class="opacity-80">· {{ chatContext.specific.reshipment.status_label }}</span>
                </button>
              </div>

              <!-- Сводка по отправке деталей -->
              <div v-if="expandedReshipId && expandedReshipId === chatContext.specific?.reshipment?.id"
                class="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5">
                <div class="flex items-center justify-between mb-1.5">
                  <div class="flex items-center gap-2 text-xs font-semibold text-emerald-900">
                    <span>Отправка деталей #{{ chatContext.specific.reshipment.id }}</span>
                    <span class="text-emerald-300">·</span>
                    <span>{{ chatContext.specific.reshipment.status_label }}</span>
                  </div>
                  <button @click="expandedReshipId=null" class="text-gray-300 hover:text-gray-500 text-lg leading-none">×</button>
                </div>
                <div class="space-y-1 text-xs text-slate-700">
                  <p v-if="chatContext.specific.reshipment.track_number">
                    Трек: <span class="font-medium">{{ chatContext.specific.reshipment.track_number }}</span>
                    <span v-if="chatContext.specific.reshipment.delivery_method" class="text-slate-400">
                      · {{ chatContext.specific.reshipment.delivery_method === 'yandex' ? 'Яндекс' : 'СДЭК' }}
                    </span>
                  </p>
                  <p v-if="chatContext.specific.reshipment.pvz" class="text-slate-600">ПВЗ: {{ chatContext.specific.reshipment.pvz }}</p>
                  <p v-if="chatContext.specific.reshipment.matched_srid" class="text-slate-500">Заказ: {{ chatContext.specific.reshipment.matched_srid }}</p>
                </div>
                <button type="button"
                  @click="openReshipmentFromChat(chatContext.specific.reshipment.id)"
                  class="mt-2 text-[11px] font-semibold text-emerald-800 hover:text-emerald-950 underline underline-offset-2">
                  Открыть карточку отправки →
                </button>
              </div>

              <!-- Сводка по возврату + решение (WB/ЯМ) прямо из чата -->
              <div v-if="expandedClaimId && expandedClaimId === chatContext.specific?.claim?.claim_id"
                class="mt-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2.5">
                <div class="flex items-center justify-between mb-1.5">
                  <div class="flex items-center gap-2 text-xs font-semibold text-orange-800">
                    <span>{{ chatContext.specific.claim.claim_type_label || 'Возврат' }}</span>
                    <span class="text-orange-300">·</span>
                    <span :class="claimStatusClass(chatContext.specific.claim.status_label)">
                      {{ chatContext.specific.claim.status_label }}
                    </span>
                  </div>
                  <button @click="expandedClaimId=''" class="text-gray-300 hover:text-gray-500 text-lg leading-none">×</button>
                </div>
                <p v-if="chatContext.specific.claim.body && chatContext.specific.claim.body !== '—'"
                  class="text-xs text-slate-700 leading-relaxed">{{ chatContext.specific.claim.body }}</p>
                <p v-else class="text-xs text-gray-400 italic">Комментарий не указан</p>

                <div v-if="claimDetailLoading" class="mt-2 text-[11px] text-slate-400">Загружаем действия…</div>
                <div v-else-if="claimPrimaryActions.length" class="mt-2 space-y-1.5">
                  <button
                    v-for="a in claimPrimaryActions"
                    :key="a.action"
                    :disabled="claimDeciding"
                    @click="onChatClaimAction(a)"
                    :class="[
                      'w-full text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 text-left',
                      a.kind === 'approve_keep' ? 'bg-emerald-600 text-white border-emerald-600 hover:bg-emerald-700' :
                      a.kind === 'approve_return' ? 'bg-white text-emerald-800 border-emerald-300 hover:bg-emerald-50' :
                      'bg-white text-rose-700 border-rose-300 hover:bg-rose-50'
                    ]"
                  >{{ a.label }}</button>
                  <div v-if="claimRejectOpen" class="space-y-1.5 pt-1">
                    <select v-if="claimRejectReasons.length" v-model="claimRejectReason"
                      class="w-full text-[11px] border border-rose-200 rounded-lg px-2 py-1.5 bg-white">
                      <option value="">Причина отказа</option>
                      <option v-for="r in claimRejectReasons" :key="r.value" :value="r.value">{{ r.label }}</option>
                    </select>
                    <textarea v-model="claimRejectComment" rows="2"
                      placeholder="Причина отказа для покупателя"
                      class="w-full text-[11px] border border-rose-200 rounded-lg px-2 py-1.5"></textarea>
                    <div class="flex gap-1.5">
                      <button :disabled="claimDeciding" @click="submitChatReject"
                        class="flex-1 text-[11px] font-semibold px-2 py-1.5 rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50">
                        Отправить отказ
                      </button>
                      <button @click="claimRejectOpen=false" class="text-[11px] px-2 py-1.5 text-slate-500">Отмена</button>
                    </div>
                  </div>
                  <p v-if="claimDecisionError" class="text-[11px] text-rose-600">{{ claimDecisionError }}</p>
                </div>
                <p v-else-if="claimDetail?.decision_note" class="mt-2 text-[11px] text-slate-500">{{ claimDetail.decision_note }}</p>

                <button
                  v-if="chatContext.specific.claim.claim_id"
                  @click="openReturnFromChat(chatContext.specific.claim.claim_id)"
                  class="mt-2.5 w-full text-[11px] font-semibold px-2.5 py-1.5 rounded-lg bg-orange-600 text-white hover:bg-orange-700 text-center transition-colors"
                >Открыть в возвратах →</button>
              </div>
            </template>
          </div>

          <!-- Лента сообщений -->
          <div ref="chatScroll" class="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-gray-50">
            <div v-if="chatLoading" class="flex justify-center py-8">
              <div :class="['w-7 h-7 border-4 border-t-transparent rounded-full animate-spin', mpUi.spin]"></div>
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
                <div :class="['flex', isSystem(msg) ? 'justify-center' : (isSeller(msg) ? 'justify-end' : 'justify-start')]">
                  <div :class="['max-w-[75%] rounded-2xl px-3.5 py-2',
                  isSystem(msg) ? 'bg-gray-100 text-gray-600 rounded-xl text-center'
                    : isSeller(msg) ? (mpUi.bubble + ' rounded-br-md') : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md']">
                  <div v-if="msgImages(msg).length" class="mb-1.5 space-y-1.5">
                    <a v-for="(attachment, i) in msgImages(msg)" :key="i" :href="attachment.url" target="_blank" rel="noopener" class="block">
                      <img :src="attachment.url" :alt="attachment.name || 'Изображение из чата'" class="rounded-lg max-w-[220px] max-h-[260px] object-cover" />
                    </a>
                  </div>
                  <div v-if="msgDocuments(msg).length" class="mb-1.5 space-y-1.5">
                    <a v-for="(attachment, i) in msgDocuments(msg)" :key="i"
                      :href="attachment.url" target="_blank" rel="noopener"
                      class="flex min-h-11 items-center gap-2 rounded-lg border border-current/20 bg-white/10 px-2.5 py-2 text-xs font-medium underline-offset-2 hover:underline">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <path d="M14 2v6h6M8 15h8M8 18h5"/>
                      </svg>
                      <span class="break-all">{{ attachment.name || 'Открыть документ' }}</span>
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
            <div v-if="chatSuggest && chatSuggest.reply_text"
              :class="['mb-2 rounded-xl border px-3 py-2 flex items-start justify-between gap-2', mpUi.softBorder]">
              <div class="min-w-0">
                <p :class="['text-xs font-semibold', mpUi.textStrong]">
                  Предложенный ответ
                  <span v-if="chatSuggest.theme_name" class="font-normal opacity-80">
                    · {{ chatSuggest.theme_name }}
                  </span>
                  <span v-if="chatSuggest.confidence != null" class="font-normal opacity-70">
                    · {{ Math.round((chatSuggest.confidence || 0) * 100) }}%
                  </span>
                  <span v-if="chatSuggest.need_human" class="ml-1 text-rose-600">· лучше проверить</span>
                  <span v-if="chatSuggest.from_cache" :class="['ml-1 font-normal', mpUi.textMuted]">· из кэша</span>
                </p>
                <p :class="['text-[10px] mt-0.5', mpUi.textMuted]">Можно править и отправить. Автоотправки нет.</p>
              </div>
              <div class="flex flex-col items-stretch gap-1.5 shrink-0">
                <button @click="generateSuggestAi" :disabled="chatSuggestGenerating"
                  class="text-xs font-medium px-3 py-1.5 rounded-lg border border-violet-200 bg-violet-50 text-violet-800 hover:bg-violet-100 disabled:opacity-60">
                  {{ chatSuggestGenerating ? 'Генерируем…' : 'Сгенерировать' }}
                </button>
                <button @click="clearSuggest"
                  class="text-xs font-medium px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50">
                  Очистить
                </button>
              </div>
            </div>
            <div v-else-if="chatSuggestLoading" class="mb-2 text-[10px] text-gray-400">Подбираем шаблон…</div>
            <div v-else-if="chatSuggestMiss"
              class="mb-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 flex items-center justify-between gap-3">
              <div class="min-w-0">
                <p class="text-xs font-semibold text-amber-900">
                  {{ chatSuggestMiss.need_human ? 'Нужен менеджер' : 'Ответ не подобран' }}
                </p>
                <p class="text-[10px] text-amber-800/80 mt-0.5 leading-snug">
                  {{ chatSuggestMiss.reason || (chatSuggestMiss.need_human
                    ? 'Автоответ отключён: возьмите чат в работу вручную.'
                    : 'Нет подходящего шаблона для этого сообщения.') }}
                </p>
              </div>
              <button v-if="!chatSuggestMiss.need_human && chatSuggestMiss.can_generate !== false"
                @click="generateSuggestAi" :disabled="chatSuggestGenerating"
                class="shrink-0 text-xs font-medium px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-60">
                {{ chatSuggestGenerating ? 'Генерируем…' : 'Сгенерировать черновик' }}
              </button>
            </div>
            <div v-if="chatTemplatesLoading || visibleChatTemplates.length" class="relative mb-2">
              <div class="flex min-w-0 items-center gap-1.5">
                <p class="shrink-0 text-[10px] font-medium text-gray-400">Быстрые шаблоны</p>
                <div v-if="chatTemplatesLoading" class="text-[10px] text-gray-400">Загружаем…</div>
                <template v-else>
                  <div class="flex min-w-0 flex-1 gap-1.5 overflow-hidden">
                    <button v-for="template in compactChatTemplates" :key="template.id"
                      type="button"
                      :title="template.title"
                      @click="applyChatTemplate(template)"
                      :class="['min-w-0 max-w-44 truncate rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors',
                        isChatTemplateActive(template)
                          ? mpUi.chipOn
                          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50']">
                      {{ template.title }}
                    </button>
                  </div>
                  <button v-if="hasMoreChatTemplates" type="button"
                    @click="chatTemplatesExpanded = !chatTemplatesExpanded"
                    :aria-expanded="chatTemplatesExpanded"
                    aria-controls="chat-template-panel"
                    :class="['flex min-h-9 shrink-0 items-center gap-1 rounded-lg border px-2 text-[11px] font-medium transition-colors focus:outline-none focus:ring-2',
                      chatTemplatesExpanded ? mpUi.chipOn : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50']">
                    <span>{{ chatTemplatesExpanded ? 'Свернуть' : `Все · ${visibleChatTemplates.length}` }}</span>
                    <svg :class="['h-3.5 w-3.5 transition-transform', chatTemplatesExpanded ? 'rotate-180' : '']"
                      viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                      <path d="m6 9 6 6 6-6" />
                    </svg>
                  </button>
                </template>
              </div>
              <div v-if="chatTemplatesExpanded" id="chat-template-panel"
                class="absolute inset-x-0 bottom-full z-20 mb-2 max-h-64 overflow-y-auto rounded-xl border border-gray-200 bg-white p-2.5 shadow-lg">
                <div class="mb-2 flex items-center justify-between gap-3 px-0.5">
                  <p class="text-xs font-semibold text-gray-700">Все шаблоны · {{ visibleChatTemplates.length }}</p>
                  <button type="button" @click="chatTemplatesExpanded = false"
                    class="min-h-9 rounded-lg px-2 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-300">
                    Свернуть
                  </button>
                </div>
                <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  <button v-for="template in visibleChatTemplates" :key="template.id"
                    type="button"
                    :title="template.title"
                    @click="applyChatTemplate(template)"
                    :class="['min-h-[44px] rounded-lg border px-3 py-2 text-left text-xs font-medium leading-snug transition-colors',
                      isChatTemplateActive(template)
                        ? mpUi.chipOn
                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50']">
                    {{ template.title }}
                  </button>
                </div>
              </div>
            </div>
            <div v-if="chatSendWarning" role="alert"
              class="mb-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <p class="font-semibold">Нужна сверка отправки</p>
              <p class="mt-0.5">{{ chatSendWarning.message }}</p>
              <p v-if="chatSendWarning.uncertainNames?.length" class="mt-1 break-words">
                Не повторять до сверки: {{ chatSendWarning.uncertainNames.join(', ') }}
              </p>
              <button type="button" @click="reconcileChatSend" class="mt-1.5 font-semibold underline underline-offset-2">
                Обновить историю
              </button>
            </div>
            <div v-if="chatAttachments.length" class="mb-2 flex flex-wrap gap-2" aria-label="Выбранные вложения">
              <div v-for="attachment in chatAttachments" :key="attachment.id"
                class="flex min-w-0 max-w-64 items-center gap-2 rounded-xl border border-gray-200 bg-gray-50 p-1.5 pr-1">
                <img v-if="attachment.previewUrl" :src="attachment.previewUrl" alt=""
                  class="h-10 w-10 shrink-0 rounded-lg border border-gray-200 object-cover" />
                <div v-else class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <path d="M14 2v6h6M8 15h8M8 18h5"/>
                  </svg>
                </div>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-xs font-medium text-gray-700" :title="attachment.file.name">{{ attachment.file.name }}</p>
                  <p class="text-[10px] text-gray-400">{{ fmtFileSize(attachment.file.size) }}</p>
                </div>
                <button type="button" @click="removeChatAttachment(attachment.id)"
                  :aria-label="`Убрать файл ${attachment.file.name}`"
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-white hover:text-rose-600 focus:outline-none focus:ring-2 focus:ring-rose-300">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12"/>
                  </svg>
                </button>
              </div>
            </div>
            <div class="flex items-end gap-2">
              <input ref="chatAttachmentInput" type="file" multiple class="hidden"
                accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
                @change="onChatAttachmentsSelected" />
              <button type="button" @click="chooseChatAttachments"
                :disabled="chatSending"
                title="Прикрепить JPEG, PNG или PDF до 5 МиБ"
                :class="['w-11 h-11 rounded-xl flex items-center justify-center shrink-0 border transition-colors focus:outline-none focus:ring-2',
                  !chatSending
                    ? `border-gray-200 bg-white text-gray-500 hover:bg-gray-50 hover:text-gray-700 ${mpUi.ring}`
                    : 'border-gray-200 bg-gray-100 text-gray-300 cursor-not-allowed']">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
                <span class="sr-only">Прикрепить файл</span>
              </button>
              <textarea ref="chatReplyEl" v-model="chatReply"
                @input="autoGrowReply"
                @keydown.enter.exact.prevent="sendChatMessage"
                placeholder="Сообщение… (Enter — отправить, Shift+Enter — новая строка)"
                rows="1"
                :class="['flex-1 border border-gray-200 rounded-xl px-3.5 py-2.5 text-sm leading-5 text-gray-900 bg-white resize-none overflow-y-auto focus:outline-none focus:ring-2 min-h-[44px] max-h-40', mpUi.ring]"></textarea>
              <button @click="sendChatMessage" :disabled="!chatCanSend"
                aria-label="Отправить сообщение"
                :class="['w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-all focus:outline-none focus:ring-2',
                  chatCanSend ? mpUi.btn : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                <svg v-if="chatSending" class="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M21 12a9 9 0 1 1-6.22-8.56"/>
                </svg>
                <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- ══ РАЗБОР: корзины, фильтр, безопасное закрытие ═══════════════════ -->
    <div v-else-if="tab === 'triage' && isAdmin" class="flex-1 overflow-y-auto bg-gray-50" style="height: calc(100vh - 110px)">
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
              :class="['text-xs border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2', mpUi.ring]">
              <option :value="0">любой срок</option>
              <option :value="3">3 дней</option>
              <option :value="5">5 дней</option>
              <option :value="14">14 дней</option>
            </select>
            <button @click="loadTriage" :class="['text-xs', mpUi.text]">Обновить</button>
          </div>

          <div v-if="trLoading" class="flex justify-center py-8">
            <div :class="['w-6 h-6 border-4 border-t-transparent rounded-full animate-spin', mpUi.spin]"></div>
          </div>
          <div v-else class="grid sm:grid-cols-3 gap-2">
            <button v-for="c in trSummary" :key="c.category"
              @click="selectCategory(c.category)"
              :class="['text-left rounded-xl border px-3 py-2.5 transition-colors',
                trCategory === c.category ? mpUi.chipOn : 'border-gray-200 hover:border-gray-300 bg-white']">
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
                      :class="['text-[11px] px-2 py-1 rounded-lg border border-gray-200 text-gray-600 transition-colors', mpUi.hoverLink]">
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
            <div :class="['w-6 h-6 border-4 border-t-transparent rounded-full animate-spin', mpUi.spin]"></div>
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
                  <span v-if="a.mp" class="uppercase font-semibold text-gray-500">{{ a.mp }} · </span>
                  Закрыл {{ a.dismissed_by || '—' }}{{ a.note ? ' · ' + a.note : '' }}
                </p>
              </div>
              <button @click="restoreChat(a)"
                :class="['text-[11px] font-medium shrink-0', mpUi.text]">Вернуть</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ АВТООТВЕТЫ: темы, режимы, тексты ═══════════════════════════════ -->
    <div v-else-if="tab === 'playbook' && canViewChatTemplates" class="flex-1 overflow-y-auto bg-gray-50" style="height: calc(100vh - 110px)">
      <div class="max-w-4xl mx-auto px-5 py-6 space-y-6">

        <div v-if="pbLoading" class="flex justify-center py-16">
          <div :class="['w-7 h-7 border-4 border-t-transparent rounded-full animate-spin', mpUi.spin]"></div>
        </div>

        <template v-else>
          <!-- ── Настройки ─────────────────────────────────────────────── -->
          <template v-if="pbSection === 'settings' && canEditChatTemplates">
          <!-- Общие настройки -->
          <div class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
            <div class="px-5 py-3 border-b border-gray-100">
              <h2 class="text-sm font-semibold text-gray-900">Общие настройки автоответов</h2>
              <p class="text-[11px] text-gray-400 mt-0.5">
                Управление ботом — для площадки из сайдбара
                (<span class="font-medium text-gray-600">{{ pbMpLabel }}</span>);
                модели ИИ, портрет и тексты — общие
              </p>
            </div>

            <div class="divide-y divide-gray-100">
              <!-- Работа бота (per-mp from sidebar) -->
              <div class="px-5 py-4 space-y-3">
                <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
                  Работа бота · {{ pbMpLabel }}
                </p>
                <p v-if="pbMp !== 'wb'" class="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
                  Автоотправка подключена и для Ozon/ЯМ: при включённом тумблере и выключенном shadow бот пишет клиенту (zone=auto). Пока тумблер выключен — только журнал при ручном прогоне.
                </p>
                <p v-else-if="currentMp === 'all'" class="text-[11px] text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-2.5 py-1.5">
                  В сайдбаре выбрано «все площадки» — показаны настройки WB. Переключите WB / Ozon / ЯМ в сайдбаре, чтобы править другую.
                </p>

                <label class="flex items-start justify-between gap-4 cursor-pointer">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">Автоотправка клиенту</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">Если выкл — только подсказки менеджеру. Если вкл — бот сам пишет в чат (темы в режиме «Авто»).</p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="mpSettingOn('bot_enabled')"
                    @click="toggleMpSetting('bot_enabled')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      mpSettingOn('bot_enabled') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      mpSettingOn('bot_enabled') ? 'translate-x-5' : '']"/>
                  </button>
                </label>

                <label class="flex items-start justify-between gap-4 cursor-pointer">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">Подставлять ответ при открытии чата</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">Менеджер видит черновик в поле ввода и сам жмёт «Отправить».</p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="mpSettingOn('bot_suggest_on_open')"
                    @click="toggleMpSetting('bot_suggest_on_open')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      mpSettingOn('bot_suggest_on_open') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      mpSettingOn('bot_suggest_on_open') ? 'translate-x-5' : '']"/>
                  </button>
                </label>

                <label class="flex items-start justify-between gap-4 cursor-pointer">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">Только чаты без ответа менеджера</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">Не лезть в переписки, где уже писал человек (кроме тех, что бот уже вёл).</p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="mpSettingOn('bot_only_no_manager')"
                    @click="toggleMpSetting('bot_only_no_manager')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      mpSettingOn('bot_only_no_manager') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      mpSettingOn('bot_only_no_manager') ? 'translate-x-5' : '']"/>
                  </button>
                </label>

                <label class="flex items-start justify-between gap-4 cursor-pointer">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">ИИ-черновик, если нет шаблона</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">Клиенту сам не отправит — только черновик менеджеру. Модель ИИ общая (ниже).</p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="mpSettingOn('bot_ai_draft')"
                    @click="toggleMpSetting('bot_ai_draft')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      mpSettingOn('bot_ai_draft') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      mpSettingOn('bot_ai_draft') ? 'translate-x-5' : '']"/>
                  </button>
                </label>

                <div class="grid sm:grid-cols-2 gap-3 pt-1">
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Брать чаты за последние</label>
                    <div class="flex items-center gap-2">
                      <input type="number" min="0" max="90" step="1"
                        :value="mpSettingNum('bot_only_days')"
                        @change="setMpSettingNum('bot_only_days', $event.target.value)"
                        :class="['w-20 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2', mpUi.ring]" />
                      <span class="text-xs text-gray-500">дней <span class="text-gray-400">(0 = без лимита)</span></span>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Мин. уверенность</label>
                    <div class="flex items-center gap-2">
                      <input type="range" min="30" max="95" step="5"
                        :value="Math.round(mpSettingNum('bot_confidence_min') * 100)"
                        @input="setMpSettingNum('bot_confidence_min', ($event.target.value / 100).toFixed(2), false)"
                        @change="setMpSettingNum('bot_confidence_min', ($event.target.value / 100).toFixed(2))"
                        :class="['flex-1', mpUi.accent]" />
                      <span class="text-xs font-semibold text-gray-700 w-10 text-right">
                        {{ Math.round(mpSettingNum('bot_confidence_min') * 100) }}%
                      </span>
                    </div>
                    <p class="text-[11px] text-gray-400 mt-1">Ниже — меню уточнения 1–5, а не сразу менеджеру.</p>
                  </div>
                </div>

                <div class="pt-1">
                  <label class="text-xs font-medium text-gray-700 block mb-1">
                    Тестовый allowlist chat_id
                    <span class="font-normal text-gray-400">· {{ pbMpLabel }}</span>
                  </label>
                  <textarea
                    rows="2"
                    :value="mpSettingVal('bot_test_chat_ids')"
                    @change="setMpSettingVal('bot_test_chat_ids', $event.target.value)"
                    placeholder="id1, id2, id3 — пусто = все чаты очереди"
                    class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-gray-300 resize-y"
                  />
                  <p class="text-[11px] text-gray-400 mt-1">
                    Если заполнено — бот обрабатывает <span class="font-medium text-gray-600">только</span> эти чаты (удобно для пилота на 4–5 диалогах). После теста очистите поле.
                  </p>
                </div>
              </div>

              <!-- ИИ (global) -->
              <div class="px-5 py-4 space-y-3">
                <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">ИИ · общее</p>

                <div class="grid sm:grid-cols-2 gap-3">
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Модель для темы</label>
                    <select :value="settingVal('bot_model')"
                      @change="setSettingVal('bot_model', $event.target.value)"
                      :class="['w-full border border-gray-200 rounded-lg px-2.5 py-2 text-xs bg-white focus:outline-none focus:ring-2', mpUi.ring]">
                      <option value="yandexgpt-lite">YandexGPT Lite — быстрее и дешевле</option>
                      <option value="yandexgpt">YandexGPT Pro — точнее</option>
                    </select>
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Модель для черновика</label>
                    <select :value="settingVal('bot_ai_draft_model')"
                      @change="setSettingVal('bot_ai_draft_model', $event.target.value)"
                      :class="['w-full border border-gray-200 rounded-lg px-2.5 py-2 text-xs bg-white focus:outline-none focus:ring-2', mpUi.ring]">
                      <option value="yandexgpt-lite">YandexGPT Lite — быстрее и дешевле</option>
                      <option value="yandexgpt">YandexGPT Pro — точнее</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label class="text-xs font-medium text-gray-700 block mb-1">Портрет ИИ (тон и правила)</label>
                  <textarea rows="5"
                    :value="settingVal('bot_persona')"
                    @change="setSettingVal('bot_persona', $event.target.value)"
                    :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:ring-2 leading-relaxed', mpUi.ring]"></textarea>
                </div>
              </div>

              <!-- Тексты -->
              <div class="px-5 py-4 space-y-3">
                <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Тексты · общее</p>
                <div class="grid sm:grid-cols-2 gap-3">
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Приветствие</label>
                    <p class="text-[11px] text-gray-400 mb-1">Добавляется автоматически 1 раз в календарный день (МСК). В текстах шаблонов «Здравствуйте» не дублируйте.</p>
                    <input type="text"
                      :value="settingVal('greeting_text')"
                      @change="setSettingVal('greeting_text', $event.target.value)"
                      :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-2', mpUi.ring]" />
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Подпись</label>
                    <p class="text-[11px] text-gray-400 mb-1">Под содержательным ответом</p>
                    <textarea rows="2"
                      :value="settingVal('signature')"
                      @change="setSettingVal('signature', $event.target.value)"
                      :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:ring-2', mpUi.ring]"></textarea>
                  </div>
                </div>

                <label class="flex items-start justify-between gap-4 cursor-pointer pt-1">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">Сменные насадки в наличии</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">
                      Вкл — бот даёт ссылку/подсказку, где купить. Выкл — отвечает, что сейчас нет.
                    </p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="settingOn('nasadki_in_stock')"
                    @click="toggleSetting('nasadki_in_stock')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      settingOn('nasadki_in_stock') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      settingOn('nasadki_in_stock') ? 'translate-x-5' : '']"/>
                  </button>
                </label>
              </div>

              <!-- Красная зона -->
              <div class="px-5 py-4 space-y-3">
                <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Передача менеджеру</p>

                <label class="flex items-start justify-between gap-4 cursor-pointer">
                  <div class="min-w-0">
                    <p class="text-xs font-medium text-gray-800">Уведомление в Telegram</p>
                    <p class="text-[11px] text-gray-400 mt-0.5">Когда бот передаёт чат человеку.</p>
                  </div>
                  <button type="button" role="switch"
                    :aria-checked="settingOn('red_zone_notify_tg')"
                    @click="toggleSetting('red_zone_notify_tg')"
                    :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                      settingOn('red_zone_notify_tg') ? mpUi.toggleOn : 'bg-gray-200']">
                    <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                      settingOn('red_zone_notify_tg') ? 'translate-x-5' : '']"/>
                  </button>
                </label>

                <div>
                  <label class="text-xs font-medium text-gray-700 block mb-1">Сообщение клиенту</label>
                  <textarea rows="2"
                    :value="settingVal('red_zone_client_text')"
                    @change="setSettingVal('red_zone_client_text', $event.target.value)"
                    :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:ring-2', mpUi.ring]"></textarea>
                </div>
                <div>
                  <label class="text-xs font-medium text-gray-700 block mb-1">Текст в Telegram менеджеру</label>
                  <p class="text-[11px] text-gray-400 mb-1">Плейсхолдеры: {reason} {client} {sku} {message} {link}</p>
                  <textarea rows="3"
                    :value="settingVal('red_zone_manager_text')"
                    @change="setSettingVal('red_zone_manager_text', $event.target.value)"
                    :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs resize-y focus:outline-none focus:ring-2 font-mono', mpUi.ring]"></textarea>
                </div>
              </div>

              <!-- Закрытие -->
              <div class="px-5 py-4 space-y-3">
                <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Закрытие диалога</p>
                <div>
                  <label class="text-xs font-medium text-gray-700 block mb-1">Уточняющий вопрос</label>
                  <input type="text"
                    :value="settingVal('closing_question_text')"
                    @change="setSettingVal('closing_question_text', $event.target.value)"
                    :class="['w-full border border-gray-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-2', mpUi.ring]" />
                </div>
                <div class="grid sm:grid-cols-2 gap-3">
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Спросить через</label>
                    <div class="flex items-center gap-2">
                      <input type="number" min="1" max="168" step="1"
                        :value="settingNum('closing_question_delay_hours')"
                        @change="setSettingNum('closing_question_delay_hours', $event.target.value)"
                        :class="['w-20 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2', mpUi.ring]" />
                      <span class="text-xs text-gray-500">часов молчания</span>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-700 block mb-1">Закрыть через</label>
                    <div class="flex items-center gap-2">
                      <input type="number" min="1" max="336" step="1"
                        :value="settingNum('closing_auto_close_hours')"
                        @change="setSettingNum('closing_auto_close_hours', $event.target.value)"
                        :class="['w-20 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2', mpUi.ring]" />
                      <span class="text-xs text-gray-500">часов без ответа</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          </template>

          <!-- ── Шаблоны ───────────────────────────────────────────────── -->
          <template v-else-if="pbSection === 'templates'">
          <!-- Как это работает -->
          <div class="bg-white rounded-2xl border border-gray-200 p-5">
            <h2 class="text-sm font-semibold text-gray-900 mb-2">Шаблоны автоответов</h2>
            <p class="text-xs text-gray-500 leading-relaxed mb-3">
              Сейчас показаны шаблоны для
              <span class="font-medium text-gray-600">{{ pbMpLabel }}</span>.
              <template v-if="pbMp === 'ozon'">
                Темы доотправки детали на Ozon скрыты — бот ведёт на возврат.
              </template>
              <template v-else-if="pbMp === 'ym'">
                На ЯМ доотправку детали предлагаем в чате (как на WB).
              </template>
              Режим темы (Авто / Согласование / Менеджер) общий для всех площадок.
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

          <!-- Темы -->
          <div v-for="t in visiblePbThemes" :key="t.theme_key"
            :class="['bg-white rounded-2xl border overflow-hidden',
              t.zone==='auto' ? 'border-l-4 border-l-emerald-400 border-gray-200'
              : t.zone==='draft' ? 'border-l-4 border-l-amber-400 border-gray-200'
              : 'border-l-4 border-l-rose-400 border-gray-200']">

            <div class="px-5 py-4">
              <div class="flex items-start justify-between gap-3 flex-wrap">
                <div class="min-w-0 flex-1">
                  <h3 class="text-sm font-semibold text-gray-900">{{ themeDisplayName(t) }}</h3>
                  <p class="text-xs text-gray-500 mt-0.5 leading-relaxed">{{ themeDisplayWhen(t) }}</p>
                  <p v-if="themeDisplayNote(t)" :class="['text-[11px] rounded-lg px-2.5 py-1.5 mt-2 leading-snug', mpUi.note]">
                    {{ themeDisplayNote(t) }}
                  </p>
                </div>
                <div v-if="canEditChatTemplates" class="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5 shrink-0">
                  <button v-for="z in [['auto','Авто'],['draft','Согласование'],['human','Менеджер']]" :key="z[0]"
                    @click="setZone(t, z[0])"
                    :class="['px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors',
                      t.zone === z[0]
                        ? (z[0]==='auto' ? 'bg-emerald-600 text-white' : z[0]==='draft' ? 'bg-amber-500 text-white' : 'bg-rose-600 text-white')
                        : 'text-gray-500 hover:text-gray-700']">
                    {{ z[1] }}
                  </button>
                </div>
                <div v-else class="shrink-0">
                  <span :class="['px-2.5 py-1 text-[11px] font-medium rounded-md',
                    t.zone==='auto' ? 'bg-emerald-100 text-emerald-800' : t.zone==='draft' ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800']">
                    {{ t.zone==='auto' ? 'Авто' : t.zone==='draft' ? 'Согласование' : 'Менеджер' }}
                  </span>
                </div>
              </div>
            </div>

            <!-- Варианты по веткам -->
            <div class="border-t border-gray-100 bg-gray-50/60 px-5 py-3 space-y-4">
              <p v-if="!Object.keys(groupedVariants(t)).length"
                class="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
                {{ emptyVariantHint(t) }}
              </p>
              <div v-for="(vars, bkey) in groupedVariants(t)" :key="bkey">
                <p v-if="bkey" class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                  {{ vars[0].branch_label || bkey }}
                </p>
                <div class="space-y-2">
                  <div v-for="v in vars" :key="v.id"
                    :class="['rounded-xl border bg-white p-2.5', v.active ? 'border-gray-200' : 'border-gray-200 opacity-50']">
                    <textarea v-model="v.body" rows="3"
                      :readonly="!canEditChatTemplates"
                      @blur="canEditChatTemplates && saveVariant(v)"
                      class="w-full border-0 p-0 text-xs text-gray-700 resize-y focus:outline-none bg-transparent leading-relaxed"></textarea>
                    <div class="flex items-center justify-between gap-2 pt-1.5 mt-1 border-t border-gray-50">
                      <div class="flex items-center gap-2">
                        <span :class="['text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded border', variantMpBadgeClass(v.mp)]">
                          {{ variantMpLabel(v.mp) }}
                        </span>
                        <label v-if="canEditChatTemplates" class="flex items-center gap-1.5 text-[11px] text-gray-500 cursor-pointer">
                          <input type="checkbox" v-model="v.active" @change="saveVariant(v)"
                            :class="['w-3.5 h-3.5 rounded', mpUi.accent]" />
                          Используется
                        </label>
                        <span v-else class="text-[11px] text-gray-400">{{ v.active ? 'Используется' : 'Выкл' }}</span>
                      </div>
                      <button v-if="canEditChatTemplates" @click="deleteVariant(t, v)"
                        class="text-[11px] text-gray-400 hover:text-rose-600 transition-colors">Удалить</button>
                    </div>
                  </div>
                  <button v-if="canEditChatTemplates" @click="addVariant(t, bkey, vars[0]?.branch_label || '')"
                    :class="['text-[11px] font-medium transition-colors', mpUi.text]">
                    + Добавить вариант
                  </button>
                </div>
              </div>
              <button v-if="canEditChatTemplates && !Object.keys(groupedVariants(t)).length"
                @click="addVariant(t, '', '')"
                :class="['text-[11px] font-medium transition-colors', mpUi.text]">
                + Добавить вариант для {{ pbMpLabel }}
              </button>
            </div>
          </div>
          </template>

          <!-- ── Проверка ──────────────────────────────────────────────── -->
          <template v-else-if="pbSection === 'check' && canEditChatTemplates">
          <div class="bg-white rounded-2xl border border-gray-200 overflow-hidden">
            <div class="px-5 py-4 border-b border-gray-100">
              <h2 class="text-sm font-semibold text-gray-900">Проверка автоответов</h2>
              <p class="text-xs text-gray-500 mt-1 leading-relaxed max-w-2xl">
                Сейчас бот в <b>{{ botStatus?.shadow ? 'теневом режиме' : 'боевом режиме' }}</b>
                {{ botStatus?.shadow ? '— клиенту ничего не пишет, только показывает, что бы ответил.' : '— реально пишет клиентам.' }}
                Кнопка ниже берёт <b>следующие 15 чатов</b>, которые ещё не разбирали (за последние {{ botStatus?.only_days ?? 2 }} дн.).
              </p>
            </div>

            <div class="px-5 py-3 flex flex-wrap items-center gap-2 border-b border-gray-50">
              <button @click="runBotOnce" :disabled="botRunning"
                :class="['text-xs px-3.5 py-2 rounded-lg disabled:opacity-50 font-medium', mpUi.btn]">
                {{ botRunning ? 'Считает…' : 'Прогнать следующие 15 чатов' }}
              </button>
              <button @click="loadBotLog"
                class="text-xs px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">
                Обновить
              </button>
              <button @click="clearBotLog"
                class="text-xs px-3 py-2 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50">
                Очистить журнал
              </button>
              <span class="text-[11px] text-gray-400 ml-auto">
                Ещё не разобрано: <b class="text-gray-700">{{ botStatus?.pending_chats ?? '—' }}</b>
              </span>
            </div>

            <div v-if="botLastRun" :class="['px-5 py-2.5 border-b text-xs', mpUi.lastRun]">
              Последний прогон:
              обработано <b>{{ botLastRun.processed }}</b>
              <template v-if="botLastRun.by_zone">
                · готов ответить {{ botLastRun.by_zone.auto || 0 }}
                · черновик {{ botLastRun.by_zone.draft || 0 }}
                · менеджеру {{ botLastRun.by_zone.human || 0 }}
              </template>
              <span v-if="botLastRun.shadow" class="text-amber-700"> · тень (клиенту не ушло)</span>
            </div>

            <div class="px-5 py-3 grid grid-cols-2 sm:grid-cols-4 gap-2 border-b border-gray-50">
              <div class="rounded-xl bg-gray-50 px-3 py-2">
                <p class="text-[10px] text-gray-400">Сегодня разобрано</p>
                <p class="text-sm font-semibold text-gray-800">{{ botStatus?.today?.today_total ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-emerald-50 px-3 py-2">
                <p class="text-[10px] text-emerald-600">Готов ответить</p>
                <p class="text-sm font-semibold text-emerald-800">{{ botStatus?.today?.today_auto ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-amber-50 px-3 py-2">
                <p class="text-[10px] text-amber-600">Черновик</p>
                <p class="text-sm font-semibold text-amber-800">{{ botStatus?.today?.today_draft ?? 0 }}</p>
              </div>
              <div class="rounded-xl bg-rose-50 px-3 py-2">
                <p class="text-[10px] text-rose-600">Менеджеру</p>
                <p class="text-sm font-semibold text-rose-800">{{ botStatus?.today?.today_human ?? 0 }}</p>
              </div>
            </div>

            <div class="px-5 py-2 border-b border-gray-50 flex items-center gap-2">
              <span class="text-[11px] text-gray-500">Журнал:</span>
              <button v-for="f in botLogFilters" :key="f.key" @click="botLogFilter = f.key; loadBotLog()"
                :class="['text-[11px] px-2 py-0.5 rounded-full border',
                  botLogFilter === f.key ? 'bg-gray-800 text-white border-gray-800' : 'bg-white text-gray-500 border-gray-200']">
                {{ f.label }}
              </button>
            </div>

            <div class="divide-y divide-gray-50 max-h-[420px] overflow-y-auto">
              <div v-if="!botLog.length" class="px-5 py-8 text-center text-sm text-gray-400">
                Журнал пуст. Нажмите «Прогнать следующие 15 чатов» — здесь появятся решения бота.
              </div>
              <div v-for="row in botLog" :key="row.id" class="px-5 py-3 hover:bg-gray-50/80">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5 flex-wrap text-[11px]">
                      <span :class="zoneBadgeClass(row.zone)">{{ zoneLabel(row.zone) }}</span>
                      <span class="text-gray-400">{{ themeLabel(row.theme) }}</span>
                      <span class="text-gray-300">·</span>
                      <span class="text-gray-400">{{ Math.round((row.confidence || 0) * 100) }}%</span>
                      <span v-if="row.shadow && !row.sent" class="text-amber-600">· тень</span>
                      <span v-if="row.sent" class="text-emerald-600">· ушло клиенту</span>
                    </div>
                    <p class="text-xs text-gray-700 mt-1.5"><span class="text-gray-400">Клиент:</span> {{ row.client_message }}</p>
                    <p v-if="row.reply_text" :class="['text-xs mt-1 whitespace-pre-line', mpUi.textStrong]">
                      <span class="text-gray-400">Бот:</span> {{ row.reply_text }}
                    </p>
                    <p v-if="row.reason" class="text-[10px] text-gray-400 mt-1">{{ row.reason }}</p>
                  </div>
                  <button @click="openChatFromTriage({ chat_id: row.chat_id, mp: row.mp || pbMp, client_name: '', good_name: '', last_message: row.client_message })"
                    :class="['text-[11px] shrink-0', mpUi.text]">Открыть</button>
                </div>
              </div>
            </div>
          </div>
          </template>
        </template>

      </div>
    </div>

    <!-- Карточка отзыва поверх чата (без ухода в /reviews) -->
    <ReviewCard
      v-if="reviewCard.show"
      :mp="reviewCard.mp"
      :review-id="reviewCard.id"
      show-go-to-reviews
      @close="reviewCard.show = false"
    />

    <!-- Toast -->
    <transition name="toast">
      <div v-if="toast" role="status" aria-live="polite"
        :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white max-w-sm',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">
        {{ toast.msg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Centrifuge } from 'centrifuge'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import { usePermissionsStore } from '../stores/permissions'
import { useToast } from '../composables/useToast'
import { CX_BASE, fmtAge, ageCls } from '../utils/cx'
import ReviewCard from '../components/ReviewCard.vue'
import {
  canSendChatContent,
  chatAttachmentKey,
  chatAttachmentsSupported,
  reconcileChatSendProgress,
  validateChatAttachment,
  validateChatAttachmentAddition,
} from '../utils/chatAttachments'

const { toast, showToast } = useToast()

const router = useRouter()
const route  = useRoute()
const permissionsStore = usePermissionsStore()
const isAdmin = computed(() => permissionsStore.isAdmin)
const isCsManager = computed(() => permissionsStore.role === 'cs_manager')
/** Просмотр шаблонов чатов: админ и менеджер КС */
const canViewChatTemplates = computed(() => isAdmin.value || isCsManager.value)
/** Редактирование плейбука / настроек бота — только админ */
const canEditChatTemplates = computed(() => isAdmin.value)

const reviewCard = ref({ show: false, mp: 'wb', id: '' })
const chatFindQ = ref('')

function openReviewCard(reviewId) {
  if (!reviewId) return
  const mp = chatMp(chatItem.value) || 'wb'
  reviewCard.value = { show: true, mp, id: String(reviewId) }
}

/** Извлечь chatId из UUID, query или полной ссылки ЛК WB/Ozon.
 *  Важно: не резать префикс `1:` у наших внутренних id WB. */
function extractChatId(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  try {
    if (/chatId=/i.test(s)) {
      const q = s.includes('://') ? new URL(s).searchParams : new URLSearchParams(s.split('?')[1] || s)
      const id = (q.get('chatId') || q.get('chatid') || '').trim()
      if (id) return id
    }
    if (/[?&]id=/i.test(s) && /ozon/i.test(s)) {
      return new URL(s).searchParams.get('id') || ''
    }
  } catch { /* ignore */ }
  // Уже наш id (в т.ч. `1:uuid`) — возвращаем как есть
  return s
}

/** Сравнение id: `1:uuid` ↔ `uuid` (ссылка из ЛК WB без префикса). */
function chatIdMatches(a, b) {
  const x = String(a || '').trim()
  const y = String(b || '').trim()
  if (!x || !y) return false
  if (x === y) return true
  const bare = (v) => (v.includes(':') ? v.slice(v.lastIndexOf(':') + 1) : v)
  return bare(x) === bare(y)
}

function cxChatShareUrl(item) {
  const id = item?.id
  if (!id) return ''
  const mp = chatMp(item)
  const base = typeof window !== 'undefined' ? window.location.origin : 'https://cxvo.ru'
  return `${base}/chats?chat=${encodeURIComponent(id)}&mp=${encodeURIComponent(mp)}`
}

async function copyCxChatLink() {
  const url = cxChatShareUrl(chatItem.value)
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    showToast('Ссылка для коллеги скопирована', 'ok')
  } catch {
    showToast(url, 'ok')
  }
}

async function findChatByPaste() {
  const id = extractChatId(chatFindQ.value)
  if (!id) {
    showToast('Вставьте chatId или ссылку из ЛК WB', 'error')
    return
  }
  let mp = 'wb'
  const raw = chatFindQ.value || ''
  if (/ozon/i.test(raw)) mp = 'ozon'
  else if (/yandex|market\.yandex/i.test(raw)) mp = 'ym'
  await router.replace({ query: { ...route.query, chat: id, mp } })
  await openChatFromQuery(id)
  showToast('Чат открыт', 'ok')
}

function openReturnFromChat(claimId) {
  if (!claimId) return
  const d = chatContext.value?.specific?.claim
  const mp = (d?.mp || chatMp(chatItem.value) || currentMp.value || 'wb')
  const q = { claim: claimId, mp }
  if (d?.claim_id === claimId && d?.is_archive) q.tab = 'archive'
  else if (['Одобрено', 'Отказ', 'Закрыта', 'Нет в API WB', 'Отклонено', 'Отменено', 'Отменена'].includes(d?.status_label)) q.tab = 'archive'
  router.push({ path: '/returns', query: q })
}

function openReshipmentFromChat(reqId) {
  if (!reqId) return
  router.push({ path: '/reshipment', query: { id: String(reqId) } })
}

function claimStatusClass(label) {
  if (label === 'Одобрено') return 'text-emerald-600'
  if (['Отказ', 'Закрыта', 'Нет в API WB', 'Отклонено', 'Отменено', 'Отменена'].includes(label)) return 'text-slate-600'
  return 'text-orange-700'
}

const claimDetail = ref(null)
const claimDetailLoading = ref(false)
const claimDeciding = ref(false)
const claimRejectOpen = ref(false)
const claimRejectComment = ref('')
const claimRejectReason = ref('')
const claimRejectAction = ref('')
const claimDecisionError = ref('')

const claimPrimaryActions = computed(() =>
  (claimDetail.value?.actions || []).filter(a => a.primary)
)
const claimRejectReasons = computed(() => {
  const a = claimPrimaryActions.value.find(x => x.action === claimRejectAction.value || x.kind === 'reject')
  return a?.reason_types || []
})

async function loadClaimDetailForChat(claimId) {
  claimDetail.value = null
  claimDecisionError.value = ''
  claimRejectOpen.value = false
  if (!claimId) return
  const d = chatContext.value?.specific?.claim
  const mp = d?.mp || chatMp(chatItem.value) || 'wb'
  if (!['wb', 'ym', 'ozon'].includes(mp)) return
  claimDetailLoading.value = true
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claimId)}?mp=${mp}`)
    if (r.ok) claimDetail.value = await r.json()
  } catch (e) {
    claimDecisionError.value = e.message || 'Ошибка загрузки'
  } finally {
    claimDetailLoading.value = false
  }
}

function onChatClaimAction(a) {
  claimDecisionError.value = ''
  if (a.kind === 'reject' || a.action === 'rejectcustom' || a.action === 'DECLINE_REFUND') {
    claimRejectOpen.value = true
    claimRejectAction.value = a.action
    claimRejectReason.value = (a.reason_types || [])[0]?.value || ''
    return
  }
  const mp = claimDetail.value?.mp || chatContext.value?.specific?.claim?.mp || 'wb'
  const name = mp === 'ym' ? 'ЯМ' : mp === 'ozon' ? 'Ozon' : 'WB'
  if (!confirm(`${a.label}?\n\nРешение уйдёт в ${name} сразу.`)) return
  submitChatClaimDecision(a.action, '')
}

function submitChatReject() {
  const mp = claimDetail.value?.mp || chatContext.value?.specific?.claim?.mp || 'wb'
  const action = claimRejectAction.value || (mp === 'wb' ? 'rejectcustom' : 'DECLINE_REFUND')
  const minLen = mp === 'wb' ? 10 : 5
  if (String(claimRejectComment.value || '').trim().length < minLen) {
    claimDecisionError.value = `Укажите причину отказа (от ${minLen} символов)`
    return
  }
  submitChatClaimDecision(action, claimRejectComment.value, claimRejectReason.value)
}

async function submitChatClaimDecision(action, comment, reason = '') {
  const claimId = chatContext.value?.specific?.claim?.claim_id
  const mp = claimDetail.value?.mp || chatContext.value?.specific?.claim?.mp || chatMp(chatItem.value) || 'wb'
  if (!claimId || !action) return
  claimDeciding.value = true
  claimDecisionError.value = ''
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claimId)}/decision?mp=${mp}`, {
      method: 'POST',
      body: JSON.stringify({ action, comment: comment || '', reason: reason || '' }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.detail || d.error || 'Не удалось отправить решение')
    claimRejectOpen.value = false
    showToast('Решение отправлено', 'ok')
    await loadClaimDetailForChat(claimId)
    if (chatContext.value?.specific?.claim) {
      chatContext.value.specific.claim.status_label = d.status_label || claimDetail.value?.status_label
      chatContext.value.specific.claim.status = d.status || claimDetail.value?.status
    }
  } catch (e) {
    claimDecisionError.value = e.message || 'Ошибка'
  } finally {
    claimDeciding.value = false
  }
}

function wbChatUrl(chatId) {
  const uuid = chatId?.split(':').slice(1).join(':') || chatId || ''
  return `https://seller.wildberries.ru/chat-with-clients?chatId=${uuid}`
}

function ymChatType(item) {
  if (chatMp(item) !== 'ym') return ''
  const t = String(item?.context_type || item?.sku || '').toUpperCase()
  if (['DIRECT', 'RETURN', 'ORDER'].includes(t)) return t
  return ''
}
function ymChatTypeLabel(item) {
  const t = ymChatType(item)
  if (t === 'DIRECT') return 'Вопрос по товару'
  if (t === 'RETURN') return 'Возврат'
  if (t === 'ORDER') return 'Заказ'
  return t
}
function ymChatTypeShort(item) {
  const t = ymChatType(item)
  if (t === 'DIRECT') return 'Товар'
  if (t === 'RETURN') return 'Возврат'
  if (t === 'ORDER') return 'Заказ'
  return t
}
function listSubtitle(item) {
  const mp = chatMp(item)
  if (mp === 'ym') {
    const t = ymChatTypeLabel(item)
    return [t, item.sku, item.order_number].filter(Boolean).join(' · ') || 'Яндекс Маркет'
  }
  if (mp === 'ozon') {
    const contact = String(item.contact || '')
    const art = item.sku && contact !== item.sku ? item.sku : ''
    const ord = item.order_number
      ? (contact.includes(item.order_number) ? '' : `Заказ ${item.order_number}`)
      : ''
    return [art, ord].filter(Boolean).join(' · ') || 'Ozon'
  }
  return item.sku || item.nm_id || '—'
}
function chatHeaderTitle(item) {
  const mp = chatMp(item)
  if (mp === 'ym') {
    // Артикул важнее ярлыка типа — иначе вместо карточки «Вопрос по товару»
    if (item.sku) return item.sku
    return item.contact && item.contact !== 'Покупатель'
      ? item.contact
      : (ymChatTypeLabel(item) || 'Чат Яндекс Маркет')
  }
  if (mp === 'ozon') {
    // Артикул в заголовке, если уже резолвили; иначе заказ / подпись
    return item.sku || item.contact || item.order_number || 'Чат Ozon'
  }
  return item.sku || (item.nm_id ? `Арт. ${item.nm_id}` : 'Товар без артикула')
}
function previewBody(text) {
  const s = String(text || '').trim()
  if (!s) return '—'
  if (/^!\[[^\]]*\]\(https?:\/\/[^)]+\)$/.test(s) || (s.startsWith('![') && s.includes('http') && s.length < 400))
    return '📷 Фото'
  return s.replace(/!\[[^\]]*\]\(https?:\/\/[^)]+\)/g, '📷').trim() || '—'
}
function marketplaceChatLabel(item) {
  const mp = chatMp(item)
  if (mp === 'ozon') return 'Чат Ozon'
  if (mp === 'ym') return 'Чат ЯМ'
  return 'Чат WB'
}
function marketplaceChatUrl(item) {
  const mp = chatMp(item)
  if (mp === 'wb') return wbChatUrl(item.id)
  if (mp === 'ozon') {
    if (item?.cabinet_url) return item.cabinet_url
    const id = String(item?.id || '').trim()
    return id
      ? `https://seller.ozon.ru/app/messenger/?group=customers_v2&id=${encodeURIComponent(id)}`
      : 'https://seller.ozon.ru/app/messenger/?group=customers_v2'
  }
  if (mp === 'ym') {
    if (item?.cabinet_url) return item.cabinet_url
    const group = ymChatType(item) || 'DIRECT'
    const biz = '203987989'
    const camp = '146194774'
    const q = new URLSearchParams({ campaignId: camp, chatGroup: group })
    // Только внутренний id кабинета (0/43/uuid). Числовой API-id ломает страницу.
    const cab = String(item?.cabinet_chat_id || '').trim()
    if (cab.includes('/')) q.set('chatId', cab)
    return `https://partner.market.yandex.ru/business/${biz}/arbiter?${q}`
  }
  return ''
}

async function openChatFromQuery(chatId) {
  const id = extractChatId(chatId)
  if (!id) return
  tab.value = 'chats'
  const mpQ = String(route.query.mp || '').toLowerCase()
  const mp = ['wb', 'ozon', 'ym'].includes(mpQ) ? mpQ : 'wb'
  if (mp !== 'wb' && currentMp.value !== mp && currentMp.value !== 'all') {
    platformStore.setPlatform(mp)
  }
  if (!queueItems.value.length) await loadQueue()
  const found = queueItems.value.find(i => chatIdMatches(i.id, id))
  if (found) {
    await selectChat(found)
    return
  }
  // Чата нет в текущей очереди — подтянем карточку из API (в т.ч. резолв `uuid` → `1:uuid`)
  try {
    const r = await apiFetch(`${BASE}/chat-card/${mp}/${encodeURIComponent(id)}`)
    if (r.ok) {
      const d = await r.json()
      if (d?.id) {
        await selectChat({ ...d, item_type: 'chat', mp: d.mp || mp })
        return
      }
    }
  } catch { /* fallback stub */ }
  await selectChat({
    id, item_type: 'chat', mp,
    contact: 'Покупатель', sku: '', nm_id: null,
    body: '', last_ts: Date.now(), needs_answer: true,
  })
}

const BASE = CX_BASE
const PB   = '/api/v1/chat-playbook'
const platformStore = usePlatformStore()
const currentMp = computed(() => platformStore.platform || 'all')
const chatsMpLabel = computed(() => {
  const m = currentMp.value
  if (m === 'wb') return 'Wildberries'
  if (m === 'ozon') return 'Ozon'
  if (m === 'ym') return 'Яндекс Маркет'
  return 'WB · Ozon · Яндекс'
})

function chatMp(item) {
  return (item?.mp || 'wb').toLowerCase()
}
function chatMessagesPath(item) {
  const mp = chatMp(item)
  return `${BASE}/chat-messages/${mp}/${encodeURIComponent(item.id)}`
}
function chatSendPath(item) {
  const mp = chatMp(item)
  return `${BASE}/${mp}/chats/${encodeURIComponent(item.id)}/message`
}
function chatSendFilesPath(item) {
  const mp = chatMp(item)
  return `${BASE}/${mp}/chats/${encodeURIComponent(item.id)}/message-with-files`
}

// ── Вкладки: переписка / настройка автоответов ──────────────────────────────
const tab        = ref('chats')
const pbThemes   = ref([])
/** Темы доотправки WB/ЯМ — на Ozon бот их не использует (→ возврат). */
const OZON_HIDDEN_THEME_KEYS = new Set([
  'nekomplekt', 'soglasie', 'ssylka', 'status_zayavki', 'otkaz_dootpravki',
  'nevozvratny',
])
const OZON_THEME_UI = {
  dengi: {
    note: 'Деньгами управляет Ozon и банк. Бот не обещает сроки от продавца.',
  },
  srok_hraneniya: {
    note: 'Сроком хранения управляет Ozon, не продавец.',
    when: 'Продлить хранение на ПВЗ Ozon — направляем в поддержку Ozon.',
  },
  dostavka_wb: {
    name: 'Где мой заказ / сроки доставки',
    when: 'Сдвиг срока, статус доставки, «когда приедет» на Ozon.',
    note: 'Сроки формирует Ozon. Бот направляет в карточку заказа и поддержку Ozon.',
  },
  cheque: {
    when: 'Где скачать чек по заказу Ozon.',
  },
  teh_sboi: {
    note: 'Техсбой на стороне Ozon — в поддержку площадки.',
  },
  vozvrat: {
    note: 'На Ozon: раздел «Заказы» → «Вернуть». Доотправки деталей нет.',
  },
  brak: {
    note: 'На Ozon: «Заказы» → «Вернуть» → причина «брак», лучше с фото.',
  },
  zapchasti: {
    name: 'Насадки (сменные)',
    when: 'Где купить сменные насадки или тряпку к швабре.',
    note: 'Только насадки — ссылка на карточку Ozon, если включено «в наличии». Заглушки — тема «Заглушки и крепёж».',
  },
  krepezh: {
    name: 'Заглушки и крепёж',
    when: 'Заглушки сливные, защёлки, пробки, мелкий крепёж отдельно.',
    note: 'Отдельно не продаём. Не путать с насадками.',
  },
  return_expired: {
    note: 'Не давить «Заказы» — вести в поддержку Ozon. Решение за площадкой.',
  },
}
const YM_THEME_UI = {
  dengi: {
    note: 'Деньгами управляет Яндекс Маркет и банк. Бот не обещает сроки от продавца.',
  },
  srok_hraneniya: {
    note: 'Сроком хранения управляет Яндекс Маркет, не продавец.',
    when: 'Продлить хранение на ПВЗ ЯМ — направляем в поддержку Яндекс Маркета.',
  },
  dostavka_wb: {
    name: 'Где мой заказ / сроки доставки',
    when: 'Сдвиг срока, статус доставки, «когда приедет» на Яндекс Маркете.',
    note: 'Сроки формирует Яндекс Маркет. Бот направляет в карточку заказа и поддержку площадки.',
  },
  cheque: {
    when: 'Где скачать чек по заказу Яндекс Маркета.',
  },
  teh_sboi: {
    note: 'Техсбой на стороне Яндекс Маркета — в поддержку площадки.',
  },
  vozvrat: {
    note: 'На ЯМ: заказ в кабинете → оформить возврат. Доотправку детали предлагаем в чате.',
  },
  brak: {
    note: 'На ЯМ: можно предложить замену детали (если товар в списке) или возврат через заказ.',
  },
  zapchasti: {
    name: 'Насадки (сменные)',
    when: 'Где купить сменные насадки или тряпку к швабре.',
    note: 'На ЯМ отдельной карточки насадок нет — подсказка поиска (если «в наличии»). Заглушки — тема «Заглушки и крепёж».',
  },
  krepezh: {
    name: 'Заглушки и крепёж',
    when: 'Заглушки сливные, защёлки, пробки, мелкий крепёж отдельно.',
    note: 'Отдельно не продаём. Не путать с насадками.',
  },
  return_expired: {
    note: 'Вести в поддержку Яндекс Маркета. Решение за площадкой.',
  },
}
const WB_THEME_UI = {
  return_expired: {
    note: 'Вести в поддержку Wildberries (профиль → «Задать вопрос»). Решение за площадкой.',
  },
  zapchasti: {
    name: 'Сменные насадки',
    when: 'Где купить сменные насадки или тряпку к швабре.',
    note: 'Только сменные насадки. Заглушки, защёлки и мелкий крепёж — отдельная тема ниже. Наличие — переключатель в настройках.',
  },
  krepezh: {
    name: 'Заглушки и крепёж',
    when: 'Заглушки, защёлки, пробки, сливные элементы, мелкий крепёж отдельно.',
    note: 'Это не насадки. Отдельно обычно не продаём.',
  },
}
const visiblePbThemes = computed(() => {
  const list = pbThemes.value || []
  if (pbMp.value === 'ozon') {
    return list.filter(t => !OZON_HIDDEN_THEME_KEYS.has(t.theme_key))
  }
  // WB и ЯМ: доотправка в чатах; невозвратность сушилок — только WB
  return list.filter(t => t.theme_key !== 'nevozvratny')
})

function hiddenTheme(themeKey) {
  return (pbThemes.value || []).find(t => t.theme_key === themeKey) || null
}

function themeDisplayName(t) {
  if (pbMp.value === 'wb' && WB_THEME_UI[t.theme_key]?.name) {
    return WB_THEME_UI[t.theme_key].name
  }
  if (pbMp.value === 'ozon' && OZON_THEME_UI[t.theme_key]?.name) {
    return OZON_THEME_UI[t.theme_key].name
  }
  if (pbMp.value === 'ym' && YM_THEME_UI[t.theme_key]?.name) {
    return YM_THEME_UI[t.theme_key].name
  }
  return t.name
}
function themeDisplayWhen(t) {
  if (pbMp.value === 'wb' && WB_THEME_UI[t.theme_key]?.when) {
    return WB_THEME_UI[t.theme_key].when
  }
  if (pbMp.value === 'ozon' && OZON_THEME_UI[t.theme_key]?.when) {
    return OZON_THEME_UI[t.theme_key].when
  }
  if (pbMp.value === 'ym' && YM_THEME_UI[t.theme_key]?.when) {
    return YM_THEME_UI[t.theme_key].when
  }
  return t.when_text
}
function themeDisplayNote(t) {
  if (pbMp.value === 'wb' && WB_THEME_UI[t.theme_key]?.note) {
    return WB_THEME_UI[t.theme_key].note
  }
  if (pbMp.value === 'ozon' && OZON_THEME_UI[t.theme_key]?.note) {
    return OZON_THEME_UI[t.theme_key].note
  }
  if (pbMp.value === 'ym' && YM_THEME_UI[t.theme_key]?.note) {
    return YM_THEME_UI[t.theme_key].note
  }
  if (pbMp.value === 'wb' && t.theme_key === 'vozvrat') {
    const base = t.note || ''
    const addon = nonreturnableCats.value.length
      ? `Для категорий ${nonreturnableCatsLabel.value} система автоматически подставляет текст о невозвратности WB.`
      : 'Для невозвратных категорий WB система автоматически подставляет отдельный текст.'
    return base ? `${base} ${addon}` : addon
  }
  // Не показываем WB-заметки на Ozon; на ЯМ доотправка своя — оставляем заметки
  if (pbMp.value === 'ozon' && /WB|Wildberries|доотправ/i.test(t.note || '')) {
    return ''
  }
  if (pbMp.value === 'ym' && /WB|Wildberries/i.test(t.note || '')) {
    return ''
  }
  return t.note || ''
}
const pbSettings = ref([])
const pbLoading  = ref(false)
const pbSection  = ref('settings')
const nonreturnableCats = ref([])
const pbSections = [
  { id: 'settings', label: 'Настройки' },
  { id: 'templates', label: 'Шаблоны' },
  { id: 'check', label: 'Проверка' },
]
/** Площадка для per-mp настроек — из главного сайдбара */
const pbMp = computed(() => {
  const m = (currentMp.value || 'wb').toLowerCase()
  if (m === 'ozon' || m === 'ym' || m === 'wb') return m
  return 'wb'
})
const pbMpLabel = computed(() => {
  if (pbMp.value === 'ozon') return 'Ozon'
  if (pbMp.value === 'ym') return 'Яндекс Маркет'
  return 'Wildberries'
})
const nonreturnableCatsLabel = computed(() =>
  nonreturnableCats.value.length ? nonreturnableCats.value.join(', ') : 'не выбраны'
)

/** Акценты UI под площадку из сайдбара (как в App.vue) */
const mpUi = computed(() => {
  const m = (currentMp.value || 'all').toLowerCase()
  if (m === 'ozon') return {
    iconBg: 'bg-blue-50 text-blue-600',
    toggleOn: 'bg-blue-500',
    btn: 'bg-blue-600 hover:bg-blue-700 text-white',
    btnSoft: 'bg-white border border-gray-200 text-blue-700 hover:bg-blue-50',
    ring: 'focus:ring-blue-300',
    accent: 'accent-blue-600',
    soft: 'bg-blue-50 border-blue-200 text-blue-900',
    softBorder: 'border-blue-200 bg-blue-50',
    text: 'text-blue-600 hover:text-blue-800',
    textMuted: 'text-blue-700/70',
    textStrong: 'text-blue-800',
    bubble: 'bg-blue-600 text-white',
    selected: 'bg-blue-50 border-l-[3px] border-l-blue-500',
    spin: 'border-blue-500',
    note: 'text-blue-700 bg-blue-50',
    lastRun: 'bg-blue-50/70 border-blue-100 text-blue-900',
    chipOn: 'border-blue-400 bg-blue-50',
    hoverLink: 'hover:border-blue-300 hover:text-blue-700',
  }
  if (m === 'ym') return {
    iconBg: 'bg-orange-50 text-orange-600',
    toggleOn: 'bg-orange-500',
    btn: 'bg-orange-500 hover:bg-orange-600 text-white',
    btnSoft: 'bg-white border border-gray-200 text-orange-700 hover:bg-orange-50',
    ring: 'focus:ring-orange-300',
    accent: 'accent-orange-500',
    soft: 'bg-orange-50 border-orange-200 text-orange-900',
    softBorder: 'border-orange-200 bg-orange-50',
    text: 'text-orange-600 hover:text-orange-800',
    textMuted: 'text-orange-700/70',
    textStrong: 'text-orange-800',
    bubble: 'bg-orange-500 text-white',
    selected: 'bg-orange-50 border-l-[3px] border-l-orange-500',
    spin: 'border-orange-500',
    note: 'text-orange-700 bg-orange-50',
    lastRun: 'bg-orange-50/70 border-orange-100 text-orange-900',
    chipOn: 'border-orange-400 bg-orange-50',
    hoverLink: 'hover:border-orange-300 hover:text-orange-700',
  }
  // wb и «все»
  return {
    iconBg: 'bg-purple-50 text-purple-600',
    toggleOn: 'bg-purple-500',
    btn: 'bg-purple-600 hover:bg-purple-700 text-white',
    btnSoft: 'bg-white border border-gray-200 text-purple-700 hover:bg-purple-50',
    ring: 'focus:ring-purple-300',
    accent: 'accent-purple-600',
    soft: 'bg-purple-50 border-purple-200 text-purple-900',
    softBorder: 'border-purple-200 bg-purple-50',
    text: 'text-purple-600 hover:text-purple-800',
    textMuted: 'text-purple-700/70',
    textStrong: 'text-purple-800',
    bubble: 'bg-purple-600 text-white',
    selected: 'bg-purple-50 border-l-[3px] border-l-purple-500',
    spin: 'border-purple-500',
    note: 'text-purple-700 bg-purple-50',
    lastRun: 'bg-purple-50/70 border-purple-100 text-purple-900',
    chipOn: 'border-purple-400 bg-purple-50',
    hoverLink: 'hover:border-purple-300 hover:text-purple-700',
  }
})

const settingsMap = computed(() => {
  const m = {}
  for (const s of pbSettings.value) m[s.key] = s
  return m
})

function settingVal(key, fallback = '') {
  const s = settingsMap.value[key]
  return s ? (s.value ?? fallback) : fallback
}
function settingOn(key) {
  return String(settingVal(key, 'false')).toLowerCase() === 'true'
}
function settingNum(key, fallback = 0) {
  const n = Number(settingVal(key, fallback))
  return Number.isFinite(n) ? n : fallback
}
function mpCtrlKey(key) {
  return `${key}_${pbMp.value}`
}
function mpSettingVal(key, fallback = '') {
  const k = mpCtrlKey(key)
  if (settingsMap.value[k]) return settingVal(k, fallback)
  // fallback legacy без суффикса (старые WB-настройки)
  if (pbMp.value === 'wb') return settingVal(key, fallback)
  return fallback
}
function mpSettingOn(key) {
  return String(mpSettingVal(key, 'false')).toLowerCase() === 'true'
}
function mpSettingNum(key, fallback = 0) {
  const n = Number(mpSettingVal(key, fallback))
  return Number.isFinite(n) ? n : fallback
}
async function patchSetting(key, value) {
  let s = settingsMap.value[key]
  if (!s) {
    // создаём локально — backend UPSERT
    s = { key, value: String(value), descr: '' }
    pbSettings.value = [...pbSettings.value, s]
  }
  const prev = s.value
  s.value = String(value)
  try {
    await apiFetch(`${PB}/settings/${encodeURIComponent(key)}`, {
      method: 'PUT', body: JSON.stringify({ value: s.value }),
    })
  } catch {
    s.value = prev
    showToast('Не удалось сохранить настройку', 'error')
  }
}
function toggleSetting(key) {
  patchSetting(key, settingOn(key) ? 'false' : 'true')
}
function toggleMpSetting(key) {
  patchSetting(mpCtrlKey(key), mpSettingOn(key) ? 'false' : 'true')
}
function setSettingVal(key, value) {
  patchSetting(key, value ?? '')
}
function setSettingNum(key, value, save = true) {
  const next = String(value)
  const s = settingsMap.value[key]
  if (s) s.value = next
  if (save) patchSetting(key, next)
}
function setMpSettingNum(key, value, save = true) {
  setSettingNum(mpCtrlKey(key), value, save)
}
function setMpSettingVal(key, value) {
  patchSetting(mpCtrlKey(key), value ?? '')
}

const BOT = '/api/v1/chat-bot'
const botStatus   = ref(null)
const botLog      = ref([])
const botRunning  = ref(false)
const botLastRun  = ref(null)
const botLogFilter = ref('all')
const botLogFilters = [
  { key: 'all', label: 'Все' },
  { key: 'auto', label: 'Готов ответить' },
  { key: 'draft', label: 'Черновик' },
  { key: 'human', label: 'Менеджеру' },
]

const reviewItems   = ref([])
const reviewLoading = ref(false)
const reviewCount   = ref(0)

function zoneLabel(z) {
  if (z === 'auto') return 'Готов ответить'
  if (z === 'draft') return 'Черновик'
  return 'Менеджеру'
}
function zoneBadgeClass(z) {
  if (z === 'auto') return 'px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium'
  if (z === 'draft') return 'px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 font-medium'
  return 'px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200 font-medium'
}
function themeLabel(t) {
  const map = {
    nekomplekt: 'Некомплект', money: 'Деньги/возврат', thanks: 'Благодарность',
    status: 'Статус отправки', greeting: 'Приветствие', other: 'Другое',
  }
  return map[t] || t || '—'
}

async function loadBotStatus() {
  try {
    const r = await apiFetch(`${BOT}/status?mp=${encodeURIComponent(pbMp.value || 'wb')}`)
    botStatus.value = await r.json()
  } catch { /* тихо */ }
}

async function loadBotLog() {
  try {
    const r = await apiFetch(`${BOT}/log?limit=60&mp=${encodeURIComponent(pbMp.value || '')}`)
    const d = await r.json()
    let items = d.items || []
    if (botLogFilter.value === 'auto') items = items.filter(i => i.zone === 'auto')
    else if (botLogFilter.value === 'draft') items = items.filter(i => i.zone === 'draft')
    else if (botLogFilter.value === 'human') items = items.filter(i => i.need_human || i.zone === 'human')
    botLog.value = items
  } catch { showToast('Не удалось загрузить журнал бота', 'error') }
  await loadBotStatus()
}

async function runBotOnce() {
  botRunning.value = true
  try {
    const mp = pbMp.value || 'wb'
    const r = await apiFetch(`${BOT}/run-once?limit=15&mp=${encodeURIComponent(mp)}`, { method: 'POST' })
    const d = await r.json()
    botLastRun.value = d
    if (!d.ok) {
      showToast(d.reason || 'Бот не запустился', 'error')
    } else if (!d.processed) {
      showToast('Новых чатов для разбора нет — все уже прогнаны или нет свежих', 'ok')
    } else {
      const z = d.by_zone || {}
      const shadowNote = d.shadow ? ' · только журнал' : ''
      showToast(`Готово (${mp}): ${d.processed} · авто ${z.auto||0} · черновик ${z.draft||0} · менеджеру ${z.human||0}${shadowNote}`, 'ok')
    }
    await loadBotLog()
    await loadReviewQueue(true)
  } catch { showToast('Ошибка прогона бота', 'error') }
  finally { botRunning.value = false }
}

async function clearBotLog() {
  if (!confirm('Очистить журнал и разрешить прогнать те же чаты заново?')) return
  try {
    const r = await apiFetch(`${BOT}/log/clear`, { method: 'POST' })
    const d = await r.json()
    botLog.value = []
    botLastRun.value = null
    showToast(`Журнал очищен (${d.deleted_log || 0})`, 'ok')
    await loadBotStatus()
    await loadReviewQueue(true)
  } catch { showToast('Не удалось очистить', 'error') }
}

async function loadReviewQueue(silent = false) {
  if (!silent) reviewLoading.value = true
  try {
    const mp = currentMp.value || 'all'
    const r = await apiFetch(`${BOT}/review-queue?limit=80&mp=${encodeURIComponent(mp)}`)
    const d = await r.json()
    reviewItems.value = d.items || []
    reviewCount.value = d.count || reviewItems.value.length
  } catch {
    if (!silent) showToast('Не удалось загрузить очередь проверки', 'error')
  } finally {
    reviewLoading.value = false
  }
}

async function loadPlaybook() {
  pbLoading.value = true
  try {
    const r = await apiFetch(PB)
    const d = await r.json()
    pbThemes.value   = d.themes   || []
    pbSettings.value = d.settings || []
    await loadNonreturnableCats()
    // Журнал бота нужен только админу (вкладка «Проверка»)
    if (canEditChatTemplates.value) await loadBotLog()
  } catch { showToast('Не удалось загрузить автоответы', 'error') }
  finally { pbLoading.value = false }
}

async function loadNonreturnableCats() {
  if (pbMp.value !== 'wb') {
    nonreturnableCats.value = []
    return
  }
  try {
    const r = await apiFetch('/api/v1/nonreturnable-cats')
    const d = await r.json()
    nonreturnableCats.value = (d.items || [])
      .filter(item => item.enabled)
      .map(item => String(item.category_1 || '').trim())
      .filter(Boolean)
  } catch {
    nonreturnableCats.value = []
  }
}

function emptyVariantHint(theme) {
  if (theme?.zone === 'human') {
    return `Для ${pbMpLabel.value} текст не обязателен: эта тема сразу уходит менеджеру.`
  }
  if (theme?.zone === 'draft') {
    return `Нет текста для ${pbMpLabel.value}. Менеджеру придётся писать ответ вручную или опираться на ИИ-черновик.`
  }
  return `Нет текста для ${pbMpLabel.value}. Добавьте вариант кнопкой ниже — иначе бот попробует запасной ответ (ИИ/FAQ, если он есть).`
}

function openPlaybook() {
  if (!canViewChatTemplates.value) return
  tab.value = 'playbook'
  if (!canEditChatTemplates.value) pbSection.value = 'templates'
  if (!pbThemes.value.length) loadPlaybook()
  else if (canEditChatTemplates.value) loadBotLog()
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
  if (!isAdmin.value) return
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
  const mp = String(ch.mp || 'wb').toLowerCase()
  // чата нет в очереди (например, старше 30 дней) — открываем по минимальным данным
  selectChat({
    id: ch.chat_id, item_type: 'chat', mp: ['wb', 'ozon', 'ym'].includes(mp) ? mp : 'wb',
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
    const mp = currentMp.value === 'all' ? 'all' : currentMp.value
    const r = await apiFetch(`${TR}/archive?mp=${encodeURIComponent(mp)}`)
    const d = await r.json()
    arItems.value = d.items || []
    arTotal.value = d.total || 0
  } catch { showToast('Не удалось загрузить архив', 'error') }
  finally { arLoading.value = false }
}

function openArchive() { tab.value = 'archive'; loadArchive() }

async function restoreChat(a) {
  try {
    await apiFetch(`${TR}/restore`, {
      method: 'POST',
      body: JSON.stringify({ chat_id: a.chat_id, mp: a.mp || 'wb' }),
    })
    arItems.value = arItems.value.filter(x => x.chat_id !== a.chat_id)
    arTotal.value = Math.max(0, arTotal.value - 1)
    showToast('Чат вернулся в работу', 'ok')
  } catch { showToast('Не удалось вернуть', 'error') }
}

function groupedVariants(theme) {
  const out = {}
  const want = pbMp.value === 'ozon' ? 'ozon' : pbMp.value === 'ym' ? 'ym' : 'wb'
  for (const v of (theme.variants || [])) {
    const vmp = (v.mp || '').toLowerCase()
    // mp='' — явно общий шаблон: показываем его на любой площадке.
    if (vmp && vmp !== want) continue
    (out[v.branch_key || ''] ||= []).push(v)
  }
  if (pbMp.value === 'wb' && theme.theme_key === 'vozvrat') {
    const nonreturnable = hiddenTheme('nevozvratny')
    for (const v of (nonreturnable?.variants || [])) {
      const vmp = (v.mp || '').toLowerCase()
      if (vmp && vmp !== want) continue
      const clone = {
        ...v,
        branch_key: '__wb_nonreturnable__',
        branch_label: 'WB · невозвратный товар',
      }
      ;(out[clone.branch_key] ||= []).push(clone)
    }
  }
  return out
}

function variantMpLabel(mp) {
  const value = String(mp || '').toLowerCase()
  if (!value) return 'Все МП'
  if (value === 'ozon') return 'Ozon'
  if (value === 'ym') return 'ЯМ'
  return 'WB'
}

function variantMpBadgeClass(mp) {
  const value = String(mp || '').toLowerCase()
  if (!value) return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (value === 'ozon') return 'bg-blue-50 text-blue-700 border-blue-200'
  if (value === 'ym') return 'bg-orange-50 text-orange-700 border-orange-200'
  return 'bg-purple-50 text-purple-700 border-purple-200'
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
    const mp = pbMp.value === 'ozon' ? 'ozon' : pbMp.value === 'ym' ? 'ym' : 'wb'
    const r = await apiFetch(`${PB}/variants`, {
      method: 'POST',
      body: JSON.stringify({
        theme_key: theme.theme_key, branch_key: branchKey || '',
        branch_label: branchLabel || '', body: 'Новый вариант ответа — впишите текст',
        mp,
      }),
    })
    const d = await r.json()
    theme.variants.push({
      id: d.id, theme_key: theme.theme_key, branch_key: branchKey || '',
      branch_label: branchLabel || '', body: 'Новый вариант ответа — впишите текст',
      active: true, mp,
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
  // «Новые» = новое сообщение именно клиента (ждёт ответа), не наш ответ / не system МП
  if (item.item_type !== 'chat') return false
  if (!item.needs_answer) return false
  if (senderKind(item) === 'system') return false
  const lastView = chatLastViewed.value[item.id] || 0
  return (item.last_ts || 0) > lastView
}

function senderKind(item) {
  const s = String(item?.last_sender || '').toLowerCase()
  if (!s) return 'unknown'
  if (['customer', 'buyer', 'client'].includes(s)) return 'customer'
  if (['seller', 'courier'].includes(s)) return 'seller'
  if (['system', 'notificationuser', 'notification', 'chatbot', 'support', 'ozon'].includes(s)) return 'system'
  return 'unknown'
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

/** Возраст чата: age_sec из API или расчёт по last_ts (deep-link / chat-card). */
function chatAgeSec(item) {
  if (item?.age_sec != null && Number.isFinite(Number(item.age_sec))) {
    return Math.max(0, Number(item.age_sec))
  }
  const ts = Number(item?.last_ts || 0)
  if (!ts) return null
  const ms = ts > 1e12 ? ts : ts * 1000
  return Math.max(0, Math.round((Date.now() - ms) / 1000))
}

// ── Контекст клиента ─────────────────────────────────────────────────────────
const chatContext        = ref(null)
const chatContextLoading = ref(false)

async function loadChatContext(item, requestId) {
  if (!isCurrentChatSelection(requestId, item?.id)) return
  chatContext.value = null
  if (!item?.id) return
  const mp = chatMp(item)
  if (!['wb', 'ozon', 'ym'].includes(mp)) return
  chatContextLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/chat-context/${mp}/${encodeURIComponent(item.id)}`)
    const d = await r.json()
    if (!isCurrentChatSelection(requestId, item.id)) return
    if (d.specific?.claim || d.specific?.review || d.reviews?.length || d.questions?.length || d.claims?.length) {
      chatContext.value = d
    }
  } catch { /* silent */ } finally {
    if (isCurrentChatSelection(requestId, item.id)) chatContextLoading.value = false
  }
}

// ── Фильтры ──────────────────────────────────────────────────────────────────
const chatFilter      = ref('')   // '' | pending | review | new
const senderFilter    = ref('all') // all | customer | system | unknown
// Раскрытие карточки возврата — подтягиваем живые actions с площадки
const expandedClaimId = ref('')
const expandedReshipId = ref(null)
watch(expandedClaimId, (id) => {
  claimRejectOpen.value = false
  claimDecisionError.value = ''
  if (id) loadClaimDetailForChat(id)
  else claimDetail.value = null
})

const unreadChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && (currentMp.value === 'all' || chatMp(i) === currentMp.value) && hasNewMsg(i)).length
)
const pendingChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && (currentMp.value === 'all' || chatMp(i) === currentMp.value) && i.needs_answer).length
)
const pendingCustomerCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && (currentMp.value === 'all' || chatMp(i) === currentMp.value) && i.needs_answer && senderKind(i) === 'customer').length
)
const pendingSystemCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && (currentMp.value === 'all' || chatMp(i) === currentMp.value) && i.needs_answer && senderKind(i) === 'system').length
)
const pendingUnknownCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && (currentMp.value === 'all' || chatMp(i) === currentMp.value) && i.needs_answer && senderKind(i) === 'unknown').length
)
const senderFilters = computed(() => [
  { key: 'all', label: 'Все', count: pendingChatCount.value },
  { key: 'customer', label: 'Клиент', count: pendingCustomerCount.value },
  { key: 'system', label: 'МП', count: pendingSystemCount.value },
  { key: 'unknown', label: '?', count: pendingUnknownCount.value },
])

function setChatFilter(key) {
  const next = chatFilter.value === key ? '' : key
  chatFilter.value = next
  if (next !== 'pending' || currentMp.value !== 'ozon') senderFilter.value = 'all'
  currentPage.value = 1
  if (next === 'review') loadReviewQueue(true)
}

// ── Очередь ──────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const perPage      = ref(50)
const currentPage  = ref(1)

const filteredItems = computed(() => {
  const mp = (currentMp.value || 'all').toLowerCase()
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'chat')
  // Жёстко держим площадку: иначе после закрытия/«На проверку» всплывают чужие МП
  if (mp !== 'all') {
    list = list.filter(i => chatMp(i) === mp)
  }

  if (chatFilter.value === 'pending') {
    list = list.filter(i => i.needs_answer)
    // Фильтр клиент/Ozon-оповещение — только для Ozon
    if (currentMp.value === 'ozon' && senderFilter.value !== 'all') {
      list = list.filter(i => senderKind(i) === senderFilter.value)
    }
  } else if (chatFilter.value === 'new') {
    list = list.filter(i => hasNewMsg(i))
  } else if (chatFilter.value === 'review') {
    const reviewForMp = (reviewItems.value || []).filter(r => {
      const rmp = (r.mp || 'wb').toLowerCase()
      return mp === 'all' || rmp === mp
    })
    const ids = new Set(reviewForMp.map(r => r.chat_id))
    const fromQueue = list.filter(i => ids.has(i.id))
    const have = new Set(fromQueue.map(i => i.id))
    const extras = reviewForMp
      .filter(r => !have.has(r.chat_id))
      .map(r => ({
        id: r.chat_id,
        item_type: 'chat',
        mp: (r.mp || mp || 'wb').toLowerCase(),
        contact: r.client_name || 'Покупатель',
        sku: r.good_name || '',
        nm_id: r.nm_id,
        body: r.client_message || '',
        needs_answer: !!r.needs_answer,
        last_ts: r.last_ts || 0,
        age_sec: r.last_ts ? Math.max(0, Math.round((Date.now() - Number(r.last_ts)) / 1000)) : null,
      }))
    list = [...fromQueue, ...extras]
  }

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

watch(currentMp, () => {
  senderFilter.value = 'all'
  loadQueue()
  if (isAdmin.value) loadReviewQueue(true)
  if (tab.value === 'archive') loadArchive()
  if (tab.value === 'playbook') loadNonreturnableCats()
})

watch(chatFilter, () => { currentPage.value = 1 })

// ── Чат: выбор и сообщения ───────────────────────────────────────────────────
const chatItem    = ref(null)
const chatMsgs    = ref([])
const chatLoading = ref(false)
const chatReply   = ref('')
const chatReplyEl = ref(null)
const chatSending = ref(false)
const chatAttachmentInput = ref(null)
const chatAttachments = ref([])
const chatSendWarning = ref(null)
const chatScroll  = ref(null)
const chatSuggest = ref(null)
const chatSuggestMiss = ref(null)
const chatSuggestLoading = ref(false)
const chatSuggestGenerating = ref(false)
const chatTemplates = ref([])
const chatTemplatesLoading = ref(false)
const chatTemplatesExpanded = ref(false)
const chatTemplateGreeting = ref('Здравствуйте!')
const chatTemplateSignature = ref('')
const productCardOpen = ref(false)
const productCardLoading = ref(false)
const productCard = ref(null)
let chatPollTimer = null
let chatSelectionRequest = 0

const productCardRequest = computed(() => {
  const mp = chatMp(chatItem.value)
  const chatId = String(chatItem.value?.id || '').trim()
  const article = String(chatItem.value?.sku || '').trim()
  if (['ym', 'ozon'].includes(mp)) {
    const markerOnly = ['DIRECT', 'RETURN', 'ORDER'].includes(article.toUpperCase())
    if (!chatId && !article) return null
    return { mp, article: markerOnly ? '' : article, nmId: null, chatId }
  }
  const nmId = Number(chatItem.value?.nm_id || 0)
  if (mp === 'wb' && Number.isFinite(nmId) && nmId > 0) {
    return { mp, article: '', nmId, chatId }
  }
  return null
})

function productDimensionsLine(dimensions) {
  if (!dimensions || dimensions.length == null || dimensions.width == null || dimensions.height == null) return ''
  return `${dimensions.length} × ${dimensions.width} × ${dimensions.height}`
}

async function toggleProductCard() {
  productCardOpen.value = !productCardOpen.value
  if (!productCardOpen.value || productCard.value || productCardLoading.value || !productCardRequest.value) return
  const selectedId = chatItem.value?.id
  productCardLoading.value = true
  try {
    const params = new URLSearchParams({ mp: productCardRequest.value.mp || 'wb' })
    if (productCardRequest.value.article) params.set('article', productCardRequest.value.article)
    if (productCardRequest.value.nmId) params.set('nm_id', String(productCardRequest.value.nmId))
    if (productCardRequest.value.chatId) params.set('chat_id', productCardRequest.value.chatId)
    const r = await apiFetch(`/api/v1/chat-bot/product-card?${params}`)
    const data = await r.json()
    if (chatItem.value?.id === selectedId) productCard.value = data
  } catch {
    if (chatItem.value?.id === selectedId) productCard.value = { status: 'unavailable' }
  } finally {
    if (chatItem.value?.id === selectedId) productCardLoading.value = false
  }
}

const chatAttachmentsAvailable = computed(() => chatAttachmentsSupported(chatMp(chatItem.value)))
const chatCanSend = computed(() => canSendChatContent(
  chatReply.value,
  chatAttachments.value,
  chatSending.value,
))

function fmtFileSize(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} КБ`
  return `${(value / (1024 * 1024)).toFixed(1)} МБ`
}

function chooseChatAttachments() {
  if (chatSending.value) return
  chatAttachmentInput.value?.click()
}

function onChatAttachmentsSelected(event) {
  const selected = Array.from(event?.target?.files || [])
  const existing = new Set(chatAttachments.value.map(item => item.id))
  let firstError = ''
  for (const file of selected) {
    const error = validateChatAttachment(file)
    if (error) {
      firstError ||= `${file.name}: ${error}`
      continue
    }
    const id = chatAttachmentKey(file)
    if (existing.has(id)) continue
    const additionError = validateChatAttachmentAddition(
      chatAttachments.value.map(item => item.file),
      file,
    )
    if (additionError) {
      firstError ||= additionError
      continue
    }
    existing.add(id)
    chatAttachments.value.push({
      id,
      file,
      previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : '',
    })
  }
  if (event?.target) event.target.value = ''
  if (firstError) showToast(firstError, 'error')
}

function removeChatAttachment(id) {
  const index = chatAttachments.value.findIndex(item => item.id === id)
  if (index < 0) return
  const [removed] = chatAttachments.value.splice(index, 1)
  if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl)
}

function clearChatAttachments() {
  chatAttachments.value.forEach(item => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  })
  chatAttachments.value = []
  if (chatAttachmentInput.value) chatAttachmentInput.value.value = ''
}

async function reconcileChatSend() {
  await fetchChatMsgs(true, chatSelectionRequest)
  showToast('История обновлена. Сверьте сообщения перед повторной отправкой', 'ok')
}

function isCurrentChatSelection(requestId, chatId) {
  return requestId === chatSelectionRequest
    && String(chatItem.value?.id || '') === String(chatId || '')
}

const visibleChatTemplates = computed(() => {
  const mp = chatMp(chatItem.value)
  let templates = chatTemplates.value || []
  if (mp === 'ozon') {
    templates = templates.filter(template => !OZON_HIDDEN_THEME_KEYS.has(template.theme_key))
  } else if (mp === 'ym') {
    templates = templates.filter(template => template.theme_key !== 'nevozvratny')
  }
  const suggestedTheme = chatSuggest.value?.theme || chatSuggestMiss.value?.theme || ''
  return [...templates].sort((a, b) => {
    if (suggestedTheme && a.theme_key === suggestedTheme && b.theme_key !== suggestedTheme) return -1
    if (suggestedTheme && b.theme_key === suggestedTheme && a.theme_key !== suggestedTheme) return 1
    return 0
  })
})

const compactChatTemplates = computed(() => visibleChatTemplates.value.slice(0, 2))
const hasMoreChatTemplates = computed(() => visibleChatTemplates.value.length > compactChatTemplates.value.length)

function normalizeChatTemplateText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function stripChatGreeting(value) {
  return String(value || '')
    .replace(/^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*/i, '')
    .trim()
}

function moscowDateKey(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(date)
}

function chatNeedsGreeting() {
  const today = moscowDateKey(new Date())
  return !chatMsgs.value.some(msg => isSeller(msg) && moscowDateKey(msg.createdAt) === today)
}

function polishChatTemplate(body) {
  const text = stripChatGreeting(body)
  if (!text) return ''
  const parts = []
  const greeting = String(chatTemplateGreeting.value || '').trim()
  if (greeting && chatNeedsGreeting()) parts.push(greeting)
  parts.push(text)
  let result = parts.join('\n\n').trim()

  const signature = String(chatTemplateSignature.value || '').trim()
  const shortWithoutSignature = /^(спасибо|благодар|ок|окей|хорошо|пожалуйста|рад\s+помочь|понял|принято)[\s!.❤🙏👍]*$/i.test(text)
  if (signature && text.length > 40 && !shortWithoutSignature && !result.includes(signature)) {
    result = `${result}\n\n${signature}`
  }
  return result
}

function isChatTemplateActive(template) {
  const reply = normalizeChatTemplateText(chatReply.value)
  const body = normalizeChatTemplateText(stripChatGreeting(template.body))
  if (!reply || !body) return false
  const sample = body.slice(0, Math.min(60, body.length))
  return reply.includes(sample)
}

function applyChatTemplate(template) {
  chatTemplatesExpanded.value = false
  chatReply.value = polishChatTemplate(template.body)
  nextTick(autoGrowReply)
}

async function loadChatTemplates(mp = 'wb', theme = '', requestId = chatSelectionRequest, chatId = chatItem.value?.id) {
  if (!isCurrentChatSelection(requestId, chatId)) return
  chatTemplatesLoading.value = true
  try {
    const query = new URLSearchParams({ mp: mp || 'wb' })
    if (theme) query.set('theme', theme)
    const response = await apiFetch(`${BOT}/chat-templates?${query}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    if (!isCurrentChatSelection(requestId, chatId)) return
    chatTemplates.value = data.templates || []
    chatTemplateGreeting.value = String(data.greeting_text || '').trim()
    chatTemplateSignature.value = String(data.signature || '').trim()
  } catch {
    if (isCurrentChatSelection(requestId, chatId)) chatTemplates.value = []
  } finally {
    if (isCurrentChatSelection(requestId, chatId)) chatTemplatesLoading.value = false
  }
}

function clearSuggest() {
  chatSuggest.value = null
  chatSuggestMiss.value = null
  chatReply.value = ''
  nextTick(autoGrowReply)
}

async function loadSuggest(chatId, mp = 'wb', requestId = chatSelectionRequest) {
  if (!isCurrentChatSelection(requestId, chatId)) return
  chatSuggest.value = null
  chatSuggestMiss.value = null
  chatSuggestLoading.value = true
  try {
    const q = `?mp=${encodeURIComponent(mp || 'wb')}`
    const r = await apiFetch(`${BOT}/suggest/${encodeURIComponent(chatId)}${q}`)
    const d = await r.json()
    if (!isCurrentChatSelection(requestId, chatId)) return
    if (d?.skip) {
      chatSuggest.value = null
      chatSuggestMiss.value = null
      return
    }
    if (d?.ok && d.reply_text) {
      chatSuggest.value = d
      chatSuggestMiss.value = null
      chatReply.value = String(d.reply_text || '').replace(/^\s+/, '')
      nextTick(autoGrowReply)
    } else if (d?.ok) {
      chatSuggest.value = null
      chatSuggestMiss.value = {
        reason: d.reason || 'Нет подходящего шаблона',
        can_generate: d.can_generate !== false,
        theme: d.theme,
        theme_name: d.theme_name,
        need_human: d.need_human === true,
      }
    } else {
      chatSuggestMiss.value = {
        reason: d?.reason || 'Не удалось подобрать ответ',
        can_generate: true,
      }
    }
  } catch {
    if (isCurrentChatSelection(requestId, chatId)) {
      chatSuggest.value = null
      chatSuggestMiss.value = {
        reason: 'Ошибка подбора шаблона',
        can_generate: true,
      }
    }
  } finally {
    if (isCurrentChatSelection(requestId, chatId)) chatSuggestLoading.value = false
  }
}

async function generateSuggestAi() {
  if (!chatItem.value?.id || chatSuggestGenerating.value) return
  const requestId = chatSelectionRequest
  const chatId = chatItem.value.id
  const mp = chatMp(chatItem.value)
  chatSuggestGenerating.value = true
  try {
    const q = `?mp=${encodeURIComponent(mp || 'wb')}`
    const r = await apiFetch(`${BOT}/suggest/${encodeURIComponent(chatId)}/generate${q}`, {
      method: 'POST',
    })
    const d = await r.json()
    if (!isCurrentChatSelection(requestId, chatId)) return
    if (d?.ok && d.reply_text) {
      chatSuggest.value = d
      chatSuggestMiss.value = null
      chatReply.value = String(d.reply_text || '').replace(/^\s+/, '')
      nextTick(autoGrowReply)
      showToast('ИИ предложил ответ', 'ok')
    } else {
      showToast(d?.reason || 'ИИ не смог предложить текст', 'error')
    }
  } catch {
    if (isCurrentChatSelection(requestId, chatId)) showToast('Ошибка генерации', 'error')
  } finally {
    if (isCurrentChatSelection(requestId, chatId)) chatSuggestGenerating.value = false
  }
}

function autoGrowReply() {
  const el = chatReplyEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function isSeller(msg) {
  const t = (msg.senderType || msg.sender || '').toLowerCase()
  return ['seller', 'client_seller', 'courier', 'partner', 'merchant'].includes(t) || msg.isMine === true
}
function isSystem(msg) {
  const t = (msg.senderType || msg.sender || '').toLowerCase()
  return t === 'system' || t === 'wb' || t === 'market' || t === 'chatbot'
}

function msgImages(msg) {
  return msgAttachments(msg).filter(item => item.type === 'image')
}

function msgDocuments(msg) {
  return msgAttachments(msg).filter(item => item.type !== 'image')
}

function msgAttachments(msg) {
  if (Array.isArray(msg.attachments)) return msg.attachments.filter(item => item?.url)
  return (Array.isArray(msg.images) ? msg.images : []).map(url => ({
    type: 'image', url, name: 'Изображение из чата',
  }))
}

function scrollChatBottom() {
  requestAnimationFrame(() => {
    if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
  })
}

async function fetchChatMsgs(scroll = false, requestId = chatSelectionRequest) {
  const item = chatItem.value
  if (!item || !isCurrentChatSelection(requestId, item.id)) return
  try {
    const r = await apiFetch(chatMessagesPath(item))
    const d = await r.json()
    if (!isCurrentChatSelection(requestId, item.id)) return
    const msgs = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    // Всегда подменяем ленту с сервера (иначе local+API дубли остаются на экране)
    chatMsgs.value = msgs
    if (scroll) scrollChatBottom()
  } catch { /* тихо */ }
}

async function selectChat(item) {
  const requestId = ++chatSelectionRequest
  clearInterval(chatPollTimer)
  chatPollTimer = null
  clearChatAttachments()
  chatSendWarning.value = null
  chatLastViewed.value[item.id] = item.last_ts || Date.now()
  _saveViewed()
  expandedClaimId.value = ''
  expandedReshipId.value = null
  reviewCard.value.show = false
  chatItem.value    = item
  chatMsgs.value    = []
  chatReply.value   = ''
  chatSuggest.value = null
  chatSuggestMiss.value = null
  chatTemplates.value = []
  chatSuggestLoading.value = false
  chatSuggestGenerating.value = false
  chatTemplatesLoading.value = false
  chatTemplatesExpanded.value = false
  productCardOpen.value = false
  productCardLoading.value = false
  productCard.value = null
  chatLoading.value = true
  loadChatContext(item, requestId)
  const mp = chatMp(item)
  if (String(route.query.chat || '') !== String(item.id) || String(route.query.mp || '') !== mp) {
    router.replace({ query: { ...route.query, chat: String(item.id), mp } })
  }
  try {
    const r = await apiFetch(chatMessagesPath(item))
    const d = await r.json()
    if (!isCurrentChatSelection(requestId, item.id)) return
    chatMsgs.value = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    scrollChatBottom()
  } catch {
    if (isCurrentChatSelection(requestId, item.id)) chatMsgs.value = []
  } finally {
    if (isCurrentChatSelection(requestId, item.id)) chatLoading.value = false
  }
  if (!isCurrentChatSelection(requestId, item.id)) return
  chatPollTimer = setInterval(() => fetchChatMsgs(true, requestId), 8000)
  await loadSuggest(item.id, mp, requestId)
  if (!isCurrentChatSelection(requestId, item.id)) return
  await loadChatTemplates(
    mp,
    chatSuggest.value?.theme || chatSuggestMiss.value?.theme || '',
    requestId,
    item.id,
  )
}

async function dismissChatItem() {
  if (!chatItem.value) return
  if (!confirm('Закрыть чат и перенести в архив?\n\nВернуть можно из вкладки «Архив».')) return
  const item = chatItem.value
  try {
    await apiFetch(`${BASE}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({
        mp: item.mp || 'wb',
        item_type: item.item_type || 'chat',
        item_id: String(item.id),
        note: 'Закрыто вручную из переписки',
      }),
    })
    queueItems.value = queueItems.value.filter(i => i !== item && i.id !== item.id)
    chatItem.value   = null
    chatContext.value = null
    chatSuggest.value = null
    chatSuggestMiss.value = null
    chatReply.value = ''
    clearChatAttachments()
    chatSendWarning.value = null
    showToast('Чат закрыт и убран в архив', 'ok')
  } catch { showToast('Ошибка', 'error') }
}

async function sendChatMessage() {
  const txt = chatReply.value.trim()
  if (!chatCanSend.value || !chatItem.value) return
  if (chatAttachments.value.length && !chatAttachmentsAvailable.value) {
    showToast('Вложения для этой площадки недоступны', 'error')
    return
  }
  const item = chatItem.value
  const requestId = chatSelectionRequest
  const attachments = [...chatAttachments.value]
  const sentSummary = txt || `Файлы: ${attachments.map(entry => entry.file.name).join(', ')}`
  chatSending.value = true
  try {
    if (attachments.length) {
      const body = new FormData()
      body.append('text', txt)
      attachments.forEach(entry => body.append('files', entry.file, entry.file.name))
      const response = await apiFetch(chatSendFilesPath(item), { method: 'POST', body })
      const progress = await response.json()
      if (!isCurrentChatSelection(requestId, item.id)) return

      const reconciliation = reconcileChatSendProgress(
        progress,
        attachments.map(entry => entry.id),
      )
      const confirmedFiles = (progress.files || [])
        .filter(step => step.state === 'confirmed')
        .map(step => attachments[step.index])
        .filter(Boolean)
      const unknownNames = (progress.files || [])
        .filter(step => step.state === 'unknown')
        .map(step => step.name)
      if (reconciliation.textUnknown) unknownNames.unshift('текст сообщения')

      if (reconciliation.clearText) chatReply.value = ''
      const removedIds = [...reconciliation.confirmedIds, ...reconciliation.uncertainIds]
      removedIds.forEach(id => removeChatAttachment(id))

      const optimisticParts = []
      if (progress.text?.state === 'confirmed' && txt) optimisticParts.push(txt)
      if (confirmedFiles.length) {
        optimisticParts.push(`Вложения отправлены: ${confirmedFiles.map(entry => entry.file.name).join(', ')}`)
      }
      if (optimisticParts.length) {
        chatMsgs.value.push({
          text: optimisticParts.join('\n\n'),
          senderType: 'seller',
          createdAt: Date.now(),
          pendingSync: true,
        })
        item.last_ts = Date.now()
        item.age_sec = 0
        item.body = optimisticParts.join(' · ')
        item.needs_answer = false
      }

      const needsReconciliation = progress.status === 'uncertain'
        || progress.persistence === 'failed'
        || (progress.status === 'partial' && optimisticParts.length)
      chatSendWarning.value = needsReconciliation ? {
        message: reconciliation.warnings.join(' ') || 'Отправилась только часть шагов. Не повторяйте уже подтверждённые шаги.',
        uncertainNames: unknownNames,
      } : null

      chatSuggest.value = null
      chatSuggestMiss.value = null
      await nextTick()
      autoGrowReply()
      scrollChatBottom()
      setTimeout(() => fetchChatMsgs(true, requestId), 2500)

      if (reconciliation.complete) {
        queueItems.value = queueItems.value.filter(i => i !== item)
        showToast('Сообщение и вложения отправлены', 'ok')
      } else if (progress.status === 'failed') {
        showToast('Вложения не отправлены; выбор сохранён', 'error')
      } else {
        showToast('Отправилась только часть. Проверьте предупреждение и оставшиеся файлы', 'error')
      }
      return
    } else {
      await apiFetch(chatSendPath(item), {
        method: 'POST',
        body: JSON.stringify({ text: txt }),
      })
    }
    if (!isCurrentChatSelection(requestId, item.id)) return
    chatMsgs.value.push({ text: sentSummary, senderType: 'seller', createdAt: Date.now() })
    chatReply.value = ''
    chatSuggest.value = null
    chatSuggestMiss.value = null
    item.last_ts = Date.now()
    item.age_sec = 0
    item.body = sentSummary
    item.needs_answer = false
    await nextTick()
    autoGrowReply()
    scrollChatBottom()
    await fetchChatMsgs(true, requestId)
    queueItems.value = queueItems.value.filter(i => i !== item)
    showToast('Сообщение отправлено', 'ok')
  } catch (e) {
    if (attachments.length && !e?.status && isCurrentChatSelection(requestId, item.id)) {
      // Browser/network failure has an unknown server outcome: quarantine the
      // whole request so a click cannot blindly repeat a possibly accepted step.
      const uncertainNames = attachments.map(entry => entry.file.name)
      if (txt) uncertainNames.unshift('текст сообщения')
      chatReply.value = ''
      attachments.forEach(entry => removeChatAttachment(entry.id))
      chatSendWarning.value = {
        message: 'Соединение оборвалось без ответа сервера. Запрос мог быть обработан.',
        uncertainNames,
      }
    }
    showToast('Ошибка отправки: ' + (e?.message || ''), 'error')
  } finally {
    chatSending.value = false
  }
}

// ── Форматирование ─────────────────────────────────────────────────────────
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
  if (!permissionsStore.loaded) await permissionsStore.load()
  if (!canViewChatTemplates.value && tab.value === 'playbook') tab.value = 'chats'
  if (!isAdmin.value && tab.value === 'triage') tab.value = 'chats'
  if (tab.value === 'playbook' && !canEditChatTemplates.value) pbSection.value = 'templates'
  _loadViewed()
  await loadQueue()
  if (isAdmin.value) loadReviewQueue(true)
  initCentrifugo()
  const qChat = route.query.chat
  if (qChat) await openChatFromQuery(String(qChat))
})

watch([isAdmin, canViewChatTemplates], () => {
  if (!canViewChatTemplates.value && tab.value === 'playbook') tab.value = 'chats'
  if (!isAdmin.value && (tab.value === 'triage' || chatFilter.value === 'review')) {
    if (tab.value === 'triage') tab.value = 'chats'
    if (chatFilter.value === 'review') chatFilter.value = 'pending'
  }
  if (tab.value === 'playbook' && !canEditChatTemplates.value) pbSection.value = 'templates'
})

watch(() => route.query.chat, async (chatId) => {
  if (!chatId) return
  const id = extractChatId(String(chatId))
  if (chatItem.value && chatIdMatches(chatItem.value.id, id)) return
  await openChatFromQuery(id)
})

onUnmounted(() => {
  chatSelectionRequest += 1
  clearInterval(chatPollTimer)
  clearChatAttachments()
  centrifuge?.disconnect()
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
</style>
