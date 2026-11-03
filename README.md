# meshcore-local-flasher

One script that sets up a **local copy of the MeshCore web flasher** and the
**Repeater Setup (config) tool** on your own computer, so you can

* flash your **own firmware builds**, **original releases** you downloaded, and
  other modified firmware from a folder on your disk,
* work **offline** (after one online run),
* keep using Chrome/Edge Web Serial, because everything is served from
  `http://localhost`.

This is an unofficial helper. It is not affiliated with the MeshCore project.
It does not contain any code of the flasher: the script downloads
[flasher.meshcore.io](https://github.com/meshcore-dev/flasher.meshcore.io) and
[config.meshcore.io](https://github.com/meshcore-dev/config.meshcore.io)
(both MIT licensed) when it runs.

## Why a script is needed

The flasher is a static web app, but at start-up it asks the server behind
flasher.meshcore.io for `/releases?repo=...`. That endpoint is not part of the
repository, and the page does not start without it. The script generates this
file from the firmware files in your folder, adapts the flasher's
`config.json` (removes entries whose files are not available locally, widens
one file-name pattern, adds WiFi companion entries), and starts a small Python
web server. The complete explanation is in the header of
`setup_local_flasher.py`.

## Requirements

* **Python 3.8 or newer** (standard library only; no packages to install)
* internet for the first run (download, icon fonts); `git` is optional
* Chrome, Edge or Opera (Web Serial)

Works with the same command on Windows, Linux, macOS and WSL (with WSL2 the
browser can run on Windows and open `http://localhost:8080` of the WSL server).

> **Tested:** Linux (Debian, in WSL2) with Chrome on Windows, and a run on
> native Windows (setup and flashing worked). Please report problems.

## Quick start

```bash
git clone https://github.com/m0urs/meshcore-local-flasher.git
cd meshcore-local-flasher
python3 setup_local_flasher.py --fw-dir ~/my-firmware
```

Windows (Command Prompt or PowerShell, with Python installed):

```bat
py setup_local_flasher.py --fw-dir C:\MeshCoreFirmware
```
(or `python setup_local_flasher.py ...`). Without git, download the ZIP of this
repository from GitHub and unpack it; the script only needs this one file.

Open <http://localhost:8080> (flasher) or <http://localhost:8080/config/>
(Repeater Setup). Stop with Ctrl+C.

Usage: `python setup_local_flasher.py [options] [TARGET_DIR=./meshcore-local-flasher] [PORT=8080]`

| Option | Meaning |
|---|---|
| `--fw-dir PATH` | the one firmware folder (see below); default `TARGET_DIR/fw`; remembered for later runs |
| `--refresh` | no download; only re-scan the folder and regenerate `releases`/`config.json` (run after adding files, then reload the page) |
| `--no-serve` | prepare only |
| `--own-tag TAG` | mark your own builds, default `mups` (see below) |
| `--no-clock-patch` | do not patch the console's `clock sync` command |
| `--no-git` | download ZIP archives instead of using git |

The choices are stored in `TARGET_DIR/local-flasher.json`, so after the first
run `python setup_local_flasher.py --refresh` is enough.

## The firmware folder

All firmware lives in **one folder**: your builds, original releases and the
fixed-name nRF52 helper files. The web server shows this folder under two
URLs (`/fw/...` and `/firmware/...`); no symbolic links are needed.

```
my-firmware/
  Heltec_v3_repeater/Heltec_v3_repeater-v1.18.0.mups1.bin     your builds, any sub-folders
  Heltec_v3_repeater/Heltec_v3_repeater-v1.18.0-81dbae5.bin   original releases
  FLASH_ERASE_nrf52_softdevice_v6.uf2                         nRF52 helper files: top level only
```

The folder structure does not matter. You can put every file directly into the
folder or arrange them in any sub-folders (per model, per version, ...);
sub-folders are scanned recursively. Only the **file names** are evaluated. The
one exception are the nRF52 helper files (see below), which must lie in the top
level.

Keep each file only once. Files are grouped by role and the version part of
the name, not by folder: if the same file name exists in two folders, both
copies end up under the same version in the flasher.

File names must follow the MeshCore scheme
`<build environment>-<version>[-merged].<bin|zip|uf2>`, where the environment
contains `companion_radio_ble|usb|wifi`, `repeater` or `room_server`. Original
release files already have this form. Files that do not match are listed by the
script; they can still be flashed with the flasher's "Custom Firmware" button.

The drop-down shows a label before each version:

| File name version | Label |
|---|---|
| `v1.18.0.mups1` | `MUPS v1.18.0.mups1` (own release; the tag is `--own-tag`) |
| `v1.18.0.dev-abc1234` | `MUPS-dev v1.18.0.dev-abc1234` (own test build) |
| `v1.18.0-81dbae5` | `Original v1.18.0-81dbae5` |
| anything else | `Custom <version>` |

Order: own releases, own test builds, Custom, Original; the first entry is
pre-selected.

### nRF52 helper files

"Erase device" and the bootloader update links of nRF52 devices use files with
fixed names under `/firmware/` that are not in the flasher repository (for
example `FLASH_ERASE_nrf52_softdevice_v6.zip` or `*_bootloader-*-OTAFIX*.zip`).
They are not included here. After every run the script lists which of them are
still missing for the devices you offer. Download them once from the official
flasher and save them in the **top level** of the firmware folder.

## Offline use

After one online run the target folder is self-contained. Start it without the
setup script (Python is enough):

```bash
cd TARGET_DIR
python serve.py          # Windows: py serve.py
```

`serve.py` is generated by the script. It also reads the firmware folder from
`local-flasher.json`. Use it instead of `python -m http.server`: it maps
`/fw` and `/firmware` to the firmware folder, sends correct MIME types for
`.js` (Windows can get this wrong, and the page does not start then),
delivers `index.html` to page loads (requests accepting text/html) for the flasher's address-bar URLs such as
`/heltec-v3/repeater/` (otherwise a reload or the "Close" button after
flashing ends in a 404) and disables caching, so a reload shows new firmware.

The script also saves the Material Symbols icon fonts next to the CSS files.
Still online-only: the radio presets of the config tool (`api.meshcore.nz`),
map tiles (OpenStreetMap). How the config tool behaves without these has not
been tested.

## What the script changes in the flasher

* `config.json`: removes entries with fixed file names (KISS modem, Ripple GUI),
  widens the update pattern `-<7 hex>.bin` to also accept `.<TAG>N.bin`, adds a
  "WiFi" twin for every Bluetooth companion entry.
* `js/app.js`: the Repeater Setup button opens `/config/` instead of
  config.meshcore.io.
* `js/app.js` (optional, `--no-clock-patch` disables it): typing `clock sync` in
  the serial console sends `time <epoch of this computer>`. Over USB the device
  gets no sender time, so the original command cannot work: official firmware
  answers `ERR: clock cannot go backwards`; firmware that allows setting the
  clock backwards would set it to 1970.

Re-running is safe: the repositories are updated, the patches re-applied.
(With ZIP downloads, files are overwritten, not deleted; with git they are
reset to the upstream state.)

## Moving to another machine

Copy the target folder and the firmware folder (with `tar`/`rsync -a` on
Linux). The firmware folder is stored in `local-flasher.json` relative to the
target folder where possible; if it is not found on the other machine, run the
script once with `--fw-dir NEW_PATH`.

## License

MIT, see `LICENSE`. The downloaded flasher and config tool have their own
licenses (MIT). Firmware files are not part of this repository.
