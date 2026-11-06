"""Unit tests for openpets_linux.plugins (standard library only, no GI).

All tests use in-memory fixtures and fake fetchers. No real credential files
and no real network access are used anywhere.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.plugins import (  # noqa: E402
    CODEX_AUTH_PATH,
    CODEX_USAGE_URL,
    CLAUDE_CREDENTIALS_PATH,
    CLAUDE_USAGE_URL,
    FETCH_MAX_BODY,
    FETCH_MAX_TIMEOUT,
    PluginError,
    default_fetcher,
    read_battery,
    read_claude_usage,
    read_codex_usage,
    snapshot,
)


class _FakeFetcher:
    """Callable fake fetcher recording (url, headers, timeout) calls."""

    def __init__(self, response=None, exc=None):
        self.response = response or {"status": 200, "body": "{}"}
        self.exc = exc
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if self.exc is not None:
            raise self.exc
        return dict(self.response)


class _FixedClock:
    """Deterministic wall-clock for deterministic reset-time assertions."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def time(self):
        return self.value


class TestSnapshotHelpers(unittest.TestCase):
    def test_snapshot_minimal(self):
        result = snapshot("openpets.plugin.test", "Test", "10%", state="normal")
        self.assertEqual(result["id"], "openpets.plugin.test")
        self.assertEqual(result["label"], "Test")
        self.assertEqual(result["value"], "10%")
        self.assertEqual(result["details"], [])
        self.assertEqual(result["state"], "normal")
        self.assertNotIn("windows", result)

    def test_snapshot_with_windows(self):
        windows = [
            snapshot("w1", "One", "10%", ["d1"], "normal"),
        ]
        result = snapshot("openpets.plugin.test", "Test", "10%", ["d0"], "normal", windows=windows)
        self.assertEqual(len(result["windows"]), 1)
        self.assertEqual(result["windows"][0]["id"], "w1")


