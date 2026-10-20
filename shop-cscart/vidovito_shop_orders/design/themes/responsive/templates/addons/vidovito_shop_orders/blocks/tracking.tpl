{if !$smarty.capture.vidovito_receive_help}
{capture name="vidovito_receive_help"}1{/capture}
    {include file="addons/vidovito_shop_orders/blocks/receive_help.tpl"}
{/if}
{if !$smarty.capture.vidovito_cx_track_done && $order_info.vidovito_shop_order.cx}
{capture name="vidovito_cx_track_done"}1{/capture}
    {$cx = $order_info.vidovito_shop_order.cx}
    <div class="ty-vidovito-track" style="margin:16px 0;padding:16px 18px;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;">
        <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:8px;">Доставка</div>
        {if $cx.status_label}
            <div style="margin-bottom:8px;">
                <span style="display:inline-block;padding:3px 10px;border-radius:999px;background:#ede9fe;color:#5b21b6;font-size:13px;font-weight:600;">{$cx.status_label}</span>
                {if $cx.carrier_label}<span style="margin-left:8px;color:#64748b;font-size:13px;">{$cx.carrier_label}</span>{/if}
            </div>
        {/if}
        {if $cx.pvz_address || $cx.pvz_name}
            <div style="font-size:13px;color:#334155;margin-bottom:10px;">
                Пункт выдачи: {if $cx.pvz_name}{$cx.pvz_name}{/if}{if $cx.pvz_name && $cx.pvz_address} — {/if}{if $cx.pvz_address}{$cx.pvz_address}{/if}
            </div>
        {/if}
        {if $cx.parcels}
            {foreach from=$cx.parcels item="parcel" name="vidovito_parcels"}
                <div style="padding:10px 0;{if !$smarty.foreach.vidovito_parcels.last}border-bottom:1px solid #e2e8f0;{/if}">
                    <div style="font-size:13px;color:#0f172a;">
                        {$parcel.name|default:"Посылка"}
                        {if $parcel.status_label}
                            <span style="color:#64748b;"> — {$parcel.status_label}</span>
                        {/if}
                    </div>
                    {if $parcel.track_number}
                        <div style="margin-top:4px;font-size:13px;">
                            Номер: <strong>{$parcel.track_number}</strong>
                            {if $parcel.tracking_url && $cx.carrier != "ozon"}
                                · <a href="{$parcel.tracking_url}" target="_blank" rel="noopener">Отследить</a>
                            {/if}
                        </div>
                    {/if}
                </div>
            {/foreach}
        {/if}
    </div>
{/if}
