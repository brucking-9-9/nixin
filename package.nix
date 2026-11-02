# nixin as a nixpkgs-style Python application. Called with
# `python3Packages.callPackage ./package.nix { }` from flake.nix (and from the
# overlay), so the Python set is whatever nixpkgs the consumer passes in.
{
  lib,
  buildPythonApplication,
  setuptools,
  requests,
  textual,
  pyside6,
  qt6,
  pytestCheckHook,
  pytest-qt,
  nixfmt,
  git,
}:
let
  project = (lib.importTOML ./pyproject.toml).project;
  # The GUI (PySide6 + QML): Qt Quick and its Controls, the Wayland platform plugin, SVG.
  qtModules = with qt6; [
    qtbase
    qtdeclarative
    qtwayland
    qtsvg
  ];
  # What Qt needs to find its plugins and QML modules outside a wrapper: the tests (headless)
  # and the dev shell. The installed programs get the same from wrapQtAppsHook.
  qtEnv = {
    QT_PLUGIN_PATH = lib.makeSearchPath qt6.qtbase.qtPluginPrefix qtModules;
    NIXPKGS_QT6_QML_IMPORT_PATH = lib.makeSearchPath qt6.qtbase.qtQmlPrefix qtModules;
  };
in
buildPythonApplication {
  pname = project.name;
  inherit (project) version;
  pyproject = true;

  src = lib.cleanSource ./.;

  build-system = [ setuptools ];
  nativeBuildInputs = [ qt6.wrapQtAppsHook ];
  buildInputs = qtModules;
  dependencies = [
    requests
    textual
    pyside6
  ];

  # nixfmt also in the check: the writer's render test formats for real;
  # git: the apply tests run real git on temp repos (nix, sudo and nixos-rebuild are faked);
  # qtdeclarative: qmllint over the GUI's QML; pytest-qt: the GUI offscreen
  nativeCheckInputs = [
    pytestCheckHook
    pytest-qt
    nixfmt
    git
    qt6.qtdeclarative
  ];

  preCheck = ''
    export QT_QPA_PLATFORM=offscreen
    export QT_PLUGIN_PATH=${qtEnv.QT_PLUGIN_PATH}
    export NIXPKGS_QT6_QML_IMPORT_PATH=${qtEnv.NIXPKGS_QT6_QML_IMPORT_PATH}
  '';

  # The writer formats the files it generates. nix and git are deliberately
  # NOT wrapped in: nixin must drive the host's own nix (same version as its
  # daemon) and the user's git, both from PATH. The Qt environment goes into the
  # same Python wrapper (one wrapper, not a Qt wrapper around it). One bash
  # array: a makeWrapperArgs list attribute would arrive as a single string.
  dontWrapQtApps = true;
  preFixup = ''
    makeWrapperArgs+=(--prefix PATH : ${lib.makeBinPath [ nixfmt ]} "''${qtWrapperArgs[@]}")
  '';

  # The installed GUI starts headless and quits after its first frame.
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME=$TMPDIR XDG_RUNTIME_DIR=$TMPDIR QT_QPA_PLATFORM=offscreen \
      $out/bin/nixin-gui --config $TMPDIR/no-config --self-test
    runHook postInstallCheck
  '';

  passthru = { inherit qtEnv; };

  meta = {
    inherit (project) description;
    homepage = "https://github.com/brucking-9-9/nixin";
    license = lib.licenses.mit;
    mainProgram = "nixin";
    platforms = lib.platforms.linux;
  };
}
