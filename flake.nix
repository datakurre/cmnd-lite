{
  description = "";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/master";
    poetry2nix = { url = "github:nix-community/poetry2nix"; inputs.nixpkgs.follows = "nixpkgs"; inputs.flake-utils.follows = "flake-utils"; };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix, ... }: flake-utils.lib.eachDefaultSystem (system: let pkgs = import nixpkgs { inherit system; overlays = [ poetry2nix.overlay ]; config.allowUnfree = true; }; in {
    devShells.default = pkgs.callPackage ./pkgs/shell {};
  });
}
