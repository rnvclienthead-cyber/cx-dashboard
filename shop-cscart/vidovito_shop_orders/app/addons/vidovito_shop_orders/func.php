<?php

use Tygh\Registry;
use Tygh\Http;

if (!defined('BOOTSTRAP')) { die('Access denied'); }

function fn_vidovito_shop_orders_settings()
{
    $own = Registry::get('addons.vidovito_shop_orders') ?: array();
    $url = rtrim((string) ($own['api_url'] ?? ''), '/');
    $token = (string) ($own['api_token'] ?? '');
    if ($url === '' || $token === '') {
        $delivery = Registry::get('addons.vidovito_shop_delivery') ?: array();
        if ($url === '') {
            $url = rtrim((string) ($delivery['api_url'] ?? 'https://cxvo.ru'), '/');
        }
        if ($token === '') {
            $token = (string) ($delivery['api_token'] ?? '');
        }
    }
    if ($url === '') {
        $url = 'https://cxvo.ru';
    }
    return array(
        'api_url' => $url,
        'token' => $token,
    );
}

function fn_vidovito_shop_orders_to_grams($weight)
{
    $weight = floatval($weight);
    if ($weight <= 0) {
        return 0;
    }
    $symbol = strtolower((string) Registry::get('settings.General.weight_symbol'));
    if (in_array($symbol, array('g', 'гр', 'gram', 'grams', 'г'), true)) {
        return (int) max(1, round($weight));
    }
    return (int) max(1, round($weight * 1000));
}

function fn_vidovito_shop_orders_save_extra($order_id, $selected)
{
    $order_id = intval($order_id);
    if ($order_id < 1 || !is_array($selected)) {
        return;
    }
    if (empty($selected['carrier']) && empty($selected['cx'])) {
        return;
    }
    db_query('REPLACE INTO ?:order_data ?e', array(
        'order_id' => $order_id,
        'type' => 'X',
        'data' => serialize($selected),
    ));
}

function fn_vidovito_shop_orders_load_extra($order_id)
{
    $raw = db_get_field(
        'SELECT data FROM ?:order_data WHERE order_id = ?i AND type = ?s',
        intval($order_id),
        'X'
    );
    if (!$raw) {
        return array();
    }
    $data = @unserialize($raw);
    return is_array($data) ? $data : array();
}

function fn_vidovito_shop_orders_selected($cart = null)
{
    if (is_array($cart) && !empty($cart['vidovito_shop_delivery']) && is_array($cart['vidovito_shop_delivery'])) {
        return $cart['vidovito_shop_delivery'];
    }
    if (function_exists('fn_vidovito_shop_delivery_selected')) {
        $from_delivery = fn_vidovito_shop_delivery_selected();
        if (is_array($from_delivery) && !empty($from_delivery['carrier'])) {
            return $from_delivery;
        }
    }
    $all = isset($_SESSION['vidovito_shop_delivery']['selected'])
        ? $_SESSION['vidovito_shop_delivery']['selected']
        : array();
    return is_array($all) ? $all : array();
}

function fn_vidovito_shop_orders_product_box($product_id, $item = array())
{
    $product_id = intval($product_id);
    $row = $product_id ? db_get_row('SELECT * FROM ?:products WHERE product_id = ?i', $product_id) : array();
    if (!is_array($row)) {
        $row = array();
    }
    $params = array();
    if (!empty($row['shipping_params'])) {
        $decoded = @unserialize($row['shipping_params']);
        if (is_array($decoded)) {
            $params = $decoded;
        }
    }
    $length = intval(round(floatval($params['box_length'] ?? $row['length'] ?? 0)));
    $width = intval(round(floatval($params['box_width'] ?? $row['width'] ?? 0)));
    $height = intval(round(floatval($params['box_height'] ?? $row['height'] ?? 0)));
    if ($length < 1 || $width < 1 || $height < 1) {
        $length = 40;
        $width = 30;
        $height = 20;
    }
    $grams = fn_vidovito_shop_orders_to_grams($item['weight'] ?? $row['weight'] ?? 0);
    if ($grams < 1) {
        $grams = 2000;
    }
    return array($length, $width, $height, $grams);
}

