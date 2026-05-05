"""
Flipper Zero serial transport — CLI mode over USB CDC-ACM.

The Flipper exposes two interfaces on the same serial port:
  - CLI mode  (default)  text commands, used for SubGHz/IR/NFC/BadUSB
  - RPC mode             protobuf frames, entered with 'start_rpc_session'

This module stays in CLI mode. The port is read from the FLIPPER_PORT
env var (default /dev/flipper — set by the udev rule in companion/udev/).
"""

import os
import threading
import time
from typing import Optional

import serial

BAUD = 230400
PROMPT = b">: "
DEFAULT_TIMEOUT = 5.0
DEFAULT_PORT = "/dev/flipper"


class FlipperTransport:
    def __init__(self, port: Optional[str] = None, baud: int = BAUD):
        self.port = port or os.environ.get("FLIPPER_PORT", DEFAULT_PORT)
        self.baud = baud
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._ser = serial.Serial(self.port, baudrate=self.baud, timeout=DEFAULT_TIMEOUT)
        self._ser.reset_input_buffer()
        self._ser.write(b"\r\n")
        self._read_until_prompt(timeout=3.0)

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def is_connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _read_until_prompt(self, timeout: float = DEFAULT_TIMEOUT) -> bytes:
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waiting = self._ser.in_waiting
            chunk = self._ser.read(waiting if waiting else 1)
            if chunk:
                buf += chunk
                if buf.endswith(PROMPT):
                    return buf
        return buf

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cmd(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Send a CLI command and return the response (prompt and echo stripped)."""
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write((command + "\r\n").encode())
            raw = self._read_until_prompt(timeout)
            text = raw.decode(errors="replace")
            lines = text.splitlines()
            # Drop echo (first line) and prompt (last line ">: ")
            lines = [l for l in lines if l.strip() and l.strip() != ">:" and l != command]
            return "\n".join(lines).strip()

    def write_file(self, remote_path: str, data: bytes, timeout: float = 15.0) -> str:
        """
        Write binary data to a file on Flipper storage.

        The CLI `storage write` command expects:
          1. Command line
          2. Raw bytes
          3. EOF signal (ctrl+C / 0x03)

        Returns the Flipper's response.
        """
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(f"storage write {remote_path}\r\n".encode())
            time.sleep(0.05)
            self._ser.write(data)
            self._ser.write(b"\x03")  # ETX = end of input
            raw = self._read_until_prompt(timeout)
            return raw.decode(errors="replace").strip()
