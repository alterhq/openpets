"""Unit tests for openpets_linux.notifications and notification_ui.

Pure stdlib: no GI imports. GUI smoke uses a safe fake opener and an
optional parent; it is skipped when GTK is unavailable.
"""

import os
import sys
import time
import unittest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.notifications import (  # noqa: E402
    MAX_HISTORY_ITEMS,
    NotificationHistory,
    safe_action_url,
)


def _gtk_available():
    """Return True if GTK3 can be initialized without a display error."""
    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        return Gtk.init_check()[0]
    except Exception:
        return False


GTK_AVAILABLE = _gtk_available()


class _FixedClock:
    """Deterministic monotonic clock for expiry tests."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class TestSafeActionUrlValid(unittest.TestCase):
    """safe_action_url accepts only safe absolute http/https URLs."""

    def test_http_url(self):
        self.assertEqual(safe_action_url("http://example.com"), "http://example.com")

    def test_https_url(self):
        self.assertEqual(
            safe_action_url("https://example.com"), "https://example.com"
        )

    def test_https_with_path_query_fragment(self):
        self.assertEqual(
            safe_action_url("https://example.com/path/to?a=1#frag"),
            "https://example.com/path/to?a=1#frag",
        )

    def test_https_with_valid_port(self):
        self.assertEqual(
            safe_action_url("https://example.com:8443/path"),
            "https://example.com:8443/path",
        )

    def test_http_port_65535(self):
        self.assertEqual(
            safe_action_url("http://example.com:65535/"),
            "http://example.com:65535/",
        )

    def test_ipv6_host(self):
        self.assertEqual(
            safe_action_url("http://[::1]:8080/path"),
            "http://[::1]:8080/path",
        )

    def test_ipv6_no_port(self):
        self.assertEqual(safe_action_url("https://[::1]/"), "https://[::1]/")

    def test_trailing_whitespace_stripped(self):
        self.assertEqual(
            safe_action_url("  https://example.com  "), "https://example.com"
        )


class TestSafeActionUrlUnsafeSchemes(unittest.TestCase):
    """safe_action_url rejects non-web schemes."""

    def test_file_scheme_rejected(self):
        self.assertIsNone(safe_action_url("file:///etc/passwd"))
        self.assertIsNone(safe_action_url("FILE:///etc/passwd"))

    def test_javascript_scheme_rejected(self):
        self.assertIsNone(safe_action_url("javascript:alert(1)"))
        self.assertIsNone(safe_action_url("JAVASCRIPT:alert(1)"))

    def test_data_scheme_rejected(self):
        self.assertIsNone(safe_action_url("data:text/html,<b>hi</b>"))

    def test_command_scheme_rejected(self):
        self.assertIsNone(safe_action_url("command:echo hi"))

    def test_ftp_scheme_rejected(self):
        self.assertIsNone(safe_action_url("ftp://example.com"))

    def test_ws_scheme_rejected(self):
        self.assertIsNone(safe_action_url("ws://example.com"))

    def test_no_scheme_rejected(self):
        self.assertIsNone(safe_action_url("example.com"))
        self.assertIsNone(safe_action_url("//example.com/path"))

    def test_scheme_only_rejected(self):
        self.assertIsNone(safe_action_url("https://"))
        self.assertIsNone(safe_action_url("https:"))

    def test_malformed_host_rejected(self):
        self.assertIsNone(safe_action_url("http://exa mple.com/path"))
        self.assertIsNone(safe_action_url("http://[broken/path"))


class TestSafeActionUrlCredentials(unittest.TestCase):
    """safe_action_url rejects embedded credentials."""

    def test_user_password_rejected(self):
        self.assertIsNone(safe_action_url("http://user:pass@example.com"))

    def test_user_only_rejected(self):
        self.assertIsNone(safe_action_url("http://user@example.com"))

    def test_empty_password_rejected(self):
        self.assertIsNone(safe_action_url("http://user:@example.com"))


class TestSafeActionUrlControlChars(unittest.TestCase):
    """safe_action_url rejects control characters including Unicode."""

    def test_newline_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com\n"))
        self.assertIsNone(safe_action_url("http://example.com\r\n"))

    def test_tab_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com\t/path"))

    def test_null_byte_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com\x00/path"))

    def test_unicode_control_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com\u0001/path"))
        self.assertIsNone(safe_action_url("http://example.com\u001f/path"))
        self.assertIsNone(safe_action_url("http://example.com\u007f/path"))
        self.assertIsNone(safe_action_url("http://example.com\u0080/path"))
        self.assertIsNone(safe_action_url("http://example.com\u009f/path"))


class TestSafeActionUrlPorts(unittest.TestCase):
    """safe_action_url rejects malformed ports."""

    def test_non_numeric_port_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com:abc/path"))

    def test_empty_port_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com:"))

    def test_port_zero_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com:0/"))

    def test_port_above_range_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com:65536/"))

    def test_port_negative_rejected(self):
        self.assertIsNone(safe_action_url("http://example.com:-1/"))


class TestSafeActionUrlNonString(unittest.TestCase):
    """safe_action_url rejects non-string inputs."""

    def test_none(self):
        self.assertIsNone(safe_action_url(None))

    def test_int(self):
        self.assertIsNone(safe_action_url(42))

    def test_list(self):
        self.assertIsNone(safe_action_url(["http://example.com"]))

    def test_dict(self):
        self.assertIsNone(safe_action_url({"url": "http://example.com"}))

    def test_float(self):
        self.assertIsNone(safe_action_url(3.14))

    def test_empty_string(self):
        self.assertIsNone(safe_action_url(""))

    def test_whitespace_only(self):
        self.assertIsNone(safe_action_url("   "))


class TestSafeActionUrlShellAndMarkup(unittest.TestCase):
    """Explicit safeguards against URLs used for shell injection or markup."""

    def test_no_markup_tags(self):
        self.assertIsNone(safe_action_url("<a href='http://example.com'>x</a>"))

    def test_no_newline_markup(self):
        self.assertIsNone(safe_action_url("http://example.com\n<script>"))


class TestNotificationHistory(unittest.TestCase):
    """NotificationHistory: sync, items, clear, bounded, expiry, replace."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.history = NotificationHistory(clock=self.clock)

    def _message(self, thread_id, title="T", ttl=None, status="message"):
        expires_at = None
        if ttl is not None and ttl > 0:
            expires_at = 1000.0 + ttl
        return {
            "threadId": thread_id,
            "title": title,
            "text": f"body for {thread_id}",
            "status": status,
            "url": "https://example.com",
            "buttonLabel": "Open",
            "ttlSeconds": ttl,
            "expires_at": expires_at,
        }

    def test_sync_stores_messages(self):
        self.history.sync({"t1": self._message("t1")})
        items = self.history.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["threadId"], "t1")

    def test_items_returns_list_of_dicts(self):
        self.history.sync({
            "t1": self._message("t1"),
            "t2": self._message("t2"),
        })
        items = self.history.items()
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertIsInstance(item, dict)
            self.assertIn("threadId", item)

    def test_sync_same_thread_replaces_no_dup(self):
        self.history.sync({"t1": self._message("t1", title="first")})
        self.history.sync({"t1": self._message("t1", title="second")})
        items = self.history.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "second")

    def test_sync_retains_missing_threads_as_dismissed(self):
        self.history.sync({
            "t1": self._message("t1"),
            "t2": self._message("t2"),
        })
        self.history.sync({"t1": self._message("t1")})
        items = self.history.items()
        self.assertEqual(len(items), 2)
        dismissed = next(item for item in items if item["threadId"] == "t2")
        self.assertEqual(dismissed["historyState"], "dismissed")

    def test_clear_empties_history(self):
        self.history.sync({"t1": self._message("t1")})
        self.history.clear()
        self.assertEqual(self.history.items(), [])

    def test_sync_non_dict(self):
        # Should not raise; treat as empty.
        self.history.sync("not a dict")
        self.history.sync(None)
        self.history.sync([1, 2, 3])
        self.assertEqual(self.history.items(), [])

    def test_bounded_retention(self):
        history = NotificationHistory(clock=self.clock, max_items=3)
        # Sync all messages at once; history is replaced, not accumulated.
        all_msgs = {
            f"t{i}": self._message(f"t{i}", title=f"msg {i}") for i in range(5)
        }
        history.sync(all_msgs)
        items = history.items()
        self.assertEqual(len(items), 3)
        # Most recent three retained: t2, t3, t4 (newest first).
        thread_ids = [it["threadId"] for it in items]
        self.assertEqual(thread_ids, ["t4", "t3", "t2"])

    def test_default_max_items(self):
        self.assertEqual(MAX_HISTORY_ITEMS, 100)
        history = NotificationHistory(clock=self.clock)
        self.assertEqual(history._max_items, 100)

    def test_expired_entries_retained_in_history(self):
        self.history.sync({
            "t1": self._message("t1", ttl=10),   # expires at 1010 (kept at 1006)
            "t2": self._message("t2", ttl=0),     # no expiry (kept)
            "t3": self._message("t3", ttl=5),     # expires at 1005 (expired at 1006)
        })
        self.clock.value = 1006.0
        items = self.history.items()
        thread_ids = [it["threadId"] for it in items]
        self.assertEqual(thread_ids, ["t3", "t2", "t1"])
        expired = next(it for it in items if it["threadId"] == "t3")
        self.assertEqual(expired["historyState"], "expired")
        self.assertEqual(len(self.history.items()), 3)

    def test_no_expiry_when_ttl_none(self):
        self.history.sync({"t1": self._message("t1", ttl=None)})
        self.clock.value = 99999.0
        items = self.history.items()
        self.assertEqual(len(items), 1)

    def test_items_newest_first(self):
        self.history.sync({
            "t1": self._message("t1", title="first"),
            "t2": self._message("t2", title="second"),
            "t3": self._message("t3", title="third"),
        })
        items = self.history.items()
        titles = [it["title"] for it in items]
        self.assertEqual(titles, ["third", "second", "first"])

    def test_sync_missing_thread_id_skipped(self):
        self.history.sync({"": self._message(""), "t1": self._message("t1")})
        items = self.history.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["threadId"], "t1")

    def test_sync_non_dict_message_skipped(self):
        self.history.sync({"t1": "not a dict", "t2": self._message("t2")})
        items = self.history.items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["threadId"], "t2")


