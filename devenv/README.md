# Realms dev environment (VM)

A fresh Ubuntu 24.04 + GNOME (minimal) VM that walks through the developer
journey end to end, driven entirely from the command line. The same entry
point runs on a laptop and in GitHub Actions (`.github/workflows/devenv.yml`).

```bash
devenv/devenv.sh run
```

`run` does, inside a brand-new VM:

1. installs the `realms` CLI (from this checkout, or PyPI with `--cli-source pypi`);
2. `realms local start`: local network (icp-cli, installed on first use), realm
   backend + frontend and file registry from the matching GitHub release;
3. `realms new extension my-hello-world` (hello-world button) and
   `realms new codex my-hello-codex --extension my_hello_world`;
4. `realms local publish` both, then `realms local setup --codex my_hello_codex`
   (installs the codex and its extensions, launches the realm);
5. checks `realms local call my_hello_world greet` returns `Hello, World!`;
6. opens the realm in Chromium (Playwright), logs in with a throwaway key,
   presses the hello button and waits for `Hello, World!`.

It exits 0 only if every step passed. Artifacts land in `devenv/artifacts/`
(`--out DIR` or `DEVENV_OUT` to change):

| File | |
|---|---|
| `onboard.log` | full log of the guest steps |
| `hello-result.png`, `before-click.png`, `browser-check.log` | browser check |
| `status.json`, `greet.json`, `state.json`, `*-manifest.json` | realm state |
| `desktop.png`, `vm-console.log` | GNOME desktop and serial console at the end |

## Requirements (Linux host)

- QEMU with KVM (`qemu-system-x86 qemu-utils`, writable `/dev/kvm`), **or**
  VirtualBox 7 (plus `qemu-utils` to convert the cloud image);
- `genisoimage`, `xorriso` or `cloud-localds` for the cloud-init seed;
- `python3`, `ssh`, `curl`, `flock`; ~15 GB free disk, 4 CPUs and 6 GB RAM for the VM.

The hypervisor is picked automatically: QEMU when `/dev/kvm` is usable (GitHub
runners), otherwise VirtualBox (e.g. laptops where VirtualBox owns
virtualization), otherwise QEMU without KVM (very slow). Force one with
`--hypervisor qemu|virtualbox` or `DEVENV_HYPERVISOR`.

## Commands

```bash
devenv/devenv.sh run [--keep]   # whole flow; --keep leaves the VM running afterwards
devenv/devenv.sh base [--force] # build (or rebuild) the base image only
devenv/devenv.sh up [--gui]     # boot a fresh VM from the base image
devenv/devenv.sh ssh [cmd]      # shell or command in the VM (user dev, password dev)
devenv/devenv.sh gui            # show the desktop (VirtualBox window / QEMU VNC on :1)
devenv/devenv.sh screenshot [file.png]
devenv/devenv.sh down           # power off and discard the VM; the base image is kept
```

The base image (Ubuntu cloud image + GNOME, Node 22, Firefox, Playwright's
Chromium) is built once with cloud-init and cached in `~/.cache/realms-devenv`
(`DEVENV_HOME`). It is rebuilt automatically when `cloud-init/`,
`guest/provision.sh` or the Playwright version change. The first build takes
5–10 minutes; later runs boot a throwaway copy (QEMU overlay / VirtualBox
snapshot) and take about 8 minutes.

Tunables: `DEVENV_CPUS` (4), `DEVENV_MEMORY_MB` (6144), `DEVENV_DISK_GB` (32),
`DEVENV_SSH_PORT` (2222), `DEVENV_VM_NAME` (`realms-devenv`). VirtualBox VMs
created by the script carry a marker in their description; the script never
touches other VMs.

## Layout

- `devenv.sh`: host side (image, VM lifecycle, copying the source in, artifacts).
- `cloud-init/user-data.yaml`, `guest/provision.sh`: base image provisioning.
- `guest/onboard.sh`: the developer journey, run as `dev` in the VM. It also
  works on any Ubuntu machine with Node 20+ and pipx.
- `browser-check/`: Playwright check of the hello button against any local
  realm (options at the top of `check.mjs`).

Everything in the VM uses a local, throwaway identity; nothing touches mainnet.
