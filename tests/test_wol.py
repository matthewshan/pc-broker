"""Tests for WoL service."""
from __future__ import annotations

import pytest
from app.services.wol import _build_magic_packet


def test_magic_packet_length():
    pkt = _build_magic_packet("aa:bb:cc:dd:ee:ff")
    # 6 bytes sync stream + 16 * 6 byte MAC = 102 bytes
    assert len(pkt) == 102


def test_magic_packet_sync_stream():
    pkt = _build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert pkt[:6] == b"\xff" * 6


def test_magic_packet_mac_repeated():
    mac = "aa:bb:cc:dd:ee:ff"
    mac_bytes = bytes.fromhex(mac.replace(":", ""))
    pkt = _build_magic_packet(mac)
    assert pkt[6:12] == mac_bytes
    # Check all 16 repetitions
    for i in range(16):
        assert pkt[6 + i * 6 : 6 + (i + 1) * 6] == mac_bytes


def test_magic_packet_invalid_mac():
    with pytest.raises(ValueError):
        _build_magic_packet("zz:zz:zz")
