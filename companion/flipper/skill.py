"""
Flipper Zero skill for hermes-agent.

Drop this file into hermes-agent's skills/ directory. The TOOLS list at the
bottom is the registry hermes-agent uses to discover callable tools.

Environment:
  FLIPPER_PORT       serial device path (default: /dev/flipper)
  FLIPPER_AUDIT_LOG  JSONL audit log path (default: ~/.flipper_audit.jsonl)

Setup on host:
  1. Install udev rule from companion/udev/99-flipper.rules
  2. pip install pyserial
  3. Copy this file to hermes-agent/skills/flipper_skill.py
"""

import os
from typing import Optional

from .risk import classified, check_path_blocked, LOW, MEDIUM, HIGH, BLOCKED
from .transport import FlipperTransport

_transport: Optional[FlipperTransport] = None


def _t() -> FlipperTransport:
    """Return connected transport, reconnecting if necessary."""
    global _transport
    if not (_transport and _transport.is_connected()):
        _transport = FlipperTransport()
        _transport.connect()
    return _transport


# ---------------------------------------------------------------------------
# Low risk — read-only
# ---------------------------------------------------------------------------

@classified(LOW)
def flipper_ping() -> str:
    """Check whether the Flipper Zero is connected and responsive."""
    result = _t().cmd("device_info", timeout=3.0)
    return "Flipper connected." if result else "No response from Flipper."


@classified(LOW)
def flipper_device_info() -> str:
    """Return Flipper Zero hardware model, firmware version, and battery level."""
    return _t().cmd("device_info")


@classified(LOW)
def flipper_power_info() -> str:
    """Return battery charge percentage and charging state."""
    return _t().cmd("power_info")


@classified(LOW)
def flipper_storage_list(path: str = "/ext") -> str:
    """
    List files and directories on Flipper storage.

    path: /ext  — SD card (default)
          /int  — internal flash
    """
    return _t().cmd(f"storage list {path}")


@classified(LOW)
def flipper_storage_read(path: str) -> str:
    """Read and return the contents of a file on Flipper storage."""
    return _t().cmd(f"storage read {path}", timeout=10.0)


@classified(LOW)
def flipper_storage_stat(path: str) -> str:
    """Return file metadata (size, type) for a path on Flipper storage."""
    return _t().cmd(f"storage stat {path}")


# ---------------------------------------------------------------------------
# Medium risk — mutations, result shown in reply
# ---------------------------------------------------------------------------

@classified(MEDIUM)
def flipper_storage_mkdir(path: str) -> str:
    """Create a directory on Flipper storage."""
    check_path_blocked(path)
    return _t().cmd(f"storage mkdir {path}")


@classified(MEDIUM)
def flipper_storage_write(path: str, content: str) -> str:
    """
    Write text content to a file on Flipper storage. Creates or overwrites the file.

    path:    full storage path, e.g. /ext/subghz/my_signal.sub
    content: text content to write
    """
    check_path_blocked(path)
    return _t().write_file(path, content.encode())


@classified(MEDIUM)
def flipper_storage_delete(path: str) -> str:
    """Delete a file from Flipper storage."""
    check_path_blocked(path)
    return _t().cmd(f"storage remove {path}")


@classified(MEDIUM)
def flipper_app_launch(app_name: str) -> str:
    """
    Launch an installed app on the Flipper by name.

    app_name: e.g. 'NFC', 'Sub-GHz', 'Infrared', 'iButton', 'Bad USB'
    """
    return _t().cmd(f"loader open {app_name!r}")


# ---------------------------------------------------------------------------
# High risk — RF/execution, require explicit user confirmation
# ---------------------------------------------------------------------------

@classified(HIGH, confirm_prompt="This will transmit an infrared signal. Confirm?")
def flipper_ir_send(protocol: str, address: int, command: int) -> str:
    """
    Transmit an infrared signal.

    protocol: IR protocol name, e.g. NEC, Samsung32, RC6
    address:  device address (decimal integer)
    command:  IR command code (decimal integer)
    """
    return _t().cmd(f"ir tx {protocol} {address} {command}", timeout=10.0)


@classified(HIGH, confirm_prompt="This will transmit a SubGHz RF signal. Confirm?")
def flipper_subghz_tx(file_path: str) -> str:
    """
    Transmit a SubGHz signal from a .sub file already on the Flipper SD card.

    file_path: full path to .sub file, e.g. /ext/subghz/my_signal.sub
    """
    return _t().cmd(f"subghz tx {file_path}", timeout=20.0)


@classified(HIGH, confirm_prompt="This will scan for SubGHz signals. Confirm?")
def flipper_subghz_rx(frequency_hz: int = 433920000, timeout_sec: int = 10) -> str:
    """
    Listen for SubGHz signals on a given frequency for a set duration.

    frequency_hz: frequency in Hz, default 433920000 (433.92 MHz)
    timeout_sec:  how long to listen, default 10 seconds
    """
    return _t().cmd(f"subghz rx {frequency_hz}", timeout=timeout_sec + 5.0)


@classified(HIGH, confirm_prompt="This will scan for NFC/RFID cards. Confirm?")
def flipper_nfc_read() -> str:
    """Scan for and read an NFC or RFID card placed on the Flipper."""
    return _t().cmd("nfc detect", timeout=15.0)


@classified(HIGH, confirm_prompt="This will emulate an NFC card. Confirm?")
def flipper_nfc_emulate(file_path: str) -> str:
    """
    Emulate an NFC card from a saved .nfc file on the Flipper SD card.

    file_path: full path, e.g. /ext/nfc/my_card.nfc
    """
    return _t().cmd(f"nfc emulate {file_path}", timeout=20.0)


@classified(HIGH, confirm_prompt="This will execute a BadUSB payload on a connected computer. Confirm?")
def flipper_badusb_run(file_path: str) -> str:
    """
    Execute a BadUSB (Ducky Script) payload from Flipper storage.

    file_path: full path to .txt script, e.g. /ext/badusb/payload.txt
    """
    return _t().cmd(f"badusb run {file_path}", timeout=60.0)


# ---------------------------------------------------------------------------
# Tool registry — hermes-agent discovers tools from this list
# ---------------------------------------------------------------------------

TOOLS = [
    flipper_ping,
    flipper_device_info,
    flipper_power_info,
    flipper_storage_list,
    flipper_storage_read,
    flipper_storage_stat,
    flipper_storage_mkdir,
    flipper_storage_write,
    flipper_storage_delete,
    flipper_app_launch,
    flipper_ir_send,
    flipper_subghz_tx,
    flipper_subghz_rx,
    flipper_nfc_read,
    flipper_nfc_emulate,
    flipper_badusb_run,
]
