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

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}
