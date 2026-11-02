# A tiny config for the writer tests: never evaluated (no lock), only scanned.
{
  inputs.nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  inputs.home-manager.url = "github:nix-community/home-manager";

  outputs =
    { nixpkgs, home-manager, ... }@inputs:
    {
      nixosConfigurations."19BK" = nixpkgs.lib.nixosSystem {
        specialArgs = { inherit inputs; };
        modules = [
          ./configuration.nix
          home-manager.nixosModules.home-manager
          {
            home-manager.useGlobalPkgs = true;
            home-manager.users.alice = import ./home/home.nix;
          }
        ];
      };
      nixosConfigurations.mbp13 = nixpkgs.lib.nixosSystem { modules = [ ./hosts/mbp13 ]; };
      nixosConfigurations.bare = nixpkgs.lib.nixosSystem { modules = [ ./hosts/bare.nix ]; };
    };
}
