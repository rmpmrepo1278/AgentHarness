"""Auto-detect free host ports for Docker stack deploys."""
import socket
import logging

log = logging.getLogger("port_allocator")

_PORT_RANGE_START = 8000
_PORT_RANGE_END = 9000


def is_port_free(port: int) -> bool:
    """Check if a TCP port is available on the host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("0.0.0.0", port))
            return result != 0
    except OSError:
        return False


def find_free_port(preferred: int = None) -> int:
    """Find a free port. Tries preferred first, then scans the range."""
    if preferred and is_port_free(preferred):
        return preferred

    for port in range(_PORT_RANGE_START, _PORT_RANGE_END):
        if is_port_free(port):
            if preferred and port != preferred:
                log.info(f"Port {preferred} in use, allocated {port} instead")
            return port

    raise RuntimeError(f"No free ports in range {_PORT_RANGE_START}-{_PORT_RANGE_END}")
