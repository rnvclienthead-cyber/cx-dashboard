<?php

$schema['vidovito_cdek'] = array(
    'tracking_url_template' => 'https://www.cdek.ru/track.html?order_id=[tracking_number]',
);

$schema['vidovito_yandex'] = array(
    'tracking_url_template' => 'https://dostavka.yandex.ru/tracking?track_id=[tracking_number]',
);

$schema['vidovito_ozon'] = array(
    'tracking_url_template' => 'https://www.ozon.ru/my/orderdetails/?order=[tracking_number]',
);

return $schema;
