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
      # then `pkgs.nixin`, built against the consumer's own nixpkgs. The modules below do
      # not need it.
      overlays.default = final: prev: { nixin = final.python3Packages.callPackage ./package.nix { }; };

      # programs.nixin.{enable, package, optionIndex, outdated, pending}: README, "Modules".
      # A path, so the module system dedups a second import and names it in declarations.
      nixosModules.default = ./modules/nixos.nix;
      homeModules.default = ./modules/home.nix;
      homeManagerModules.default = self.homeModules.default; # the older output name

      # The option index builder the modules use: lib/options-index.nix.
      lib.optionsIndex = import ./lib/options-index.nix { inherit (nixpkgs) lib; };

      devShells = forAll (
        pkgs:
        let
          nixin = self.packages.${pkgs.stdenv.hostPlatform.system}.nixin;
        in
        {
          default = pkgs.mkShell {
            inputsFrom = [ nixin ];
            packages = with pkgs; [
              python3Packages.pytest
              python3Packages.pytest-qt
              qt6.qtdeclarative # qmllint
              ruff
              nixfmt
            ];
            # Qt's plugins and QML modules for pytest and for the GUI run from the tree
            env = nixin.qtEnv;
          };
        }
      );

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
