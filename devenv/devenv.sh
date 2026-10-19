#!/usr/bin/env bash
# Realms dev environment in a VM: Ubuntu 24.04 + GNOME (minimal), driven from
# the command line so the same steps run on a laptop and in GitHub Actions.
#
#   devenv/devenv.sh run        # base image (cached) → fresh VM → onboard.sh → artifacts → VM off
#   devenv/devenv.sh base       # build the base image only (--force to rebuild)
#   devenv/devenv.sh up [--gui] # boot a fresh VM from the base image
#   devenv/devenv.sh ssh [cmd]  # shell (or command) in the running VM
#   devenv/devenv.sh gui        # show the desktop of the running VM
#   devenv/devenv.sh screenshot [file.png]
#   devenv/devenv.sh down       # power off and discard the VM (base image kept)
#
# Hypervisor: QEMU/KVM when /dev/kvm is usable (GitHub runners), else
# VirtualBox, else QEMU without KVM (slow). Override with --hypervisor.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

DEVENV_HOME="${DEVENV_HOME:-$HOME/.cache/realms-devenv}"
HYPERVISOR="${DEVENV_HYPERVISOR:-auto}"
CPUS="${DEVENV_CPUS:-4}"
MEMORY_MB="${DEVENV_MEMORY_MB:-6144}"
DISK_GB="${DEVENV_DISK_GB:-32}"
SSH_PORT="${DEVENV_SSH_PORT:-2222}"
VNC_DISPLAY="${DEVENV_VNC_DISPLAY:-1}"
VM_NAME="${DEVENV_VM_NAME:-realms-devenv}"
BASE_TIMEOUT_S="${DEVENV_BASE_TIMEOUT_S:-5400}"
OUT="${DEVENV_OUT:-$HERE/artifacts}"
CLI_SOURCE="${CLI_SOURCE:-checkout}"
CLI_VERSION="${CLI_VERSION:-}"
UBUNTU_RELEASE=noble
IMAGE_NAME="${UBUNTU_RELEASE}-server-cloudimg-amd64.img"
IMAGE_BASE_URL="https://cloud-images.ubuntu.com/${UBUNTU_RELEASE}/current"
VBOX_MARKER="realms-devenv (managed by devenv/devenv.sh)"

KEEP=0
GUI=0
FORCE=0