function fn_vidovito_shop_orders_api_post($path, $body)
{
    $settings = fn_vidovito_shop_orders_settings();
    if ($settings['token'] === '') {
        return array('ok' => false, 'status' => 0, 'data' => null, 'raw' => '');
    }
    $url = $settings['api_url'] . $path;
    $payload = json_encode($body, JSON_UNESCAPED_UNICODE);
    $headers = array(
        'X-Shop-Token: ' . $settings['token'],
        'Content-Type: application/json',
        'Accept: application/json',
    );
    $raw = @file_get_contents($url, false, stream_context_create(array(
        'http' => array(
            'method' => 'POST',
            'header' => implode("\r\n", $headers) . "\r\n",
            'content' => $payload,
            'timeout' => 20,
            'ignore_errors' => true,
        ),
    )));
    if (!is_string($raw) || $raw === '') {
        $raw = Http::post($url, $payload, array(
            'timeout' => 20,
            'headers' => $headers,
        ));
    }
    $decoded = json_decode((string) $raw, true);
    return array(
        'ok' => is_array($decoded) && (($decoded['status'] ?? '') === 'ok'),
        'status' => is_array($decoded) ? ($decoded['status'] ?? 'error') : 'error',
        'data' => $decoded,
        'raw' => $raw,
    );
}

function fn_vidovito_shop_orders_payload($order_id, $order = null)
{
    $order_id = intval($order_id);
    if (!is_array($order) || empty($order['products'])) {
        $order = fn_get_order_info($order_id);
    }
    if (!is_array($order) || empty($order['order_id'])) {
        return null;
    }
    $selected = fn_vidovito_shop_orders_load_extra($order_id);
    if (!$selected) {
        $selected = fn_vidovito_shop_orders_selected($order);
        if ($selected) {
            fn_vidovito_shop_orders_save_extra($order_id, $selected);
        }
    }
    $shipping_method = '';
    if (!empty($order['shipping']) && is_array($order['shipping'])) {
        $first = reset($order['shipping']);
        $shipping_method = (string) ($first['shipping'] ?? '');
    }
    $payment_name = '';
    if (!empty($order['payment_method']['payment'])) {
        $payment_name = (string) $order['payment_method']['payment'];
    } elseif (!empty($order['payment_method'])) {
        $payment_name = (string) $order['payment_method'];
    }
    $track = (string) ($order['tracking'] ?? '');
    if ($track === '' && !empty($order['shipments']) && is_array($order['shipments'])) {
        $shipment = reset($order['shipments']);
        $track = (string) ($shipment['tracking_number'] ?? '');
    }
    $items = array();
    foreach ($order['products'] as $item) {
        if (!empty($item['extra']['exclude_from_calculate'])) {
            continue;
        }
        list($length, $width, $height, $grams) = fn_vidovito_shop_orders_product_box(
            intval($item['product_id'] ?? 0),
            $item
        );
        $items[] = array(
            'product_id' => intval($item['product_id'] ?? 0),
            'product_code' => (string) ($item['product_code'] ?? ''),
            'name' => (string) ($item['product'] ?? $item['product_code'] ?? 'Товар'),
            'amount' => max(1, intval($item['amount'] ?? 1)),
            'price' => floatval($item['price'] ?? 0),
            'weight_g' => $grams,
            'length_cm' => $length,
            'width_cm' => $width,
            'height_cm' => $height,
        );
    }
    $paid_statuses = array('P', 'C');
    $status = strtoupper((string) ($order['status'] ?? 'O'));
    $status_title = fn_vidovito_shop_orders_status_title($status);
    $status_kind = fn_vidovito_shop_orders_kind_by_title($status_title);
    if ($status_kind === '') {
        $status_kind = fn_vidovito_shop_orders_kind($status);
    }
    $paid = in_array($status, $paid_statuses, true) || $status_kind === 'paid';
    if (!$paid && (stripos($payment_name, 'тест') !== false || stripos($payment_name, 'test') !== false)) {
        $paid = true;
    }
    return array(
        'cscart_order_id' => $order_id,
        'cscart_status' => $status,
        'status_title' => $status_title,
        'status_kind' => $status_kind,
        'paid' => $paid,
        'payment_name' => $payment_name,
        'shipping_method' => $shipping_method,
        'total' => floatval($order['total'] ?? 0),
        'shipping_cost' => floatval($order['shipping_cost'] ?? $order['display_shipping_cost'] ?? 0),
        'currency' => (string) ($order['secondary_currency'] ?? 'RUB'),
        'created_at' => date('c', intval($order['timestamp'] ?? TIME)),
        'notes' => (string) ($order['notes'] ?? ''),
        'details' => (string) ($order['details'] ?? ''),
        'shop_barcode' => $track,
        'customer' => array(
            'name' => trim((string) ($order['firstname'] ?? '') . ' ' . (string) ($order['lastname'] ?? '')),
            'email' => (string) ($order['email'] ?? ''),
            'phone' => (string) ($order['phone'] ?? ($order['s_phone'] ?? ($order['b_phone'] ?? ''))),
            'company' => (string) ($order['company'] ?? ''),
        ),
        'shipping_address' => array(
            'city' => (string) ($order['s_city'] ?? ''),
            'address' => (string) ($order['s_address'] ?? ''),
            'zip' => (string) ($order['s_zipcode'] ?? ''),
            'state' => (string) ($order['s_state_descr'] ?? ($order['s_state'] ?? '')),
            'country' => (string) ($order['s_country_descr'] ?? ($order['s_country'] ?? 'Россия')),
            'phone' => (string) ($order['s_phone'] ?? ''),
        ),
        'delivery' => is_array($selected) ? $selected : array(),
        'items' => $items,
    );
}

