# nixin as a nixpkgs-style Python application. Called with
# `python3Packages.callPackage ./package.nix { }` from flake.nix (and from the
# overlay), so the Python set is whatever nixpkgs the consumer passes in.
{
  lib,
  buildPythonApplication,
  setuptools,
  requests,
  textual,
  pytestCheckHook,
  nixfmt,
  git,
}:
let
  project = (lib.importTOML ./pyproject.toml).project;
in
buildPythonApplication {
  pname = project.name;
  inherit (project) version;
  pyproject = true;

  src = lib.cleanSource ./.;

  build-system = [ setuptools ];
  dependencies = [
    requests
    textual
  ];

  # nixfmt also in the check: the writer's render test formats for real;
  # git: the apply tests run real git on temp repos (nix, sudo and nixos-rebuild are faked)
  nativeCheckInputs = [
    pytestCheckHook
    nixfmt
    git
  ];

  # The writer formats the files it generates. nix and git are deliberately
  # NOT wrapped in: nixin must drive the host's own nix (same version as its
  # daemon) and the user's git, both from PATH.
  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [ nixfmt ])
  ];

  meta = {
    inherit (project) description;
    homepage = "https://github.com/brucking-9-9/nixin";
    license = lib.licenses.mit;
    mainProgram = "nixin";
    platforms = lib.platforms.linux;
  };
}
