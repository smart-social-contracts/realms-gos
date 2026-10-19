#!/usr/bin/env bash
# Base image provisioning: runs once, as root, from cloud-init on a fresh
# Ubuntu 24.04 cloud image. Result: GNOME (minimal) with autologin, Firefox,
# Node.js 22, pipx and Playwright's Chromium. Only what a developer needs before
# installing the Realms CLI; the CLI itself is installed later by onboard.sh.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
DEV_USER=dev
HYPERVISOR="$(cat /etc/realms-devenv/hypervisor 2>/dev/null || echo qemu)"
PLAYWRIGHT_VERSION="$(cat /etc/realms-devenv/playwright-version)"

# Background apt jobs would race the installs below and later runs.
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
apt-get update

# What the Realms CLI needs from the OS: Python (pipx) and git.
apt-get install -y --no-install-recommends git curl ca-certificates pipx python3-venv python3-pip unzip

# GNOME, without recommends (keeps it lightweight).
apt-get install -y --no-install-recommends \
  ubuntu-desktop-minimal gdm3 gnome-shell gnome-session gnome-terminal nautilus \
  xserver-xorg xwayland fonts-dejavu-core fonts-noto-color-emoji
apt-get purge -y gnome-initial-setup || true
# update-notifier can't be purged (ubuntu-desktop-minimal depends on it), so silence it instead.
sed -i 's/^Prompt=.*/Prompt=never/' /etc/update-manager/release-upgrades
echo 'Hidden=true' >> /etc/xdg/autostart/update-notifier.desktop
case "$HYPERVISOR" in
  virtualbox) apt-get install -y --no-install-recommends virtualbox-guest-utils virtualbox-guest-x11 || true ;;
  qemu) apt-get install -y --no-install-recommends qemu-guest-agent spice-vdagent || true ;;
esac

# Firefox as a .deb (the Ubuntu package is a snap shim; snapd is heavy in a VM).
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc
echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
  > /etc/apt/sources.list.d/mozilla.list
cat > /etc/apt/preferences.d/mozilla <<'EOF'
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
EOF

# Node.js 22 (Ubuntu's nodejs 18 is too old for the extension toolchain).
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y --no-install-recommends nodejs firefox
# No first-run / terms-of-use dialogs over the realm page.
install -d /etc/firefox/policies
cat > /etc/firefox/policies/policies.json <<'EOF'
{
  "policies": {
    "DisableTelemetry": true,
    "DisableFirefoxStudies": true,
    "DontCheckDefaultBrowser": true,
    "OverrideFirstRunPage": "",
    "OverridePostUpdatePage": "",
    "SkipTermsOfUse": true,
    "UserMessaging": {"SkipOnboarding": true, "ExtensionRecommendations": false,
                      "FeatureRecommendations": false, "MoreFromMozilla": false, "Locked": true}
  }
}
EOF

# Log straight into GNOME (Wayland) as the developer. On VirtualBox's VMSVGA
# without 3D, a GNOME Xorg session only ever shows its first frame on the
# virtual screen; Wayland (KMS page flips) updates it properly.
cat > /etc/gdm3/custom.conf <<EOF
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=${DEV_USER}
EOF
# A headless VM gets no resize hints and KMS offers 4096x2160 first.
install -d -o "$DEV_USER" -g "$DEV_USER" "/home/$DEV_USER/.config"
cat > "/home/$DEV_USER/.config/monitors.xml" <<'EOF'
<monitors version="2">
  <configuration>
    <logicalmonitor>
      <x>0</x>
      <y>0</y>
      <scale>1</scale>
      <primary>yes</primary>
      <monitor>
        <monitorspec>
          <connector>Virtual-1</connector>
          <vendor>unknown</vendor>
          <product>unknown</product>
          <serial>unknown</serial>
        </monitorspec>
        <mode>
          <width>1280</width>
          <height>800</height>
          <rate>59.810</rate>
        </mode>
      </monitor>
    </logicalmonitor>
  </configuration>
</monitors>
EOF
chown "$DEV_USER:$DEV_USER" "/home/$DEV_USER/.config/monitors.xml"
systemctl set-default graphical.target
# No screen lock / blanking in a throwaway dev VM.
install -d /etc/dconf/profile /etc/dconf/db/local.d
printf 'user-db:user\nsystem-db:local\n' > /etc/dconf/profile/user
cat > /etc/dconf/db/local.d/00-realms-devenv <<'EOF'
[org/gnome/desktop/session]
idle-delay=uint32 0
[org/gnome/desktop/screensaver]
lock-enabled=false
[org/gnome/shell]
favorite-apps=['firefox.desktop', 'org.gnome.Terminal.desktop', 'org.gnome.Nautilus.desktop']
EOF
dconf update

# Playwright's Chromium and its system libraries (browser check in onboard.sh).
npx -y "playwright@${PLAYWRIGHT_VERSION}" install-deps chromium
sudo -u "$DEV_USER" -H npx -y "playwright@${PLAYWRIGHT_VERSION}" install chromium

# Networking that survives without cloud-init: DHCP on any wired NIC, by
# systemd-networkd (the desktop packages default netplan to NetworkManager,
# which this minimal install does not run).
cat > /etc/netplan/01-realms-devenv.yaml <<'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    wired:
      renderer: networkd
      match:
        name: "en*"
      dhcp4: true
EOF
chmod 600 /etc/netplan/01-realms-devenv.yaml
rm -f /etc/netplan/50-cloud-init.yaml
# Later boots (the per-run VMs) skip cloud-init entirely.
touch /etc/cloud/cloud-init.disabled

apt-get autoremove -y
apt-get clean
rm -rf /var/lib/apt/lists/* /root/.npm /home/${DEV_USER}/.npm
fstrim -av || true
echo "realms-devenv base provisioning complete"
