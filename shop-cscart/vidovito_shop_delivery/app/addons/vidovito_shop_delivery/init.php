<?php

if (!defined('BOOTSTRAP')) { die('Access denied'); }

fn_register_hooks(
    'init_templater',
    'before_dispatch',
    'dispatch_before_display',
    'calculate_cart',
    'calculate_cart_post',
    'allow_place_order',
    'pre_place_order'
);
