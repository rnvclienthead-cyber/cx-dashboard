"""Надзор за фоновыми задачами: падение одной не должно убивать остальные."""
import asyncio
import unittest
from unittest.mock import patch

from backend import bg_runtime


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        # Не ждать 30 с между перезапусками и не слать реальные алерты.
        self._delay = bg_runtime._RESTART_DELAY_SEC
        bg_runtime._RESTART_DELAY_SEC = 0
        bg_runtime._last_alert.clear()

    def tearDown(self):
        bg_runtime._RESTART_DELAY_SEC = self._delay

    def test_crashing_worker_is_restarted(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("бум")
            # На третий раз «висим» — надзиратель больше не перезапускает.
            await asyncio.sleep(10)

        async def run():
            with patch.object(bg_runtime, "_alert_bg") as alert:
                task = asyncio.create_task(bg_runtime._supervise("flaky", flaky))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return alert.call_count

        alerts = asyncio.run(run())
        # Два падения → воркер вызван 3 раза, на каждое падение — алерт.
        self.assertEqual(calls["n"], 3)
        self.assertEqual(alerts, 2)

    def test_clean_return_is_not_restarted(self):
        calls = {"n": 0}

        async def once():
            calls["n"] += 1  # отключённый воркер: сразу вышел, это не сбой

        asyncio.run(bg_runtime._supervise("once", once))
        self.assertEqual(calls["n"], 1)

    def test_missing_key_triggers_alert(self):
        with patch.object(bg_runtime, "_alert_bg") as alert:
            with patch.dict("os.environ", {"WB_API_KEY": "", "OZON_CLIENT_ID": "",
                                           "OZON_API_KEY": "x", "YM_API_KEY": "x",
                                           "QWEN_API_KEY": "x"}, clear=False):
                bg_runtime._check_api_keys()
        self.assertEqual(alert.call_count, 1)
        # В тексте перечислены обе пустые переменные.
        msg = alert.call_args[0][1]
        self.assertIn("WB_API_KEY", msg)
        self.assertIn("OZON_CLIENT_ID", msg)

    def test_all_keys_present_no_alert(self):
        env = {k: "x" for k, _ in bg_runtime._REQUIRED_KEYS}
        with patch.object(bg_runtime, "_alert_bg") as alert:
            with patch.dict("os.environ", env, clear=False):
                bg_runtime._check_api_keys()
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
