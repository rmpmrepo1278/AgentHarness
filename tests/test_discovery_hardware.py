"""Tests for core.discovery.hardware — hardware detection for RAM, CPU, GPU, NPU, storage, network."""

import pytest

from core.discovery.hardware import discover_hardware, recommended_model_size_gb


REQUIRED_KEYS = {"total_ram_gb", "cpu_cores", "cpu_model", "architecture"}


class TestDiscoverHardware:
    def test_discover_hardware_returns_required_keys(self):
        """total_ram_gb, cpu_cores, cpu_model, architecture must be present."""
        hw = discover_hardware()
        for key in REQUIRED_KEYS:
            assert key in hw, f"Missing required key: {key}"

    def test_ram_is_positive(self):
        """total_ram_gb > 0."""
        hw = discover_hardware()
        assert hw["total_ram_gb"] > 0

    def test_cpu_cores_is_positive(self):
        """cpu_cores > 0."""
        hw = discover_hardware()
        assert hw["cpu_cores"] > 0

    def test_storage_devices_is_list(self):
        """storage_devices is a list."""
        hw = discover_hardware()
        assert isinstance(hw["storage_devices"], list)

    def test_recommended_model_size_36gb(self):
        """recommended_model_size_gb(36) returns 10-28."""
        size = recommended_model_size_gb(36)
        assert 10 <= size <= 28, f"Expected 10-28 for 36GB RAM, got {size}"

    def test_recommended_model_size_8gb(self):
        """recommended_model_size_gb(8) returns 2-5."""
        size = recommended_model_size_gb(8)
        assert 2 <= size <= 5, f"Expected 2-5 for 8GB RAM, got {size}"

    def test_recommended_model_size_4gb(self):
        """recommended_model_size_gb(4) returns 2."""
        size = recommended_model_size_gb(4)
        assert size == 2

    def test_recommended_model_size_2gb(self):
        """recommended_model_size_gb(2) returns 2 (floor)."""
        size = recommended_model_size_gb(2)
        assert size == 2

    def test_recommended_model_size_16gb(self):
        """recommended_model_size_gb(16) returns ~9-10 (60%)."""
        size = recommended_model_size_gb(16)
        assert 9 <= size <= 10