function fn_vidovito_shop_orders_push($order_id, $order = null)
{
    $payload = fn_vidovito_shop_orders_payload($order_id, $order);
    if (!$payload) {
        return false;
    }
    $resp = fn_vidovito_shop_orders_api_post('/api/v1/shop/orders/ingest', $payload);
    if (!$resp['ok']) {
        error_log('vidovito_shop_orders: CX ingest failed for order ' . intval($order_id));
    }
    return $resp['ok'];
}

function fn_vidovito_shop_orders_place_order($order_id, $action, &$order_status, &$cart, &$auth)
{
    $selected = fn_vidovito_shop_orders_selected(is_array($cart) ? $cart : null);
    if ($selected) {
        fn_vidovito_shop_orders_save_extra($order_id, $selected);
    }
}

function fn_vidovito_shop_orders_order_placement_routines($order_id, $force_notification, $order_info, $error)
{
    if ($error) {
        return;
    }
    fn_vidovito_shop_orders_push($order_id, is_array($order_info) ? $order_info : null);
}

function fn_vidovito_shop_orders_change_order_status($status_to, $status_from, $order_info)
{
    if (!empty($GLOBALS['vidovito_shop_orders_from_cx'])) {
        return;
    }
    if (empty($order_info['order_id'])) {
        return;
    }
    fn_vidovito_shop_orders_push($order_info['order_id'], $order_info);
}

function fn_vidovito_shop_orders_get_order_info(&$order, $additional_data)
{
    fn_vidovito_shop_orders_ensure_statuses();
    if (empty($order['order_id'])) {
        return;
    }
    $selected = fn_vidovito_shop_orders_load_extra($order['order_id']);
    if ($selected) {
        $order['vidovito_shop_order'] = $selected;
    }
}

