"""Wake-on-LAN service."""
from __future__ import annotations

import socket
import struct


def _build_magic_packet(mac: str) -> bytes:
    """Build a WoL magic packet for the given MAC address."""
    mac_clean = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(mac_clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    mac_bytes = bytes.fromhex(mac_clean)
    return b"\xff" * 6 + mac_bytes * 16


async def send_magic_packet(mac: str, broadcast: str, port: int = 9) -> None:
    """Send a WoL magic packet to the broadcast address."""
    packet = _build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