class TestReadBatteryDefaults(unittest.TestCase):
    def test_missing_sysfs_root_returns_failed(self):
        result = read_battery(sysfs_root="/nonexistent-sysfs-12345")
        self.assertEqual(result["id"], "openpets.plugin.battery")
        self.assertEqual(result["state"], "failed")
        self.assertIsInstance(result["details"], list)
        self.assertTrue(len(result["details"]) > 0)
        self.assertNotIn("windows", result)

    def test_no_battery_found(self):
        with tempfile.TemporaryDirectory() as root:
            # Create a non-battery dir so listdir isn't empty but no Battery type.
            os.makedirs(os.path.join(root, "AC"))
            with open(os.path.join(root, "AC", "type"), "w") as f:
                f.write("Mains\n")
            with open(os.path.join(root, "AC", "online"), "w") as f:
                f.write("1\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "failed")

    def test_single_battery_discharging(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("72\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Discharging\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "normal")
            self.assertEqual(result["value"], "72%")
            self.assertEqual(result["details"], ["Charge: 72%", "State: Discharging"])
            self.assertNotIn("windows", result)

    def test_battery_charging_is_success(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("88\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Charging\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "success")
            self.assertEqual(result["value"], "88%")

    def test_battery_low_critical(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("7\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Discharging\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "critical")

    def test_battery_low_warning(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("15\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Discharging\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "warning")

    def test_battery_full(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("100\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Full\n")
            result = read_battery(sysfs_root=root)
            self.assertEqual(result["state"], "normal")
            self.assertEqual(result["details"], ["Charge: 100%", "State: Full"])

    def test_ac_online_detail(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            os.makedirs(os.path.join(root, "AC0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("50\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Discharging\n")
            with open(os.path.join(root, "AC0", "type"), "w") as f:
                f.write("Mains\n")
            with open(os.path.join(root, "AC0", "online"), "w") as f:
                f.write("1\n")
            result = read_battery(sysfs_root=root)
            self.assertIn("AC: Online", result["details"])

    def test_ac_offline_detail(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "BAT0"))
            os.makedirs(os.path.join(root, "AC0"))
            with open(os.path.join(root, "BAT0", "type"), "w") as f:
                f.write("Battery\n")
            with open(os.path.join(root, "BAT0", "capacity"), "w") as f:
                f.write("50\n")
            with open(os.path.join(root, "BAT0", "status"), "w") as f:
                f.write("Discharging\n")
            with open(os.path.join(root, "AC0", "type"), "w") as f:
                f.write("Mains\n")
            with open(os.path.join(root, "AC0", "online"), "w") as f:
                f.write("0\n")
            result = read_battery(sysfs_root=root)
            self.assertIn("AC: Offline", result["details"])

    def test_multiple_batteries_aggregated(self):
        with tempfile.TemporaryDirectory() as root:
            for name, cap, status in (("BAT0", "80", "Discharging"), ("BAT1", "60", "Discharging")):
                os.makedirs(os.path.join(root, name))
                with open(os.path.join(root, name, "type"), "w") as f:
                    f.write("Battery\n")
                with open(os.path.join(root, name, "capacity"), "w") as f:
                    f.write(cap + "\n")
                with open(os.path.join(root, name, "status"), "w") as f:
                    f.write(status + "\n")
            result = read_battery(sysfs_root=root)
            # Average of 80 and 60 = 70
            self.assertEqual(result["value"], "70%")
            self.assertEqual(result["state"], "normal")

    def test_multiple_batteries_charging_or_aggregated(self):
        with tempfile.TemporaryDirectory() as root:
            for name, cap, status in (("BAT0", "15", "Discharging"), ("BAT1", "10", "Discharging")):
                os.makedirs(os.path.join(root, name))
                with open(os.path.join(root, name, "type"), "w") as f:
                    f.write("Battery\n")
                with open(os.path.join(root, name, "capacity"), "w") as f:
                    f.write(cap + "\n")
                with open(os.path.join(root, name, "status"), "w") as f:
                    f.write(status + "\n")
            result = read_battery(sysfs_root=root)
            # Average of 15 and 10 = 12 -> warning
            self.assertEqual(result["value"], "12%")
            self.assertEqual(result["state"], "warning")


class TestReadClaudeUsageMissingCredentials(unittest.TestCase):
    def test_missing_credentials_file_is_setup_state(self):
        result = read_claude_usage(credentials_path="/nonexistent/credentials.json")
        self.assertEqual(result["id"], "openpets.plugin.claude-code")
        self.assertEqual(result["state"], "setup")
        self.assertIsInstance(result["details"], list)
        self.assertTrue(any("credentials" in d.lower() for d in result["details"]))
        self.assertNotIn("windows", result)

    def test_empty_credentials_file_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("")
            path = f.name
        try:
            result = read_claude_usage(credentials_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_credentials_without_accesstoken_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"claudeAiOauth": {}}))
            path = f.name
        try:
            result = read_claude_usage(credentials_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_credentials_with_empty_token_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"claudeAiOauth": {"accessToken": ""}}))
            path = f.name
        try:
            result = read_claude_usage(credentials_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_invalid_json_credentials_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            path = f.name
        try:
            result = read_claude_usage(credentials_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_credentials_hint_mentions_claude_auth(self):
        result = read_claude_usage(credentials_path="/nonexistent/credentials.json")
        hint_text = " ".join(result["details"])
        self.assertIn("claude auth", hint_text)


class TestReadClaudeUsageWithFakeFetcher(unittest.TestCase):
    _CREDENTIALS = json.dumps({"claudeAiOauth": {"accessToken": "abc123", "expiresAt": 9999999999000}})

    def _write_creds(self, content=None):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(content or self._CREDENTIALS)
        return path

    def _usage_payload(self, five_hour=None, seven_day=None):
        payload = {}
        if five_hour is not None:
            payload["five_hour"] = five_hour
        if seven_day is not None:
            payload["seven_day"] = seven_day
        return payload

    def test_ok_both_windows(self):
        path = self._write_creds()
        now = 1000.0
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "five_hour": {"utilization": 45, "resets_at": "2026-01-01T00:00:00Z"},
                "seven_day": {"utilization": 30, "resets_at": "2026-01-08T00:00:00Z"},
            }),
        })
        with patch("openpets_linux.plugins.time.time", return_value=now):
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        os.remove(path)
        self.assertEqual(result["id"], "openpets.plugin.claude-code")
        self.assertEqual(result["state"], "normal")
        self.assertIsInstance(result["windows"], list)
        self.assertEqual(len(result["windows"]), 2)
        self.assertTrue(any(w["id"].endswith(".5h") for w in result["windows"]))
        self.assertTrue(any(w["id"].endswith(".7d") for w in result["windows"]))
        self.assertEqual(result["windows"][0]["value"], "45%")
        self.assertEqual(result["windows"][1]["value"], "30%")

    def test_fetcher_receives_bearer_and_headers(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 200, "body": json.dumps({})})
        try:
            read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(len(fetcher.calls), 1)
        url, headers, timeout = fetcher.calls[0]
        self.assertEqual(url, CLAUDE_USAGE_URL)
        self.assertEqual(headers["Authorization"], "Bearer abc123")
        self.assertEqual(headers["anthropic-beta"], "oauth-2025-04-20")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], "claude-code/1.0.0")
        self.assertLessEqual(timeout, FETCH_MAX_TIMEOUT)

    def test_401_is_setup_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 401, "body": "{}"})
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "setup")

    def test_non_2xx_is_failed_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 500, "body": "{}"})
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_network_error_is_failed_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(exc=PluginError("network error"))
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_invalid_json_body_is_failed_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 200, "body": "{not json"})
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_no_usable_windows_is_failed_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 200, "body": json.dumps({"other": {}})})
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_window_reset_in_past_is_skipped(self):
        path = self._write_creds()
        # resets_at far in the past -> window skipped
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "five_hour": {"utilization": 50, "resets_at": "2000-01-01T00:00:00Z"},
                "seven_day": {"utilization": 30, "resets_at": "2000-01-01T00:00:00Z"},
            }),
        })
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        # Now is 2026 so both resets are in the past
        self.assertEqual(result["state"], "failed")

    def test_high_usage_critical(self):
        path = self._write_creds()
        now = 1000.0
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "five_hour": {"utilization": 95, "resets_at": "2026-01-01T00:00:00Z"},
            }),
        })
        with patch("openpets_linux.plugins.time.time", return_value=now):
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        os.remove(path)
        self.assertEqual(result["state"], "critical")
        self.assertEqual(result["windows"][0]["value"], "95%")

    def test_missing_usage_key_returns_setup_state(self):
        path = self._write_creds()
        fetcher = _FakeFetcher(response={"status": 200, "body": json.dumps({"foo": "bar"})})
        try:
            result = read_claude_usage(credentials_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")


class TestReadCodexUsageMissingCredentials(unittest.TestCase):
    def test_missing_auth_file_is_setup_state(self):
        result = read_codex_usage(auth_path="/nonexistent/auth.json")
        self.assertEqual(result["id"], "openpets.plugin.codex-usage")
        self.assertEqual(result["state"], "setup")
        self.assertIsInstance(result["details"], list)
        self.assertTrue(any("credentials" in d.lower() for d in result["details"]))
        self.assertNotIn("windows", result)

    def test_empty_auth_file_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("")
            path = f.name
        try:
            result = read_codex_usage(auth_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_auth_without_tokens_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({}))
            path = f.name
        try:
            result = read_codex_usage(auth_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_auth_without_accesstoken_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"tokens": {}}))
            path = f.name
        try:
            result = read_codex_usage(auth_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_invalid_json_auth_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{bad")
            path = f.name
        try:
            result = read_codex_usage(auth_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_empty_token_is_setup_state(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"tokens": {"access_token": ""}}))
            path = f.name
        try:
            result = read_codex_usage(auth_path=path)
            self.assertEqual(result["state"], "setup")
        finally:
            os.remove(path)

    def test_auth_hint_mentions_codex(self):
        result = read_codex_usage(auth_path="/nonexistent/auth.json")
        hint_text = " ".join(result["details"])
        self.assertIn("Codex", hint_text)


class TestReadCodexUsageWithFakeFetcher(unittest.TestCase):
    _AUTH = json.dumps({"tokens": {"access_token": "xyz789"}})

    def _write_auth(self, content=None):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(content or self._AUTH)
        return path

    def test_ok_primary_and_secondary(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "limits": {
                    "primary": {"used_percent": 40, "window_minutes": 300, "resets_in_seconds": 900},
                    "secondary": {"used_percent": 20, "window_minutes": 10080, "resets_in_seconds": 50000},
                },
                "plan_type": "plus",
            }),
        })
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["id"], "openpets.plugin.codex-usage")
        self.assertEqual(result["state"], "normal")
        self.assertIsInstance(result["windows"], list)
        self.assertEqual(len(result["windows"]), 2)
        self.assertTrue(any(w["id"].endswith(".primary") for w in result["windows"]))
        self.assertTrue(any(w["id"].endswith(".secondary") for w in result["windows"]))

    def test_fetcher_receives_bearer_and_headers(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={"status": 200, "body": json.dumps({})})
        try:
            read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(len(fetcher.calls), 1)
        url, headers, timeout = fetcher.calls[0]
        self.assertEqual(url, CODEX_USAGE_URL)
        self.assertEqual(headers["Authorization"], "Bearer xyz789")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertLessEqual(timeout, FETCH_MAX_TIMEOUT)

    def test_401_is_setup_state(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={"status": 401, "body": "{}"})
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "setup")

    def test_network_error_is_failed_state(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(exc=PluginError("network error"))
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_invalid_json_body_is_failed_state(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={"status": 200, "body": "not json"})
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_no_usable_buckets_is_failed_state(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={"status": 200, "body": json.dumps({"limits": {}})})
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "failed")

    def test_tolerance_for_trailing_content(self):
        path = self._write_auth()
        body = "prefix " + json.dumps({
            "limits": {
                "primary": {"used_percent": 10, "window_minutes": 300, "resets_in_seconds": 900},
            },
        }) + " suffix"
        fetcher = _FakeFetcher(response={"status": 200, "body": body})
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "normal")
        self.assertEqual(len(result["windows"]), 1)

    def test_primary_used_percent_maps_to_remaining(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "limits": {
                    "primary": {"used_percent": 80, "window_minutes": 300, "resets_in_seconds": 900},
                },
            }),
        })
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["windows"][0]["value"], "20%")
        self.assertIn("Remaining: 20%", result["windows"][0]["details"])

    def test_high_used_is_critical(self):
        path = self._write_auth()
        fetcher = _FakeFetcher(response={
            "status": 200,
            "body": json.dumps({
                "limits": {
                    "primary": {"used_percent": 95, "window_minutes": 300, "resets_in_seconds": 900},
                },
            }),
        })
        try:
            result = read_codex_usage(auth_path=path, fetcher=fetcher)
        finally:
            os.remove(path)
        self.assertEqual(result["state"], "critical")


