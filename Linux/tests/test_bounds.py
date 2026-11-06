import unittest

from openpets_linux.core import MAX_MESSAGES, State


class BoundsTests(unittest.TestCase):
    def test_nonfinite_ttl(self):
        for ttl in (float("nan"), float("inf"), -(float("inf")), 10**400):
            for command in (
                {"type": "playAnimation", "name": "waving", "ttlSeconds": ttl},
                {"type": "notify", "notification": {"title": "T", "status": "done", "ttlSeconds": ttl}},
            ):
                self.assertFalse(State().apply(command)["ok"])

    def test_message_cap_and_updates(self):
        state = State()
        for index in range(MAX_MESSAGES):
            self.assertTrue(state.apply({"type": "notify", "notification": {
                "title": "T", "status": "message", "threadId": str(index)
            }})["ok"])
        self.assertFalse(state.apply({"type": "notify", "notification": {
            "title": "Too many", "status": "message"
        }})["ok"])
        self.assertTrue(state.apply({"type": "notify", "notification": {
            "title": "Update", "status": "done", "threadId": "0"
        }})["ok"])
        self.assertEqual(len(state.messages), MAX_MESSAGES)

    def test_thread_type(self):
        state = State()
        self.assertFalse(state.apply({"type": "notify", "notification": {
            "title": "T", "status": "message", "threadId": []
        }})["ok"])

    def test_notify_resets_once_flag(self):
        state = State()
        state.apply({"type": "playAnimation", "name": "waving", "loop": False})
        state.apply({"type": "notify", "notification": {"title": "T", "status": "message"}})
        self.assertTrue(state.loop)

    def test_null_loop_uses_default(self):
        state = State()
        self.assertTrue(state.apply({"type": "playAnimation", "name": "waving", "loop": None})["ok"])
        self.assertTrue(state.loop)
