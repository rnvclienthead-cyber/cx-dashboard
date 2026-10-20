<?php

use Tygh\Languages\Languages;
use Tygh\Registry;
use Tygh\Http;
use Tygh\Tygh;

if (!defined('BOOTSTRAP')) { die('Access denied'); }

function fn_vidovito_shop_delivery_install()
{
    $services = array(
        'vidovito_cdek' => 'СДЭК (пункт выдачи)',
        'vidovito_yandex' => 'Яндекс Доставка (пункт выдачи)',
        'vidovito_ozon' => 'Ozon Доставка (пункт выдачи)',
    );

    foreach ($services as $code => $description) {
        $exists = db_get_field('SELECT service_id FROM ?:shipping_services WHERE module = ?s AND code = ?s', $code, $code);
        if ($exists) {
            continue;
        }
        $service_id = db_query('INSERT INTO ?:shipping_services ?e', array(
            'status' => 'A',
            'module' => $code,
            'code' => $code,
            'sp_file' => '',
        ));
        foreach (array_keys(Languages::getAll()) as $lang_code) {
            db_query('INSERT INTO ?:shipping_service_descriptions ?e', array(
                'service_id' => $service_id,
                'description' => $description,
                'lang_code' => $lang_code,
            ));
        }
    }
}

function fn_vidovito_shop_delivery_method_titles()
{
    return array(
        'vidovito_cdek' => 'СДЭК (пункт выдачи)',
        'vidovito_yandex' => 'Яндекс Доставка (пункт выдачи)',
        'vidovito_ozon' => 'Ozon Доставка (пункт выдачи)',
    );
}

function fn_vidovito_shop_delivery_ensure_methods()
{
    static $done = false;
    if ($done) {
        return;
    }
    $done = true;
    fn_vidovito_shop_delivery_install();
    $prefix = (string) Registry::get('config.table_prefix');
    $has_storefronts = $prefix !== '' && (bool) db_get_field('SHOW TABLES LIKE ?s', $prefix . 'storefronts_shippings');
    $storefronts = $has_storefronts ? db_get_fields('SELECT storefront_id FROM ?:storefronts') : array();

    foreach (fn_vidovito_shop_delivery_method_titles() as $code => $title) {
        $service_id = (int) db_get_field(
            'SELECT service_id FROM ?:shipping_services WHERE module = ?s AND code = ?s',
            $code,
            $code
        );
        if ($service_id < 1) {
            continue;
        }
        $shipping_id = (int) db_get_field(
            'SELECT shipping_id FROM ?:shippings WHERE service_id = ?i AND status = ?s ORDER BY shipping_id ASC',
            $service_id,
            'A'
        );
        if ($shipping_id < 1) {
            $shipping_id = (int) db_get_field(
                'SELECT shipping_id FROM ?:shippings WHERE service_id = ?i ORDER BY shipping_id ASC',
                $service_id
            );
            if ($shipping_id > 0) {
                db_query(
                    'UPDATE ?:shippings SET status = ?s, rate_calculation = ?s WHERE shipping_id = ?i',
                    'A',
                    'R',
                    $shipping_id
                );
            } else {
                $shipping_id = (int) db_query('INSERT INTO ?:shippings ?e', array(
                    'company_id' => 0,
                    'min_weight' => 0,
                    'max_weight' => 0,
                    'rate_calculation' => 'R',
                    'service_id' => $service_id,
                    'service_params' => '',
                    'localization' => '',
                    'tax_ids' => '',
                    'usergroup_ids' => '0',
                    'position' => 0,
                    'status' => 'A',
                ));
                if ($shipping_id > 0) {
                    foreach (array_keys(Languages::getAll()) as $lang_code) {
                        db_query('INSERT INTO ?:shipping_descriptions ?e', array(
                            'shipping_id' => $shipping_id,
                            'lang_code' => $lang_code,
                            'shipping' => $title,
                            'delivery_time' => '2–5 дней',
                            'description' => '',
                        ));
                    }
                }
            }
        }
        if ($shipping_id < 1 || !$has_storefronts) {
            continue;
        }
        foreach ($storefronts as $storefront_id) {
            $linked = db_get_field(
                'SELECT shipping_id FROM ?:storefronts_shippings WHERE storefront_id = ?i AND shipping_id = ?i',
                $storefront_id,
                $shipping_id
            );
            if ($linked) {
                continue;
            }
            db_query('INSERT INTO ?:storefronts_shippings ?e', array(
                'storefront_id' => $storefront_id,
                'shipping_id' => $shipping_id,
            ));
        }
    }
}

