{
  description = "nixin — package and option installer for NixOS + Home Manager flake configs";

  inputs.nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        nixin = pkgs.python3Packages.callPackage ./package.nix { };
        default = nixin;
      });

      # For consumers (the nixos-config flake): `nixpkgs.overlays = [ nixin.overlays.default ]`
      # then `pkgs.nixin`, built against the consumer's own nixpkgs.
      overlays.default = final: prev: { nixin = final.python3Packages.callPackage ./package.nix { }; };

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ self.packages.${pkgs.stdenv.hostPlatform.system}.nixin ];
          packages = with pkgs; [
            python3Packages.pytest
            ruff
            nixfmt
          ];
        };
      });

      # `nix fmt` passes no paths, and bare nixfmt then reads stdin. Without a path
      # argument (only flags, e.g. `nix fmt -- --check`) this formats the tracked *.nix files.
      formatter = forAll (
        pkgs:
        pkgs.writeShellApplication {
          name = "nixfmt-tracked";
          runtimeInputs = [
            pkgs.nixfmt
            pkgs.git
          ];
          text = ''
            for a in "$@"; do
              if [ "$a" = - ] || [ -e "$a" ]; then exec nixfmt "$@"; fi
            done
            mapfile -d "" -t files < <(git ls-files -z -- ':(top)*.nix')
            exec nixfmt "$@" "''${files[@]}"
          '';
        }
      );
    };
}
