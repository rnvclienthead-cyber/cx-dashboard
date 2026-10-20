<?php

namespace Tygh\Shippings\Services;

use Tygh\Shippings\IService;

class VidovitoOzon implements IService
{
    private $shipping_info = array();

    public function prepareData($shipping_info)
    {
        $this->shipping_info = $shipping_info;
    }

    public function processResponse($response)
    {
        if (is_array($response)) {
            return $response;
        }
        $data = json_decode((string) $response, true);
        return is_array($data) ? $data : array('cost' => false, 'error' => true);
    }

    public function processErrors($response)
    {
        return false;
    }

    public function allowMultithreading()
    {
        return false;
    }

    public function getRequestData()
    {
        return array();
    }

    public function getSimpleRates()
    {
        return json_encode(fn_vidovito_shop_delivery_quote('ozon', $this->shipping_info));
    }

    public static function getInfo()
    {
        return array(
            'name' => 'Ozon Доставка',
            'tracking_url' => 'https://www.ozon.ru/my/orderdetails/?order=',
        );
    }
}