function fn_vidovito_shop_delivery_uninstall()
{
    $ids = db_get_fields(
        'SELECT service_id FROM ?:shipping_services WHERE module IN (?a)',
        array('vidovito_cdek', 'vidovito_yandex', 'vidovito_ozon')
    );
    if ($ids) {
        db_query('DELETE FROM ?:shipping_service_descriptions WHERE service_id IN (?n)', $ids);
        db_query('DELETE FROM ?:shipping_services WHERE service_id IN (?n)', $ids);
    }
}

function fn_vidovito_shop_delivery_builtin_token()
{
    return 'QmiNVy7-uQMX_nLm_oTizGdXc4I7Nkdx0ztNqk6G5qI';
}

function fn_vidovito_shop_delivery_restore_token()
{
    static $done = false;
    if ($done) {
        return;
    }
    $done = true;
    $settings = Registry::get('addons.vidovito_shop_delivery') ?: array();
    if (trim((string) ($settings['api_token'] ?? '')) !== '') {
        return;
    }
    $token = fn_vidovito_shop_delivery_builtin_token();
    Registry::set('addons.vidovito_shop_delivery.api_token', $token);
    if (trim((string) ($settings['api_url'] ?? '')) === '') {
        Registry::set('addons.vidovito_shop_delivery.api_url', 'https://cxvo.ru');
    }
    $section_id = (int) db_get_field(
        'SELECT section_id FROM ?:settings_sections WHERE name = ?s AND type = ?s',
        'vidovito_shop_delivery',
        'ADDON'
    );
    if ($section_id < 1) {
        return;
    }
    db_query(
        'UPDATE ?:settings_objects SET value = ?s WHERE name = ?s AND section_id = ?i AND value = ?s',
        $token,
        'api_token',
        $section_id,
        ''
    );
}

function fn_vidovito_shop_delivery_settings()
{
    fn_vidovito_shop_delivery_restore_token();
    $settings = Registry::get('addons.vidovito_shop_delivery') ?: array();
    $url = rtrim((string) ($settings['api_url'] ?? ''), '/');
    if ($url === '') {
        $url = 'https://cxvo.ru';
    }
    $token = trim((string) ($settings['api_token'] ?? ''));
    if ($token === '') {
        $token = fn_vidovito_shop_delivery_builtin_token();
    }
    return array(
        'api_url' => $url,
        'token' => $token,
    );
}

function fn_vidovito_shop_delivery_to_grams($weight)
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

function fn_vidovito_shop_delivery_packages($package_info = null)
{
    $cart = isset(Tygh::$app['session']['cart']) ? Tygh::$app['session']['cart'] : array();
    $packages = array();
    foreach ($cart['products'] ?? array() as $item) {
        if (!empty($item['extra']['exclude_from_calculate'])) {
            continue;
        }
        $amount = max(1, intval($item['amount'] ?? 1));
        $product_id = intval($item['product_id'] ?? 0);
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
        $grams = fn_vidovito_shop_delivery_to_grams($item['weight'] ?? $row['weight'] ?? 0);
        if ($grams < 1) {
            $grams = 2000;
        }
        for ($i = 0; $i < $amount; $i++) {
            $packages[] = array(
                'weight_g' => $grams,
                'length_cm' => $length,
                'width_cm' => $width,
                'height_cm' => $height,
            );
            if (count($packages) >= 30) {
                return $packages;
            }
        }
    }
    if (!$packages) {
        $weight = 0;
        if ($package_info !== null) {
            $weight = floatval($package_info['W'] ?? 0);
        } else {
            $weight = floatval($cart['weight'] ?? 0);
        }
        $grams = fn_vidovito_shop_delivery_to_grams($weight);
        $packages[] = array(
            'weight_g' => $grams > 0 ? $grams : 2000,
            'length_cm' => 40,
            'width_cm' => 30,
            'height_cm' => 20,
        );
    }
    return $packages;
}

