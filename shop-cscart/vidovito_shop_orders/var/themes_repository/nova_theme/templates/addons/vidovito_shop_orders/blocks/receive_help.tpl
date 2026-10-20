<button type="button" class="ty-vidovito-guide-open" data-vidovito-own="1">Информация по доставке</button>

<div class="ty-vidovito-guide" id="vidovito-guide" hidden>
    <div class="ty-vidovito-guide__back" data-vidovito-close="1"></div>
    <div class="ty-vidovito-guide__panel" role="dialog" aria-modal="true" aria-label="Информация по доставке">
        <button type="button" class="ty-vidovito-guide__x" data-vidovito-close="1" aria-label="Закрыть">×</button>
        <div class="ty-vidovito-guide__title">Информация по доставке</div>

        {if $order_info.order_id}
            <div class="ty-vidovito-guide__order">
                <div class="ty-vidovito-guide__order-no">№{$order_info.order_id}</div>
                {if $order_info.timestamp}
                    <div class="ty-vidovito-guide__muted">{$order_info.timestamp|date_format:"%d.%m.%Y, %H:%M"}</div>
                {/if}
                {if $order_info.vidovito_shop_order.cx}
                    {$cx = $order_info.vidovito_shop_order.cx}
                    {if $cx.status_label}
                        <div class="ty-vidovito-guide__badge">{$cx.status_label|escape}</div>
                    {/if}
                    {if $cx.carrier_label}
                        <div>{$cx.carrier_label|escape}</div>
                    {/if}
                    {if $cx.pvz_name || $cx.pvz_address}
                        <div class="ty-vidovito-guide__pvz">
                            Пункт выдачи:
                            {if $cx.pvz_name}{$cx.pvz_name|escape}{/if}
                            {if $cx.pvz_name && $cx.pvz_address} — {/if}
                            {if $cx.pvz_address}{$cx.pvz_address|escape}{/if}
                        </div>
                    {elseif $order_info.vidovito_shop_order.pvz_name || $order_info.vidovito_shop_order.pvz_address}
                        <div class="ty-vidovito-guide__pvz">
                            Пункт выдачи:
                            {if $order_info.vidovito_shop_order.pvz_name}{$order_info.vidovito_shop_order.pvz_name|escape}{/if}
                            {if $order_info.vidovito_shop_order.pvz_name && $order_info.vidovito_shop_order.pvz_address} — {/if}
                            {if $order_info.vidovito_shop_order.pvz_address}{$order_info.vidovito_shop_order.pvz_address|escape}{/if}
                        </div>
                    {/if}
                    {if $cx.parcels}
                        {foreach from=$cx.parcels item="parcel"}
                            <div class="ty-vidovito-guide__parcel">
                                <div>{$parcel.name|default:"Посылка"|escape}{if $parcel.status_label} — {$parcel.status_label|escape}{/if}</div>
                                {if $parcel.track_number}
                                    <div>Номер отправления: <strong>{$parcel.track_number|escape}</strong>
                                        {if $parcel.tracking_url && $cx.carrier != "ozon"}
                                            · <a href="{$parcel.tracking_url|escape}" target="_blank" rel="noopener">Отследить</a>
                                        {/if}
                                    </div>
                                {/if}
                            </div>
                        {/foreach}
                    {/if}
                    {if !$cx.status_label && !$cx.parcels}
                        <div class="ty-vidovito-guide__muted">Статус и номер отправления появятся здесь, когда заказ передадут в доставку.</div>
                    {/if}
                {elseif $order_info.vidovito_shop_order.pvz_name || $order_info.vidovito_shop_order.pvz_address}
                    <div class="ty-vidovito-guide__pvz">
                        Пункт выдачи:
                        {if $order_info.vidovito_shop_order.pvz_name}{$order_info.vidovito_shop_order.pvz_name|escape}{/if}
                        {if $order_info.vidovito_shop_order.pvz_name && $order_info.vidovito_shop_order.pvz_address} — {/if}
                        {if $order_info.vidovito_shop_order.pvz_address}{$order_info.vidovito_shop_order.pvz_address|escape}{/if}
                    </div>
                    <div class="ty-vidovito-guide__muted">Статус и номер отправления появятся здесь, когда заказ передадут в доставку.</div>
                {else}
                    <div class="ty-vidovito-guide__muted">Статус и номер отправления появятся здесь, когда заказ передадут в доставку.</div>
                {/if}
                <div class="ty-vidovito-guide__muted">Штрихкод для получения показывается в приложении Ozon, когда посылка уже в пункте выдачи.</div>
            </div>
        {/if}

        <div class="ty-vidovito-guide__how">Как открыть заказ в приложении Ozon</div>
        <div class="ty-vidovito-guide__slides">
            <figure class="is-on">
                <img src="/js/addons/vidovito_shop_orders/guide/1.png" alt="Шаг 1. На главном экране нажмите Ozon Доставка">
                <figcaption>1. На главном экране нажмите «Ozon Доставка».</figcaption>
            </figure>
            <figure>
                <img src="/js/addons/vidovito_shop_orders/guide/2.png" alt="Шаг 2. Внизу нажмите Заказы">
                <figcaption>2. Внизу нажмите «Заказы».</figcaption>
            </figure>
            <figure>
                <img src="/js/addons/vidovito_shop_orders/guide/3.png" alt="Шаг 3. Откройте вкладку Получения">
                <figcaption>3. Откройте вкладку «Получения».</figcaption>
            </figure>
            <figure>
                <img src="/js/addons/vidovito_shop_orders/guide/4.png" alt="Шаг 4. Нажмите В ожидании">
                <figcaption>4. Нажмите «В ожидании».</figcaption>
            </figure>
            <figure>
                <img src="/js/addons/vidovito_shop_orders/guide/5.png" alt="Шаг 5. Откройте заказ магазина Видовито">
                <figcaption>5. Откройте заказ магазина Видовито.</figcaption>
            </figure>
            <figure>
                <img src="/js/addons/vidovito_shop_orders/guide/6.png" alt="Шаг 6. На этом экране статус заказа">
                <figcaption>6. Здесь статус заказа. Штрихкод для получения появится, когда посылка будет в пункте выдачи.</figcaption>
            </figure>
        </div>
        <div class="ty-vidovito-guide__nav">
            <button type="button" data-vidovito-prev="1">Назад</button>
            <div class="ty-vidovito-guide__dots" data-vidovito-dots="1"></div>
            <button type="button" data-vidovito-next="1">Дальше</button>
        </div>
    </div>
