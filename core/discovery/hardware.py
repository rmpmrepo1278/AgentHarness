"""Hardware discovery — detect RAM, CPU, GPU, NPU, storage, network interfaces.

Cross-platform: works on both Linux (/proc/*, lspci, lsblk) and macOS (sysctl,
system_profiler). Missing tools are handled gracefully — each probe returns
a safe default when the underlying command is unavailable.
"""

import os
import platform
import re
import socket
import subprocess


def _run(cmd: str) -> str:
    """Run a shell command safely, returning stdout or empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _detect_ram_gb() -> float:
    """Detect total RAM in GB."""
    system = platform.system()
    if system == "Darwin":
        out = _run("sysctl -n hw.memsize")
        if out:
            return int(out) / (1024 ** 3)
    else:
        # Linux: /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        except (OSError, ValueError):
            pass
        # Fallback
        out = _run("free -b 2>/dev/null | awk '/Mem:/{print $2}'")
        if out:
            return int(out) / (1024 ** 3)
    return 0.0


def _detect_ram_dimms() -> list:
    """Detect RAM DIMM info."""
    system = platform.system()
    if system == "Darwin":
        out = _run("system_profiler SPMemoryDataType 2>/dev/null")
        dimms = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Size:"):
                current["size"] = line.split(":", 1)[1].strip()
            elif line.startswith("Type:"):
                current["type"] = line.split(":", 1)[1].strip()
            elif line.startswith("Speed:"):
                current["speed"] = line.split(":", 1)[1].strip()
                dimms.append(current)
                current = {}
        if current and "size" in current:
            dimms.append(current)
        return dimms
    else:
        out = _run("sudo dmidecode -t memory 2>/dev/null")
        if not out:
            return []
        dimms = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Size:") and "No Module" not in line:
                current["size"] = line.split(":", 1)[1].strip()
            elif line.startswith("Type:") and current:
                current["type"] = line.split(":", 1)[1].strip()
            elif line.startswith("Speed:") and current:
                current["speed"] = line.split(":", 1)[1].strip()
                dimms.append(current)
                current = {}
        return dimms


def _detect_cpu_cores() -> int:
    """Detect number of CPU cores."""
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _detect_cpu_model() -> str:
    """Detect CPU model string."""
    system = platform.system()
    if system == "Darwin":
        out = _run("sysctl -n machdep.cpu.brand_string")
        if out:
            return out
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        out = _run("lscpu 2>/dev/null | grep 'Model name'")
        if out:
            return out.split(":", 1)[1].strip()
    return "unknown"


def _detect_cpu_flags() -> dict:
    """Detect AVX2 and AVX512 support."""
    system = platform.system()
    flags_str = ""
    if system == "Darwin":
        # macOS: check sysctl for AVX features
        avx2 = _run("sysctl -n hw.optional.avx2_0 2>/dev/null")
        avx512 = _run("sysctl -n hw.optional.avx512f 2>/dev/null")
        return {
            "cpu_has_avx2": avx2 == "1",
            "cpu_has_avx512": avx512 == "1",
        }
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("flags"):
                        flags_str = line
                        break
        except OSError:
            pass
    return {
        "cpu_has_avx2": "avx2" in flags_str,
        "cpu_has_avx512": "avx512f" in flags_str,
    }


def _detect_gpu_devices() -> list:
    """Detect GPU devices."""
    system = platform.system()
    gpus = []
    if system == "Darwin":
        out = _run("system_profiler SPDisplaysDataType 2>/dev/null")
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Chipset Model:"):
                gpus.append(line.split(":", 1)[1].strip())
    else:
        out = _run("lspci 2>/dev/null | grep -iE 'VGA|3D|Display'")
        for line in out.splitlines():
            if line.strip():
                gpus.append(line.strip())
    return gpus


def _detect_nvidia() -> bool:
    """Check if NVIDIA GPU is present."""
    out = _run("nvidia-smi 2>/dev/null")
    return "NVIDIA" in out


def _detect_amd_gpu(gpu_devices: list) -> bool:
    """Check if AMD GPU is present."""
    for gpu in gpu_devices:
        if "AMD" in gpu.upper() or "RADEON" in gpu.upper():
            return True
    return False


def _detect_npu() -> bool:
    """Check for NPU/neural accelerator."""
    system = platform.system()
    if system == "Darwin":
        # Apple Silicon has ANE (Apple Neural Engine)
        arch = platform.machine()
        return arch == "arm64"
    else:
        # Check for Intel NPU or other accelerators
        out = _run("lspci 2>/dev/null | grep -i 'neural\\|npu\\|accelerator'")
        if out:
            return True
        # Check for /dev/accel* devices
        out = _run("ls /dev/accel* 2>/dev/null")
        return bool(out)


def _detect_storage_devices() -> list:
    """Detect storage devices."""
    system = platform.system()
    devices = []
    if system == "Darwin":
        out = _run("diskutil list 2>/dev/null")
        for line in out.splitlines():
            # Match lines like "/dev/disk0 (internal, physical):"
            m = re.match(r"^(/dev/disk\d+)\s+\((.+?)\):", line)
            if m:
                devices.append({"device": m.group(1), "info": m.group(2)})
    else:
        out = _run("lsblk -J 2>/dev/null")
        if out:
            try:
                import json
                data = json.loads(out)
                for dev in data.get("blockdevices", []):
                    devices.append({
                        "device": f"/dev/{dev.get('name', '?')}",
                        "size": dev.get("size", "?"),
                        "type": dev.get("type", "?"),
                    })
            except (ValueError, KeyError):
                pass
        if not devices:
            out = _run("lsblk -o NAME,SIZE,TYPE 2>/dev/null")
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "disk":
                    devices.append({
                        "device": f"/dev/{parts[0]}",
                        "size": parts[1],
                        "type": parts[2],
                    })
    return devices


def _detect_usb_drives() -> list:
    """Detect USB drives."""
    system = platform.system()
    drives = []
    if system == "Darwin":
        out = _run("system_profiler SPUSBDataType 2>/dev/null")
        # Look for storage-like USB devices
        lines = out.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("USB") and ":" not in stripped:
                # This is a device name
                drives.append(stripped)
    else:
        out = _run("lsusb 2>/dev/null")
        for line in out.splitlines():
            if line.strip():
                drives.append(line.strip())
    return drives


def _detect_network_interfaces() -> list:
    """Detect network interfaces."""
    system = platform.system()
    interfaces = []
    if system == "Darwin":
        out = _run("ifconfig -l 2>/dev/null")
        if out:
            interfaces = out.split()
    else:
        out = _run("ip -o link show 2>/dev/null")
        if out:
            for line in out.splitlines():
                m = re.match(r"\d+:\s+(\S+?):", line)
                if m:
                    interfaces.append(m.group(1))
        if not interfaces:
            out = _run("ifconfig -a 2>/dev/null")
            for line in out.splitlines():
                m = re.match(r"^(\S+?):", line)
                if m:
                    interfaces.append(m.group(1))
    return interfaces


def discover_hardware() -> dict:
    """Discover hardware info and return a dict with all hardware details.

    Keys: total_ram_gb, ram_dimms, cpu_cores, cpu_model, architecture,
    platform, hostname, cpu_has_avx2, cpu_has_avx512, gpu_devices,
    has_nvidia, has_amd_gpu, has_npu, storage_devices, usb_drives,
    network_interfaces
    """
    gpu_devices = _detect_gpu_devices()
    cpu_flags = _detect_cpu_flags()

    return {
        "total_ram_gb": round(_detect_ram_gb(), 2),
        "ram_dimms": _detect_ram_dimms(),
        "cpu_cores": _detect_cpu_cores(),
        "cpu_model": _detect_cpu_model(),
        "architecture": platform.machine(),
        "platform": platform.system().lower(),
        "hostname": socket.gethostname(),
        "cpu_has_avx2": cpu_flags["cpu_has_avx2"],
        "cpu_has_avx512": cpu_flags["cpu_has_avx512"],
        "gpu_devices": gpu_devices,
        "has_nvidia": _detect_nvidia(),
        "has_amd_gpu": _detect_amd_gpu(gpu_devices),
        "has_npu": _detect_npu(),
        "storage_devices": _detect_storage_devices(),
        "usb_drives": _detect_usb_drives(),
        "network_interfaces": _detect_network_interfaces(),
    }


def recommended_model_size_gb(total_ram_gb: float) -> int:
    """Recommend max model size in GB based on available RAM.

    Heuristic:
        <=4 GB  -> 2 GB
        <=8 GB  -> 50% of RAM
        <=16 GB -> 60% of RAM
        >16 GB  -> total - 8 (reserve 8 GB for OS + services)
    """
    if total_ram_gb <= 4:
        return 2
    elif total_ram_gb <= 8:
        return int(total_ram_gb * 0.5)
    elif total_ram_gb <= 16:
        return int(total_ram_gb * 0.6)
    else:
        return int(total_ram_gb - 8)