function fn_vidovito_shop_delivery_weight_g($package_info = null)
{
    $sum = 0;
    foreach (fn_vidovito_shop_delivery_packages($package_info) as $pkg) {
        $sum += intval($pkg['weight_g'] ?? 0);
    }
    return max(1, $sum);
}

function fn_vidovito_shop_delivery_api($path, $query = array())
{
    $settings = fn_vidovito_shop_delivery_settings();
    if ($settings['token'] === '') {
        return array('ok' => false, 'status' => 0, 'data' => null);
    }
    $raw = Http::get($settings['api_url'] . $path, $query, array(
        'timeout' => 20,
        'headers' => array(
            'X-Shop-Token: ' . $settings['token'],
            'Accept: application/json',
        ),
    ));
    $decoded = json_decode($raw, true);
    return array(
        'ok' => is_array($decoded),
        'status' => is_array($decoded) ? ($decoded['status'] ?? 'ok') : 'error',
        'data' => $decoded,
        'raw' => $raw,
    );
}

function fn_vidovito_shop_delivery_selected($carrier = null)
{
    $all = isset($_SESSION['vidovito_shop_delivery']['selected'])
        ? $_SESSION['vidovito_shop_delivery']['selected']
        : array();
    if ($carrier && (($all['carrier'] ?? '') !== $carrier)) {
        return array();
    }
    return is_array($all) ? $all : array();
}

function fn_vidovito_shop_delivery_quote($carrier, $shipping_info)
{
    $selected = fn_vidovito_shop_delivery_selected($carrier);
    if (!empty($selected['pvz_code'])) {
        $cost = ($carrier === 'ozon') ? 0.0 : floatval($selected['cost'] ?? 0);
        if ($carrier === 'ozon' || $cost > 0) {
            $time = $selected['delivery_time'] ?? '2–5 дней';
            $pvz = $selected['pvz_address'] ?: $selected['pvz_name'];
            if ($pvz) {
                $time = trim($time . ($time ? ', ' : '') . $pvz);
            }
            return array(
                'cost' => $cost,
                'error' => false,
                'delivery_time' => $time,
            );
        }
    }

    $settings = fn_vidovito_shop_delivery_settings();
    $location = $shipping_info['package_info']['location'] ?? array();
    $city = trim((string) ($location['city'] ?? ''));
    if ($city === '' || $settings['token'] === '') {
        // Чтобы способ оставался в оформлении до выбора ПВЗ
        return array('cost' => 1, 'error' => false, 'delivery_time' => 'Укажите адрес и пункт выдачи');
    }

    $resp = fn_vidovito_shop_delivery_api('/api/v1/shop/delivery/quote', array(
        'carrier' => $carrier,
        'city' => $city,
        'address' => trim(implode(', ', array_filter(array(
            $location['address'] ?? '',
            $location['address_2'] ?? '',
            $location['zipcode'] ?? '',
        )))) ?: $city,
        'weight_g' => fn_vidovito_shop_delivery_weight_g($shipping_info['package_info'] ?? array()),
        'declared_cost' => floatval($shipping_info['package_info']['C'] ?? 500),
        'phone' => (string) ($location['phone'] ?? (Tygh::$app['session']['cart']['user_data']['phone'] ?? '')),
        'packages' => json_encode(fn_vidovito_shop_delivery_packages($shipping_info['package_info'] ?? array()), JSON_UNESCAPED_UNICODE),
    ));
    $decoded = $resp['data'];
    if (!is_array($decoded) || ($decoded['status'] ?? '') !== 'ok' || empty($decoded['data']['cost'])) {
        return array('cost' => 1, 'error' => false, 'delivery_time' => 'Укажите адрес и пункт выдачи');
    }
    $data = $decoded['data'];
    $pvz = $data['pvz_address'] ?: $data['pvz_name'];
    $time = $data['delivery_time'] ?? '';
    if ($pvz) {
        $time = trim($time . ($time ? ', ' : '') . $pvz);
    }
    return array(
        'cost' => floatval($data['cost']),
        'error' => false,
        'delivery_time' => $time,
    );
}

