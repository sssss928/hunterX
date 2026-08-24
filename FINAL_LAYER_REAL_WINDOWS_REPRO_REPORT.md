# Final-Layer Real Windows Reproduction Report

## User evidence

The supplied Windows RC2 log repeatedly showed the production chain:

`TixCraft area -> ticket -> /login -> ConnectionClosedError -> raise_if_terminal_browser_error() -> run_runtime_iteration -> _run_main -> asyncio.run -> PyInstaller termination`.

The linked ChatGPT conversation required authentication in the in-app browser, so no unsupported claim was inferred from that page. The exact crash chain supplied in the task and the locally available project behavior were used as evidence.

## Deterministic RC2 negative control

An immutable RC2 extraction was executed through the real `runtime.main()` and `_run_main()` lifecycle, the real production iteration, and the real TixCraft terminal classifier. Only the external browser transport was replaced with a deterministic fake. The test failed because the real `ConnectionClosedError` escaped the lifecycle boundary. This is materially stronger than testing only that `run_runtime_iteration()` re-raises.

## RC3 result

The same reproducer no longer reaches `asyncio.run()`. It is owned at `_run_main()`, classified, and routed through the bounded supervisor/session-manager policy. Twenty fresh-process repetitions passed.

## Scope limitation

No live third-party ticket purchase, CAPTCHA bypass, queue bypass, challenge bypass, checkout automation, payment automation, or packet capture was performed. The short browser integration used local synthetic pages and Microsoft Edge. Therefore this report proves the lifecycle boundary and packaged executable smoke, not a purchase against a live TixCraft event.

