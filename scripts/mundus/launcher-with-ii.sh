#!/usr/bin/env bash
# The icp network launcher, with the Internet Identity canister on the local network (`--ii`), for `realms mundus`.
# icp-cli starts whatever ICP_CLI_NETWORK_LAUNCHER_PATH names, and Casals writes the network's icp.yaml without an
# Internet Identity: this adds it without changing either. MUNDUS_LAUNCHER is the launcher icp-cli cached on this
# machine, which `realms mundus` finds, so that `icp network start` does not ask GitHub for the latest release.
exec "${MUNDUS_LAUNCHER:?set by realms mundus}" "$@" --ii
