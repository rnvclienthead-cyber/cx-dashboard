<?php

if (!defined('BOOTSTRAP')) { die('Access denied'); }

fn_register_hooks(
    'place_order',
    'order_placement_routines',
    'change_order_status',
    'get_order_info',
    'csrf_validate_request'
);

if (defined('AREA') && AREA === 'A' && function_exists('fn_vidovito_shop_orders_ensure_statuses')) {
    fn_vidovito_shop_orders_ensure_statuses();
}
