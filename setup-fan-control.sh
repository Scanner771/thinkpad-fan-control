#!/bin/bash
# One-shot installer for fan-control.py privilege path. Run once with sudo:
#   sudo bash ~/setup-fan-control.sh
set -euo pipefail

# Resolve the invoking user under either sudo or pkexec (pkexec sets PKEXEC_UID).
if [ -n "${SUDO_USER:-}" ]; then
    USER_NAME="$SUDO_USER"
elif [ -n "${PKEXEC_UID:-}" ]; then
    USER_NAME="$(id -nu "$PKEXEC_UID")"
else
    USER_NAME="$(logname 2>/dev/null || echo "${USER:-root}")"
fi
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing fanctl helper to /usr/local/bin/fanctl"
install -m 0755 -o root -g root "$SRC_DIR/fanctl" /usr/local/bin/fanctl

echo "==> Granting passwordless sudo for fanctl to $USER_NAME"
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/fanctl\n' "$USER_NAME" > /etc/sudoers.d/fan-control
chmod 0440 /etc/sudoers.d/fan-control
visudo -cf /etc/sudoers.d/fan-control

echo "==> Persisting fan_control=1 across reboots"
echo 'options thinkpad_acpi fan_control=1' > /etc/modprobe.d/thinkpad_acpi.conf

echo "==> Enabling fan_control now (reloading thinkpad_acpi)"
if [ "$(cat /sys/module/thinkpad_acpi/parameters/fan_control 2>/dev/null)" != "Y" ]; then
    if modprobe -r thinkpad_acpi 2>/dev/null && modprobe thinkpad_acpi; then
        echo "    reloaded; fan_control=$(cat /sys/module/thinkpad_acpi/parameters/fan_control)"
    else
        echo "    !! could not reload module (in use) — reboot to activate fan_control=1"
    fi
else
    echo "    already enabled"
fi

echo "==> Done. Launch the app and try a fan level."
