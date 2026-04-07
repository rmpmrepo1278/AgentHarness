"""Tests for core.scheduler.windows — network window detection."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core.scheduler.windows import (
    get_network_state,
    get_window,
    is_task_due,
)


class TestGetNetworkState(unittest.TestCase):
    """Tests for get_network_state()."""

    @patch("core.scheduler.windows._ping", return_value=True)
    def test_get_network_state_online(self, mock_ping):
        self.assertEqual(get_network_state(), "online")

    @patch("core.scheduler.windows._ping_host", return_value=False)
    @patch("core.scheduler.windows._ping", return_value=False)
    def test_get_network_state_offline(self, mock_ping, mock_ping_host):
        self.assertEqual(get_network_state(minipc_ip="192.168.1.100"), "offline")

    @patch("core.scheduler.windows._ping_host", return_value=True)
    @patch("core.scheduler.windows._ping", return_value=False)
    def test_get_network_state_lan_only(self, mock_ping, mock_ping_host):
        self.assertEqual(
            get_network_state(minipc_ip="192.168.1.100"), "lan_only"
        )


class TestGetWindow(unittest.TestCase):
    """Tests for get_window()."""

    def test_get_window_online(self):
        self.assertEqual(get_window("online"), "online")

    def test_get_window_offline(self):
        self.assertEqual(get_window("offline"), "offline")

    def test_get_window_lan(self):
        self.assertEqual(get_window("lan_only"), "offline_lan")


class TestIsTaskDue(unittest.TestCase):
    """Tests for is_task_due()."""

    def test_is_task_due_daily(self):
        last_run = time.time() - (25 * 3600)  # 25 hours ago
        self.assertTrue(is_task_due("daily", last_run))

    def test_is_task_not_due(self):
        last_run = time.time() - (1 * 3600)  # 1 hour ago
        self.assertFalse(is_task_due("daily", last_run))


if __name__ == "__main__":
    unittest.main()