function fn_vidovito_shop_delivery_shipping_map()
{
    fn_vidovito_shop_delivery_ensure_methods();
    $rows = db_get_array(
        'SELECT s.shipping_id, ss.module FROM ?:shippings AS s'
        . ' INNER JOIN ?:shipping_services AS ss ON ss.service_id = s.service_id'
        . ' WHERE ss.module IN (?a) AND s.status = ?s',
        array('vidovito_cdek', 'vidovito_yandex', 'vidovito_ozon'),
        'A'
    );
    $map = array('cdek' => 0, 'yandex' => 0, 'ozon' => 0);
    foreach ($rows as $row) {
        if ($row['module'] === 'vidovito_cdek') {
            $map['cdek'] = (int) $row['shipping_id'];
        }
        if ($row['module'] === 'vidovito_yandex') {
            $map['yandex'] = (int) $row['shipping_id'];
        }
        if ($row['module'] === 'vidovito_ozon') {
            $map['ozon'] = (int) $row['shipping_id'];
        }
    }
    return $map;
}

function fn_vidovito_shop_delivery_shipping_id($carrier)
{
    $map = fn_vidovito_shop_delivery_shipping_map();
    return (int) ($map[strtolower((string) $carrier)] ?? 0);
}

function fn_vidovito_shop_delivery_cart_group_key($cart)
{
    if (!empty($cart['product_groups']) && is_array($cart['product_groups'])) {
        $group_keys = array_keys($cart['product_groups']);
        return reset($group_keys);
    }
    return 0;
}

function fn_vidovito_shop_delivery_apply_to_cart(&$cart)
{
    $selected = fn_vidovito_shop_delivery_selected();
    if (!is_array($selected) || empty($selected['pvz_code'])) {
        return 0;
    }
    $cart['vidovito_shop_delivery'] = $selected;
    if (!isset($cart['user_data']) || !is_array($cart['user_data'])) {
        $cart['user_data'] = array();
    }
    $cart['user_data']['s_country'] = 'RU';
    if (!empty($selected['city'])) {
        $cart['user_data']['s_city'] = $selected['city'];
    }
    $address = $selected['pvz_address'] ?: ($selected['address'] ?? '');
    if ($address !== '') {
        $cart['user_data']['s_address'] = $address;
    }
    if (!empty($selected['zip'])) {
        $cart['user_data']['s_zipcode'] = $selected['zip'];
    } elseif (empty($cart['user_data']['s_zipcode'])) {
        $cart['user_data']['s_zipcode'] = '000000';
    }
    if (!empty($selected['state'])) {
        $cart['user_data']['s_state'] = $selected['state'];
    }
    $shipping_id = fn_vidovito_shop_delivery_shipping_id($selected['carrier'] ?? '');
    if ($shipping_id) {
        $group_key = fn_vidovito_shop_delivery_cart_group_key($cart);
        $cart['chosen_shipping'][$group_key] = $shipping_id;
    }
    return $shipping_id;
}

function fn_vidovito_shop_delivery_bind_shipping(&$cart)
{
    $shipping_id = fn_vidovito_shop_delivery_apply_to_cart($cart);
    if (!$shipping_id) {
        return 0;
    }
    $selected = fn_vidovito_shop_delivery_selected();
    $carrier = strtolower((string) ($selected['carrier'] ?? ''));
    $rate = ($carrier === 'ozon') ? 0.0 : floatval($selected['cost'] ?? 0);
    $name = 'Ozon Доставка';
    if ($carrier === 'cdek') {
        $name = 'СДЭК';
    } elseif ($carrier === 'yandex') {
        $name = 'Яндекс Доставка';
    }
    $lang = defined('CART_LANGUAGE') ? CART_LANGUAGE : 'ru';
    $stored_name = db_get_field(
        'SELECT shipping FROM ?:shipping_descriptions WHERE shipping_id = ?i AND lang_code = ?s',
        $shipping_id,
        $lang
    );
    if ($stored_name) {
        $name = $stored_name;
    }
    $cart['shipping_failed'] = false;
    $cart['company_shipping_failed'] = false;
    $included = floatval($cart['shipping_cost'] ?? 0);
    if (empty($cart['product_groups']) || !is_array($cart['product_groups'])) {
        $cart['chosen_shipping'] = array($shipping_id);
        $cart['shipping_cost'] = $rate;
        $cart['display_shipping_cost'] = $rate;
        if (isset($cart['total'])) {
            $cart['total'] = round(floatval($cart['total']) - $included + $rate, 2);
        }
        return $shipping_id;
    }
    foreach ($cart['product_groups'] as $key => &$group) {
        $info = array(
            'shipping_id' => $shipping_id,
            'shipping' => $name,
            'rate' => $rate,
            'delivery_time' => (string) ($selected['delivery_time'] ?? '2–5 дней'),
            'module' => 'vidovito_' . $carrier,
            'service_code' => 'vidovito_' . $carrier,
            'group_key' => $key,
            'need_shipping_id' => $shipping_id,
        );
        if (!isset($group['shippings']) || !is_array($group['shippings'])) {
            $group['shippings'] = array();
        }
        $group['shippings'][$shipping_id] = $info;
        $group['chosen_shipping'] = array($shipping_id);
        $group['chosen_shippings'] = array($info);
        $cart['chosen_shipping'][$key] = $shipping_id;
        $cart['shipping'][$shipping_id] = $info;
    }
    unset($group);
    $cart['shipping_cost'] = $rate;
    $cart['display_shipping_cost'] = $rate;
    if (isset($cart['total'])) {
        $cart['total'] = round(floatval($cart['total']) - $included + $rate, 2);
    }
    return $shipping_id;
}

