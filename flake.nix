{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (pkgs) importNpmLock;
        nodejs = pkgs.nodejs_24;
        npmDeps = importNpmLock.buildNodeModules {
          npmRoot = ./.;
          inherit nodejs;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            pinact
            zizmor
            ghalint
            nodejs
            importNpmLock.hooks.linkNodeModulesHook
            npm-check-updates
          ];

          inherit npmDeps;
        };
      }
    );
}
