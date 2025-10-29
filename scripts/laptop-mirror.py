#!/usr/bin/env python3
"""
QEMU Laptop Device Host Mirror

Mirrors host laptop hardware state (battery, AC adapter, lid button) to QEMU
guest devices via QMP commands. Designed for desktop virtualization scenarios
where the guest should reflect the host's laptop hardware state.

This is a reference implementation demonstrating how to integrate host hardware
state with QEMU's laptop ACPI devices using QMP.

Copyright (c) 2024 Leonid Bloch <lb.workbox@gmail.com>
SPDX-License-Identifier: GPL-2.0-or-later
"""

import argparse
import json
import socket
import sys
import time
import signal
from pathlib import Path
from typing import Optional, Dict, Any


class QMPClient:
    """Simple QMP client for sending commands to QEMU."""

    def __init__(self, address: str):
        """Initialize QMP client with socket address (host:port or unix path)."""
        self.address = address
        self.sock: Optional[socket.socket] = None
        self._buffer = b''

    def connect(self) -> None:
        """Connect to QMP socket and perform handshake."""
        if ':' in self.address:
            # TCP socket (host:port)
            host, port = self.address.rsplit(':', 1)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((host, int(port)))
        else:
            # Unix socket
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.address)

        # Read greeting
        greeting = self._recv_message()
        if 'QMP' not in greeting:
            raise RuntimeError(f"Invalid QMP greeting: {greeting}")

        # Negotiate capabilities
        self.execute('qmp_capabilities')

    def _recv_message(self) -> Dict[str, Any]:
        """Receive one JSON message from QMP socket."""
        while b'\n' not in self._buffer:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("QMP connection closed")
            self._buffer += data

        line, self._buffer = self._buffer.split(b'\n', 1)
        return json.loads(line.decode('utf-8'))

    def execute(self, command: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a QMP command and return the response."""
        cmd = {'execute': command}
        if arguments:
            cmd['arguments'] = arguments

        message = json.dumps(cmd).encode('utf-8') + b'\n'
        self.sock.send(message)

        response = self._recv_message()
        if 'error' in response:
            raise RuntimeError(f"QMP error: {response['error']}")

        return response.get('return', {})

    def close(self) -> None:
        """Close the QMP connection."""
        if self.sock:
            self.sock.close()
            self.sock = None


class BatteryMonitor:
    """Monitor host battery state from sysfs."""

    def __init__(self, sysfs_path: str = '/sys/class/power_supply'):
        self.sysfs_path = Path(sysfs_path)
        self.battery_path: Optional[Path] = None
        self._find_battery()

    def _find_battery(self) -> None:
        """Find the first battery device in sysfs."""
        if not self.sysfs_path.exists():
            return

        for device in self.sysfs_path.iterdir():
            type_file = device / 'type'
            if type_file.exists():
                device_type = type_file.read_text().strip()
                if device_type == 'Battery':
                    self.battery_path = device
                    return

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Read current battery state from sysfs."""
        if not self.battery_path:
            return None

        try:
            # Read battery status
            status = (self.battery_path / 'status').read_text().strip()
            
            # Try to read capacity percentage directly
            capacity_file = self.battery_path / 'capacity'
            if capacity_file.exists():
                capacity = int(capacity_file.read_text().strip())
            else:
                # Calculate from energy values
                energy_now = int((self.battery_path / 'energy_now').read_text().strip())
                energy_full = int((self.battery_path / 'energy_full').read_text().strip())
                capacity = int((energy_now / energy_full) * 100) if energy_full > 0 else 0

            # Try to read current power rate
            rate = 0
            for rate_file in ['power_now', 'current_now']:
                rate_path = self.battery_path / rate_file
                if rate_path.exists():
                    rate = int(rate_path.read_text().strip())
                    break

            # Convert to QEMU battery state format
            state = {
                'present': True,
                'charging': status in ('Charging', 'Full'),
                'discharging': status in ('Discharging', 'Not charging'),
                'charge-percent': max(0, min(100, capacity)),
                'rate': rate // 1000,  # Convert µW to mW
            }

            return state

        except (FileNotFoundError, ValueError, ZeroDivisionError):
            return None


class ACAdapterMonitor:
    """Monitor host AC adapter state from sysfs."""

    def __init__(self, sysfs_path: str = '/sys/class/power_supply'):
        self.sysfs_path = Path(sysfs_path)
        self.adapter_path: Optional[Path] = None
        self._find_adapter()

    def _find_adapter(self) -> None:
        """Find the first AC adapter device in sysfs."""
        if not self.sysfs_path.exists():
            return

        for device in self.sysfs_path.iterdir():
            type_file = device / 'type'
            if type_file.exists():
                device_type = type_file.read_text().strip()
                if device_type == 'Mains':
                    self.adapter_path = device
                    return

    def get_state(self) -> Optional[bool]:
        """Read current AC adapter state from sysfs. Returns True if connected."""
        if not self.adapter_path:
            return None

        try:
            online_file = self.adapter_path / 'online'
            if online_file.exists():
                return int(online_file.read_text().strip()) == 1
        except (FileNotFoundError, ValueError):
            pass

        return None


class LidButtonMonitor:
    """Monitor host lid button state from procfs."""

    def __init__(self, procfs_path: str = '/proc/acpi/button'):
        self.procfs_path = Path(procfs_path)
        self.lid_state_path: Optional[Path] = None
        self._find_lid()

    def _find_lid(self) -> None:
        """Find the lid state file in procfs."""
        lid_dir = self.procfs_path / 'lid'
        if not lid_dir.exists():
            return

        # Find first lid subdirectory with a state file
        for subdir in lid_dir.iterdir():
            if subdir.is_dir():
                state_file = subdir / 'state'
                if state_file.exists():
                    self.lid_state_path = state_file
                    return

    def get_state(self) -> Optional[bool]:
        """Read current lid state from procfs. Returns True if open."""
        if not self.lid_state_path:
            return None

        try:
            state_line = self.lid_state_path.read_text().strip()
            # Format: "state:      open" or "state:      closed"
            return 'open' in state_line.lower()
        except FileNotFoundError:
            return None


class LaptopMirror:
    """Main application to mirror laptop state to QEMU."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.qmp = QMPClient(args.qmp)
        self.running = False

        # Initialize monitors based on enabled devices
        self.battery = BatteryMonitor() if args.battery else None
        self.ac_adapter = ACAdapterMonitor() if args.ac_adapter else None
        self.lid = LidButtonMonitor() if args.lid else None

        # Track previous state to detect changes
        self.prev_battery_state: Optional[Dict[str, Any]] = None
        self.prev_ac_state: Optional[bool] = None
        self.prev_lid_state: Optional[bool] = None

    def _setup_signals(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            print("\nShutting down...", file=sys.stderr)
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _update_battery(self) -> None:
        """Update guest battery state if changed."""
        if not self.battery:
            return

        state = self.battery.get_state()
        if state and state != self.prev_battery_state:
            try:
                self.qmp.execute('battery-set-state', {'state': state})
                if self.args.verbose:
                    print(f"Battery: {state['charge-percent']}% "
                          f"({'charging' if state['charging'] else 'discharging'})")
                self.prev_battery_state = state
            except RuntimeError as e:
                if self.args.verbose:
                    print(f"Failed to update battery: {e}", file=sys.stderr)

    def _update_ac_adapter(self) -> None:
        """Update guest AC adapter state if changed."""
        if not self.ac_adapter:
            return

        connected = self.ac_adapter.get_state()
        if connected is not None and connected != self.prev_ac_state:
            try:
                self.qmp.execute('ac-adapter-set-state', {'connected': connected})
                if self.args.verbose:
                    print(f"AC Adapter: {'connected' if connected else 'disconnected'}")
                self.prev_ac_state = connected
            except RuntimeError as e:
                if self.args.verbose:
                    print(f"Failed to update AC adapter: {e}", file=sys.stderr)

    def _update_lid(self) -> None:
        """Update guest lid button state if changed."""
        if not self.lid:
            return

        lid_open = self.lid.get_state()
        if lid_open is not None and lid_open != self.prev_lid_state:
            try:
                self.qmp.execute('lid-button-set-state', {'open': lid_open})
                if self.args.verbose:
                    print(f"Lid: {'open' if lid_open else 'closed'}")
                self.prev_lid_state = lid_open
            except RuntimeError as e:
                if self.args.verbose:
                    print(f"Failed to update lid: {e}", file=sys.stderr)

    def run(self) -> int:
        """Main loop to monitor and update device states."""
        try:
            # Connect to QMP
            if self.args.verbose:
                print(f"Connecting to QMP at {self.args.qmp}...")
            self.qmp.connect()
            if self.args.verbose:
                print("Connected to QMP")

            # Check if any devices are available
            devices_available = False
            if self.battery and self.battery.battery_path:
                devices_available = True
                if self.args.verbose:
                    print(f"Monitoring battery: {self.battery.battery_path}")
            if self.ac_adapter and self.ac_adapter.adapter_path:
                devices_available = True
                if self.args.verbose:
                    print(f"Monitoring AC adapter: {self.ac_adapter.adapter_path}")
            if self.lid and self.lid.lid_state_path:
                devices_available = True
                if self.args.verbose:
                    print(f"Monitoring lid: {self.lid.lid_state_path}")

            if not devices_available:
                print("Warning: No host laptop devices found to monitor", file=sys.stderr)

            # Setup signal handlers
            self._setup_signals()

            # Main monitoring loop
            self.running = True
            if self.args.verbose:
                print(f"Monitoring every {self.args.interval} seconds (Ctrl-C to stop)...")

            while self.running:
                self._update_battery()
                self._update_ac_adapter()
                self._update_lid()
                time.sleep(self.args.interval)

            return 0

        except ConnectionError as e:
            print(f"Failed to connect to QMP: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        finally:
            self.qmp.close()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Mirror host laptop hardware state to QEMU guest via QMP',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Mirror all devices to QEMU on TCP socket
  %(prog)s --qmp localhost:4444

  # Mirror only battery and AC adapter, using Unix socket
  %(prog)s --qmp /tmp/qemu-qmp.sock --no-lid

  # Mirror with custom update interval
  %(prog)s --qmp localhost:4444 --interval 5

  # Run with verbose output
  %(prog)s --qmp localhost:4444 --verbose
        """
    )

    parser.add_argument(
        '--qmp',
        required=True,
        metavar='ADDRESS',
        help='QMP socket address (host:port or unix socket path)'
    )

    parser.add_argument(
        '--interval',
        type=float,
        default=2.0,
        metavar='SECONDS',
        help='polling interval in seconds (default: 2.0)'
    )

    parser.add_argument(
        '--battery',
        action='store_true',
        default=True,
        help='monitor battery device (default: enabled)'
    )

    parser.add_argument(
        '--no-battery',
        dest='battery',
        action='store_false',
        help='disable battery monitoring'
    )

    parser.add_argument(
        '--ac-adapter',
        action='store_true',
        default=True,
        help='monitor AC adapter device (default: enabled)'
    )

    parser.add_argument(
        '--no-ac-adapter',
        dest='ac_adapter',
        action='store_false',
        help='disable AC adapter monitoring'
    )

    parser.add_argument(
        '--lid',
        action='store_true',
        default=True,
        help='monitor lid button device (default: enabled)'
    )

    parser.add_argument(
        '--no-lid',
        dest='lid',
        action='store_false',
        help='disable lid button monitoring'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='verbose output'
    )

    args = parser.parse_args()

    # Check if at least one device is enabled
    if not any([args.battery, args.ac_adapter, args.lid]):
        parser.error('At least one device must be enabled')

    mirror = LaptopMirror(args)
    return mirror.run()


if __name__ == '__main__':
    sys.exit(main())

