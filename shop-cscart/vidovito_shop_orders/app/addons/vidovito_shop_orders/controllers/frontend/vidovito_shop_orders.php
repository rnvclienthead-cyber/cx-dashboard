<?php

if (!defined('BOOTSTRAP')) { die('Access denied'); }

if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $mode !== 'set_status') {
    return;
}

header('Content-Type: application/json; charset=utf-8');

$settings = fn_vidovito_shop_orders_settings();
$expected = (string) ($settings['token'] ?? '');
$got = (string) ($_SERVER['HTTP_X_SHOP_TOKEN'] ?? '');
if ($expected === '' || !hash_equals($expected, $got)) {
    echo json_encode(array('status' => 'error', 'message' => 'Нет доступа'), JSON_UNESCAPED_UNICODE);
    exit;
}

$raw = file_get_contents('php://input');
$json = json_decode((string) $raw, true);
if (!is_array($json)) {
    $json = array();
}

$order_id = intval($json['order_id'] ?? ($_REQUEST['order_id'] ?? 0));
$status = strtoupper((string) ($json['status'] ?? ($_REQUEST['status'] ?? '')));
$result = fn_vidovito_shop_orders_apply_status($order_id, $status, $json);
echo json_encode(array(
    'status' => !empty($result['ok']) ? 'ok' : 'error',
    'data' => $result,
), JSON_UNESCAPED_UNICODE);
exit;
