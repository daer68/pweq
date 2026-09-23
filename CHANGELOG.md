# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- AirPlay (RAOP) speakers were missing from the output list because PipeWire
  marks them `node.virtual`. Processing sinks are now recognised by their
  `node.link-group` instead.
- The desktop entry now starts pweq by absolute path, so it launches from app
  menus whose `PATH` doesn't include `~/.local/bin`.

## [0.1.0] - 2026-09-23

### Added

- Per-output parametric EQ using PipeWire's builtin filter-chain biquads.
- AutoEQ / Equalizer APO `.txt` preset parser: PK, LSC/LS, HSC/HS, LP, HP, BP, NO, AP, preamp.
- Auto-follow daemon: one EQ process per connected output with a preset;
  moves the default output onto its EQ sink; event driven, no polling.
- GTK 4 / libadwaita GUI: per-output preset selection, preset import, band view,
  edit in text editor, delete, service status banner.
- CLI: `status`, `import`, `assign`, `import-easyeffects`, `reload`, `daemon`.
- EasyEffects importer for output presets and autoload rules.
- systemd user units, including a path unit that reloads on preset changes.
- `install.sh` for per-user, system-wide and staged (packaging) installs; Arch PKGBUILD.

[Unreleased]: https://github.com/daer68/pweq/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/daer68/pweq/releases/tag/v0.1.0
