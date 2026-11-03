#!/usr/bin/env python3
# =============================================================================
# setup_local_flasher.py
#
# Sets up a LOCAL copy of the MeshCore web flasher AND the MeshCore
# "Repeater Setup" (config) web tool, and serves both on http://localhost.
# Firmware files stored on your disk (your own builds, original firmware,
# other people's modified firmware) are offered in the flasher's device list.
#
# Pure Python 3.8+ (standard library only): runs the same on Windows, Linux,
# macOS and WSL. No bash, and git is optional.
#
# QUICK START
#     python setup_local_flasher.py --fw-dir C:\Firmware       (Windows; also: py ...)
#     python3 setup_local_flasher.py --fw-dir ~/firmware       (Linux, macOS, WSL)
# then open http://localhost:8080 in Chrome, Edge or Opera.
#
# BACKGROUND (why this script is needed)
# --------------------------------------
# * The flasher (github.com/meshcore-dev/flasher.meshcore.io) is a static web
#   app (HTML/JS). At start-up it requests "/releases?repo=..." to get the list
#   of firmware releases. That endpoint is provided by the server behind
#   flasher.meshcore.io and is NOT part of the repository; if the request does
#   not return HTTP 200 the page does not start. This script generates a file
#   named "releases" from the firmware files in your firmware folder, and the
#   small web server (serve.py, generated) delivers it.
# * The flasher's "config.json" decides which firmware is offered for which
#   device by matching FILE NAMES against regular expressions. Entries that
#   list fixed file names (e.g. "KISS Modem", Ripple GUI) point to files under
#   /firmware/ that are not part of the repository, so they could not be
#   flashed locally. This script removes those entries from the local
#   config.json; only firmware found in your firmware folder is offered.
# * The flasher has a "Repeater Setup" button that normally opens
#   https://config.meshcore.io in a popup. This script also downloads the config
#   tool (github.com/meshcore-dev/config.meshcore.io, also static) into
#   "config/" and patches ONE line in js/app.js so the button opens "/config/".
# * The serial console's "clock sync" command cannot work over USB: the device
#   takes the time from the SENDER of the command (meant for remote admin over
#   LoRa), and the serial interface has no sender time (it is always 0). The
#   official firmware answers "ERR: clock cannot go backwards"; firmware that
#   allows setting the clock backwards (PR #1349 of the MeshCore repository)
#   would set the clock to 1970. This script patches the flasher's console
#   (js/app.js) so that typing "clock sync" sends "time <epoch seconds of this
#   computer>" instead. The device receives a normal "time" command; the
#   official firmware only accepts it if it moves the clock forward. Disable
#   with --no-clock-patch.
# * The web page uses the browser's Web Serial API (USB access). That only works
#   in Chrome / Edge / Opera and only on HTTPS or "localhost" -> the server binds
#   to 127.0.0.1. The flasher uses absolute paths (/js/..., /lib/...), so it
#   must be served from the root of a web server (serve.py does that).
#
# THE ONE FIRMWARE FOLDER
# -----------------------
# Everything lives in a single "firmware folder": your builds, original
# releases and the nRF52 helper files (erase image, bootloaders; fixed names,
# see below). It is --fw-dir PATH, or TARGET_DIR/fw when not given. The choice
# is remembered in TARGET_DIR/local-flasher.json, so later runs need no option.
# The web server presents this one folder under two URLs - no symbolic links
# are needed (they are unreliable on Windows):
#   /fw/<path>        used for the firmware files in the generated "releases"
#   /firmware/<name>  the flasher fetches the nRF52 helper files by fixed name
#                     from here, so they must lie in the TOP LEVEL of the folder
#
# Firmware files may be anywhere below the folder (sub-folders are scanned; the
# layout does not matter, e.g. <model>/ per device). File names must follow the
# MeshCore naming scheme
#       <build environment>-<version>[-merged].<bin|zip|uf2>
# where the build environment contains one of
#       companion_radio_ble | companion_radio_usb | companion_radio_wifi
#       repeater (or Repeater) | room_server
# Examples (the part in the middle becomes the VERSION in the drop-down):
#   Heltec_v3_repeater-v1.18.0.mups1.bin            -> version v1.18.0.mups1 (own build)
#   Heltec_v3_repeater-v1.18.0.mups1-merged.bin     -> same version (full image)
#   RAK_4631_repeater-v1.18.0.mups1.zip / .uf2      -> nRF52 update / UF2 file
#   (the tag "mups" is the default of --own-tag; use your own tag)
#   Heltec_v3_repeater-1.18.0-0123abc.bin           -> an original release
# Keep every file only once: files are grouped by role and version, not by
# folder, so the same name in two folders would be listed twice.
#
# The drop-down text gets a label so the kinds can be told apart:
#   "MUPS v1.18.0.mups1" | "MUPS-dev v1.18.0.dev-abc1234" (test build) |   (TAG = --own-tag)
#   "Custom <version>" (other modified firmware) |
#   "Original v1.18.0-81dbae5" (official release: version + 7-digit git hash).
# Order: own builds, own test builds, Custom, Original; the first entry is
# pre-selected. Within a kind the highest version number comes first (test
# builds: newest file).
#
# What is done with the files:
#   * the role (companion / repeater / room server) is taken from the name;
#   * the file is offered for every device whose entry in config.json has a
#     matching file-name pattern (see the report printed by the script);
#   * "-merged.bin" = full image (use with "Erase device"), plain ".bin" =
#     application update that keeps settings; "*.zip" = nRF52 update;
#   * the original update pattern "-<7 hex digits>.bin" (git hash) is widened
#     to also accept ".<TAG>N.bin" (--own-tag);
#   * for every Bluetooth companion entry a "WiFi" twin is added (pattern
#     *_companion_radio_wifi*), because config.json has no WiFi entries.
#     Twins without files stay hidden.
# Files that do not follow the scheme, or that no device pattern accepts, are
# listed by the script. They can still be flashed with the flasher's
# "Custom Firmware" button (file picker) - no naming rules apply there.
#
# nRF52 HELPER FILES
# ------------------
# The "Erase device" button and the bootloader update links of nRF52 devices use
# files with fixed names under /firmware/ that are not in the flasher repository
# (e.g. FLASH_ERASE_nrf52_softdevice_v6.zip, *_bootloader-*-OTAFIX*.zip). Save
# them once, downloaded from the official flasher, in the top level of the
# firmware folder. After every run the script lists the files still missing for
# the devices it offers.
#
# OFFLINE USE
# -----------
# After one online run the TARGET_DIR is self-contained. Start it without this
# script (only Python is needed):
#       cd TARGET_DIR
#       python serve.py            (Windows: py serve.py)
# Icons: the CSS loads the icon font "Material Symbols" from a file next to the
# CSS first and from cdn.jsdelivr.net second. The script saves the three .woff2
# files (about 1.1 MB) next to each beer.css so icons work offline. Without
# internet a warning is printed and the icons show as text.
# Still online-only: the radio presets of the config tool (api.meshcore.nz) and
# its map tiles (OpenStreetMap).
#
# USAGE
# -----
#   python setup_local_flasher.py [options] [TARGET_DIR] [PORT]
#   TARGET_DIR   default: ./meshcore-local-flasher
#   PORT         default: 8080 (or the remembered one)
#   options:
#     --fw-dir PATH     the firmware folder (see above)
#     --refresh         do not download anything; only re-scan the firmware
#                       folder and regenerate "releases" and "config.json" (run
#                       this after adding files; reload the browser page after)
#     --no-serve        prepare only, do not start the web server
#     --own-tag TAG     tag that marks YOUR firmware builds in the version part
#                       of the file name: "<model>-v1.18.0.TAG1.bin" is a release
#                       of your own line, "<model>-v1.18.0.dev-<hash>.bin" a test
#                       build. Default: mups (the author's firmware line). They
#                       are shown as "TAG ..." / "TAG-dev ..." (upper case).
#     --no-clock-patch  do not patch the console's "clock sync" command
#     --no-git          download ZIP archives instead of using git
#
# Downloading: with git installed (and TARGET_DIR empty/new or already a git
# clone) the script uses "git clone --depth 1" / "git pull"; otherwise it
# downloads the ZIP archive of the repository's main branch. A ZIP update only
# overwrites files; files you added stay (so do the patches' re-application: the
# patches are applied to the fresh files every time).
# Re-running is safe. Stop the server with Ctrl+C.
#
# REQUIREMENTS: Python 3.8+; internet access for the first run (git optional).
# =============================================================================
import argparse
import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

