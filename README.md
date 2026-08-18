<p align="center">
  <img src="https://raw.githubusercontent.com/peternaame-boop/ytm-player/master/docs/images/header.svg" alt="ytm-player — YouTube Music in your terminal — synced lyrics, vim keys, mpv backend. Runs on Linux, macOS, Windows. Free-tier supported." width="720" />
</p>

<p align="center">
  <a href="https://ytm-player.com"><img src="https://raw.githubusercontent.com/peternaame-boop/ytm-player/master/docs/images/website-button.svg" alt="Visit ytm-player.com" width="240" /></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/ytm-player/"><img src="https://img.shields.io/pypi/v/ytm-player?style=for-the-badge&logo=pypi&color=ff4e45&labelColor=0f0f0f&logoColor=ff4e45" alt="PyPI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-ff4e45?style=for-the-badge&logo=python&labelColor=0f0f0f&logoColor=ff4e45" alt="Python 3.10+"></a>
  <a href="https://github.com/peternaame-boop/ytm-player/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/peternaame-boop/ytm-player/ci.yml?style=for-the-badge&logo=githubactions&labelColor=0f0f0f&logoColor=ff4e45" alt="CI"></a>
  <a href="https://github.com/peternaame-boop/ytm-player/blob/master/LICENSE"><img src="https://img.shields.io/github/license/peternaame-boop/ytm-player?style=for-the-badge&logo=opensourceinitiative&color=ff4e45&labelColor=0f0f0f&logoColor=ff4e45" alt="License"></a>
</p>

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/Install-ff4e45?style=for-the-badge&labelColor=0f0f0f" alt="Install"></a>&nbsp;
  <a href="#quickstart"><img src="https://img.shields.io/badge/Quickstart-ff4e45?style=for-the-badge&labelColor=0f0f0f" alt="Quickstart"></a>&nbsp;
  <a href="#documentation"><img src="https://img.shields.io/badge/Documentation-ff4e45?style=for-the-badge&labelColor=0f0f0f" alt="Documentation"></a>&nbsp;
  <a href="https://github.com/peternaame-boop/ytm-player/blob/master/CONTRIBUTING.md"><img src="https://img.shields.io/badge/Contributing-ff4e45?style=for-the-badge&labelColor=0f0f0f" alt="Contributing"></a>&nbsp;
  <a href="https://github.com/peternaame-boop/ytm-player/blob/master/CHANGELOG.md"><img src="https://img.shields.io/badge/Changelog-ff4e45?style=for-the-badge&labelColor=0f0f0f" alt="Changelog"></a>
</p>

> [!NOTE]
> This is a feature fork of [peternaame-boop/ytm-player](https://github.com/peternaame-boop/ytm-player) with native Matugen dynamic theming, Keyring authentication, YouTube Premium 256kbps stream support, search navigation enhancements, and Linux desktop integration.

## Fork Enhancements & Changes

- **Native Matugen Dynamic Theming & Live Hot-Reload**:
  - Built-in `matugen` theme reading colors directly from `~/.config/ytm-player/theme.toml`.
  - Automatic file watcher that detects wallpaper changes (e.g. via Waypaper) and reloads Textual CSS variables live without restarting the player.
  - `ytm setup-matugen` CLI command to link and generate Matugen template files automatically.
- **YouTube Music Premium 256kbps Audio Support**:
  - Automatic export of session cookies into a secure Netscape `cookies.txt` for `yt-dlp`.
  - Resolves native **Format 774 (256kbps Opus)** high-bitrate audio for Premium accounts without 403 Forbidden errors.
- **Robust Authentication & System Keyring**:
  - Secure credential storage via system Keyring (GNOME Keyring / KWallet / KeePassXC) with safe `0600` file fallback.
  - Native Zen Browser cookie detection across Linux, macOS, and Windows.
  - Multi-account slot probing (`x-goog-authuser` 0–4) to select and lock onto the correct YouTube Music profile.
  - Essential cookie filtering to prevent header bloat and false "session expired" errors.
  - First-launch zero-touch auto-login on startup.
- **Search & Keyboard Navigation**:
  - Added arrow keys (`Down`/`Up`), Vim (`Ctrl+j`/`Ctrl+k`, `Alt+j`/`Alt+k`), and Emacs (`Ctrl+n`/`Ctrl+p`) navigation between the search input box and suggestion dropdown.
  - High-contrast border styling for the search input bar.
- **Desktop & MPRIS Integration**:
  - Track start `Seeked(0)` signal for instant Waybar/AGS seekbar synchronization.
  - XDG `.desktop` launcher entry (`ytm-player.desktop`) with `Terminal=true`.
  - Updated Arch Linux `aur/PKGBUILD` to install the desktop launcher file.

## Install

```bash
# Install with pipx (recommended)
pipx install git+https://github.com/Min34r/ytm-player.git

# Or install from local source
git clone https://github.com/Min34r/ytm-player.git
cd ytm-player
pip install -e .

# Arch Linux (local PKGBUILD)
cd aur && makepkg -si
```

## Quickstart

```bash
ytm setup           # One-time auth (auto-detects browser cookies)
ytm setup-matugen   # (Optional) Setup Matugen dynamic theme integration
ytm                 # Launch the TUI
```

## Contributors & Upstream Credit

This fork builds upon [peternaame-boop/ytm-player](https://github.com/peternaame-boop/ytm-player). Thanks to the original author and upstream contributors:
[@peternaame-boop](https://github.com/peternaame-boop), [@dmnmsc](https://github.com/dmnmsc), [@Villoh](https://github.com/Villoh), [@valkyrieglasc](https://github.com/valkyrieglasc), [@dsafxP](https://github.com/dsafxP), [@Thayrov](https://github.com/Thayrov), [@glywil](https://github.com/glywil), [@Kineforce](https://github.com/Kineforce), [@CarterSnich](https://github.com/CarterSnich), [@Tohbuu](https://github.com/Tohbuu), [@nitsujri](https://github.com/nitsujri), [@uhs-robert](https://github.com/uhs-robert), [@moschi](https://github.com/moschi), [@firedev](https://github.com/firedev), [@wgordon17](https://github.com/wgordon17), [@gitiy1](https://github.com/gitiy1), [@hanandewa5](https://github.com/hanandewa5), [@aimar-a](https://github.com/aimar-a), [@Gimar250](https://github.com/Gimar250), [@Wiibleyde](https://github.com/Wiibleyde), [@szx19970521](https://github.com/szx19970521), [@aaguilar-hub](https://github.com/aaguilar-hub), [@TheoBassaw](https://github.com/TheoBassaw), [@holstvoogd](https://github.com/holstvoogd), and [@ProfessionalGriefer](https://github.com/ProfessionalGriefer).
