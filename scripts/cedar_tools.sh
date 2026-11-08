#!/usr/bin/env bash
# The pinned Cedar analysis tools (docs/codices/layered-codex.md §12.3, R1 realms-gos#481): the
# experimental Cedar CLI 4.14.1, which has the `symcc` subcommands, and the cvc5 1.3.1 solver it
# calls, both prebuilt for x86_64 Linux and checked against their sha256 before use.
#
#   scripts/cedar_tools.sh [dir]      downloads into dir (default .cache/cedar-tools) and prints
#                                     CEDAR_CLI=<cedar> and CVC5=<cvc5>, ready for $GITHUB_ENV
#
# The tests that prove `never-errors` for every generated forbid (cli/tests/test_legislation_forbids.py)
# run when both variables are set and skip otherwise. Nothing is installed system-wide.
set -euo pipefail

dir=${1:-.cache/cedar-tools}
cedar_version=4.14.1
cedar_archive=cedar-policy-cli-experimental-x86_64-unknown-linux-gnu.tar.xz
cedar_sha256=fb7b341770a8b88a369437b76482be5de7576073f51e075c2f5d4165bb1c615c
cvc5_version=1.3.1
cvc5_archive=cvc5-Linux-x86_64-static.zip
cvc5_sha256=1a1cda20d2df4938fa4944a69f33ddc9172e319ece0eed0aa09c4d7abede3ed1

mkdir -p "$dir"
cd "$dir"
cedar=cedar-policy-cli-experimental-x86_64-unknown-linux-gnu/cedar
cvc5=cvc5-Linux-x86_64-static/bin/cvc5

if [ ! -x "$cedar" ]; then
  curl -fsSL -o "$cedar_archive" \
    "https://github.com/cedar-policy/cedar/releases/download/cedar-policy-cli-v${cedar_version}/${cedar_archive}"
  echo "$cedar_sha256  $cedar_archive" | sha256sum -c - >&2
  tar xf "$cedar_archive"
  rm "$cedar_archive"
fi
if [ ! -x "$cvc5" ]; then
  curl -fsSL -o "$cvc5_archive" \
    "https://github.com/cvc5/cvc5/releases/download/cvc5-${cvc5_version}/${cvc5_archive}"
  echo "$cvc5_sha256  $cvc5_archive" | sha256sum -c - >&2
  unzip -q "$cvc5_archive"
  rm "$cvc5_archive"
fi

"$cedar" --version >&2
"$cvc5" --version | head -1 >&2
echo "CEDAR_CLI=$PWD/$cedar"
echo "CVC5=$PWD/$cvc5"
