# Arch Linux package

EQxEar's stable Arch package targets x86_64 and installs a system launcher, desktop entry, icon, Python application, and precompiled native DSP and FFT. Python 3.11 or newer and the GTK/PipeWire runtime dependencies are required. The C compiler and LV2 headers are build dependencies; running the installed package does not require them.

These instructions describe version 0.1.1. Building from GitHub requires its matching tag and release asset to be available.

## Build the package

Install Arch's build tools, then use a normal user account to build the tagged release. `makepkg -s` prompts to install any missing build and runtime dependencies through pacman.

```sh
sudo pacman -S --needed base-devel git
git clone --branch v0.1.1 --depth 1 https://github.com/jerodkdunn/EQxEar.git
cd EQxEar/packaging/arch
makepkg -s
```

Install the resulting package, then run `eqxear` or choose EQxEar in your desktop launcher:

```sh
sudo pacman -U ./eqxear-0.1.1-1-x86_64.pkg.tar.zst
eqxear
```

Release assets are hosted on [GitHub](https://github.com/jerodkdunn/EQxEar/releases/tag/v0.1.1). The package is not yet in the AUR.

The stable recipe downloads `eqxear-0.1.1.tar.gz` from the GitHub release and verifies its SHA-256 checksum. `makepkg` builds a package without installing it or changing your desktop audio. Review the resulting package before choosing to install it with `pacman -U`. The recipe does not enable autostart or run the application during installation.

The package installs `/usr/bin/eqxear`, application files under `/usr/lib/eqxear`, the desktop entry under `/usr/share/applications`, and its icon under `/usr/share/icons/hicolor/scalable/apps`. License notices are under `/usr/share/licenses/eqxear`, and documentation is under `/usr/share/doc/eqxear`. Existing per-user presets, drafts and layouts retain their current XDG locations. Installed Python modules also have checked-hash bytecode caches whose source filenames use the installed prefix. The `PACKAGED` marker tells the application to use its installed native binaries; missing binaries are an installation error, not a request to compile into a user cache.

## Update or remove

Before an update or removal, choose **Quit background service** in the app and close its window. For an update, build the new release's recipe and install its package with `sudo pacman -U`. Restart EQxEar afterward so the updated backend and native libraries load. The package prints this reminder during upgrades; it does not stop user processes. Removal likewise leaves an existing audio session running until you quit its background service or log out.

To uninstall:

```sh
sudo pacman -R eqxear
```

Pacman removes package files and keeps your presets, draft and layout in `$XDG_DATA_HOME/eqxear-v2`, normally `~/.local/share/eqxear-v2`.

If you previously ran `./install-launcher` from a checkout, its per-user desktop entry takes precedence over the packaged entry. Remove `~/.local/share/applications/com.eqxear.App.desktop`, or the equivalent under your custom `$XDG_DATA_HOME`, when switching to the package. This leaves your presets intact.

## Build and stage without makepkg

```sh
make
make DESTDIR="$PWD/stage" PREFIX=/usr install
```

`CC`, `CFLAGS`, `CPPFLAGS` and `LDFLAGS` control the native build. `PREFIX` selects the installed prefix and `DESTDIR` selects a staging root; the staging path never appears in the installed wrapper. Installation copies application files and does not invoke the app, write user presets, or compile native code at runtime. A source checkout still supports `./run` and builds native code in the user cache as before.

## Produce a release archive

```sh
python3 tools/dist.py
```

The version comes from `eqxear/__init__.py`. The archive uses a fixed file order, timestamps and permissions, so identical release files produce an identical SHA-256 checksum. Its allowlist includes application and native source, build tools, tests and interoperability fixtures, the icon, public user/contributor documentation, and license notices. It excludes Git metadata, caches, build outputs, private audit/research/publishing notes, and `packaging/arch` itself. Excluding the recipe prevents its checksum from changing the archive it verifies.

After all release files are final, generate the archive, update `sha256sums` in `packaging/arch/PKGBUILD` with the printed digest, and run `makepkg --printsrcinfo > .SRCINFO` from that directory. Publish that exact archive as the matching versioned release asset. Never replace an already published version's archive with different contents. Changes after publication need a new upstream version or a packaging-only `pkgrel` bump as appropriate.

CI may test an unreleased tree by creating the same allowlisted archive and substituting its local source path and checksum into a temporary copy of the recipe. That substitution is only for the candidate build; the versioned recipe targets the matching release asset, which must be published before that recipe can download it. A successful candidate build does not mean a release has been published or an AUR submission has been made.

## Package verification

CI runs `namcap` on the recipe and built package, fails on errors and retains warnings in the job log. It tests the installed application with `PYTHONSAFEPATH=1`. A separate test upgrades the published 0.1.0 baseline during virtual-speaker playback, checks continuity, explicitly restarts the updated service, removes the package during playback, and then verifies graceful disconnect and preset preservation. `tests/package_transactions.py` refuses to run outside an explicitly marked disposable container.