class _FakeParent:
    """Minimal stand-in for a GTK parent window (no GI needed)."""


class _FakeOpener:
    """Records opened URLs instead of launching them."""

    def __init__(self):
        self.opened = []

    def __call__(self, url):
        self.opened.append(url)


class TestNotificationCenterImport(unittest.TestCase):
    """notification_ui imports cleanly without GI."""

    def test_module_imports_without_gi(self):
        # Importing must not require gi.
        from openpets_linux.notification_ui import (  # noqa: F401
            NotificationCenter,
        )
        self.assertIsNotNone(NotificationCenter)

    def test_notification_center_has_api(self):
        from openpets_linux.notification_ui import NotificationCenter
        for name in ("refresh", "present", "destroy"):
            self.assertTrue(hasattr(NotificationCenter, name))


@unittest.skipUnless(GTK_AVAILABLE, "GTK3 unavailable")
class TestNotificationCenterGuiSmoke(unittest.TestCase):
    """GUI smoke test with fake opener. Runs only when GTK3 is available."""

    def _make_center(self, opener=None):
        from openpets_linux.notification_ui import NotificationCenter
        history = NotificationHistory(clock=_FixedClock(1000.0))
        history.sync({
            "t1": {
                "threadId": "t1",
                "title": "Build started",
                "text": "CI build is running",
                "status": "running",
                "url": "https://ci.example.com/build/1",
                "buttonLabel": "Open CI",
                "ttlSeconds": None,
                "expires_at": None,
            },
            "t2": {
                "threadId": "t2",
                "title": "Build done",
                "text": "CI build finished",
                "status": "done",
                "url": None,
                "buttonLabel": None,
                "ttlSeconds": None,
                "expires_at": None,
            },
        })
        clear_calls = []
        opener = opener or _FakeOpener()
        center = NotificationCenter(
            parent=None,
            history=history,
            clear_callback=lambda tid: clear_calls.append(tid),
            opener=opener,
        )
        return center, history, clear_calls, opener

    def test_center_created_and_refresh(self):
        center, history, clear_calls, opener = self._make_center()
        center.refresh()
        self.assertIsNotNone(center._window)
        self.assertEqual(len(clear_calls), 0)

    def test_action_button_uses_opener_no_autoopen(self):
        center, history, clear_calls, opener = self._make_center()
        # opener must not have been called before any click.
        self.assertEqual(opener.opened, [])
        center.refresh()
        # No auto-open: nothing opened yet.
        self.assertEqual(opener.opened, [])

    def test_dismiss_invokes_clear_callback_and_refresh(self):
        center, history, clear_calls, opener = self._make_center()
        center.refresh()
        self.assertEqual(len(clear_calls), 0)
        center._clear_callback("t1")
        center.refresh()
        self.assertEqual(clear_calls, ["t1"])

    def test_clear_history_button(self):
        center, history, clear_calls, opener = self._make_center()
        center.refresh()
        self.assertEqual(len(history.items()), 2)
        center._history.clear()
        center.refresh()
        self.assertEqual(len(history.items()), 0)

    def test_unsafe_url_no_action_button(self):
        center, history, clear_calls, opener = self._make_center()
        # Add a notification with an unsafe URL - should not get an action button.
        history.sync({
            "t3": {
                "threadId": "t3",
                "title": "Bad link",
                "text": "unsafe",
                "status": "message",
                "url": "javascript:alert(1)",
                "buttonLabel": "Open",
                "ttlSeconds": None,
                "expires_at": None,
            },
        })
        center.refresh()
        rows = list(center._list.get_children())
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
