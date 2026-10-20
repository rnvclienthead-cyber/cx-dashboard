/**
 * Веб-приложение «Курьеры и заказы» для листа WB DBW.
 *
 * Как поставить (отдельное развёртывание, рядом с «Поиск курьера»):
 * 1. script.google.com → Новый проект (или второй проект у таблицы)
 * 2. Вставь этот Code.gs
 * 3. Файл → Создать → HTML-файл → имя Index → вставь Index.html
 * 4. Развернуть → Новое развёртывание → Веб-приложение
 *    - Описание: Курьеры WB DBW
 *    - Запуск от имени: Меня
 *    - Кто имеет доступ: Все, у кого есть ссылка
 * 5. Ссылку отдай сотрудникам (редактор таблицы не нужен).
 *
 * Данные: лист «В доставке» той же таблицы.
 */

var SPREADSHEET_ID = '1aLeFFmXHgXzf4pp2JobSNzP9gf_er3U33xFuU7_2xUo';
var SHEET_NAME = 'В доставке';

var COL = {
  ORDER: 0,
  STICKER: 1,
  ARTICLE: 2,
  NAME: 3,
  DELIVERY: 4,
  STATUS: 5,
  COURIER: 6,
  CHANGE: 7
};

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Курьеры WB · В доставке')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Полная доска: курьеры → заказы.
 * Фильтры применяются на клиенте; сервер отдаёт актуальный снимок листа.
 * @return {{ok:boolean, total:number, courier_count:number, couriers:Object[]}}
 */
function getBoard() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.getSheets()[0];
  }

  var values = sheet.getDataRange().getDisplayValues();
  if (!values || values.length < 2) {
    return { ok: true, total: 0, courier_count: 0, couriers: [], updated_at: '' };
  }

  var header = values[0].map(function (c) {
    return String(c || '').trim().toLowerCase();
  });
  var courierCol = header.indexOf('данные курьера');
  var changeCol = header.indexOf('смена курьера');
  if (courierCol < 0) courierCol = COL.COURIER;
  if (changeCol < 0) changeCol = COL.CHANGE;

  var groups = {}; // key -> courier bucket
  var total = 0;

  for (var i = 1; i < values.length; i++) {
    var row = values[i];
    var orderId = String(row[COL.ORDER] || '').replace(/\D/g, '');
    if (!orderId) continue;
    total++;

    var courierRaw = String(row[courierCol] || '').trim();
    var changeRaw = String(row[changeCol] || '').trim();
    var parsed = parseCourier_(courierRaw);
    var changed = isCourierChanged_(changeRaw);
    var key = parsed.key;

    if (!groups[key]) {
      groups[key] = {
        key: key,
        name: parsed.name,
        phone: parsed.phone,
        car: parsed.car,
        raw: courierRaw,
        waiting: parsed.waiting,
        order_count: 0,
        changed_count: 0,
        orders: []
      };
    }

    var order = {
      order_id: orderId,
      sticker: String(row[COL.STICKER] || ''),
      article: String(row[COL.ARTICLE] || ''),
      name: String(row[COL.NAME] || ''),
      delivery: String(row[COL.DELIVERY] || ''),
      status: String(row[COL.STATUS] || ''),
      courier: courierRaw,
      courier_change: changeRaw,
      changed: changed
    };
    groups[key].orders.push(order);
    groups[key].order_count++;
    if (changed) groups[key].changed_count++;
  }

  var couriers = Object.keys(groups).map(function (k) {
    return groups[k];
  });

  couriers.sort(function (a, b) {
    if (a.waiting !== b.waiting) return a.waiting ? 1 : -1;
    if (b.changed_count !== a.changed_count) return b.changed_count - a.changed_count;
    if (b.order_count !== a.order_count) return b.order_count - a.order_count;
    return a.name.localeCompare(b.name, 'ru');
  });

  couriers.forEach(function (c) {
    c.orders.sort(function (a, b) {
      if (a.changed !== b.changed) return a.changed ? -1 : 1;
      return String(a.delivery).localeCompare(String(b.delivery), 'ru') ||
        String(a.order_id).localeCompare(String(b.order_id));
    });
  });

  return {
    ok: true,
    total: total,
    courier_count: couriers.length,
    changed_orders: couriers.reduce(function (n, c) { return n + c.changed_count; }, 0),
    couriers: couriers,
    updated_at: Utilities.formatDate(new Date(), 'Europe/Moscow', 'dd.MM.yyyy HH:mm')
  };
}

/** Разбор ячейки «ФИО, +7 …, А123ВС77» или «Ждём данные курьера». */
function parseCourier_(raw) {
  var text = String(raw || '').trim();
  if (!text || /^ждём данные/i.test(text) || /^ждем данные/i.test(text)) {
    return {
      key: '__waiting__',
      name: 'Ожидает курьера',
      phone: '',
      car: '',
      waiting: true
    };
  }

  var parts = text.split(',').map(function (p) { return p.trim(); }).filter(Boolean);
  var name = parts[0] || 'Без имени';
  var phone = '';
  var car = '';
  for (var i = 1; i < parts.length; i++) {
    if (/^\+?\d[\d\s\-()]+$/.test(parts[i].replace(/\s/g, '')) || parts[i].indexOf('+7') === 0) {
      phone = parts[i];
    } else if (!car) {
      car = parts[i];
    }
  }

  return {
    key: normalize_(name) || '__unknown__',
    name: name,
    phone: phone,
    car: car,
    waiting: false
  };
}

function isCourierChanged_(changeCell) {
  var s = normalize_(changeCell);
  if (!s) return false;
  return s.indexOf('замен') !== -1 || s.indexOf('смен') !== -1;
}

function normalize_(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Проверка из редактора Apps Script. */
function testBoard() {
  var data = getBoard();
  Logger.log('couriers=' + data.courier_count + ' orders=' + data.total);
  if (data.couriers[0]) {
    Logger.log(JSON.stringify(data.couriers[0], null, 2));
  }
}
