#!/usr/bin/env bash
# Temporarily test AIC8800 compatibility parameters; no driver files are replaced.
set -euo pipefail

mode="${1:---compat}"
profile="${2:-406_wifi 1}"
wifi_if="${3:-wlx6c1ff7e54a58}"
driver="aic8800_fdrv"

if [[ "$mode" != "--compat" && "$mode" != "--restore" ]]; then
    echo "Usage: sudo bash $0 [--compat|--restore] [connection-name] [wifi-interface]" >&2
    exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
    echo "Driver reload requires administrator privileges. Run this script with sudo in your local terminal." >&2
    exit 2
fi

modinfo "$driver" >/dev/null
nmcli connection show "$profile" >/dev/null
if [[ ! -e "/sys/class/net/$wifi_if" ]]; then
    echo "Wi-Fi interface is absent: $wifi_if. Reinsert the adapter before running this test." >&2
    exit 2
fi

# Validate all requested parameter names before disconnecting the interface.
for param in ps_on dpsm uapsd_queues uapsd_timeout he_on; do
    if ! modinfo -p "$driver" | cut -d: -f1 | grep -Fxq "$param"; then
        echo "Installed driver does not expose the required parameter: $param" >&2
        exit 2
    fi
done

echo "Mode: $mode; connection: $profile; interface: $wifi_if"
echo "Before reload:"
for param in ps_on dpsm uapsd_queues uapsd_timeout he_on; do
    printf '%s=' "$param"
    cat "/sys/module/$driver/parameters/$param"
done

recover_default() {
    local code="$?"
    trap - ERR
    echo "Compatibility test failed; attempting to reload the default driver and reconnect." >&2
    nmcli device disconnect "$wifi_if" || true
    modprobe -r "$driver" || true
    modprobe "$driver" || true
    udevadm settle --timeout=10 || true
    nmcli --wait 20 connection up id "$profile" ifname "$wifi_if" || true
    exit "$code"
}
trap recover_default ERR

nmcli device disconnect "$wifi_if"
modprobe -r "$driver"
if [[ "$mode" == "--compat" ]]; then
    modprobe "$driver" ps_on=0 dpsm=0 uapsd_queues=0 uapsd_timeout=0 he_on=0
else
    # Uses existing system defaults, without creating modprobe configuration files.
    modprobe "$driver"
fi
udevadm settle --timeout=10
nmcli --wait 25 connection up id "$profile" ifname "$wifi_if"
trap - ERR

echo "After reload:"
for param in ps_on dpsm uapsd_queues uapsd_timeout he_on; do
    printf '%s=' "$param"
    cat "/sys/module/$driver/parameters/$param"
done
iw dev "$wifi_if" get power_save
ip -4 -brief address show "$wifi_if"
echo "This is a temporary test. No files under /etc/modprobe.d were changed."
echo "Restore with: sudo bash $0 --restore '$profile' '$wifi_if'"
