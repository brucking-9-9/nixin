#!/usr/bin/env bash
# Re-record tests/fixtures/optindex/options-{nixos,home}.json from the module tests'
# configs (tests/nix/record-fixtures.nix). Needs nix, the store, and a config flake whose
# lock names home-manager ($NIXIN_CONFIG, else ~/nixos), for Home Manager's source.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
config=${NIXIN_CONFIG:-$HOME/nixos}
hm=$(python3 -c '
import json, sys
n = json.load(open(sys.argv[1]))["nodes"]["home-manager"]["locked"]
print(" ".join(f"{k} = \"{n[k]}\";" for k in ("type", "owner", "repo", "rev", "narHash")))
' "$config/flake.lock")
out=$here/tests/fixtures/optindex
mkdir -p "$out"
for scope in nixos home; do
  nix eval --json --impure --expr "
    let nixin = builtins.getFlake \"path:$here\"; in
    (import \"\${nixin}/tests/nix/record-fixtures.nix\" {
      inherit nixin;
      homeManager = builtins.fetchTree { $hm };
    }).$scope" >"$out/options-$scope.json"
  echo "$out/options-$scope.json: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["count"])' "$out/options-$scope.json") options"
done