function fn_vidovito_shop_orders_install()
{
    fn_vidovito_shop_orders_ensure_statuses();
}

function fn_vidovito_shop_orders_status_defs()
{
    return array(
        'placed' => array(
            'title' => 'Заказ оформлен',
            'aliases' => array('Открыт', 'Принят'),
            'color' => '#0284C7',
            'preferred' => array('O'),
        ),
        'paid' => array(
            'title' => 'Оплачен',
            'aliases' => array('Обработан'),
            'color' => '#059669',
            'preferred' => array('P'),
        ),
        'packed' => array(
            'title' => 'Собран',
            'color' => '#0D9488',
            'preferred' => array('K', 'L', 'M', 'S', 'T', 'W', 'X', 'Z', 'G'),
        ),
        'shipped' => array(
            'title' => 'Отправлен',
            'color' => '#059669',
            'preferred' => array('S', 'L', 'M', 'T', 'W', 'X', 'Z', 'K', 'G'),
        ),
        'in_transit' => array(
            'title' => 'В пути',
            'color' => '#4F46E5',
            'preferred' => array('T', 'L', 'M', 'W', 'X', 'Z', 'G', 'K', 'S'),
        ),
        'ready_for_pickup' => array(
            'title' => 'Готов к выдаче',
            'color' => '#7C3AED',
            'preferred' => array('G', 'L', 'M', 'W', 'X', 'Z', 'T', 'K', 'S'),
        ),
        'delivered' => array(
            'title' => 'Выдан',
            'color' => '#334155',
            'preferred' => array('W', 'L', 'M', 'X', 'Z', 'G', 'T', 'K', 'S'),
        ),
        'returned' => array(
            'title' => 'Возвращён',
            'aliases' => array('Возвращен', 'Возврат'),
            'color' => '#B45309',
            'preferred' => array('R', 'L', 'M', 'X', 'Z', 'W', 'G', 'T', 'K'),
        ),
        'cancelled_seller' => array(
            'title' => 'Отменён продавцом',
            'aliases' => array('Отменен продавцом'),
            'color' => '#B91C1C',
            'preferred' => array('A', 'E', 'J', 'Q', 'U', 'V'),
        ),
        'cancelled_buyer' => array(
            'title' => 'Отменён покупателем',
            'aliases' => array('Отменен покупателем'),
            'color' => '#9F1239',
            'preferred' => array('E', 'J', 'Q', 'U', 'V', 'A'),
        ),
        'incomplete' => array(
            'title' => 'Незавершённый заказ',
            'aliases' => array('Незавершенный заказ', 'Незавершённый', 'Незавершенный', 'Не завершен', 'Не завершён'),
            'color' => '#78716C',
            'preferred' => array('N', 'J', 'Q', 'U', 'V', 'E'),
        ),
    );
}

function fn_vidovito_shop_orders_norm_title($title)
{
    $title = str_replace(array('ё', 'Ё'), array('е', 'Е'), trim((string) $title));
    if (function_exists('fn_strtolower')) {
        return fn_strtolower($title);
    }
    return mb_strtolower($title, 'UTF-8');
}

function fn_vidovito_shop_orders_status_letter_by_title($type, $titles)
{
    $wanted = array();
    foreach ((array) $titles as $title) {
        $key = fn_vidovito_shop_orders_norm_title($title);
        if ($key !== '' && !in_array($key, $wanted, true)) {
            $wanted[] = $key;
        }
    }
    if (!$wanted || !function_exists('fn_get_statuses')) {
        return '';
    }
    $all = fn_get_statuses($type);
    if (!is_array($all)) {
        return '';
    }
    $found = array();
    foreach ($all as $code => $row) {
        $name = fn_vidovito_shop_orders_norm_title($row['description'] ?? '');
        if ($name !== '' && !isset($found[$name])) {
            $found[$name] = (string) $code;
        }
    }
    foreach ($wanted as $name) {
        if (isset($found[$name])) {
            return $found[$name];
        }
    }
    return '';
}

