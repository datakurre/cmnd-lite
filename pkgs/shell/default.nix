{ pkgs
, deploy-rs
, enableVSCodium ? true
, enableVSCode ? true
, enableVim ? true
, enableCoPilot ? true
}:

pkgs.mkShell {
  buildInputs = [
    pkgs.ruff
    (pkgs.poetry2nix.mkPoetryEnv {
      python = pkgs.python310;
      projectDir = ./.;
      preferWheels = true;
      overrides = pkgs.poetry2nix.overrides.withDefaults (self: super: {
        click-default-group-wheel = super.click-default-group-wheel.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.setuptools ];
        });
        python-multipart = super.python-multipart.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.hatchling ];
        });
        datasette = super.datasette.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.pytest-runner ];
        });
        datasette-graphql = super.datasette-graphql.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.setuptools ];
        });
        datasette-pretty-json = super.datasette-pretty-json.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.setuptools ];
        });
        datasette-render-markdown = super.datasette-render-markdown.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.setuptools ];
        });
        datasette-dashboards = super.datasette-dashboards.overridePythonAttrs(old: {
          buildInputs = old.buildInputs ++ [ self.setuptools ];
        });
        apsw = (pkgs.callPackage ./apsw.nix { inherit (self) buildPythonPackage python isPyPy; });
      });
    })
  ] ++ pkgs.lib.optionals enableVSCodium [
    (pkgs.vscode-with-extensions.override {
      vscode = if enableVSCode then pkgs.vscode else pkgs.vscodium;
      vscodeExtensions = [
        pkgs.vscode-extensions.bbenoist.nix
#       pkgs.vscode-extensions.ms-pyright.pyright
        pkgs.vscode-extensions.ms-python.python
        pkgs.vscode-extensions.ms-vscode.makefile-tools
        (pkgs.vscode-utils.buildVscodeMarketplaceExtension rec {
          mktplcRef = {
            name = "ruff";
            publisher = "charliermarsh";
            version = "2023.13.10931546";
            sha256 = "sha256-2FAq5jEbnQbfXa7O9O231aun/pJ8mkoBf1u4ekkBQu8=";
          };
          postInstall = ''
            rm $out/share/vscode/extensions/charliermarsh.ruff/bundled/libs/bin/ruff
            ln -s ${pkgs.ruff}/bin/ruff $out/share/vscode/extensions/charliermarsh.ruff/bundled/libs/bin/ruff
          '';
        })
        (pkgs.vscode-utils.buildVscodeMarketplaceExtension rec {
          mktplcRef = {
            name = "robotframework-lsp";
            publisher = "robocorp";
            version = "1.8.1";
            sha256 = "sha256-5a3S170r/o4vrNOEfLKTUdNn0cQCVJ2VjvcmbifI11k=";
          };
        })
      ] ++ pkgs.lib.optionals enableVim [
        pkgs.vscode-extensions.vscodevim.vim
      ] ++ pkgs.lib.optionals enableCoPilot [
        pkgs.vscode-extensions.github.copilot
      ];
    })
  ];
}