</div>

<style>
.ty-vidovito-guide-open {
    display: inline-block;
    width: 100%;
    margin: 12px 0;
    padding: 12px 16px;
    border: 0;
    border-radius: 12px;
    background: #FFC400;
    color: #223247;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
}
.ty-vidovito-guide {
    position: fixed;
    inset: 0;
    z-index: 10000;
}
.ty-vidovito-guide[hidden] { display: none; }
.ty-vidovito-guide__back {
    position: absolute;
    inset: 0;
    background: rgba(34, 50, 71, 0.45);
}
.ty-vidovito-guide__panel {
    position: relative;
    width: min(420px, calc(100% - 24px));
    max-height: min(92vh, 860px);
    margin: 4vh auto;
    overflow: auto;
    background: #fff;
    border-radius: 16px;
    padding: 16px 16px 18px;
    box-sizing: border-box;
}
.ty-vidovito-guide__x {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 36px;
    height: 36px;
    border: 0;
    border-radius: 999px;
    background: #f4f6f8;
    color: #223247;
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
}
.ty-vidovito-guide__title {
    font-size: 18px;
    font-weight: 700;
    color: #223247;
    margin: 0 36px 12px 0;
}
.ty-vidovito-guide__order {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 14px;
    color: #223247;
    font-size: 14px;
    line-height: 1.4;
}
.ty-vidovito-guide__order-no { font-weight: 700; font-size: 16px; }
.ty-vidovito-guide__muted { color: #64748b; font-size: 13px; margin-top: 6px; }
.ty-vidovito-guide__badge {
    display: inline-block;
    margin-top: 8px;
    padding: 3px 10px;
    border-radius: 999px;
    background: #FFC400;
    color: #223247;
    font-size: 13px;
    font-weight: 700;
}
.ty-vidovito-guide__pvz, .ty-vidovito-guide__parcel { margin-top: 8px; }
.ty-vidovito-guide__how {
    font-size: 14px;
    font-weight: 700;
    color: #223247;
    margin: 0 0 8px;
}
.ty-vidovito-guide__slides figure { display: none; margin: 0; }
.ty-vidovito-guide__slides figure.is-on { display: block; }
.ty-vidovito-guide__slides img {
    display: block;
    width: 100%;
    max-height: 58vh;
    object-fit: contain;
    background: #f4f6f8;
    border-radius: 12px;
}
.ty-vidovito-guide__slides figcaption {
    margin-top: 8px;
    font-size: 14px;
    line-height: 1.4;
    color: #223247;
}
.ty-vidovito-guide__nav {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
}
.ty-vidovito-guide__nav button {
    border: 0;
    border-radius: 10px;
    background: #223247;
    color: #fff;
    font-weight: 700;
    padding: 8px 12px;
    cursor: pointer;
}
.ty-vidovito-guide__nav button:disabled { opacity: 0.35; cursor: default; }
.ty-vidovito-guide__dots { display: flex; gap: 6px; flex: 1; justify-content: center; }
.ty-vidovito-guide__dots button {
    width: 8px;
    height: 8px;
    padding: 0;
    border-radius: 999px;
    background: #cbd5e1;
}
.ty-vidovito-guide__dots button.is-on { background: #FFC400; }
</style>