function fn_vidovito_shop_orders_status_title($status)
{
    $status = strtoupper(trim((string) $status));
    if ($status === '' || !function_exists('fn_get_statuses')) {
        return '';
    }
    $type = defined('STATUSES_ORDER') ? STATUSES_ORDER : 'O';
    $all = fn_get_statuses($type);
    if (!is_array($all) || empty($all[$status]['description'])) {
        return '';
    }
    return trim((string) $all[$status]['description']);
}

function fn_vidovito_shop_orders_kind_by_title($title)
{
    $key = fn_vidovito_shop_orders_norm_title($title);
    if ($key === '') {
        return '';
    }
    foreach (fn_vidovito_shop_orders_status_defs() as $kind => $def) {
        $names = array_merge(array($def['title']), (array) ($def['aliases'] ?? array()));
        foreach ($names as $name) {
            if (fn_vidovito_shop_orders_norm_title($name) === $key) {
                return $kind;
            }
        }
    }
    return '';
}

function fn_vidovito_shop_orders_next_letter($type, $preferred)
{
    $used = db_get_fields('SELECT status FROM ?:statuses WHERE type = ?s', $type);
    if (!is_array($used)) {
        $used = array();
    }
    foreach ((array) $preferred as $letter) {
        $letter = strtoupper((string) $letter);
        if ($letter !== '' && !in_array($letter, $used, true)) {
            return $letter;
        }
    }
    foreach (range('A', 'Z') as $letter) {
        if (!in_array($letter, $used, true)) {
            return $letter;
        }
    }
    return '';
}

function fn_vidovito_shop_orders_insert_status($type, $letter, $title, $color)
{
    $letter = strtoupper((string) $letter);
    if ($letter === '' || $title === '') {
        return '';
    }
    $status_id = db_query('INSERT INTO ?:statuses ?e', array(
        'status' => $letter,
        'type' => $type,
        'is_default' => 'N',
    ));
    if (!$status_id) {
        $status_id = db_get_field(
            'SELECT status_id FROM ?:statuses WHERE status = ?s AND type = ?s',
            $letter,
            $type
        );
    }
    if (!$status_id) {
        return '';
    }
    $langs = db_get_fields('SELECT lang_code FROM ?:languages');
    if (!is_array($langs) || !$langs) {
        $langs = array(defined('CART_LANGUAGE') ? CART_LANGUAGE : 'ru');
    }
    foreach ($langs as $lang) {
        db_query('REPLACE INTO ?:status_descriptions ?e', array(
            'status_id' => $status_id,
            'description' => $title,
            'email_subj' => '',
            'email_header' => '',
            'lang_code' => $lang,
        ));
    }
    foreach (array(
        'color' => $color,
        'inventory' => 'D',
        'remove_cc_info' => 'Y',
        'repay' => 'N',
        'appearance_type' => 'D',
    ) as $param => $value) {
        db_query('REPLACE INTO ?:status_data ?e', array(
            'status_id' => $status_id,
            'param' => $param,
            'value' => $value,
        ));
    }
    return $letter;
}

function fn_vidovito_shop_orders_create_named_status($type, $title, $color, $preferred, $aliases = array())
{
    $names = array_merge(array($title), (array) $aliases);
    $existing = fn_vidovito_shop_orders_status_letter_by_title($type, $names);
    if ($existing) {
        return $existing;
    }
    $letter = fn_vidovito_shop_orders_next_letter($type, $preferred);
    if ($letter === '') {
        return '';
    }
    return fn_vidovito_shop_orders_insert_status($type, $letter, $title, $color);
}