log() { printf '\033[1m[devenv %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { printf '\033[31m[devenv] error:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required${2:+ ($2)}"; }

# fd 9 holds the run lock. VBoxManage may spawn the long-lived VBoxSVC and QEMU
# outlives `up`; neither may inherit fd 9, or the lock outlives this script.
VBoxManage() { command VBoxManage "$@" 9>&-; }

usage() { sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# ── setup ────────────────────────────────────────────────────────────────

detect_hypervisor() {
  if [ "$HYPERVISOR" != auto ]; then return; fi
  if [ -w /dev/kvm ] && command -v qemu-system-x86_64 >/dev/null; then
    HYPERVISOR=qemu
  elif command -v VBoxManage >/dev/null; then
    HYPERVISOR=virtualbox
  elif command -v qemu-system-x86_64 >/dev/null; then
    HYPERVISOR=qemu
    log "warning: no /dev/kvm access and no VirtualBox; QEMU without KVM is very slow"
  else
    die "install QEMU (qemu-system-x86 qemu-utils) or VirtualBox"
  fi
}

playwright_version() {
  python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dependencies']['playwright'])" \
    "$HERE/browser-check/package.json"
}

base_stamp() {
  {
    cat "$HERE/cloud-init/user-data.yaml" "$HERE/guest/provision.sh"
    echo "$HYPERVISOR $DISK_GB $(playwright_version) $IMAGE_NAME"
  } | sha256sum | cut -c1-16
}

ensure_key() {
  mkdir -p "$DEVENV_HOME"
  [ -f "$DEVENV_HOME/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C realms-devenv -f "$DEVENV_HOME/id_ed25519"
}

download_image() {
  local img="$DEVENV_HOME/$IMAGE_NAME"
  if [ ! -f "$img" ]; then
    log "download $IMAGE_BASE_URL/$IMAGE_NAME"
    curl -fsSL --retry 3 -o "$img.part" "$IMAGE_BASE_URL/$IMAGE_NAME"
    curl -fsSL --retry 3 -o "$DEVENV_HOME/SHA256SUMS" "$IMAGE_BASE_URL/SHA256SUMS"
    local want
    want=$(awk -v n="*$IMAGE_NAME" '$2 == n {print $1}' "$DEVENV_HOME/SHA256SUMS")
    [ -n "$want" ] || die "no checksum for $IMAGE_NAME"
    [ "$(sha256sum "$img.part" | cut -d' ' -f1)" = "$want" ] || die "checksum mismatch for $IMAGE_NAME"
    mv "$img.part" "$img"
  fi
  echo "$img"
}

make_seed() {
  local seed_dir="$DEVENV_HOME/seed" iso="$DEVENV_HOME/seed.iso"
  rm -rf "$seed_dir" && mkdir -p "$seed_dir"
  python3 - "$HERE/cloud-init/user-data.yaml" "$seed_dir/user-data" \
    "$(cat "$DEVENV_HOME/id_ed25519.pub")" "$HYPERVISOR" "$(playwright_version)" \
    "$HERE/guest/provision.sh" <<'EOF'
import base64, sys
src, dest, pubkey, hypervisor, pw, provision = sys.argv[1:]
text = open(src).read()
text = (text.replace("__SSH_PUBKEY__", pubkey)
            .replace("__HYPERVISOR__", hypervisor)
            .replace("__PLAYWRIGHT_VERSION__", pw)
            .replace("__PROVISION_B64__", base64.b64encode(open(provision, "rb").read()).decode()))
open(dest, "w").write(text)
EOF
  printf 'instance-id: realms-devenv-base\nlocal-hostname: realms-devenv\n' > "$seed_dir/meta-data"
  if command -v cloud-localds >/dev/null; then
    cloud-localds "$iso" "$seed_dir/user-data" "$seed_dir/meta-data"
  elif command -v genisoimage >/dev/null; then
    genisoimage -quiet -output "$iso" -volid cidata -joliet -rock "$seed_dir/user-data" "$seed_dir/meta-data"
  elif command -v xorriso >/dev/null; then
    xorriso -as mkisofs -quiet -output "$iso" -volid cidata -joliet -rock "$seed_dir/user-data" "$seed_dir/meta-data"
  else
    die "need cloud-localds, genisoimage or xorriso to build the cloud-init seed"
  fi
  echo "$iso"
}

# Print the console's latest line every 30s until "$@" fails (the VM stops).
watch_console() {
  local console="$1"; shift
  local started last=""
  started=$(date +%s)
  while "$@"; do
    sleep 30
    local now line
    now=$(( $(date +%s) - started ))
    line=$(tr -cd '\11\12\15\40-\176' < "$console" 2>/dev/null | grep -v '^\s*$' | tail -1 | cut -c1-150 || true)
    if [ "$line" != "$last" ]; then log "base +${now}s: $line"; last="$line"; fi
    [ "$now" -lt "$BASE_TIMEOUT_S" ] || { log "base image build timed out"; return 1; }
  done
}

check_provisioned() {
  local console="$1"
  if grep -q REALMS_DEVENV_PROVISION_OK "$console"; then return 0; fi
  log "provisioning did not finish; console tail:"
  tr -cd '\11\12\15\40-\176' < "$console" | tail -60 >&2
  return 1
}

ssh_opts() {
  echo -i "$DEVENV_HOME/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o ServerAliveInterval=30
}

vm_ssh() {
  # shellcheck disable=SC2046
  ssh $(ssh_opts) dev@127.0.0.1 "$@"
}

wait_ssh() {
  log "waiting for SSH on 127.0.0.1:$SSH_PORT"
  local i
  for i in $(seq 1 120); do
    if vm_ssh true 2>/dev/null; then log "VM is up"; return 0; fi
    sleep 5
  done
  die "VM did not come up (no SSH after 10 min)"
}

# ── QEMU ─────────────────────────────────────────────────────────────────

qemu_accel() {
  if [ -w /dev/kvm ]; then echo "-accel kvm -cpu host"; else echo "-accel tcg -cpu max"; fi
}

qemu_common() {
  # shellcheck disable=SC2046
  echo $(qemu_accel) -machine q35 -smp "$CPUS" -m "$MEMORY_MB" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22" \
    -device virtio-vga -device qemu-xhci -device usb-tablet
}

qemu_build_base() {
  need qemu-img "qemu-utils"
  local img seed tmp="$DEVENV_HOME/base-qemu.qcow2.tmp" console="$DEVENV_HOME/console-base.log" status="$DEVENV_HOME/provision-status.log"
  img=$(download_image)
  seed=$(make_seed)
  rm -f "$tmp" "$console" "$status"
  qemu-img convert -O qcow2 "$img" "$tmp"
  qemu-img resize -q "$tmp" "${DISK_GB}G"
  log "provisioning base image with QEMU ($(qemu_accel)); this takes a while the first time"
  # shellcheck disable=SC2046
  qemu-system-x86_64 $(qemu_common) \
    -drive "file=$tmp,if=virtio,discard=unmap" -drive "file=$seed,media=cdrom,readonly=on" \
    -display none -serial "file:$console" -serial "file:$status" -pidfile "$DEVENV_HOME/qemu-base.pid" 9>&- &
  local pid=$!
  watch_console "$status" kill -0 "$pid" || { kill "$pid" 2>/dev/null; die "base build timed out"; }
  wait "$pid" || true
  check_provisioned "$status" || die "base provisioning failed"
  log "compacting base image"
  qemu-img convert -O qcow2 "$tmp" "$DEVENV_HOME/base-qemu.qcow2"
  rm -f "$tmp"
}

qemu_running() { [ -f "$DEVENV_HOME/qemu.pid" ] && kill -0 "$(cat "$DEVENV_HOME/qemu.pid")" 2>/dev/null; }

qemu_up() {
  qemu_running && die "a devenv VM is already running (devenv.sh down)"
  local overlay="$DEVENV_HOME/run-qemu.qcow2"
  rm -f "$overlay"
  qemu-img create -q -f qcow2 -b "$DEVENV_HOME/base-qemu.qcow2" -F qcow2 "$overlay"
  local display="-display none -vnc 127.0.0.1:${VNC_DISPLAY}"
  [ "$GUI" = 1 ] && display="-display gtk"
  # shellcheck disable=SC2046,SC2086
  nohup qemu-system-x86_64 $(qemu_common) -drive "file=$overlay,if=virtio,discard=unmap" \
    $display -serial "file:$DEVENV_HOME/console-run.log" \
    -monitor "unix:$DEVENV_HOME/qemu-monitor.sock,server,nowait" \
    -pidfile "$DEVENV_HOME/qemu.pid" > "$DEVENV_HOME/qemu.log" 2>&1 9>&- &
  sleep 2
  qemu_running || { cat "$DEVENV_HOME/qemu.log" >&2; die "QEMU failed to start"; }
  [ "$GUI" = 1 ] || log "desktop: VNC on 127.0.0.1:$((5900 + VNC_DISPLAY))"
}

qemu_monitor() {
  python3 - "$DEVENV_HOME/qemu-monitor.sock" "$1" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
time.sleep(0.3); s.recv(65536)
s.sendall((sys.argv[2] + "\n").encode())
time.sleep(1.5)
EOF
}

qemu_screenshot() { qemu_monitor "screendump $1 -f png"; }

qemu_down() {
  if qemu_running; then
    local pid; pid=$(cat "$DEVENV_HOME/qemu.pid")
    qemu_monitor system_powerdown 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$DEVENV_HOME/run-qemu.qcow2" "$DEVENV_HOME/qemu.pid"
}

qemu_gui() {
  qemu_running || die "no running VM (devenv.sh up --gui)"
  local addr="127.0.0.1:$((5900 + VNC_DISPLAY))"
  for viewer in gvncviewer vncviewer remmina; do
    if command -v "$viewer" >/dev/null; then
      [ "$viewer" = remmina ] && exec remmina -c "vnc://$addr"
      exec "$viewer" "$addr"
    fi
  done
  die "no VNC viewer found; connect one to $addr (or: devenv.sh down && devenv.sh up --gui)"
}

# ── VirtualBox ───────────────────────────────────────────────────────────

vbox_exists() { VBoxManage showvminfo "$VM_NAME" >/dev/null 2>&1; }
vbox_info() { VBoxManage showvminfo "$VM_NAME" --machinereadable 2>/dev/null; }
vbox_state() { vbox_info | sed -n 's/^VMState="\(.*\)"/\1/p'; }
vbox_running() { vbox_exists && [ "$(vbox_state)" = running ]; }
vbox_ours() { vbox_info | grep -qF "$VBOX_MARKER"; }

vbox_wait_off() {
  for _ in $(seq 1 60); do
    case "$(vbox_state)" in poweroff|aborted|saved) return 0 ;; esac
    sleep 2
  done
  return 1
}

vbox_build_base() {
  need qemu-img "qemu-utils, to convert the cloud image for VirtualBox"
  if vbox_exists; then
    vbox_ours || die "a VirtualBox VM named '$VM_NAME' exists and was not created by devenv.sh (set DEVENV_VM_NAME)"
    log "removing the old $VM_NAME VM"
    VBoxManage controlvm "$VM_NAME" poweroff >/dev/null 2>&1 || true
    vbox_wait_off || true
    VBoxManage unregistervm "$VM_NAME" --delete >/dev/null
  fi
  local img seed vdir="$DEVENV_HOME/vbox" console="$DEVENV_HOME/console-base.log" status="$DEVENV_HOME/provision-status.log"
  img=$(download_image)
  seed=$(make_seed)
  rm -rf "$vdir" "$console" "$status" && mkdir -p "$vdir"
  local disk="$vdir/$VM_NAME.vdi"
  qemu-img convert -O vdi "$img" "$disk"
  VBoxManage modifymedium disk "$disk" --resize $((DISK_GB * 1024)) >/dev/null
  VBoxManage createvm --name "$VM_NAME" --ostype Ubuntu_64 --basefolder "$vdir" --register >/dev/null
  VBoxManage modifyvm "$VM_NAME" --description "$VBOX_MARKER" \
    --cpus "$CPUS" --memory "$MEMORY_MB" --vram 128 --graphicscontroller vmsvga \
    --ioapic on --rtc-use-utc on --audio-enabled off --usb-xhci on --mouse usbtablet \
    --nic1 nat --natpf1 "ssh,tcp,127.0.0.1,$SSH_PORT,,22" \
    --uart1 0x3F8 4 --uart-mode1 file "$console" \
    --uart2 0x2F8 3 --uart-mode2 file "$status"
  VBoxManage storagectl "$VM_NAME" --name SATA --add sata --controller IntelAhci --portcount 2 --hostiocache on
  VBoxManage storageattach "$VM_NAME" --storagectl SATA --port 0 --device 0 --type hdd --medium "$disk" --discard on --nonrotational on
  VBoxManage storageattach "$VM_NAME" --storagectl SATA --port 1 --device 0 --type dvddrive --medium "$seed"
  log "provisioning base image with VirtualBox; this takes a while the first time"
  VBoxManage startvm "$VM_NAME" --type headless >/dev/null
  watch_console "$status" vbox_running || { VBoxManage controlvm "$VM_NAME" poweroff || true; die "base build timed out"; }
  vbox_wait_off || true
  check_provisioned "$status" || die "base provisioning failed"
  VBoxManage storageattach "$VM_NAME" --storagectl SATA --port 1 --device 0 --type dvddrive --medium emptydrive
  VBoxManage snapshot "$VM_NAME" take base --description "provisioned base image" >/dev/null
}

vbox_up() {
  vbox_running && die "a devenv VM is already running (devenv.sh down)"
  VBoxManage snapshot "$VM_NAME" restore base >/dev/null
  VBoxManage modifyvm "$VM_NAME" --uart-mode1 file "$DEVENV_HOME/console-run.log"
  if [ "$GUI" = 1 ]; then
    VBoxManage startvm "$VM_NAME" --type separate >/dev/null
  else
    VBoxManage startvm "$VM_NAME" --type headless >/dev/null
  fi
}

vbox_screenshot() { VBoxManage controlvm "$VM_NAME" screenshotpng "$1"; }

vbox_down() {
  if vbox_running; then
    VBoxManage controlvm "$VM_NAME" acpipowerbutton >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do vbox_running || break; sleep 2; done
    vbox_running && VBoxManage controlvm "$VM_NAME" poweroff >/dev/null 2>&1 || true
    vbox_wait_off || true
  fi
  if vbox_exists; then
    VBoxManage snapshot "$VM_NAME" restore base >/dev/null 2>&1 || true
  fi
}

vbox_gui() {
  vbox_running || die "no running VM (devenv.sh up --gui)"
  command -v VirtualBoxVM >/dev/null && exec VirtualBoxVM --startvm "$VM_NAME" --separate
  die "VirtualBoxVM not found; restart with: devenv.sh down && devenv.sh up --gui"
}

# ── commands ─────────────────────────────────────────────────────────────

# hv <action> [args]: run qemu_<action> or vbox_<action> for the chosen hypervisor.
hv() {
  local prefix=qemu
  [ "$HYPERVISOR" = virtualbox ] && prefix=vbox
  "${prefix}_$1" "${@:2}"
}

base_ready() {
  local stamp="$DEVENV_HOME/base-$HYPERVISOR.stamp"
  [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$(base_stamp)" ] || return 1
  case "$HYPERVISOR" in
    qemu) [ -f "$DEVENV_HOME/base-qemu.qcow2" ] ;;
    virtualbox) vbox_exists && vbox_info | grep -q 'SnapshotName="base"' ;;
  esac
}

cmd_base() {
  ensure_key
  if [ "$FORCE" = 0 ] && base_ready; then log "base image is up to date ($HYPERVISOR)"; return; fi
  local free_gb
  free_gb=$(df -Pk "$DEVENV_HOME" | awk 'NR==2 {print int($4/1048576)}')
  [ "$free_gb" -ge 15 ] || die "need ~15 GB free in $DEVENV_HOME (have ${free_gb} GB)"
  local started; started=$(date +%s)
  hv build_base
  base_stamp > "$DEVENV_HOME/base-$HYPERVISOR.stamp"
  log "base image ready in $(( ($(date +%s) - started) / 60 )) min"
}

cmd_up() {
  base_ready || die "no base image yet: devenv.sh base"
  hv up
  wait_ssh
}

cmd_down() {
  hv down
  log "VM stopped and discarded"
}

cmd_screenshot() {
  local file="${1:-$OUT/desktop.png}"
  mkdir -p "$(dirname "$file")"
  file="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"
  hv screenshot "$file" && log "screenshot: $file"
}

package_source() {
  local tarball="$DEVENV_HOME/realms-gos-src.tar.gz"
  # Tracked and new (not ignored) files of this checkout, symlinks kept.
  # Submodules (extensions/, codices/) are not needed in the VM.
  (cd "$REPO" && git ls-files -co --exclude-standard -z \
    | while IFS= read -r -d '' f; do
        if [ -L "$f" ] || [ -f "$f" ]; then printf '%s\0' "$f"; fi
      done \
    | tar --null -czf "$tarball" -T -)
  echo "$tarball"
}

cmd_run() {
  cmd_base
  if case "$HYPERVISOR" in qemu) qemu_running ;; virtualbox) vbox_running ;; esac; then
    log "stopping the previous devenv VM"
    cmd_down
  fi
  rm -rf "$OUT" && mkdir -p "$OUT"
  local started; started=$(date +%s)
  cmd_up
  local tarball; tarball=$(package_source)
  log "copy realms-gos source ($(du -h "$tarball" | cut -f1)) into the VM"
  vm_ssh "rm -rf ~/realms-gos ~/devenv-artifacts && mkdir -p ~/realms-gos && tar -xzf - -C ~/realms-gos" < "$tarball"

  log "running guest/onboard.sh in the VM"
  set +e
  vm_ssh "CLI_SOURCE='$CLI_SOURCE' CLI_VERSION='$CLI_VERSION' bash ~/realms-gos/devenv/guest/onboard.sh"
  local rc=$?
  set -e

  vm_ssh "tar -C ~ -czf - devenv-artifacts" | tar -xzf - -C "$OUT" --strip-components=1 || true
  cmd_screenshot "$OUT/desktop.png" || true
  cp "$DEVENV_HOME/console-run.log" "$OUT/vm-console.log" 2>/dev/null || true
  if [ "$KEEP" = 1 ]; then
    log "VM left running: devenv.sh ssh | devenv.sh gui | devenv.sh down"
  else
    cmd_down
  fi
  log "artifacts in $OUT ($(( ($(date +%s) - started) / 60 )) min)"
  if [ "$rc" = 0 ]; then log "PASS"; else log "FAIL (onboard.sh exit $rc)"; fi
  return "$rc"
}

