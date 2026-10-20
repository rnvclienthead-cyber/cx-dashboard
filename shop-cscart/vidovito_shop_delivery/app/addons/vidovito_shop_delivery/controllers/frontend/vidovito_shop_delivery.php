<?php

use Tygh\Tygh;

if (!defined('BOOTSTRAP')) { die('Access denied'); }

if ($_SERVER['REQUEST_METHOD'] === 'POST' && $mode === 'select') {
    $carrier = strtolower((string) ($_REQUEST['carrier'] ?? ''));
    $payload = array(
        'carrier' => $carrier,
        'pvz_code' => (string) ($_REQUEST['pvz_code'] ?? ''),
        'pvz_name' => (string) ($_REQUEST['pvz_name'] ?? ''),
        'pvz_address' => (string) ($_REQUEST['pvz_address'] ?? ''),
        'city' => (string) ($_REQUEST['city'] ?? ''),
        'address' => (string) ($_REQUEST['address'] ?? ''),
        'zip' => (string) ($_REQUEST['zip'] ?? ''),
        'state' => (string) ($_REQUEST['state'] ?? ''),
        'lat' => $_REQUEST['lat'] ?? null,
        'lon' => $_REQUEST['lon'] ?? null,
        'cost' => floatval($_REQUEST['cost'] ?? 0),
        'distance_km' => $_REQUEST['distance_km'] ?? null,
        'delivery_time' => (string) ($_REQUEST['delivery_time'] ?? '2–5 дней'),
    );
    if ($carrier === 'ozon') {
        $payload['cost'] = 0;
    }
    $cost_ok = ($carrier === 'ozon') || $payload['cost'] > 0;
    if (!in_array($carrier, array('cdek', 'yandex', 'ozon'), true) || $payload['pvz_code'] === '' || !$cost_ok) {
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(array('status' => 'error', 'message' => 'Не выбран пункт выдачи'));
        exit;
    }

    $_SESSION['vidovito_shop_delivery']['selected'] = $payload;
    $cart = &Tygh::$app['session']['cart'];
    $shipping_id = fn_vidovito_shop_delivery_apply_to_cart($cart);

    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(array(
        'status' => 'ok',
        'shipping_id' => $shipping_id,
        'cost' => $payload['cost'],
    ));
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && $mode === 'clear') {
    unset($_SESSION['vidovito_shop_delivery']['selected']);
    $cart = &Tygh::$app['session']['cart'];
    unset($cart['vidovito_shop_delivery']);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(array('status' => 'ok'));
    exit;
}

if ($mode === 'suggest') {
    $resp = fn_vidovito_shop_delivery_api('/api/v1/shop/delivery/suggest-address', array(
        'q' => (string) ($_REQUEST['q'] ?? ''),
        'count' => 7,
    ));
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($resp['data'] ?: array('suggestions' => array()));
    exit;
}

if ($mode === 'points') {
    $carrier = strtolower((string) ($_REQUEST['carrier'] ?? ''));
    $query = array_filter(array(
        'carrier' => $carrier,
        'city' => (string) ($_REQUEST['city'] ?? ''),
        'address' => (string) ($_REQUEST['address'] ?? ''),
        'lat' => $_REQUEST['lat'] ?? null,
        'lon' => $_REQUEST['lon'] ?? null,
        'weight_g' => fn_vidovito_shop_delivery_weight_g(),
        'declared_cost' => floatval($_REQUEST['declared_cost'] ?? 500),
        'phone' => (string) ($_REQUEST['phone'] ?? ''),
        'packages' => json_encode(fn_vidovito_shop_delivery_packages(), JSON_UNESCAPED_UNICODE),
    ), function ($v) {
        return $v !== null && $v !== '';
    });
    $resp = fn_vidovito_shop_delivery_api('/api/v1/shop/delivery/points', $query);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($resp['data'] ?: array('status' => 'error'));
    exit;
}

if ($mode === 'maps') {
    $resp = fn_vidovito_shop_delivery_api('/api/v1/shop/delivery/maps-config');
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($resp['data'] ?: array());
    exit;
}