function fn_vidovito_shop_orders_ensure_statuses()
{
    static $map = null;
    if (is_array($map)) {
        return $map;
    }
    $type = defined('STATUSES_ORDER') ? STATUSES_ORDER : 'O';
    $map = array();
    $created = false;
    foreach (fn_vidovito_shop_orders_status_defs() as $kind => $def) {
        $names = array_merge(array($def['title']), (array) ($def['aliases'] ?? array()));
        $before = fn_vidovito_shop_orders_status_letter_by_title($type, $names);
        $letter = fn_vidovito_shop_orders_create_named_status(
            $type,
            $def['title'],
            $def['color'],
            $def['preferred'],
            $def['aliases'] ?? array()
        );
        $map[$kind] = $letter;
        if ($letter && !$before) {
            $created = true;
        }
    }
    if ($created && function_exists('fn_clear_cache')) {
        fn_clear_cache();
    }
    return $map;
}

function fn_vidovito_shop_orders_kind($status)
{
    $status = strtoupper(trim((string) $status));
    $aliases = array(
        'PLACED' => 'placed',
        'NEW' => 'placed',
        'PAID' => 'paid',
        'PACKED' => 'packed',
        'ASSEMBLED' => 'packed',
        'K' => 'packed',
        'SHIPPED' => 'shipped',
        'SENT' => 'shipped',
        'IN_TRANSIT' => 'in_transit',
        'TRANSIT' => 'in_transit',
        'READY_FOR_PICKUP' => 'ready_for_pickup',
        'PICKUP' => 'ready_for_pickup',
        'READY' => 'ready_for_pickup',
        'DELIVERED' => 'delivered',
        'ISSUED' => 'delivered',
        'RETURNED' => 'returned',
        'RETURN' => 'returned',
        'CANCELLED_SELLER' => 'cancelled_seller',
        'CANCELLED_BUYER' => 'cancelled_buyer',
        'INCOMPLETE' => 'incomplete',
    );
    if (isset($aliases[$status])) {
        return $aliases[$status];
    }
    $defs = fn_vidovito_shop_orders_status_defs();
    return isset($defs[strtolower($status)]) ? strtolower($status) : '';
}

function fn_vidovito_shop_orders_status_label($kind)
{
    $defs = fn_vidovito_shop_orders_status_defs();
    return $defs[$kind]['title'] ?? $kind;
}

function fn_vidovito_shop_orders_carrier_key($carrier)
{
    $carrier = strtolower(trim((string) $carrier));
    $map = array(
        'ozon' => 'vidovito_ozon',
        'cdek' => 'vidovito_cdek',
        'yandex' => 'vidovito_yandex',
    );
    return $map[$carrier] ?? '';
}

function fn_vidovito_shop_orders_csrf_validate_request(&$params, &$valid)
{
    $dispatch = (string) ($params['dispatch'] ?? ($_REQUEST['dispatch'] ?? ''));
    if ($dispatch === 'vidovito_shop_orders.set_status') {
        $valid = true;
    }
}

function fn_vidovito_shop_orders_sync_shipment($order_id, $carrier, $parcels)
{
    $order_id = intval($order_id);
    if ($order_id < 1 || !is_array($parcels) || !function_exists('fn_update_shipment')) {
        return;
    }
    $track = '';
    $comment = '';
    foreach ($parcels as $parcel) {
        if (!is_array($parcel)) {
            continue;
        }
        $number = trim((string) ($parcel['track_number'] ?? ''));
        if ($number !== '') {
            $track = $number;
            $comment = trim((string) ($parcel['status_label'] ?? ''));
            break;
        }
    }
    if ($track === '') {
        return;
    }
    $carrier_key = fn_vidovito_shop_orders_carrier_key($carrier);
    $existing = db_get_field(
        'SELECT s.shipment_id FROM ?:shipments AS s'
        . ' INNER JOIN ?:shipment_items AS i ON i.shipment_id = s.shipment_id'
        . ' WHERE i.order_id = ?i'
        . ' ORDER BY s.shipment_id DESC',
        $order_id
    );
    if ($existing) {
        db_query(
            'UPDATE ?:shipments SET tracking_number = ?s, carrier = ?s, comments = ?s WHERE shipment_id = ?i',
            $track,
            $carrier_key,
            $comment,
            intval($existing)
        );
        return;
    }
    $order = fn_get_order_info($order_id);
    $products = array();
    if (!empty($order['products']) && is_array($order['products'])) {
        foreach ($order['products'] as $item_id => $product) {
            $products[$item_id] = max(1, intval($product['amount'] ?? 1));
        }
    }
    $shipment = array(
        'order_id' => $order_id,
        'tracking_number' => $track,
        'carrier' => $carrier_key,
        'comments' => $comment,
        'products' => $products,
    );
    try {
        fn_update_shipment($shipment, 0, 0, true, array('C' => false, 'A' => false));
    } catch (Exception $e) {
        error_log('vidovito_shop_orders: shipment failed for order ' . $order_id . ': ' . $e->getMessage());
    }
}