function fn_vidovito_shop_delivery_calculate_cart(&$cart, &$cart_products = null, &$auth = null)
{
    fn_vidovito_shop_delivery_apply_to_cart($cart);
}

function fn_vidovito_shop_delivery_calculate_cart_post(&$cart, &$auth = null)
{
    fn_vidovito_shop_delivery_bind_shipping($cart);
}

function fn_vidovito_shop_delivery_allow_place_order(&$total, &$cart)
{
    if (defined('AREA') && AREA !== 'C') {
        return;
    }
    fn_vidovito_shop_delivery_bind_shipping($cart);
}

function fn_vidovito_shop_delivery_pre_place_order(&$cart, &$allow, &$product_groups = null)
{
    if (defined('AREA') && AREA !== 'C') {
        return;
    }
    $selected = fn_vidovito_shop_delivery_selected();
    if (!is_array($selected) || empty($selected['pvz_code'])) {
        $allow = false;
        fn_set_notification('E', __('error'), 'Выберите пункт выдачи');
        return;
    }
    $shipping_id = fn_vidovito_shop_delivery_bind_shipping($cart);
    if (!$shipping_id) {
        $allow = false;
        fn_set_notification('E', __('error'), 'Служба доставки не подключена. Не удалось оформить заказ.');
        return;
    }
    if (is_array($product_groups) && !empty($cart['product_groups'])) {
        $product_groups = $cart['product_groups'];
    }
    $allow = true;
}

function fn_vidovito_shop_delivery_init_templater(&$view)
{
    if (!defined('AREA') || AREA !== 'C' || !is_object($view)) {
        return;
    }
    if (method_exists($view, 'registerFilter')) {
        $view->registerFilter('output', 'fn_vidovito_shop_delivery_output_filter');
    }
}

function fn_vidovito_shop_delivery_before_dispatch($controller = '', $mode = '', $action = '', $dispatch_extra = '', $area = '')
{
    if ((defined('AREA') && AREA === 'C') || $area === 'C') {
        fn_vidovito_shop_delivery_sync_theme_files();
    }
}

function fn_vidovito_shop_delivery_dispatch_before_display()
{
    if (!defined('AREA') || AREA !== 'C') {
        return;
    }
    fn_vidovito_shop_delivery_sync_theme_files();
}

