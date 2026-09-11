# Arch and Omarchy packaging plan

This plan records the agreed distribution work for EQxEar. Steps 1 through 4 are authorized for implementation. Step 5 and the subsequent AUR and CLI work remain follow-ups.

## 1. Arch packaging and versioned releases

Create an `eqxear` package recipe with a `PKGBUILD` and generated `.SRCINFO`, backed by a tagged version and a checksummed source release. Start with the architectures actually tested. A separate `eqxear-git` package for development snapshots can be considered later.

## 2. Build native components during packaging

Compile the PipeWire DSP plugin and FFT library when building the package. Install those artifacts with the application so normal startup needs neither a compiler nor LV2 development headers. Keep the current cached compilation path available when running a development checkout.

## 3. Package desktop integration

Install an `eqxear` command, a desktop entry, and an original application icon. The installed command and launcher should work independently of a Git checkout. Package installation must not edit the user's Omarchy or Hyprland configuration or start audio processing.

## 4. Verify the installed package

Extend CI to build an Arch package in a clean environment and test the installed application. Verify that installed native libraries load without runtime compilation, that the command and desktop entry are valid, and that the GUI and isolated audio backend work outside the source checkout. Test removal without deleting user presets.

## 5. Improve the public presentation

Add screenshots in a couple of Omarchy themes, a short tuning demonstration, release notes, and concise install, update, and uninstall instructions. Packaging instructions and initial release notes overlap steps 1 through 4; the screenshots and demonstration remain separate presentation work.

## After packaging

Publish the reviewed package recipe to the AUR. Omarchy already offers AUR installation through its package menu. AUR submission and ongoing maintenance are separate from publishing source on GitHub; no AUR submission is authorized by the current steps 1 through 4 request.

Later, consider a small command-line interface for preset recall and EQ toggling so users can connect those actions to their own Hyprland shortcuts. Keep Omarchy-specific integration optional so ordinary Arch and other PipeWire desktops can use the app.

## Status

Steps 1 through 4 are implemented for version 0.1.0. The package compiles native libraries during the build and provides the command, desktop entry and icon. Validation covers unit tests, the installed GUI, live audio and spectrum capture. CI builds the package in an Arch build container, tests it in a separate runtime container without a compiler, and checks removal while preserving user data. See [package instructions](arch-packaging.md), [release notes](releases/0.1.0.md) and the [GitHub release](https://github.com/jerodkdunn/EQxEar/releases/tag/v0.1.0). Screenshots, a demo, AUR submission and preset/toggle CLI controls remain follow-ups.