FLASHER_GIT = "https://github.com/meshcore-dev/flasher.meshcore.io.git"
CONFIG_GIT = "https://github.com/meshcore-dev/config.meshcore.io.git"
FLASHER_ZIP = "https://github.com/meshcore-dev/flasher.meshcore.io/archive/refs/heads/main.zip"
CONFIG_ZIP = "https://github.com/meshcore-dev/config.meshcore.io/archive/refs/heads/main.zip"
SETTINGS_NAME = "local-flasher.json"
DEFAULT_PORT = 8080


class SetupError(Exception):
    """A problem the user has to fix; printed without a traceback."""


# ----------------------------------------------------------------------------
# generated web server (written to TARGET_DIR/serve.py)
# ----------------------------------------------------------------------------
SERVE_PY = r'''#!/usr/bin/env python3
"""Local web server for the MeshCore web flasher (generated by setup_local_flasher.py).

Usage:  python serve.py [PORT]        (default: remembered port or 8080)

Serves the folder this file lives in on http://localhost:PORT (127.0.0.1 only;
Web Serial needs "localhost" or HTTPS). Two URL prefixes are mapped to the
firmware folder chosen during setup (stored in local-flasher.json):
    /fw/<path>        firmware files listed in the generated "releases" file
    /firmware/<name>  nRF52 helper files (erase image, bootloaders)
Why not a plain "python -m http.server"? (1) it cannot map /fw and /firmware to a
folder outside this one without symbolic links, which are unreliable on Windows;
(2) on Windows the MIME type of .js files comes from the registry and is
sometimes "text/plain", which browsers reject for ES modules; (3) it lets the
browser cache "releases"/"config.json", so a page reload would not show new
firmware; (4) the flasher's address bar changes to URLs like /heltec-v3/repeater/
and a reload (also the "Close" button) there needs index.html instead of a 404;
this is done for requests that accept text/html (page loads), not for assets.
Stop with Ctrl+C.
"""
import json
import os
import sys
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(ROOT, "local-flasher.json")

# MIME types that must be right for the flasher to start
MIME = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".woff": "font/woff", ".wasm": "application/wasm", ".map": "application/json",
    ".txt": "text/plain", ".md": "text/plain",
}


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def firmware_dir(settings):
    value = settings.get("firmware_dir") or "fw"
    return os.path.normpath(os.path.join(ROOT, value))   # absolute values stay absolute


class Handler(SimpleHTTPRequestHandler):
    fw_dir = ""

    def guess_type(self, path):
        base = os.path.basename(path)
        if base == "releases":                    # the generated release list has no extension
            return "application/json"
        ext = os.path.splitext(base)[1].lower()
        return MIME.get(ext) or super().guess_type(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def translate_path(self, path):
        clean = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        for prefix in ("/fw/", "/firmware/"):
            if clean.startswith(prefix):
                parts = [p for p in clean[len(prefix):].split("/") if p not in ("", ".", "..")]
                if any(c in p for p in parts for c in ("\\", ":")):   # no drive letters / backslashes
                    return os.path.join(self.fw_dir, "__invalid__")
                return os.path.join(self.fw_dir, *parts)
        result = super().translate_path(path)
        # The flasher uses "pretty" URLs such as /heltec-v3/repeater/ (browser history) and
        # reloads the page on "Close". A static server must then deliver index.html for
        # every unknown path without a file extension (the router in js/router.js says so).
        # Not for /config/ (the config tool is a separate site) and not for file names with
        # an extension, so a missing script or image is still an honest 404.
        # Address-bar URLs of the flasher (e.g. /heltec-v3/companion-usb/original-v1.18.0-81dbae5)
        # may contain dots, so the file name cannot tell them apart from assets. A browser
        # page load/reload asks for text/html; script/css/image requests do not.
        wants_html = "text/html" in (self.headers.get("Accept") or "")
        if not os.path.exists(result) and not clean.startswith("/config/") and wants_html:
            return os.path.join(self.directory, "index.html")
        return result


def main():
    settings = load_settings()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(settings.get("port") or 8080)
    Handler.fw_dir = firmware_dir(settings)
    if not os.path.isdir(Handler.fw_dir):
        print("WARNING: firmware folder not found: " + Handler.fw_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=ROOT))
    print(f"Serving on http://localhost:{port}  (flasher)  and  http://localhost:{port}/config/  (repeater setup)")
    print("Firmware folder: " + Handler.fw_dir)
    print("Open it in Chrome, Edge or Opera. Stop with Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
'''


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def info(msg):
    print(msg)