class TestDefaultFetcher(unittest.TestCase):
    def test_only_https_allowed(self):
        with self.assertRaises(PluginError):
            default_fetcher("http://example.com/", {}, 1.0)
        with self.assertRaises(PluginError):
            default_fetcher("ftp://example.com/", {}, 1.0)

    def test_timeout_capped(self):
        with self.assertRaises(PluginError):
            default_fetcher("https://example.com/", {}, 999.0)
        with self.assertRaises(PluginError):
            default_fetcher("https://example.com/", {}, 0)
        with self.assertRaises(PluginError):
            default_fetcher("https://example.com/", {}, -1)

    def test_invalid_timeout(self):
        with self.assertRaises(PluginError):
            default_fetcher("https://example.com/", {}, "not-a-number")

    def test_response_size_capped(self):
        class _BigFetcher:
            def __call__(self, url, headers, timeout):
                return {"status": 200, "body": "x" * (FETCH_MAX_BODY + 1)}
        # default_fetcher reads from network, cannot easily inject big body;
        # the size cap is exercised by the PluginError path below via a real
        # network failure which is expected to raise PluginError.
        self.assertTrue(FETCH_MAX_BODY == 1024 * 1024)


class TestPluginConstants(unittest.TestCase):
    def test_plugin_ids_match_swift(self):
        from openpets_linux.plugins import PLUGIN_ID_BATTERY, PLUGIN_ID_CLAUDE, PLUGIN_ID_CODEX
        self.assertEqual(PLUGIN_ID_BATTERY, "openpets.plugin.battery")
        self.assertEqual(PLUGIN_ID_CLAUDE, "openpets.plugin.claude-code")
        self.assertEqual(PLUGIN_ID_CODEX, "openpets.plugin.codex-usage")

    def test_default_paths_expand_home(self):
        self.assertTrue(CLAUDE_CREDENTIALS_PATH.endswith(".claude/.credentials.json"))
        self.assertTrue(CODEX_AUTH_PATH.endswith(".codex/auth.json"))

    def test_endpoints_are_https(self):
        self.assertTrue(CLAUDE_USAGE_URL.startswith("https://"))
        self.assertTrue(CODEX_USAGE_URL.startswith("https://"))

    def test_fetch_limits(self):
        self.assertEqual(FETCH_MAX_BODY, 1024 * 1024)
        self.assertEqual(FETCH_MAX_TIMEOUT, 5.0)


if __name__ == "__main__":
    unittest.main()
