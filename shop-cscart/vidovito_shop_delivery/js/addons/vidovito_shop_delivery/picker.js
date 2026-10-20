(function () {
  var cfg = window.vidovitoShopDelivery || {};
  if (!cfg.suggestUrl) return;
  var VERSION = '1.5.14';
  var DRAFT_KEY = 'vidovitoCheckoutDraft';
  var LOCKER_RE = /постамат|почтомат|postamat|ozon\s*box|ozonbox|locker|lockbox|parcel[\s-]?lock|халва/i;
  if (window.__vidovitoDeliveryVersion === VERSION) return;
  window.__vidovitoDeliveryVersion = VERSION;
  window.__vidovitoDeliveryBound = true;

  var state = {
    carrier: 'ozon',
    paidOpen: false,
    query: '',
    suggest: [],
    points: [],
    client: null,
    selected: null,
    zip: '',
    map: null,
    marks: [],
    ymaps: null,
    lastOzonPhone: ''
  };
  var debounceTimer = null;
  var suggestAbort = null;
  var ignoreSuggest = false;
  var applyingFields = false;
  var pointsReq = 0;

  function checkoutForm() {
    return document.getElementById('litecheckout_payments_form')
      || document.querySelector('form[name="litecheckout_payments_form"]')
      || document.querySelector('form[action*="place_order"]');
  }

  function ensureCheckoutField(name) {
    var form = checkoutForm();
    var el = document.querySelector('[name="' + name + '"]');
    if (el) return el;
    if (!form) return null;
    el = document.createElement('input');
    el.type = 'hidden';
    el.name = name;
    if (name.indexOf('user_data[') === 0) {
      var key = name.slice('user_data['.length, -1);
      el.setAttribute('data-ca-lite-checkout-field', 'user_data.' + key);
    }
    form.appendChild(el);
    return el;
  }

  function setFieldEl(el, value, silent) {
    if (!el) return;
    var next = value == null ? '' : String(value);
    if (el.value === next) return;
    applyingFields = true;
    try {
      el.value = next;
      if (silent) return;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      if (window.Tygh && Tygh.$) {
        try { Tygh.$(el).trigger('change'); } catch (e) {}
      }
    } finally {
      applyingFields = false;
    }
  }

  function setCheckoutField(name, value, silent) {
    setFieldEl(ensureCheckoutField(name), value, silent);
  }

  function markShippingReady(shippingId) {
    var sid = String(shippingId || '0');
    if (!sid || sid === '0') return;
    applyingFields = true;
    try {
      var box = document.getElementById('shipping_rates_list')
        || document.querySelector('[data-ca-lite-checkout-element="shipping-methods"]');
      if (box) {
        var overlayClass = box.getAttribute('data-ca-lite-checkout-overlay-class') || 'litecheckout__overlay--active';
        box.classList.remove(overlayClass);
        var radio = box.querySelector('input[data-ca-vidovito-shipping="1"]');
        if (!radio) {
          radio = document.createElement('input');
          radio.type = 'radio';
          radio.setAttribute('data-ca-vidovito-shipping', '1');
          radio.setAttribute('data-ca-lite-checkout-element', 'shipping-method');
          radio.name = 'shipping_ids[0]';
          radio.id = 'vidovito_shipping_method';
          radio.className = 'hidden';
          radio.style.cssText = 'position:absolute;opacity:0;width:1px;height:1px;';
          box.appendChild(radio);
        }
        radio.value = sid;
        radio.checked = true;
        radio.disabled = false;
        radio.removeAttribute('data-ca-lite-checkout-shipping-method-disabled');
      }
      var form = checkoutForm();
      if (form) {
        var extra = form.querySelector('input[data-ca-vidovito-shipping-form="1"]');
        if (!extra) {
          extra = document.createElement('input');
          extra.type = 'hidden';
          extra.name = 'shipping_ids[0]';
          extra.setAttribute('data-ca-vidovito-shipping-form', '1');
          form.appendChild(extra);
        }
        extra.value = sid;
      }
      var lab = document.querySelector('label.cm-shipping-available-label');
      if (lab) {
        lab.classList.remove('cm-required');
        lab.classList.remove('cm-multiple-radios');
      }
    } finally {
      applyingFields = false;
    }
  }

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function phoneDigits(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function fieldValue(el) {
    if (!el) return '';
    var direct = el.value != null ? String(el.value).trim() : '';
    if (phoneDigits(direct).length >= 10) return direct;
    var inner = el.querySelector && el.querySelector('input, textarea, [contenteditable]');
    if (inner && inner !== el) {
      var nested = inner.value != null ? String(inner.value).trim() : String(inner.textContent || '').trim();
      if (phoneDigits(nested).length >= 10) return nested;
    }
    return direct;
  }

  function customerPhone() {
    var scope = document.getElementById('litecheckout_payments_form')
      || document.querySelector('form[name="litecheckout_payments_form"]')
      || document.querySelector('form[name="litecheckout_form"]')
      || document.getElementById('litecheckout_step_customer_info')
      || document.querySelector('.litecheckout')
      || document;
    var selectors = [
      '[data-ca-lite-checkout-field="user_data.phone"]',
      '[name="user_data[phone]"]',
      '[name="user_data[s_phone]"]',
      '[name="user_data[b_phone]"]',
      'input.cm-mask-phone',
      'input.cm-mask-phone-key',
      'input[type="tel"]',
      'input[name="phone"]',
      '[name="phone"]',
      'input[name*="phone"]'
    ];
    for (var i = 0; i < selectors.length; i++) {
      var els = scope.querySelectorAll(selectors[i]);
      for (var j = 0; j < els.length; j++) {
        var val = fieldValue(els[j]);
        if (phoneDigits(val).length >= 10) return val;
      }
    }
    return '';
  }

  function showCost() {
    return state.carrier !== 'ozon';
  }

  function costLabel(p) {
    if (!showCost() || !p || !p.cost) return '';
    return Math.round(p.cost) + ' ₽';
  }

  function isLocker(p) {
    if (!p) return false;
    var kind = String(p.point_type || p.type || '').toLowerCase();
    if (/postamat|postomat|pochtomat|locker|lockbox|parcel/.test(kind)) return true;
    return LOCKER_RE.test(String(p.name || '') + ' ' + String(p.address || ''));
  }

  function pickupPoints(list) {
    return (list || []).filter(function (p) { return p && !isLocker(p); });
  }

  function buyerFields() {
    return ['firstname', 'lastname', 'email', 'phone', 's_phone', 'b_phone'];
  }

  function readBuyer() {
    var out = {};
    buyerFields().forEach(function (short) {
      var nodes = document.querySelectorAll(
        '[data-ca-lite-checkout-field="user_data.' + short + '"], [name="user_data[' + short + ']"]'
      );
      for (var i = 0; i < nodes.length; i++) {
        var val = fieldValue(nodes[i]);
        if (val) {
          out[short] = val;
          break;
        }
      }
    });
    return out;
  }

  function restoreBuyer(buyer) {
    if (!buyer) return;
    buyerFields().forEach(function (short) {
      if (!buyer[short]) return;
      document.querySelectorAll(
        '[data-ca-lite-checkout-field="user_data.' + short + '"], [name="user_data[' + short + ']"]'
      ).forEach(function (el) {
        if (!fieldValue(el)) setFieldEl(el, buyer[short], true);
      });
    });
  }

  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        carrier: state.carrier,
        paidOpen: state.paidOpen,
        client: state.client,
        selected: state.selected,
        points: (state.points || []).slice(0, 30),
        query: state.query,
        zip: state.zip,
        buyer: readBuyer()
      }));
    } catch (e) {}
  }

  function loadDraft() {
    try {
      var raw = localStorage.getItem(DRAFT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function highlightCarrier() {
    var box = document.getElementById('vidovito-delivery-box');
    if (!box) return;
    box.querySelectorAll('[data-carrier]').forEach(function (b) {
      b.classList.toggle('is-active', b.getAttribute('data-carrier') === state.carrier);
    });
  }

  function hydrateDraft() {
    if (state.client || state.selected) return;
    var draft = loadDraft();
    if (!draft || (!draft.client && !draft.selected && !(draft.buyer && Object.keys(draft.buyer).length))) return;
    state.carrier = draft.carrier || 'ozon';
    state.paidOpen = !!draft.paidOpen || state.carrier !== 'ozon';
    state.client = draft.client || null;
    state.selected = draft.selected && !isLocker(draft.selected) ? draft.selected : null;
    state.points = pickupPoints(draft.points || []);
    state.query = draft.query || '';
    state.zip = draft.zip || '';
    restoreBuyer(draft.buyer);
    syncPaidUi();
    highlightCarrier();
  }

  function widgetHtml() {
    return '' +
      '<div class="ty-vidovito-delivery" id="vidovito-delivery-box">' +
        '<div class="ty-vidovito-delivery__head">' +
          '<div class="ty-vidovito-delivery__title" id="vidovito-delivery-title">Доставка в пункт выдачи Ozon</div>' +
        '</div>' +
        '<div class="ty-vidovito-delivery__carriers" id="vidovito-delivery-carriers" hidden>' +
          '<button type="button" class="ty-vidovito-delivery__carrier is-active" data-carrier="ozon">' +
            '<span class="ty-vidovito-delivery__carrier-name">Ozon</span>' +
            '<span class="ty-vidovito-delivery__carrier-meta ty-vidovito-delivery__carrier-meta--free">Бесплатно</span>' +
          '</button>' +
          '<button type="button" class="ty-vidovito-delivery__carrier" data-carrier="yandex" hidden>' +
            '<span class="ty-vidovito-delivery__carrier-name">Яндекс Доставка</span>' +
            '<span class="ty-vidovito-delivery__carrier-meta ty-vidovito-delivery__carrier-meta--paid">Платно</span>' +
          '</button>' +
          '<button type="button" class="ty-vidovito-delivery__carrier" data-carrier="cdek" hidden>' +
            '<span class="ty-vidovito-delivery__carrier-name">СДЭК</span>' +
            '<span class="ty-vidovito-delivery__carrier-meta ty-vidovito-delivery__carrier-meta--paid">Платно</span>' +
          '</button>' +
        '</div>' +
        '<p class="ty-vidovito-delivery__paid-note" id="vidovito-paid-note" hidden>Яндекс и СДЭК оплачиваются отдельно. Точная сумма — после выбора пункта выдачи.</p>' +
        '<button type="button" class="ty-vidovito-delivery__back" id="vidovito-back-ozon" hidden>Вернуться к бесплатной доставке Ozon</button>' +
        '<div class="ty-vidovito-delivery__help" id="vidovito-ozon-help" hidden>' +
          '<p class="ty-vidovito-delivery__help-title">На этом телефоне нет профиля Ozon</p>' +
          '<p>Бесплатная доставка работает только на аккаунт Ozon (Ozon ID) в приложении — с тем же номером, что в блоке «Покупатель».</p>' +
          '<ol>' +
            '<li>Скачайте приложение Ozon на телефон.</li>' +
            '<li>Зарегистрируйтесь или войдите по номеру из заказа.</li>' +
            '<li>Вернитесь сюда и нажмите «Проверить снова».</li>' +
          '</ol>' +
          '<div class="ty-vidovito-delivery__help-actions">' +
            '<button type="button" class="ty-vidovito-delivery__btn-primary" id="vidovito-ozon-retry">Проверить снова</button>' +
            '<button type="button" class="ty-vidovito-delivery__btn-ghost" id="vidovito-ozon-refuse">Нет профиля — выбрать платную доставку</button>' +
          '</div>' +
        '</div>' +
        '<div id="vidovito-delivery-step2">' +
          '<p class="ty-vidovito-delivery__hint">Город, улица и дом — выберите адрес из подсказки.</p>' +
          '<div class="ty-vidovito-delivery__address-wrap">' +
            '<input class="ty-vidovito-delivery__address" id="vidovito-delivery-address" type="text" autocomplete="off" placeholder="Например: Москва, Тверская улица, 7">' +
            '<div class="ty-vidovito-delivery__suggest" id="vidovito-delivery-suggest" hidden></div>' +
          '</div>' +
          '<p class="ty-vidovito-delivery__error" id="vidovito-delivery-error" hidden></p>' +
          '<div class="ty-vidovito-delivery__layout" id="vidovito-delivery-results" hidden>' +
            '<div class="ty-vidovito-delivery__col">' +
              '<button type="button" class="ty-vidovito-delivery__reset" id="vidovito-reset-pvz" hidden>Сбросить выбор</button>' +
              '<div class="ty-vidovito-delivery__list" id="vidovito-delivery-list"></div>' +
            '</div>' +
            '<div class="ty-vidovito-delivery__map" id="vidovito-delivery-map"></div>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  function closestBlock(el) {
    if (!el) return null;
    return el.closest('.litecheckout__group')
      || el.closest('.litecheckout__section')
      || el.closest('.ty-checkout-block')
      || el.closest('.ty-lite-checkout__block')
      || el.closest('[id^="snapping_id_"]')
      || el;
  }

  function findHeading(prefix) {
    var scope = document.querySelector('form[name="litecheckout_form"]')
      || document.querySelector('.litecheckout')
      || document.body;
    var nodes = scope.querySelectorAll(
      'h1,h2,h3,h4,h5,legend,.litecheckout__step-title,.litecheckout__group-title,.ty-lite-checkout__title,.ty-checkout__title'
    );
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest('#vidovito-delivery-root')) continue;
      var t = (el.textContent || '').replace(/\s+/g, ' ').trim();
      if (t.indexOf(prefix) === 0) return el;
    }
    return null;
  }

  function placeRoot(root) {
    if (!root) return false;
    var pay = closestBlock(findHeading('Способы оплаты'));
    if (pay && pay.parentNode && pay !== root) {
      if (root.parentNode === pay.parentNode && root.nextElementSibling === pay) return true;
      pay.parentNode.insertBefore(root, pay);
      return true;
    }
    var customer = closestBlock(
      document.getElementById('litecheckout_step_customer_info') || findHeading('Покупатель')
    );
    if (customer && customer.parentNode && customer !== root) {
      if (root.parentNode === customer.parentNode && root.previousElementSibling === customer) return true;
      customer.parentNode.insertBefore(root, customer.nextSibling);
      return true;
    }
    return false;
  }

  function ensureRoot() {
    if (!document.getElementById('litecheckout_step_customer_info')
      && !findHeading('Покупатель')
      && !findHeading('Способы оплаты')) {
      return null;
    }
    var root = document.getElementById('vidovito-delivery-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'vidovito-delivery-root';
    }
    placeRoot(root);
    return root.parentNode ? root : null;
  }

  function mount() {
    var existing = ensureRoot();
    if (!existing) return null;
    if (existing.getAttribute('data-bound') !== VERSION) {
      existing.innerHTML = widgetHtml();
      bind(existing);
      existing.setAttribute('data-bound', VERSION);
      syncPaidUi();
      restoreUi();
    }
    return existing;
  }

  function restoreUi() {
    hydrateDraft();
    var input = $('#vidovito-delivery-address');
    if (input && state.client && state.client.address) {
      ignoreSuggest = true;
      input.value = state.client.address;
    }
    if (state.selected) {
      var sid = cfg[state.carrier + 'Id'];
      markShippingReady(sid);
      fillNative(state.client);
    }
    if (!state.points.length && !state.selected) return;
    var results = $('#vidovito-delivery-results');
    if (results) results.hidden = false;
    resetMap();
    renderList();
    renderMap();
  }

  function showError(msg) {
    var el = $('#vidovito-delivery-error');
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function hideOzonHelp() {
    var el = $('#vidovito-ozon-help');
    if (el) el.hidden = true;
  }

  function showOzonHelp() {
    var suggest = $('#vidovito-delivery-suggest');
    if (suggest) {
      suggest.hidden = true;
      suggest.innerHTML = '';
    }
    var el = $('#vidovito-ozon-help');
    if (el) el.hidden = false;
    var results = $('#vidovito-delivery-results');
    if (results) results.hidden = true;
    showError('');
  }

  function syncPaidUi() {
    var carriers = $('#vidovito-delivery-carriers');
    var yandex = document.querySelector('#vidovito-delivery-box [data-carrier="yandex"]');
    var cdek = document.querySelector('#vidovito-delivery-box [data-carrier="cdek"]');
    var paidNote = $('#vidovito-paid-note');
    var back = $('#vidovito-back-ozon');
    var title = $('#vidovito-delivery-title');
    if (title) {
      title.textContent = state.paidOpen ? 'Доставка в пункт выдачи' : 'Доставка в пункт выдачи Ozon';
    }
    if (carriers) carriers.hidden = !state.paidOpen;
    if (yandex) yandex.hidden = !state.paidOpen;
    if (cdek) cdek.hidden = !state.paidOpen;
    if (paidNote) paidNote.hidden = !state.paidOpen;
    if (back) back.hidden = !state.paidOpen;
  }

  function selectCarrier(carrier) {
    state.carrier = carrier || 'ozon';
    var box = document.getElementById('vidovito-delivery-box');
    if (!box) return;
    box.querySelectorAll('[data-carrier]').forEach(function (b) {
      b.classList.toggle('is-active', b.getAttribute('data-carrier') === state.carrier);
    });
    var step = $('#vidovito-delivery-step2');
    if (step) step.hidden = false;
    state.selected = null;
    state.points = [];
    resetMap();
    var results = $('#vidovito-delivery-results');
    if (results) results.hidden = true;
    if (state.carrier === 'ozon') {
      hideOzonHelp();
    }
    showError('');
    syncPaidUi();
    if (state.client && state.client.address) {
      loadPoints(state.client);
    }
  }

  function bind(root) {
    root.querySelectorAll('[data-carrier]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (btn.disabled || btn.hidden) return;
        selectCarrier(btn.getAttribute('data-carrier'));
      });
    });
    var retry = $('#vidovito-ozon-retry', root);
    if (retry) {
      retry.addEventListener('click', function () {
        hideOzonHelp();
        selectCarrier('ozon');
      });
    }
    var refuse = $('#vidovito-ozon-refuse', root);
    if (refuse) {
      refuse.addEventListener('click', function () {
        hideOzonHelp();
        state.paidOpen = true;
        syncPaidUi();
        selectCarrier('yandex');
      });
    }
    var back = $('#vidovito-back-ozon', root);
    if (back) {
      back.addEventListener('click', function () {
        state.paidOpen = false;
        hideOzonHelp();
        syncPaidUi();
        selectCarrier('ozon');
      });
    }
    watchPhone();
    bindPlaceOrderGuard();
    var resetBtn = $('#vidovito-reset-pvz', root);
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        clearPvz();
      });
    }
    var input = $('#vidovito-delivery-address');
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchTypedAddress(true);
      }
    });
    input.addEventListener('input', function () {
      if (ignoreSuggest) {
        ignoreSuggest = false;
        saveDraft();
        return;
      }
      state.query = input.value;
      state.selected = null;
      saveDraft();
      clearTimeout(debounceTimer);
      if (state.query.trim().length < 2) {
        $('#vidovito-delivery-suggest').hidden = true;
        return;
      }
      debounceTimer = setTimeout(fetchSuggest, 250);
    });
    input.addEventListener('blur', function () {
      setTimeout(function () {
        $('#vidovito-delivery-suggest').hidden = true;
        if (state.selected) return;
        var q = (input.value || '').trim();
        var same = state.client && String(state.client.address || '').trim().toLowerCase() === q.toLowerCase();
        if (same && state.points.length) return;
        if (q.length >= 8) searchTypedAddress(false);
      }, 200);
    });
    input.addEventListener('focus', function () {
      if (state.suggest.length) {
        $('#vidovito-delivery-suggest').hidden = false;
        placeSuggest();
      }
    });
  }

  function isPhoneField(el) {
    if (!el) return false;
    var name = String(el.name || '') + ' ' + String(el.id || '') + ' ' +
      String(el.getAttribute && el.getAttribute('data-ca-lite-checkout-field') || '') + ' ' +
      String(el.className || '') + ' ' + String(el.type || '');
    return /phone|tel/i.test(name);
  }

  function watchPhone() {
    var scope = checkoutForm()
      || document.getElementById('litecheckout_step_customer_info')
      || document.querySelector('.litecheckout')
      || document.body;
    if (!scope || scope.getAttribute('data-vidovito-phone-watch')) return;
    scope.setAttribute('data-vidovito-phone-watch', '1');
    var timer = null;
    function maybeReload(e) {
      if (applyingFields) return;
      saveDraft();
      if (e && e.target && !isPhoneField(e.target)) return;
      if (state.carrier !== 'ozon' || !state.client) return;
      clearTimeout(timer);
      timer = setTimeout(function () {
        var phone = phoneDigits(customerPhone());
        if (phone.length < 10) return;
        if (phone === state.lastOzonPhone && (state.selected || state.points.length)) return;
        loadPoints(state.client);
      }, 400);
    }
    scope.addEventListener('input', maybeReload, true);
    scope.addEventListener('change', maybeReload, true);
  }

  function bindPlaceOrderGuard() {
    if (document.documentElement.getAttribute('data-vidovito-place-guard')) return;
    document.documentElement.setAttribute('data-vidovito-place-guard', '1');
    document.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest && e.target.closest('[name="dispatch[checkout.place_order]"], #vv-checkout-place-order');
      if (!btn) return;
      if (!state.selected) {
        showError('Выберите пункт выдачи');
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      markShippingReady(cfg[state.carrier + 'Id']);
      fillNative(state.client);
    }, true);
  }

  function fetchSuggest() {
    if (suggestAbort) suggestAbort.abort();
    suggestAbort = new AbortController();
    var url = cfg.suggestUrl + (cfg.suggestUrl.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(state.query);
    fetch(url, { signal: suggestAbort.signal, credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.suggest = (data.suggestions || []).filter(function (s) { return s.selectable !== false; });
        var q = state.query.trim().toLowerCase();
        var exact = state.suggest.filter(function (s) {
          return String(s.value || '').trim().toLowerCase() === q;
        })[0];
        if (exact) {
          pickAddress(exact);
          return;
        }
        renderSuggest();
      })
      .catch(function () {});
  }

  function placeSuggest() {
    var input = $('#vidovito-delivery-address');
    var box = $('#vidovito-delivery-suggest');
    if (!input || !box || box.hidden) return;
    var r = input.getBoundingClientRect();
    box.style.position = 'fixed';
    box.style.left = Math.round(r.left) + 'px';
    box.style.top = Math.round(r.bottom + 4) + 'px';
    box.style.width = Math.round(r.width) + 'px';
    box.style.right = 'auto';
    box.style.zIndex = '10000';
  }

  function searchTypedAddress(fromUser) {
    var input = $('#vidovito-delivery-address');
    var q = ((input && input.value) || state.query || '').trim();
    state.query = q;
    if (q.length < 5) {
      if (fromUser) showError('Напишите город, улицу и дом.');
      return;
    }
    $('#vidovito-delivery-suggest').hidden = true;
    if (suggestAbort) suggestAbort.abort();
    var url = cfg.suggestUrl + (cfg.suggestUrl.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(q);
    showError('');
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var list = (data.suggestions || []).filter(function (s) { return s.selectable !== false; });
        var exact = list.filter(function (s) {
          return String(s.value || '').trim().toLowerCase() === q.toLowerCase();
        })[0] || list[0];
        if (exact) {
          pickAddress(exact);
          return;
        }
        loadPoints({ city: q, address: q });
      })
      .catch(function () {
        loadPoints({ city: q, address: q });
      });
  }

  function renderSuggest() {
    var box = $('#vidovito-delivery-suggest');
    box.innerHTML = '';
    if (!state.suggest.length) {
      box.hidden = true;
      return;
    }
    state.suggest.forEach(function (item) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = item.value;
      b.addEventListener('mousedown', function (e) {
        e.preventDefault();
        pickAddress(item);
      });
      box.appendChild(b);
    });
    box.hidden = false;
    placeSuggest();
  }

  function pickAddress(item) {
    ignoreSuggest = true;
    $('#vidovito-delivery-address').value = item.value;
    var suggest = $('#vidovito-delivery-suggest');
    if (suggest) {
      suggest.hidden = true;
      suggest.innerHTML = '';
    }
    state.zip = item.zip || '';
    state.selected = null;
    state.client = {
      city: item.city || item.value,
      address: item.value,
      lat: item.lat,
      lon: item.lon,
      zip: item.zip || '',
      state: item.state || ''
    };
    fillNative(state.client);
    saveDraft();
    loadPoints(state.client);
  }

  function fillNative(client) {
    if (!client) return;
    var map = {
      's_city': client.city || '',
      's_address': client.address || '',
      's_zipcode': client.zip || '000000',
      's_country': 'RU'
    };
    if (client.state) map.s_state = client.state;
    Object.keys(map).forEach(function (short) {
      var val = map[short];
      document.querySelectorAll('[data-ca-lite-checkout-field="user_data.' + short + '"]').forEach(function (el) {
        setFieldEl(el, val, true);
      });
      document.querySelectorAll('[name="user_data[' + short + ']"]').forEach(function (el) {
        setFieldEl(el, val, true);
      });
      document.querySelectorAll('[name="' + short + '"]').forEach(function (el) {
        setFieldEl(el, val, true);
      });
    });
    var cityEl = document.getElementById('litecheckout_city')
      || document.querySelector('[data-ca-lite-checkout-element="city"]');
    if (cityEl) setFieldEl(cityEl, client.city || '', true);
  }

  function loadPoints(client) {
    if (!state.carrier || !client) return;
    if (state.selected) return;
    var req = ++pointsReq;
    showError('Ищем пункты выдачи…');
    var params = new URLSearchParams({
      carrier: state.carrier,
      city: client.city || '',
      address: client.address || '',
    });
    if (client.lat != null) params.set('lat', client.lat);
    if (client.lon != null) params.set('lon', client.lon);
    if (state.carrier === 'ozon') {
      var phone = customerPhone();
      if (phoneDigits(phone).length < 10) {
        showError('Для Ozon укажите телефон в блоке «Покупатель». Доставка идёт на аккаунт в приложении.');
        return;
      }
      params.set('phone', phone);
      state.lastOzonPhone = phoneDigits(phone);
    }
    fetch(cfg.pointsUrl + (cfg.pointsUrl.indexOf('?') >= 0 ? '&' : '?') + params.toString(), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (req !== pointsReq || state.selected) return;
        if (data.status === 'need_street') {
          showError(data.message || 'Укажите улицу и номер дома');
          return;
        }
        if (data.status === 'outside_russia') {
          showError(data.message || 'Доставляем только по России');
          return;
        }
        if (data.status === 'need_ozon_account' || data.status === 'unavailable') {
          showOzonHelp();
          return;
        }
        if (data.status !== 'ok' || !(data.points || []).length) {
          showError('Рядом нет пунктов выдачи этой службы. Попробуйте другой адрес или другую службу.');
          return;
        }
        state.client = data.client || client;
        state.points = pickupPoints(data.points);
        if (!state.points.length) {
          showError('Рядом нет пунктов выдачи этой службы. Постаматы не показываем.');
          return;
        }
        hideOzonHelp();
        showError('');
        $('#vidovito-delivery-results').hidden = false;
        resetMap();
        renderList();
        renderMap();
        saveDraft();
      })
      .catch(function () {
        if (req !== pointsReq || state.selected) return;
        showError('Не удалось загрузить пункты выдачи. Попробуйте ещё раз.');
      });
  }

  function fmtKm(km) {
    if (km == null || isNaN(Number(km))) return '';
    var n = Number(km);
    var s = Number.isInteger(n) ? String(n) : n.toFixed(1).replace('.', ',');
    return '~' + s + ' км';
  }

  function renderList() {
    var list = $('#vidovito-delivery-list');
    var resetBtn = $('#vidovito-reset-pvz');
    if (resetBtn) resetBtn.hidden = !state.selected;
    list.innerHTML = '';
    var rows = state.selected ? [state.selected] : pickupPoints(state.points);
    rows.forEach(function (p) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'ty-vidovito-delivery__pvz' + (state.selected && state.selected.code === p.code ? ' is-active' : '');
      var cost = costLabel(p);
      var paid = (state.carrier !== 'ozon' && cost) ? ' · Платно: ' + cost : (state.carrier === 'ozon' ? ' · Бесплатно' : '');
      b.innerHTML = '<div class="ty-vidovito-delivery__pvz-name">' + (p.name || 'Пункт выдачи') + '</div>' +
        '<div class="ty-vidovito-delivery__pvz-meta">' + (p.address || '') +
        (p.distance_km != null ? ' · ' + fmtKm(p.distance_km) : '') +
        paid + '</div>';
      b.addEventListener('click', function () { selectPvz(p); });
      list.appendChild(b);
    });
  }

  function loadYmaps() {
    if (state.ymaps) return Promise.resolve(state.ymaps);
    if (window.ymaps) {
      return new Promise(function (resolve) {
        window.ymaps.ready(function () {
          state.ymaps = window.ymaps;
          resolve(state.ymaps);
        });
      });
    }
    return fetch(cfg.mapsUrl, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.apikey) throw new Error('no key');
        return new Promise(function (resolve, reject) {
          var s = document.createElement('script');
          s.src = 'https://api-maps.yandex.ru/2.1/?apikey=' + encodeURIComponent(data.apikey) + '&lang=ru_RU';
          s.async = true;
          s.onload = function () {
            if (!window.ymaps) return reject();
            window.ymaps.ready(function () {
              state.ymaps = window.ymaps;
              resolve(state.ymaps);
            });
          };
          s.onerror = reject;
          document.head.appendChild(s);
        });
      });
  }

  function resetMap() {
    state.marks = [];
    if (state.map) {
      try { state.map.destroy(); } catch (e) {}
      state.map = null;
    }
  }

  function paintMarks() {
    var selectedCode = state.selected && state.selected.code;
    (state.marks || []).forEach(function (row) {
      var on = selectedCode && String(row.code) === String(selectedCode);
      if (!row.mark || !row.mark.options) return;
      row.mark.options.set('iconColor', on ? '#1E2235' : '#94A3B8');
      row.mark.options.set('zIndex', on ? 700 : 0);
    });
    if (state.selected && state.selected.lat != null && state.map) {
      try {
        state.map.setCenter([state.selected.lat, state.selected.lng]);
      } catch (e) {}
    }
  }

  function renderMap() {
    var el = $('#vidovito-delivery-map');
    if (!el || !state.points.length) return;
    loadYmaps().then(function (ymaps) {
      if (!state.map) {
        var center = state.client && state.client.lat != null
          ? [state.client.lat, state.client.lon]
          : [state.points[0].lat, state.points[0].lng];
        state.map = new ymaps.Map(el, { center: center, zoom: 13, controls: ['zoomControl'] }, { suppressMapOpenBlock: true });
        if (state.client && state.client.lat != null) {
          state.map.geoObjects.add(new ymaps.Placemark([state.client.lat, state.client.lon], {
            balloonContent: state.client.address || 'Ваш адрес',
            iconCaption: 'Адрес'
          }, { preset: 'islands#circleIcon', iconColor: '#64748B' }));
        }
        state.marks = [];
        pickupPoints(state.points).forEach(function (p) {
          if (p.lat == null || p.lng == null) return;
          var mark = new ymaps.Placemark([p.lat, p.lng], {
            balloonContent: (p.name || '') + '<br>' + (p.address || '') + (p.distance_km != null ? '<br>' + fmtKm(p.distance_km) : '')
          }, { preset: 'islands#circleIcon', iconColor: '#94A3B8' });
          mark.events.add('click', function () { selectPvz(p); });
          state.map.geoObjects.add(mark);
          state.marks.push({ code: p.code, mark: mark });
        });
      }
      paintMarks();
    }).catch(function () {
      el.textContent = 'Карта недоступна, выберите пункт из списка';
    });
  }

  function clearUrl() {
    if (cfg.clearUrl) return cfg.clearUrl;
    var u = String(cfg.selectUrl || '');
    var i = u.lastIndexOf('select');
    if (i < 0) return '';
    return u.slice(0, i) + 'clear' + u.slice(i + 6);
  }

  function clearPvz() {
    if (!state.selected) return;
    state.selected = null;
    renderList();
    paintMarks();
    showError('');
    if (state.client) fillNative(state.client);
    var radio = document.querySelector('input[data-ca-vidovito-shipping="1"]');
    if (radio && radio.parentNode) radio.parentNode.removeChild(radio);
    var extra = document.querySelector('input[data-ca-vidovito-shipping-form="1"]');
    if (extra && extra.parentNode) extra.parentNode.removeChild(extra);
    var lab = document.querySelector('label.cm-shipping-available-label');
    if (lab) {
      lab.classList.add('cm-required');
      lab.classList.add('cm-multiple-radios');
    }
    var url = clearUrl();
    if (!url) return;
    var body = new URLSearchParams({
      security_hash: (window.Tygh && Tygh.security_hash) || cfg.securityHash || ''
    });
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
      body: body.toString()
    }).catch(function () {});
    saveDraft();
  }

  function refreshCheckoutTotals() {
    if (!window.Tygh || !Tygh.$ || !Tygh.$.ceAjax) return;
    var form = document.querySelector('form[name="litecheckout_payments_form"]')
      || document.querySelector('form[name="checkout_form"]');
    if (!form) return;
    var ids = ['litecheckout_form', 'litecheckout_final_section'];
    document.querySelectorAll('[id^="checkout_info_summary"], [id^="checkout_info_order_info"], [id^="checkout_totals"]').forEach(function (el) {
      if (el.id) ids.push(el.id);
    });
    var url = (typeof fn_url === 'function') ? fn_url('checkout.checkout') : (form.getAttribute('action') || 'index.php?dispatch=checkout.checkout');
    try {
      Tygh.$.ceAjax('request', url, {
        method: 'post',
        result_ids: ids.join(','),
        full_render: true,
        data: Tygh.$(form).serialize(),
        hidden: true
      });
    } catch (e) {}
  }

  function selectPvz(p) {
    if (isLocker(p)) return;
    state.selected = p;
    saveDraft();
    renderList();
    paintMarks();
    markShippingReady(cfg[state.carrier + 'Id']);
    fillNative(state.client);
    var body = new URLSearchParams({
      carrier: state.carrier,
      pvz_code: p.code,
      pvz_name: p.name || '',
      pvz_address: p.address || '',
      city: (state.client && state.client.city) || '',
      address: (state.client && state.client.address) || '',
      zip: (state.client && state.client.zip) || state.zip || '',
      state: (state.client && state.client.state) || '',
      cost: state.carrier === 'ozon' ? '0' : (p.cost || ''),
      distance_km: p.distance_km != null ? p.distance_km : '',
      delivery_time: '2–5 дней',
      security_hash: (window.Tygh && Tygh.security_hash) || cfg.securityHash || ''
    });
    if (p.lat != null) body.set('lat', p.lat);
    if (p.lng != null) body.set('lon', p.lng);
    fetch(cfg.selectUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
      body: body.toString()
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.status !== 'ok') {
        showError(data.message || 'Не удалось сохранить пункт выдачи');
        return;
      }
      var sid = String(data.shipping_id || cfg[state.carrier + 'Id'] || 0);
      if (!sid || sid === '0') {
        showError('Служба доставки не подключена. Заказ оформить нельзя.');
        return;
      }
      markShippingReady(sid);
      fillNative(state.client);
      saveDraft();
      refreshCheckoutTotals();
    }).catch(function () {
      showError('Не удалось сохранить пункт выдачи');
    });
  }

  function boot() {
    try {
      mount();
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
  if (window.Tygh && Tygh.$) {
    Tygh.$(document).on('ce:ajaxdone ce.ajaxdone', function () {
      try {
        mount();
        if (state.selected) {
          markShippingReady(cfg[state.carrier + 'Id']);
          fillNative(state.client);
        }
      } catch (e) {}
    });
  }
})();
