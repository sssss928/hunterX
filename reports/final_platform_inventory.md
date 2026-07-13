# Final Platform Inventory

All Python files under `src/platforms` plus the main nodriver dispatcher were scanned. Counts are static indicators, not runtime proof of branch coverage.

| File | Platform | Entry Points | Reload | Sleeps | DOM Queries | Config Uses | Modified |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| src/nodriver_tixcraft.py | Main nodriver dispatcher and refresh gate | nodriver_goto_homepage, nodrver_block_urls, _inject_clarity_stub_for_ticketplus, parse_refresh_datetime, _should_suppress_target_boundary_action | 5 | 10 | 0 | 92 | No |
| src/platforms/__init__.py | __init__ |  | 0 | 0 | 0 | 0 | No |
| src/platforms/cityline.py | Cityline | is_cityline_login_page, nodriver_cityline_auto_retry_access, nodriver_cityline_login, nodriver_cityline_date_auto_select, nodriver_cityline_check_login_modal | 1 | 17 | 37 | 74 | No |
| src/platforms/common_async.py | Shared async utilities | _maybe_await, _finite_float, bounded_poll, bounded_poll, bounded_poll | 0 | 1 | 0 | 5 | tests only; file unchanged |
| src/platforms/facebook.py | Facebook login helper | _query_selector, _send_enter, nodriver_facebook_login, nodriver_facebook_main | 0 | 0 | 4 | 2 | No |
| src/platforms/famiticket.py | FamiTicket | nodriver_fami_login, nodriver_fami_activity, nodriver_fami_verify, nodriver_fami_date_auto_select, nodriver_fami_area_auto_select | 0 | 16 | 40 | 54 | No |
| src/platforms/fansigo.py | FANSI GO | is_fansigo_url, get_fansigo_page_type, fansigo_normalize_cookie_value, nodriver_fansigo_inject_cookie, nodriver_fansigo_signin | 2 | 6 | 23 | 59 | No |
| src/platforms/funone.py | FunOne | nodriver_funone_inject_cookie, nodriver_funone_check_login_status, nodriver_funone_verify_login, nodriver_funone_close_popup, nodriver_funone_date_auto_select | 6 | 18 | 56 | 82 | No |
| src/platforms/hkticketing.py | HKTicketing / Urbtix / Ticketek / Galaxy Macau | nodriver_hkticketing_login, nodriver_hkticketing_accept_cookie, nodriver_hkticketing_date_buy_button_press, nodriver_hkticketing_date_assign, nodriver_hkticketing_date_password_input | 3 | 40 | 75 | 126 | No |
| src/platforms/ibon.py | iBon | _ibon_filter_enabled_purchase_buttons, _ensure_state, register_ibon_alert_handler, dismiss_pending_ibon_dialog, nodriver_ibon_login | 7 | 39 | 91 | 259 | No |
| src/platforms/kham.py | KHAM / ticket.com.tw / UDN | _get_auto_reload_interval, _reload_page_when_due, nodriver_kham_login, nodriver_kham_go_buy_redirect, nodriver_kham_check_realname_dialog | 3 | 49 | 178 | 234 | No |
| src/platforms/kktix.py | KKTIX | _get_auto_reload_interval, _reload_page_when_due, nodriver_kktix_check_queue_page, nodriver_kktix_signin, nodriver_kktix_redirect_to_signin_if_guest | 3 | 24 | 88 | 139 | No |
| src/platforms/ticketplus.py | TicketPlus | _get_status, _ticketplus_path_segment_count, nodriver_ticketplus_detect_layout_style, nodriver_ticketplus_account_sign_in, nodriver_ticketplus_is_signin | 3 | 18 | 66 | 80 | No |
| src/platforms/tixcraft.py | TixCraft / TeamEar / IndieVox / Ticketmaster-related TixCraft flow | _is_serial_code_question, _is_tixcraft_soft_block_scope, _parse_tixcraft_soft_block_delay, _resolve_soft_block_wait_seconds, _process_queue_it_state | 1 | 23 | 62 | 196 | No |

## Platform Mapping Notes

- ticket.com.tw and UDN are handled through `src/platforms/kham.py`.
- TeamEar and IndieVox are covered by TixCraft-family routing in `src/platforms/tixcraft.py` and dispatcher URL classification.
- Urbtix, Ticketek, and Galaxy Macau are grouped with HKTicketing flow support in `src/platforms/hkticketing.py`.
- Facebook and `common_async` are supporting modules, not standalone ticketing targets.