main() {
  local cmd="${1:-}"; [ -n "$cmd" ] || usage 1
  case "$cmd" in -h|--help|help) usage ;; esac
  shift
  local args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --hypervisor) HYPERVISOR="$2"; shift 2 ;;
      --hypervisor=*) HYPERVISOR="${1#*=}"; shift ;;
      --keep) KEEP=1; shift ;;
      --gui) GUI=1; shift ;;
      --force) FORCE=1; shift ;;
      --cli-source) CLI_SOURCE="$2"; shift 2 ;;
      --out) OUT="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) args+=("$1"); shift ;;
    esac
  done
  need python3; need ssh; need curl
  detect_hypervisor
  case "$HYPERVISOR" in qemu|virtualbox) ;; *) die "unknown hypervisor: $HYPERVISOR" ;; esac
  log "hypervisor: $HYPERVISOR · state: $DEVENV_HOME"
  case "$cmd" in
    run|base|up|down)
      mkdir -p "$DEVENV_HOME"
      exec 9> "$DEVENV_HOME/.lock"
      flock -n 9 || die "another devenv.sh $cmd is running (lock: $DEVENV_HOME/.lock)"
      ;;
  esac
  case "$cmd" in
    run) cmd_run ;;
    base) cmd_base ;;
    up) cmd_up ;;
    down) cmd_down ;;
    ssh) vm_ssh "${args[@]}" ;;
    gui) hv gui ;;
    screenshot) cmd_screenshot "${args[@]}" ;;
    -h|--help|help) usage ;;
    *) usage 1 ;;
  esac
}

main "$@"
