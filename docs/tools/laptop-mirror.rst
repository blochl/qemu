========================
QEMU Laptop Mirror Tool
========================

Synopsis
--------

**laptop-mirror.py** --qmp *ADDRESS* [*OPTIONS*]

Description
-----------

The ``laptop-mirror.py`` program mirrors the state of host laptop hardware
(battery, AC adapter, and lid button) to QEMU guest devices via QMP commands.
This is useful for desktop virtualization scenarios where the guest operating
system should reflect the host's laptop hardware state.

The tool continuously monitors host hardware state through sysfs and procfs,
and updates the corresponding QEMU ACPI devices when changes are detected.
Only state changes trigger QMP commands, minimizing overhead.

This is a reference implementation demonstrating how to integrate host laptop
hardware with QEMU's ACPI devices. Users may adapt this script or implement
similar functionality in their virtualization management software.

Requirements
------------

The script requires:

* Python 3.9 or later
* Access to host sysfs (``/sys/class/power_supply/``) for battery and AC adapter
* Access to host procfs (``/proc/acpi/button/``) for lid button
* QMP socket connection to running QEMU instance
* QEMU guest with battery, AC adapter, and/or lid button devices enabled

Options
-------

.. program:: laptop-mirror.py

.. option:: --qmp ADDRESS

  **Required.** QMP socket address to connect to. This can be either:

  * TCP socket in ``host:port`` format (e.g., ``localhost:4444``)
  * Unix domain socket path (e.g., ``/tmp/qemu-qmp.sock``)

  The QEMU instance must be started with a QMP server socket. For example::

    -qmp tcp:localhost:4444,server,wait=off

  or::

    -qmp unix:/tmp/qemu-qmp.sock,server,wait=off

.. option:: --interval SECONDS

  Polling interval in seconds for checking host hardware state.
  Default: ``2.0`` seconds.

  Lower values provide faster state updates but increase CPU usage.
  Higher values reduce overhead but may miss brief state changes.

.. option:: --battery, --no-battery

  Enable or disable battery state monitoring.
  Default: **enabled**.

  When enabled, monitors ``/sys/class/power_supply/`` for battery devices
  and updates the guest battery device via ``battery-set-state`` QMP command.

.. option:: --ac-adapter, --no-ac-adapter

  Enable or disable AC adapter state monitoring.
  Default: **enabled**.

  When enabled, monitors ``/sys/class/power_supply/`` for AC adapter (Mains)
  devices and updates the guest AC adapter via ``ac-adapter-set-state`` QMP
  command.

.. option:: --lid, --no-lid

  Enable or disable lid button state monitoring.
  Default: **enabled**.

  When enabled, monitors ``/proc/acpi/button/lid/*/state`` for lid button
  state and updates the guest lid device via ``lid-button-set-state`` QMP
  command.

.. option:: -v, --verbose

  Enable verbose output. Prints connection status, detected devices, and
  state changes as they occur.

Examples
--------

Basic usage
~~~~~~~~~~~

Mirror all laptop devices to QEMU running with QMP on TCP port 4444::

  laptop-mirror.py --qmp localhost:4444

The script will run continuously, monitoring and updating all enabled devices.
Press Ctrl-C to stop.

Unix socket connection
~~~~~~~~~~~~~~~~~~~~~~

Connect to QEMU via Unix domain socket::

  laptop-mirror.py --qmp /tmp/qemu-qmp.sock --verbose

Selective device monitoring
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Monitor only battery and AC adapter, disable lid button::

  laptop-mirror.py --qmp localhost:4444 --no-lid

Monitor only battery state::

  laptop-mirror.py --qmp localhost:4444 --no-ac-adapter --no-lid

Custom polling interval
~~~~~~~~~~~~~~~~~~~~~~~

Check for state changes every 5 seconds instead of default 2 seconds::

  laptop-mirror.py --qmp localhost:4444 --interval 5

This reduces CPU overhead at the cost of slower state update propagation.

Integration with QEMU
~~~~~~~~~~~~~~~~~~~~~

Complete example starting QEMU with laptop devices and mirroring host state::

  # Start QEMU with laptop devices
  qemu-system-x86_64 \
    -device battery \
    -device acad \
    -device button \
    -qmp tcp:localhost:4444,server,wait=off \
    [other options...]

  # In another terminal, start mirroring
  laptop-mirror.py --qmp localhost:4444 --verbose

Background execution
~~~~~~~~~~~~~~~~~~~~

Run the mirror tool as a background daemon::

  laptop-mirror.py --qmp localhost:4444 > /var/log/laptop-mirror.log 2>&1 &

The script handles SIGINT and SIGTERM for graceful shutdown.

Device State Mapping
---------------------

Battery
~~~~~~~

The script maps host battery sysfs attributes to QEMU battery state:

+-------------------------+----------------------------------+
| Host sysfs attribute    | QEMU battery state field         |
+=========================+==================================+
| ``status``              | ``charging``/``discharging``     |
+-------------------------+----------------------------------+
| ``capacity``            | ``charge-percent`` (0-100)       |
+-------------------------+----------------------------------+
| ``power_now``           | ``rate`` (mW)                    |
+-------------------------+----------------------------------+
| Presence                | ``present`` (boolean)            |
+-------------------------+----------------------------------+

AC Adapter
~~~~~~~~~~

The script maps host AC adapter sysfs attributes:

+-------------------------+----------------------------------+
| Host sysfs attribute    | QEMU AC adapter state            |
+=========================+==================================+
| ``online``              | ``connected`` (boolean)          |
+-------------------------+----------------------------------+

Lid Button
~~~~~~~~~~

The script maps host lid button procfs state:

+-------------------------+----------------------------------+
| Host procfs state       | QEMU lid button state            |
+=========================+==================================+
| "state:      open"      | ``open`` = true                  |
+-------------------------+----------------------------------+
| "state:      closed"    | ``open`` = false                 |
+-------------------------+----------------------------------+

Limitations
-----------

* The script requires appropriate permissions to read sysfs and procfs files.
  Typically works for regular users, but some systems may require additional
  permissions.

* Only the first detected battery and AC adapter are monitored. Systems with
  multiple batteries will only mirror the first one found.

* State changes are detected via polling, not event-driven. Brief state changes
  between polling intervals may be missed.

* The script does not handle QEMU process restarts. If QEMU exits, the script
  must be restarted.

* Host hardware state is not preserved across guest migrations. After migration,
  the script should be reconfigured to connect to the new QEMU instance.

See Also
--------

:doc:`/specs/battery`, :doc:`/specs/acad`, :doc:`/specs/button`,
:ref:`QMP Reference Manual <qemu_qmp_ref>`

