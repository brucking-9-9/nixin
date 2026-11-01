"""nixin — package and option installer for NixOS + Home Manager flake configs.

Layers (each a subpackage, each usable without the ones above it):

    discovery   what exists: search.nixos.org (packages, NixOS options, programs),
                Home Manager options, NixHub version history, noogle (lib functions);
                the exact-rev fallback is `nix search` on the host's locked nixpkgs
    truth       what THIS host has: installed packages, every option's current value
                and declarations (file:line), lock rev, generation — from evaluating
                the config flake itself
    writer      state.json -> generated installer/*.nix (nixfmt); targeted in-place
                edits of hand-written modules when removing/changing what they declare
    apply       plan (build + diff-closures), apply (commit into the root-owned
                authoritative config + switch, the one privileged step), sync (hub repo)
    cli         the contract: every verb takes --json; the TUI and GUI consume it
"""

__version__ = "0.1.0"