function fn_vidovito_shop_delivery_sync_theme_files()
{
    static $done = false;
    if ($done) {
        return;
    }
    $done = true;

    $src_root = rtrim((string) Registry::get('config.dir.addons'), '/') . '/vidovito_shop_delivery/theme_files';
    $theme_dir = rtrim((string) Registry::get('config.dir.design_frontend'), '/');
    if ($src_root === '' || $theme_dir === '' || !is_dir($src_root) || !is_dir($theme_dir)) {
        return;
    }

    $copies = array(
        'templates/addons/vidovito_shop_delivery/hooks/index/scripts.post.tpl',
        'templates/addons/vidovito_shop_delivery/hooks/index/styles.post.tpl',
        'templates/addons/vidovito_shop_delivery/hooks/index/footer.post.tpl',
        'templates/addons/vidovito_shop_delivery/blocks/delivery_picker.tpl',
        'css/addons/vidovito_shop_delivery/styles.css',
    );
    foreach ($copies as $rel) {
        $src = $src_root . '/' . $rel;
        $dst = $theme_dir . '/' . $rel;
        if (!is_readable($src)) {
            continue;
        }
        $dst_dir = dirname($dst);
        if (!is_dir($dst_dir)) {
            @mkdir($dst_dir, 0755, true);
        }
        if (!is_file($dst) || filemtime($src) > filemtime($dst)) {
            @copy($src, $dst);
        }
    }
}

function fn_vidovito_shop_delivery_frontend_snippet()
{
    $map = fn_vidovito_shop_delivery_shipping_map();
    $hash = '';
    if (isset(Tygh::$app['session']['security_hash'])) {
        $hash = (string) Tygh::$app['session']['security_hash'];
    } elseif (Registry::get('config.security_hash')) {
        $hash = (string) Registry::get('config.security_hash');
    }
    $config = array(
        'suggestUrl' => fn_url('vidovito_shop_delivery.suggest', 'C'),
        'pointsUrl' => fn_url('vidovito_shop_delivery.points', 'C'),
        'selectUrl' => fn_url('vidovito_shop_delivery.select', 'C'),
        'clearUrl' => fn_url('vidovito_shop_delivery.clear', 'C'),
        'mapsUrl' => fn_url('vidovito_shop_delivery.maps', 'C'),
        'securityHash' => $hash,
        'cdekId' => (string) ($map['cdek'] ?? 0),
        'yandexId' => (string) ($map['yandex'] ?? 0),
        'ozonId' => (string) ($map['ozon'] ?? 0),
    );
    $js_src = rtrim((string) Registry::get('config.current_location'), '/')
        . '/js/addons/vidovito_shop_delivery/picker.js?v=1.5.10';
    $css = '';
    $css_candidates = array(
        rtrim((string) Registry::get('config.dir.addons'), '/') . '/vidovito_shop_delivery/theme_files/css/addons/vidovito_shop_delivery/styles.css',
        rtrim((string) Registry::get('config.dir.root'), '/') . '/css/addons/vidovito_shop_delivery/styles.css',
    );
    foreach ($css_candidates as $css_file) {
        if (is_readable($css_file)) {
            $css = file_get_contents($css_file);
            break;
        }
    }
    $json = json_encode($config, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    return '<style id="vidovito-delivery-css">' . $css . '</style>'
        . '<script>window.vidovitoShopDelivery=' . $json . ';</script>'
        . '<script src="' . htmlspecialchars($js_src, ENT_QUOTES, 'UTF-8') . '"></script>';
}

function fn_vidovito_shop_delivery_output_filter($tpl_output, $template = null)
{
    if (!is_string($tpl_output) || $tpl_output === '') {
        return $tpl_output;
    }
    if (stripos($tpl_output, '</body>') === false) {
        return $tpl_output;
    }
    $controller = (string) Registry::get('runtime.controller');
    $is_checkout = ($controller === 'checkout')
        || (strpos($tpl_output, 'litecheckout') !== false)
        || (strpos($tpl_output, 'name="litecheckout_form"') !== false)
        || (strpos($tpl_output, 'Оформить заказ') !== false);
    if (!$is_checkout) {
        return $tpl_output;
    }
    $has_root = strpos($tpl_output, 'id="vidovito-delivery-root"') !== false;
    $has_js = strpos($tpl_output, 'vidovitoShopDelivery') !== false;
    if ($has_root && $has_js) {
        return $tpl_output;
    }
    $snippet = '';
    if (!$has_root) {
        $snippet .= '<div id="vidovito-delivery-root"></div>';
    }
    if (!$has_js) {
        $snippet .= fn_vidovito_shop_delivery_frontend_snippet();
    }
    return str_ireplace('</body>', $snippet . '</body>', $tpl_output);
}

fn_vidovito_shop_delivery_restore_token();
