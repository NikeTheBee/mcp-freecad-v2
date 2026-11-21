"""Renode External Control API, client side in pure Python (Renode 1.17,
tools/external_control_client: protocol version 0).

Renode is started with `emulation CreateExternalControlClient "ext" <port>`: IT connects to us,
on a socket we bind to 127.0.0.1 only, so nothing listens beyond the loopback. Then we drive it:
RUN_FOR (advance emulated time), GET_TIME, and SYSTEM_BUS reads / writes (the firmware's
mailbox in RAM).

Wire format (little endian, packed): envelope <u16 id><u32 size>, then the payload
<u16 command><u8 type> + data. In client mode Renode is the TCP client: it opens with its own
CHECK_VERSION request (id with bit 0x8000), which we answer; our requests then carry server-side
ids (bit 0x8000 clear) and each response echoes its id.
"""
from __future__ import annotations

import socket
import struct
from typing import Optional

CHECK_VERSION, RUN_FOR, GET_TIME, GET_MACHINE, ADC, GPIO, SYSTEM_BUS = range(7)
T_REQUEST, T_EVENT, T_SUCCESS, T_INVALID, T_ERROR = 0, 1, 2, 3, 4
SB_READ, SB_WRITE = 0, 1
AW_MULTI_BYTE = 128


class RenodeError(RuntimeError):
    pass


class Listener:
    """Bind 127.0.0.1:0, give the port to Renode, accept its connection."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def accept(self, timeout: float) -> "Renode":
        self.sock.settimeout(timeout)
        conn, _ = self.sock.accept()
        self.sock.close()
        conn.settimeout(timeout)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return Renode(conn)


class Renode:
    def __init__(self, conn: socket.socket) -> None:
        self.c = conn
        self.n = 0
        self.machine: Optional[int] = None
        self.sysbus: Optional[int] = None

    def close(self) -> None:
        try:
            self.c.close()
        except OSError:
            pass

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = self.c.recv(n - len(buf))
            if not part:
                raise RenodeError("Renode closed the connection")
            buf += part
        return buf

    def request(self, command: int, data: bytes = b"") -> bytes:
        rid = self.n & 0x7FFF
        self.n += 1
        payload = struct.pack("<HB", command, T_REQUEST) + data
        self.c.sendall(struct.pack("<HI", rid, len(payload)) + payload)
        while True:
            mid, size = struct.unpack("<HI", self._recv(6))
            if size > 64 * 1024 * 1024:
                raise RenodeError("oversized message from Renode")
            body = self._recv(size)
            cmd, typ = struct.unpack("<HB", body[:3])
            if typ in (T_REQUEST, T_EVENT):
                self._answer(mid, cmd)
                continue
            if mid != rid:
                raise RenodeError(f"response id {mid:#x} for request {rid:#x}")
            if typ == T_SUCCESS:
                return body[3:]
            msg = body[3:].decode("utf-8", "replace")
            raise RenodeError(f"command {command} failed ({'unknown' if typ == T_INVALID else 'error'}): {msg}")

    def _answer(self, mid: int, cmd: int) -> None:
        """A request from Renode: its version check is accepted, anything else refused."""
        typ = T_SUCCESS if cmd == CHECK_VERSION else T_INVALID
        self.c.sendall(struct.pack("<HI", mid, 3) + struct.pack("<HB", cmd, typ))

    # ---------------------------------------------------------------- API
    def handshake(self, machine: str = "mcu") -> None:
        mid, size = struct.unpack("<HI", self._recv(6))       # Renode speaks first
        cmd, typ = struct.unpack("<HB", self._recv(size)[:3])
        if typ != T_REQUEST or cmd != CHECK_VERSION:
            raise RenodeError(f"unexpected opening message from Renode (command {cmd}, type {typ})")
        self._answer(mid, cmd)
        name = machine.encode()
        (self.machine,) = struct.unpack("<i", self.request(GET_MACHINE, struct.pack("<I", len(name)) + name))
        sb = b"sysbus"
        (self.sysbus,) = struct.unpack("<i", self.request(
            SYSTEM_BUS, struct.pack("<iiI", -1, self.machine, len(sb)) + sb))

    def run_for_us(self, us: int) -> None:
        self.request(RUN_FOR, struct.pack("<Q", int(us) * 1000))     # renode_time_t: ns

    def time_us(self) -> float:
        (t,) = struct.unpack("<Q", self.request(GET_TIME, struct.pack("<Q", 0)))
        return t / 1000.0

    def read(self, addr: int, n: int) -> bytes:
        return self.request(SYSTEM_BUS, struct.pack("<iBBQI", self.sysbus, SB_READ, AW_MULTI_BYTE, addr, n))

    def write(self, addr: int, data: bytes) -> None:
        self.request(SYSTEM_BUS, struct.pack("<iBBQI", self.sysbus, SB_WRITE, AW_MULTI_BYTE, addr, len(data)) + data)