def warn(msg):
    print("WARNING: " + msg, file=sys.stderr)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:      # newline="": keep CRLF/LF as is
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def git_available():
    return shutil.which("git") is not None


def run_git(*args):
    try:
        subprocess.run(["git", *args], check=True)
    except subprocess.CalledProcessError as exc:
        raise SetupError("git failed: git " + " ".join(args)) from exc


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "meshcore-local-flasher"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        raise SetupError(f"download failed: {url} ({exc})") from exc


def extract_archive(data, dest):
    """Unpacks a GitHub ZIP archive into dest, without its top-level folder.

    Existing files are overwritten, other files in dest are left alone."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SetupError("the downloaded file is not a ZIP archive") from exc
    with archive:
        for member in archive.infolist():
            parts = [p for p in member.filename.split("/")[1:] if p]   # drop "<repo>-main/"
            if not parts or member.is_dir():
                continue
            if any(p in (".", "..") or "\\" in p or ":" in p for p in parts):
                continue                                               # never write outside dest
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def sync_repo(git_url, zip_url, dest, use_git_allowed):
    """Makes dest contain the current main branch of a repository."""
    has_content = os.path.isdir(dest) and bool(os.listdir(dest))
    is_clone = os.path.isdir(os.path.join(dest, ".git"))
    if use_git_allowed and git_available() and (is_clone or not has_content):
        if is_clone:
            info(f"Updating {dest} (git) ...")
            run_git("-C", dest, "checkout", "--", ".")     # drop old patches so the pull cannot be blocked
            run_git("-C", dest, "pull", "--ff-only")
        else:
            info(f"Cloning {git_url} ...")
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
            run_git("clone", "--depth", "1", git_url, dest)
    else:
        info(f"Downloading {zip_url} ...")
        os.makedirs(dest, exist_ok=True)
        extract_archive(download(zip_url), dest)


# ----------------------------------------------------------------------------
# patches
# ----------------------------------------------------------------------------
def patch_app_js(path, clock_patch):
    text = read_text(path)

    # 1. "Repeater Setup" opens the local config tool
    if "window.open('/config/'" in text:
        pass                                               # already patched
    elif "window.open('https://config.meshcore.io'" in text:
        text = text.replace("window.open('https://config.meshcore.io'", "window.open('/config/'")
        info(f"Patched {path}: Repeater Setup now opens /config/")
    else:
        raise SetupError(
            f"expected line not found in {path}. The upstream code has probably changed; patch "
            "manually: make the 'Repeater Setup' button open '/config/' instead of "
            "'https://config.meshcore.io'.")

    # 2. console: "clock sync" -> "time <epoch of this computer>" (see header)
    old = "await serialCon.instance.sendCommand(text);"
    new = ("await serialCon.instance.sendCommand(text.trim() === 'clock sync' "
           "? 'time ' + Math.floor(Date.now() / 1000) : text);")
    if not clock_patch:
        info("Console 'clock sync' patch skipped (--no-clock-patch).")
    elif "'clock sync'" in text:
        pass                                               # already patched
    elif old in text:
        text = text.replace(old, new)
        info(f"Patched {path}: console 'clock sync' now sends 'time <epoch>'")
    else:
        warn(f"console send line not found in {path}; 'clock sync' patch skipped.")

    write_text(path, text)


def download_fonts(css_files):
    """Saves the icon fonts next to every beer.css (they are tried before the CDN)."""
    for css in css_files:
        if not os.path.isfile(css):
            continue
        text = read_text(css)
        for url, name in sorted(set(re.findall(r"url\((https?://[^)]*/([^/)]+\.woff2))\)", text))):
            dest = os.path.join(os.path.dirname(css), name)
            if os.path.exists(dest):
                continue
            try:
                data = download(url)
                with open(dest + ".part", "wb") as out:
                    out.write(data)
                os.replace(dest + ".part", dest)
                info("Font saved: " + dest)
            except (SetupError, OSError) as exc:
                if os.path.exists(dest + ".part"):
                    os.remove(dest + ".part")
                warn(f"could not download {url} ({exc}); icons will be text offline.")


# ----------------------------------------------------------------------------
# settings and firmware folder
# ----------------------------------------------------------------------------
def load_settings(root):
    try:
        with open(os.path.join(root, SETTINGS_NAME), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(root, settings):
    with open(os.path.join(root, SETTINGS_NAME), "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")


def store_path(root, path):
    """Path as stored in the settings: relative to root when possible (portable), else absolute."""
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:                                     # other drive on Windows
        return path


def resolve_firmware_dir(root, cli_value, settings):
    if cli_value:
        return os.path.abspath(os.path.expanduser(cli_value))
    if settings.get("firmware_dir"):
        return os.path.normpath(os.path.join(root, settings["firmware_dir"]))
    old_link = os.path.join(root, "fw")
    if os.path.islink(old_link):                           # setup made by the former bash script
        return os.path.realpath(old_link)
    return os.path.join(root, "fw")


def migrate_old_firmware_dir(root, master):
    """An older version kept the helper files in a real folder ROOT/firmware.

    Moves its files into the firmware folder (never overwriting)."""
    old = os.path.join(root, "firmware")
    if not os.path.isdir(old) or os.path.islink(old) or os.path.realpath(old) == os.path.realpath(master):
        return
    for name in sorted(os.listdir(old)):
        src = os.path.join(old, name)
        dst = os.path.join(master, name)
        if os.path.exists(dst):
            warn(f"{dst} exists already; the copy in {old} is left there.")
        else:
            shutil.move(src, dst)
            info(f"Moved {name} into {master}")
    try:
        os.rmdir(old)
    except OSError:
        raise SetupError(f"{old} still holds files (see above); remove them by hand, then run again.")


# ----------------------------------------------------------------------------
# generator: config.json and releases
# ----------------------------------------------------------------------------
# <env>-<version>[-merged].<ext>; <env> ends with the first role keyword that
# is followed by "-" (environment names themselves may contain "-", e.g.
# LilyGo_T-Echo_companion_radio_ble).
NAME_RE = re.compile(
    r"^(?P<env>.+?(?:companion_radio_(?:ble|usb|wifi)|[Rr]epeater|room_server))"
    r"-(?P<version>.+?)(?:-merged)?\.(?P<ext>bin|zip|uf2)$"
)
OLD_UPDATE = "-[a-f0-9]{7}\\.bin"                          # regex text, as in config.json


def source_key(firmware):
    """Name of the release source key ("github", "github-ripple", ...) or None."""
    return next((k for k in firmware if k.startswith("github")), None)


def role_of(env):
    if "companion_radio" in env:
        return "companion"
    if env.lower().endswith("repeater"):
        return "repeater"
    return "room-server"


def adapt_config(root, own_tag):
    """Rewrites config.json in place (idempotent) and returns the parsed result."""
    new_update = "(?:-[a-f0-9]{7}|\\." + re.escape(own_tag) + "[0-9]+)\\.bin"   # also accepts ...<TAG>1.bin
    path = os.path.join(root, "config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    for device in cfg["device"]:
        # 1. drop static entries (fixed file names under /firmware/, not available here)
        device["firmware"] = [fw for fw in device["firmware"] if source_key(fw)]
        # 2. widen the update pattern
        for fw in device["firmware"]:
            files = fw[source_key(fw)].get("files", {})
            for kind, rx in files.items():
                files[kind] = rx.replace(OLD_UPDATE, new_update)
        # 3. WiFi twins of Bluetooth companion entries (once per device)
        if not any(fw.get("subTitle") == "WiFi" for fw in device["firmware"]):
            twins = []
            for fw in device["firmware"]:
                key = source_key(fw)
                if key == "github" and fw.get("role") == "companionBle" and any(
                    "_companion_radio_ble" in rx for rx in fw[key].get("files", {}).values()
                ):
                    twin = copy.deepcopy(fw)
                    twin["subTitle"] = "WiFi"
                    twin[key]["files"] = {
                        k: v.replace("_companion_radio_ble", "_companion_radio_wifi")
                        for k, v in twin[key]["files"].items()
                    }
                    twins.append(twin)
            device["firmware"].extend(twins)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent="\t", ensure_ascii=False)
    return cfg


def generate(root, fw_dir, own_tag):
    """Writes config.json and releases for the files in fw_dir and prints a report."""
    cfg = adapt_config(root, own_tag)

    # per role: all file-name patterns of all devices (to report unmatched files)
    patterns = {"companion": [], "repeater": [], "room-server": []}
    for device in cfg["device"]:
        for fw in device["firmware"]:
            src = fw[source_key(fw)]
            if src.get("type") in patterns:
                patterns[src["type"]].extend(re.compile(rx) for rx in src.get("files", {}).values())

    groups = {}      # (version, role) -> {"files": [...], "mtime": float, "dir": str}
    skipped = []     # not following the naming scheme
    unmatched = []   # recognised, but no device pattern accepts them

    for dirpath, _dirs, names in os.walk(fw_dir, followlinks=True):
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, fw_dir).replace(os.sep, "/")
            m = NAME_RE.match(name)
            if not m:
                skipped.append(rel)
                continue
            role = role_of(m["env"])
            if not any(rx.search(name) for rx in patterns[role]):
                unmatched.append(rel)
                continue
            g = groups.setdefault(
                (m["version"], role),
                {"files": [], "mtime": 0.0, "dir": os.path.dirname(rel) or "."},
            )
            # url must start with "/" so the flasher uses it as is
            g["files"].append({"name": name, "url": "/fw/" + urllib.parse.quote(rel)})
            g["mtime"] = max(g["mtime"], os.path.getmtime(path))

    # The drop-down shows the "version" text only, so it carries a label that tells
    # the kinds of firmware apart. The label depends on the file name's version part:
    #   v1.18.0.mups1         -> "MUPS v1.18.0.mups1"     (own release; "mups" = own tag)
    #   v1.18.0.dev-abc1234   -> "MUPS-dev v1.18.0.dev-abc1234"  (own test build)
    #   v1.18.0-81dbae5       -> "Original v1.18.0-81dbae5"  (official release: base
    #                            version + "-" + 7-digit git hash)
    #   anything else         -> "Custom <version>"        (other modified firmware)
    # Order in the drop-down (the first entry is pre-selected): own releases, own
    # test builds, Custom, Original. Inside a kind, higher version numbers come
    # first (test builds: newest file first).
    kinds = [  # (regex on version, label, sort rank)
        (re.compile(r"\." + re.escape(own_tag) + r"\d+$"), own_tag.upper(), 0),
        (re.compile(r"\.dev-[0-9a-f]+$"), own_tag.upper() + "-dev", 1),
        (re.compile(r"^v?\d+(?:\.\d+)*-[0-9a-f]{7}$"), "Original", 3),
    ]

    def kind_of(version):
        for rx, label, rank in kinds:
            if rx.search(version):
                return label, rank
        return "Custom", 2

    def sort_key(item):
        (version, _role), g = item
        _label, rank = kind_of(version)
        numbers = [] if rank == 1 else [-int(n) for n in re.findall(r"\d+", version)]
        return (rank, numbers, -g["mtime"])

    releases = [
        {
            "type": role,
            "version": f"{kind_of(version)[0]} {version}",
            "notes": f"Local files from fw/{g['dir']}",
            "files": g["files"],
        }
        for (version, role), g in sorted(groups.items(), key=sort_key)
    ]
    with open(os.path.join(root, "releases"), "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent="\t", ensure_ascii=False)

    # nRF52 devices also need fixed-name files that are NOT part of the flasher
    # repository and are served by the official site from /firmware/: the
    # "Erase device" image (device["erase"], a .zip; the download menu also offers
    # its .uf2 twin) and the bootloader update files (device["bootloader"]).
    # Only devices that have at least one offered firmware file are checked.
    offered_names = {"companion": [], "repeater": [], "room-server": []}
    for r in releases:
        offered_names[r["type"]].extend(f["name"] for f in r["files"])

    static_needed = {}   # file name -> list of device names that use it
    for device in cfg["device"]:
        if device.get("type") != "nrf52":
            continue
        in_use = False
        for fw in device["firmware"]:
            key = source_key(fw)
            src = fw[key] if key else {}
            rxs = [re.compile(rx) for rx in src.get("files", {}).values()]
            if any(rx.search(n) for n in offered_names.get(src.get("type"), []) for rx in rxs):
                in_use = True
        if not in_use:
            continue
        names = list(device.get("bootloader", []))
        if device.get("erase"):
            names += [device["erase"], device["erase"].replace(".zip", ".uf2")]
        for n in names:
            static_needed.setdefault(n, []).append(device["name"])
    static_missing = {n: d for n, d in static_needed.items()
                      if not os.path.isfile(os.path.join(fw_dir, n))}

    # ---- report
    print(f"Firmware folder: {fw_dir}")
    print(f"{sum(len(r['files']) for r in releases)} file(s) offered in "
          f"{len({r['version'] for r in releases})} version(s)")
    for r in releases:
        print(f"  {r['version']:<34} {r['type']:<12} {len(r['files'])} file(s)")
    if unmatched:
        print("NOT offered in the device list (no device in config.json accepts these names;")
        print("flash them with the 'Custom Firmware' button):")
        for rel in unmatched:
            print("  " + rel)
    skipped = [rel for rel in skipped if os.path.basename(rel) not in static_needed]  # helper files are expected
    if skipped:
        print("Ignored (name does not follow <env>-<version>[-merged].<bin|zip|uf2>):")
        for rel in skipped:
            print("  " + rel)
    if not releases:
        print("No usable firmware found - the device list will be empty; 'Custom Firmware' still works.")
    if static_needed:
        print(f"nRF52 helper files in the top level of the firmware folder: "
              f"{len(static_needed) - len(static_missing)} of {len(static_needed)} present")
    if static_missing:
        print("MISSING in " + fw_dir + " (Erase device / bootloader links do not work without them).")
        print("Download them once from the official flasher (https://flasher.meshcore.io; the device's")
        print("download menu lists the .uf2/.zip files; the erase .zip is not linked there) and")
        print("save them in that folder:")
        for name, devs in sorted(static_missing.items()):
            print(f"  {name}   <- {', '.join(devs)}")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def load_serve_module(root):
    """Imports the generated serve.py, so serving has a single implementation."""
    spec = importlib.util.spec_from_file_location("local_flasher_serve", os.path.join(root, "serve.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Set up and serve a local copy of the MeshCore web flasher (see the header of this file).")
    p.add_argument("target", nargs="?", default="meshcore-local-flasher",
                   help="folder for the local flasher (default: ./meshcore-local-flasher)")
    p.add_argument("port", nargs="?", type=int, help=f"port (default: remembered or {DEFAULT_PORT})")
    p.add_argument("--fw-dir", metavar="PATH", help="the firmware folder (builds, originals, nRF52 helper files)")
    p.add_argument("--refresh", action="store_true",
                   help="no download; only re-scan the firmware folder and regenerate releases/config.json")
    p.add_argument("--no-serve", action="store_true", help="prepare only, do not start the web server")
    p.add_argument("--own-tag", metavar="TAG", help="tag of your own builds (default: mups)")
    p.add_argument("--no-clock-patch", action="store_true", help="do not patch the console's 'clock sync' command")
    p.add_argument("--no-git", action="store_true", help="download ZIP archives instead of using git")
    p.add_argument("--flasher-archive", help=argparse.SUPPRESS)      # test hook: ZIP URL of the flasher
    p.add_argument("--config-archive", help=argparse.SUPPRESS)       # test hook: ZIP URL of the config tool
    args = p.parse_args(argv)
    if args.own_tag is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", args.own_tag):
        p.error("--own-tag needs a plain word (letters/digits)")
    return args


def run(argv):
    args = parse_args(argv)
    root = os.path.abspath(os.path.expanduser(args.target))
    settings = load_settings(root)

    own_tag = args.own_tag or settings.get("own_tag") or "mups"
    clock_patch = False if args.no_clock_patch else settings.get("clock_patch", True)
    port = args.port or settings.get("port") or DEFAULT_PORT

    # 1./2. download the flasher and the config tool
    if args.refresh:
        if not os.path.isfile(os.path.join(root, "js", "app.js")):
            raise SetupError(f"--refresh needs an existing setup in {root} (run without --refresh first).")
    else:
        sync_repo(FLASHER_GIT, args.flasher_archive or FLASHER_ZIP, root, not args.no_git and not args.flasher_archive)
        sync_repo(CONFIG_GIT, args.config_archive or CONFIG_ZIP, os.path.join(root, "config"),
                  not args.no_git and not args.config_archive)

    # 3. patches
    patch_app_js(os.path.join(root, "js", "app.js"), clock_patch)

    # 3c. icon fonts next to the CSS files (offline use)
    download_fonts([os.path.join(root, "css", "beer.css"), os.path.join(root, "config", "lib", "css", "beer.css")])

    # 4. the one firmware folder
    fw_dir = resolve_firmware_dir(root, args.fw_dir, settings)
    if args.fw_dir and not os.path.isdir(fw_dir):
        raise SetupError(f"--fw-dir '{fw_dir}' is not a directory.")
    os.makedirs(fw_dir, exist_ok=True)
    migrate_old_firmware_dir(root, fw_dir)

    # 5. remember the choices, write the server, generate releases and config.json
    save_settings(root, {"firmware_dir": store_path(root, fw_dir), "own_tag": own_tag,
                         "clock_patch": clock_patch, "port": port})
    write_text(os.path.join(root, "serve.py"), SERVE_PY)
    generate(root, fw_dir, own_tag)

    # 6. serve
    print()
    if args.no_serve:
        print(f"Prepared in {root} (not serving, --no-serve given).")
        print(f"Start later with:  cd \"{root}\" && python serve.py")
        return 0
    sys.argv = [os.path.join(root, "serve.py"), str(port)]   # serve.py reads the port from argv[1]
    load_serve_module(root).main()
    return 0


def main():
    for stream in (sys.stdout, sys.stderr):                # never crash on odd characters in file names
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        return run(sys.argv[1:])
    except SetupError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
