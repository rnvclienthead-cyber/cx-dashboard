{if $order_info.vidovito_shop_order}
    <div class="sidebar-row" data-ca-vidovito-shop-order="1">
        <h4 class="subheader">Пункт выдачи Vidovito</h4>
        <p>
            {if $order_info.vidovito_shop_order.carrier}{$order_info.vidovito_shop_order.carrier|upper}{/if}
            {if $order_info.vidovito_shop_order.pvz_address}
                <br>{$order_info.vidovito_shop_order.pvz_address}
            {elseif $order_info.vidovito_shop_order.pvz_name}
                <br>{$order_info.vidovito_shop_order.pvz_name}
            {/if}
            {if $order_info.vidovito_shop_order.pvz_code}
                <br><small>{$order_info.vidovito_shop_order.pvz_code}</small>
            {/if}
        </p>
        {if $order_info.vidovito_shop_order.cx}
            {$cx = $order_info.vidovito_shop_order.cx}
            <p>
                {if $cx.status_label}<strong>{$cx.status_label}</strong>{/if}
            </p>
            {foreach from=$cx.parcels item="parcel"}
                <p>
                    {$parcel.name|default:"Посылка"}
                    {if $parcel.status_label} — {$parcel.status_label}{/if}
                    {if $parcel.track_number}<br>Трек: {$parcel.track_number}{/if}
                    {if $parcel.tracking_url}<br><a href="{$parcel.tracking_url}" target="_blank" rel="noopener">Отследить</a>{/if}
                </p>
            {/foreach}
        {/if}
    </div>
{/if}