function fn_vidovito_shop_orders_apply_status($order_id, $status, $extra = array())
{
    $order_id = intval($order_id);
    $kind = fn_vidovito_shop_orders_kind($status);
    $map = fn_vidovito_shop_orders_ensure_statuses();
    $letter = $kind && !empty($map[$kind]) ? (string) $map[$kind] : '';
    $parcels = array();
    if (is_array($extra) && !empty($extra['parcels']) && is_array($extra['parcels'])) {
        $parcels = array_values($extra['parcels']);
    }
    if ($order_id < 1) {
        return array('ok' => false, 'message' => 'Некорректный заказ');
    }
    $current = db_get_field('SELECT status FROM ?:orders WHERE order_id = ?i', $order_id);
    if (!$current) {
        return array('ok' => false, 'message' => 'Заказ не найден');
    }
    $selected = fn_vidovito_shop_orders_load_extra($order_id);
    if (!is_array($selected)) {
        $selected = array();
    }
    if (!empty($extra['carrier'])) {
        $selected['carrier'] = $extra['carrier'];
    }
    if (!empty($extra['pvz_name'])) {
        $selected['pvz_name'] = $extra['pvz_name'];
    }
    if (!empty($extra['pvz_address'])) {
        $selected['pvz_address'] = $extra['pvz_address'];
    }
    $selected['cx'] = array(
        'status' => $kind,
        'status_label' => $kind ? fn_vidovito_shop_orders_status_label($kind) : '',
        'carrier' => (string) ($extra['carrier'] ?? ($selected['carrier'] ?? '')),
        'carrier_label' => (string) ($extra['carrier_label'] ?? ''),
        'pvz_name' => (string) ($extra['pvz_name'] ?? ($selected['pvz_name'] ?? '')),
        'pvz_address' => (string) ($extra['pvz_address'] ?? ($selected['pvz_address'] ?? '')),
        'parcels' => $parcels,
        'updated_at' => time(),
    );
    fn_vidovito_shop_orders_save_extra($order_id, $selected);
    $GLOBALS['vidovito_shop_orders_from_cx'] = true;
    if ($parcels) {
        fn_vidovito_shop_orders_sync_shipment(
            $order_id,
            $selected['cx']['carrier'],
            $parcels
        );
    }
    if ($letter !== '' && $current !== $letter) {
        fn_change_order_status($order_id, $letter, $current, array('C' => false, 'A' => false));
    }
    $GLOBALS['vidovito_shop_orders_from_cx'] = false;
    $fresh = db_get_field('SELECT status FROM ?:orders WHERE order_id = ?i', $order_id);
    if ($letter === '') {
        return array('ok' => true, 'status' => $fresh, 'kind' => $kind, 'tracking_only' => true);
    }
    if ($current === $letter) {
        return array('ok' => true, 'status' => $letter, 'unchanged' => true, 'kind' => $kind);
    }
    return array('ok' => $fresh === $letter, 'status' => $fresh, 'kind' => $kind);
}
