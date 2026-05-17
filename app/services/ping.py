"""PC reachability check (TCP connect to port 22 or 80 as a proxy for ping)."""
from __future__ import annotations

import asyncio


async def is_reachable(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False
