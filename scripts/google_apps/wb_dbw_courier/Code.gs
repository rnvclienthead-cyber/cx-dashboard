/**
 * Веб-приложение «Поиск курьера» для листа WB DBW.
 *
 * Как поставить (один раз, владелец таблицы):
 * 1. Открой таблицу → Расширения → Apps Script
 * 2. Удали код по умолчанию, вставь этот Code.gs
 * 3. Файл → Создать → HTML-файл → имя Index → вставь Index.html
 * 4. Справа «Развернуть» → Новое развёртывание → Тип: Веб-приложение
 *    - Описание: Поиск курьера WB
 *    - Запуск от имени: Меня
 *    - Кто имеет доступ: Все, у кого есть ссылка
 * 5. Скопируй ссылку и отдай сотрудникам (редактор таблицы им не нужен).
 *
 * Таблица остаётся «базой»; сотрудники работают только через эту ссылку.
 */

var SPREADSHEET_ID = '1aLeFFmXHgXzf4pp2JobSNzP9gf_er3U33xFuU7_2xUo';
var SHEET_NAME = 'В доставке';

// Колонки листа «В доставке»
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
    .setTitle('Поиск курьера WB')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Поиск актуальных заказов по ФИО / части имени курьера.
 * @param {string} query
 * @return {{ok:boolean, query:string, count:number, total:number, orders:Object[]}}
 */
function searchCourier(query) {
  query = String(query || '').trim();
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.getSheets()[0];
  }

  var values = sheet.getDataRange().getDisplayValues();
  if (!values || values.length < 2) {
    return { ok: true, query: query, count: 0, total: 0, orders: [] };
  }

  var header = values[0].map(function (c) {
    return String(c || '').trim().toLowerCase();
  });
  var courierCol = header.indexOf('данные курьера');
  if (courierCol < 0) courierCol = COL.COURIER;

  var dataRows = values.slice(1);
  var total = 0;
  var orders = [];

  for (var i = 0; i < dataRows.length; i++) {
    var row = dataRows[i];
    var orderId = String(row[COL.ORDER] || '').replace(/\D/g, '');
    if (!orderId) continue;
    total++;
    if (!query) continue;
    var courier = String(row[courierCol] || '');
    if (!courierMatch_(courier, query)) continue;
    orders.push({
      order_id: orderId,
      sticker: String(row[COL.STICKER] || ''),
      article: String(row[COL.ARTICLE] || ''),
      name: String(row[COL.NAME] || ''),
      delivery: String(row[COL.DELIVERY] || ''),
      status: String(row[COL.STATUS] || ''),
      courier: courier,
      courier_change: String(row[COL.CHANGE] || '')
    });
  }

  return {
    ok: true,
    query: query,
    count: orders.length,
    total: total,
    orders: orders
  };
}

/** Все слова запроса должны встречаться в строке курьера (без учёта регистра, ё=е). */
function courierMatch_(courierCell, query) {
  var hay = normalize_(courierCell);
  var q = normalize_(query);
  if (!hay || !q) return false;
  var tokens = q.split(/\s+/).filter(function (t) { return t; });
  for (var i = 0; i < tokens.length; i++) {
    if (hay.indexOf(tokens[i]) === -1) return false;
  }
  return true;
}

function normalize_(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Для проверки из редактора скриптов. */
function testSearch() {
  Logger.log(JSON.stringify(searchCourier('Лысенко')));
}
