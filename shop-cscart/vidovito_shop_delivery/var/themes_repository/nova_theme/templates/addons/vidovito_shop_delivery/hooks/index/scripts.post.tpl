{if $runtime.controller == "checkout"}
    {$vidovito_ship_map = fn_vidovito_shop_delivery_shipping_map()}
    <script>
    window.vidovitoShopDelivery = {
        suggestUrl: "{"vidovito_shop_delivery.suggest"|fn_url}",
        pointsUrl: "{"vidovito_shop_delivery.points"|fn_url}",
        selectUrl: "{"vidovito_shop_delivery.select"|fn_url}",
        clearUrl: "{"vidovito_shop_delivery.clear"|fn_url}",
        mapsUrl: "{"vidovito_shop_delivery.maps"|fn_url}",
        securityHash: "{$config.security_hash|escape:"javascript"}",
        cdekId: "{$vidovito_ship_map.cdek|default:0}",
        yandexId: "{$vidovito_ship_map.yandex|default:0}",
        ozonId: "{$vidovito_ship_map.ozon|default:0}"
    };
    </script>
    {script src="js/addons/vidovito_shop_delivery/picker.js"}
{/if}
