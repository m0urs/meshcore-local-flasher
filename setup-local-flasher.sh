#!/usr/bin/env bash
# =============================================================================
# setup-local-flasher.sh
#
# Sets up a LOCAL copy of the MeshCore web flasher AND the MeshCore
# "Repeater Setup" (config) web tool, and serves both on http://localhost.
# Firmware files stored on your disk (your own builds, original firmware,
# other people's modified firmware) are offered in the flasher's device list.
#
# BACKGROUND (why this script is needed)
# --------------------------------------
# * The flasher (github.com/meshcore-dev/flasher.meshcore.io) is a static web
#   app (HTML/JS). At start-up it requests "/releases?repo=..." to get the list
#   of firmware releases. That endpoint is provided by the server behind
#   flasher.meshcore.io and is NOT part of the repository; if the request does
#   not return HTTP 200 the page does not start. This script generates a file
#   named "releases" from the firmware files in the local "fw/" folder. The
#   simple Python web server ignores the "?repo=..." query string and serves
#   that file.
# * The flasher's "config.json" decides which firmware is offered for which
#   device by matching FILE NAMES against regular expressions. Entries that
#   list fixed file names (e.g. "KISS Modem", Ripple GUI) point to files under
#   /firmware/ that are not part of the repository, so they could not be
#   flashed locally. This script therefore removes those entries from the
#   local config.json; only firmware found in fw/ is offered.
# * The flasher has a "Repeater Setup" button that normally opens
#   https://config.meshcore.io in a popup. This script clones the config tool
#   (github.com/meshcore-dev/config.meshcore.io, also static) into "config/"
#   and patches ONE line in js/app.js so the button opens "/config/" instead.
# * The serial console's "clock sync" command cannot work over USB: the device
#   takes the time from the SENDER of the command (meant for remote admin over
#   LoRa), and the serial interface has no sender time (it is always 0). The
#   official firmware answers "ERR: clock cannot go backwards"; firmware that
#   allows setting the clock backwards (PR #1349 of the MeshCore repository) would set the
#   clock to 1970. This script patches the flasher's console (js/app.js) so
#   that typing "clock sync" sends "time <epoch seconds of this computer>"
#   instead. The device receives a normal "time" command; the official
#   firmware only accepts it if it moves the clock forward. This applies to the
#   flasher's own console only, not to the console inside the config tool.
# * The flasher uses absolute paths (/js/..., /lib/...), so it must be served
#   from the root of a web server. The config tool uses relative paths.
# * Web Serial (USB access) only works in Chrome / Edge / Opera and only on
#   HTTPS or "localhost" -> the server binds to 127.0.0.1.
#
# THE ONE FIRMWARE FOLDER ("fw/" = "firmware/")
# ---------------------------------------------
# Everything lives in a single master folder: your builds, original releases and
# the nRF52 helper files (erase image, bootloaders; fixed names, see below).
# The master folder is --fw-dir PATH (e.g. the output folder of your build
# script) or, without that option, TARGET_DIR/fw. TARGET_DIR/fw and
# TARGET_DIR/firmware are both links to it:
#   fw/        scanned for firmware files; sub-folders are scanned too, the
#              layout does not matter (e.g. <model>/ for builds, ORIGINAL/)
#   firmware/  URL /firmware/<name>: the flasher fetches the nRF52 helper files by
#              fixed name from here, so they must lie in the TOP LEVEL of the
#              master folder, not in a sub-folder.
# If an older run left a separate real folder TARGET_DIR/firmware, its files
# are moved into the master folder (existing files are never overwritten).
#
# File names must follow the MeshCore naming scheme
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
# The drop-down text gets a label so the kinds can be told apart:
#   "MUPS v1.18.0.mups1" | "MUPS-dev v1.18.0.dev-abc1234" (test build) |   (TAG = --own-tag)
#   "Custom <version>" (other modified firmware) |
#   "Original v1.18.0-81dbae5" (official release: version + 7-digit git hash).
# Order: own builds, own test builds, Custom, Original; the first entry is
# pre-selected. Within a kind the highest version number comes first (test
# builds: newest file).
# Original files may be kept in a sub-folder (e.g. fw/original/) - the folder
# does not matter for the order.
#
# What is done with the files:
#   * the role (companion / repeater / room server) is taken from the name;
#   * the file is offered for every device whose entry in config.json has a
#     matching file-name pattern (see the report printed by the script);
#   * "-merged.bin" = full image (use with "Erase device"), plain ".bin" =
#     application update that keeps settings; "*.zip" = nRF52 update;
#   * the original update pattern "-<7 hex digits>.bin" (git hash) is widened
#     to also accept ".<TAG>N.bin" (--own-tag), so "...-v1.18.0.mups1.bin" is
#     recognised;
#   * for every Bluetooth companion entry a "WiFi" twin is added (pattern
#     *_companion_radio_wifi*), because config.json has no WiFi entries.
#     Twins without files stay hidden.
# Files that do not follow the scheme, or that no device pattern accepts, are
# listed by the script. They can still be flashed with the flasher's
# "Custom Firmware" button (file picker) - no naming rules apply there.
#
# WHAT STAYS ONLINE / LIMITS
# --------------------------
# * Icons: both tools' CSS (beer.css) loads the icon font "Material Symbols"
#   first from a file next to the CSS and, if that is missing, from
#   cdn.jsdelivr.net. The script downloads the three .woff2 files (about
#   1.1 MB) once and stores them next to each beer.css (css/ and
#   config/lib/css/), so the icons also work offline. If the download fails
#   (no internet), a warning is printed; everything still works, but icons are
#   shown as plain text until the fonts are in place (re-run the script, or
#   with --refresh, while online).
# * The config tool fetches radio presets from https://api.meshcore.nz/...
#   after connecting, and map tiles from tile.openstreetmap.org.
# * The nRF52 "Erase device" button and the OTAFIX bootloader links use files
#   under /firmware/ (not in the repository). They do not work locally unless
#   you save those files in the top level of the master folder yourself (e.g. downloaded once
#   from the official flasher). The script creates that folder and, after every
#   run, lists the files that are still missing for the offered nRF52 devices.
#
# STARTING LATER / OFFLINE, WITHOUT THIS SCRIPT
# ---------------------------------------------
# After one successful run the folder TARGET_DIR is self-contained (it can be
# copied to a USB stick; the other computer only needs Python 3). Start it with
#     cd TARGET_DIR
#     python3 -m http.server 8080 --bind 127.0.0.1
# and open http://localhost:8080 in Chrome/Edge. If fw/ is a link created with
# --fw-dir, the target of the link must exist on that computer, too.
# Still online-only: radio presets (api.meshcore.nz) and map tiles of the
# config tool.
#
# USAGE
# -----
#   ./setup-local-flasher.sh [options] [TARGET_DIR] [PORT]
#
#   TARGET_DIR   default: ./meshcore-local-flasher
#   PORT         default: 8080
#   options:
#     --fw-dir PATH  use PATH as the firmware folder (TARGET_DIR/fw becomes a
#                    symbolic link to it)
#     --refresh      do not touch git; only re-scan fw/ and regenerate
#                    "releases" and "config.json" (run this after adding files;
#                    reload the browser page afterwards)
#     --no-serve     prepare only, do not start the web server
#     --own-tag TAG  tag that marks YOUR firmware builds in the version part of
#                    the file name: "<model>-v1.18.0.TAG1.bin" is a release of
#                    your own line, "<model>-v1.18.0.dev-<hash>.bin" a test
#                    build. Default: mups (the author's firmware line). The
#                    drop-down shows them as "TAG ..." / "TAG-dev ..." (upper
#                    case) and lists them first.
#     --no-clock-patch  do not patch the console's "clock sync" command (see
#                    BACKGROUND)
#
# Re-running is safe: clones are updated (git pull), the patches are
# re-applied, then the server is started. Stop it with Ctrl+C. Open
# http://localhost:PORT in Chrome/Edge.
#
# REQUIREMENTS: bash, git, python3, internet access for cloning.
# =============================================================================
set -euo pipefail

FLASHER_URL="https://github.com/meshcore-dev/flasher.meshcore.io.git"
CONFIG_URL="https://github.com/meshcore-dev/config.meshcore.io.git"

# ---- argument parsing -------------------------------------------------------
SERVE=1
REFRESH=0
FW_SRC=""
OWN_TAG="mups"
CLOCK_PATCH=1
POSITIONAL=()
while (( $# )); do
  case "$1" in
    --no-serve) SERVE=0 ;;
    --refresh)  REFRESH=1 ;;
    --no-clock-patch) CLOCK_PATCH=0 ;;
    --own-tag)
      [[ $# -ge 2 && "$2" =~ ^[A-Za-z][A-Za-z0-9]*$ ]] || { echo "ERROR: --own-tag needs a plain word (letters/digits)." >&2; exit 1; }
      OWN_TAG="$2"; shift ;;
    --fw-dir)
      [[ $# -ge 2 ]] || { echo "ERROR: --fw-dir needs a path." >&2; exit 1; }
      FW_SRC="$2"; shift ;;
    -*) echo "ERROR: unknown option '$1' (see the header of this script)." >&2; exit 1 ;;
    *)  POSITIONAL+=("$1") ;;
  esac
  shift
done
TARGET_DIR="${POSITIONAL[0]:-./meshcore-local-flasher}"
PORT="${POSITIONAL[1]:-8080}"

# ---- prerequisites ----------------------------------------------------------
for tool in git python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: '$tool' is required but not installed." >&2
    exit 1
  fi
done

# clone_or_update <url> <directory>
# Clones the repository if the directory does not exist yet; otherwise it
# discards local modifications of tracked files (so a previous patch cannot
# block the update) and fast-forwards to the latest upstream commit.
# Untracked files (fw/, releases, local-setup.py) are not touched.
clone_or_update() {
  local url="$1" dir="$2"
  if [[ -d "$dir/.git" ]]; then
    echo "Updating $dir ..."
    git -C "$dir" checkout -- .
    git -C "$dir" pull --ff-only
  else
    echo "Cloning $url -> $dir ..."
    git clone --depth 1 "$url" "$dir"
  fi
}

# ---- 1./2. clone flasher and config tool (skipped with --refresh) -----------
if [[ "$REFRESH" -eq 0 ]]; then
  clone_or_update "$FLASHER_URL" "$TARGET_DIR"
  clone_or_update "$CONFIG_URL" "$TARGET_DIR/config"
elif [[ ! -f "$TARGET_DIR/js/app.js" ]]; then
  echo "ERROR: --refresh needs an existing setup in $TARGET_DIR (run without --refresh first)." >&2
  exit 1
fi

# ---- 3. patch "Repeater Setup" to use the local config tool -----------------
APP_JS="$TARGET_DIR/js/app.js"
if grep -q "window.open('/config/'" "$APP_JS"; then
  :  # already patched (e.g. --refresh)
elif grep -q "window.open('https://config.meshcore.io'" "$APP_JS"; then
  sed -i "s#window.open('https://config.meshcore.io'#window.open('/config/'#" "$APP_JS"
  echo "Patched $APP_JS: Repeater Setup now opens /config/"
else
  echo "ERROR: expected line not found in $APP_JS." >&2
  echo "       The upstream code has probably changed; patch manually:" >&2
  echo "       make the 'Repeater Setup' button open '/config/' instead of" >&2
  echo "       'https://config.meshcore.io'." >&2
  exit 1
fi

# ---- 3b. patch the console: "clock sync" -> "time <epoch of this computer>" ---
# See the header for the reason. The original line in js/app.js (sendCommand) is
#     await serialCon.instance.sendCommand(text);
if [[ "$CLOCK_PATCH" -eq 0 ]]; then
  echo "Console 'clock sync' patch skipped (--no-clock-patch)."
elif grep -q "'clock sync'" "$APP_JS"; then
  :  # already patched (e.g. --refresh)
elif grep -q "await serialCon.instance.sendCommand(text);" "$APP_JS"; then
  sed -i "s#await serialCon.instance.sendCommand(text);#await serialCon.instance.sendCommand(text.trim() === 'clock sync' ? 'time ' + Math.floor(Date.now() / 1000) : text);#" "$APP_JS"
  echo "Patched $APP_JS: console 'clock sync' now sends 'time <epoch>'"
else
  echo "WARNING: console send line not found in $APP_JS; 'clock sync' patch skipped." >&2
fi

# ---- 3c. icon fonts next to the CSS files (offline use) ------------------------
# Both beer.css files contain  src:url(material-symbols-X.woff2) ...,
# url(https://cdn.jsdelivr.net/.../material-symbols-X.woff2): the browser tries
# the local file first. Download every .woff2 referenced by a beer.css once and
# save it next to that CSS file. Existing files are kept; failures only warn.
python3 -I - "$TARGET_DIR/css/beer.css" "$TARGET_DIR/config/lib/css/beer.css" <<'PYEOF'
import os
import re
import sys
import urllib.request

for css in sys.argv[1:]:
    if not os.path.isfile(css):
        continue
    with open(css, encoding="utf-8") as fh:
        text = fh.read()
    # full URLs of font files, e.g. https://cdn.jsdelivr.net/npm/...woff2
    for url, name in sorted(set(re.findall(r"url\((https?://[^)]*/([^/)]+\.woff2))\)", text))):
        dest = os.path.join(os.path.dirname(css), name)
        if os.path.exists(dest):
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as resp, open(dest + ".part", "wb") as out:
                out.write(resp.read())
            os.replace(dest + ".part", dest)
            print(f"Font saved: {dest}")
        except Exception as exc:  # no internet etc. -> keep going
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
            print(f"WARNING: could not download {url} ({exc}); icons will be text offline.", file=sys.stderr)
PYEOF

# rel_link <master folder>
# Prints the path of the master folder RELATIVE to TARGET_DIR, e.g. "../meshcore/Firmware-MU",
# so the links fw/ and firmware/ stay valid when both folders are moved or copied to
# another machine / user name together (as long as their relative position is the same).
rel_link() {
  realpath --relative-to="$(cd "$TARGET_DIR" && pwd -P)" "$1"
}

# ---- 4. ONE firmware folder ----------------------------------------------------
# All firmware lives in a single "master" folder:
#   * your builds and original releases (any sub-folders; scanned by the script)
#   * the fixed-name nRF52 helper files (erase image, bootloaders) in its top level
# The flasher reaches it under two names, both pointing to the master folder:
#   TARGET_DIR/fw        scanned for firmware files (see local-setup.py)
#   TARGET_DIR/firmware  URL /firmware/<name>: files the flasher fetches by fixed name
# Master folder = --fw-dir PATH if given, otherwise TARGET_DIR/fw itself.
if [[ -n "$FW_SRC" ]]; then
  [[ -d "$FW_SRC" ]] || { echo "ERROR: --fw-dir '$FW_SRC' is not a directory." >&2; exit 1; }
  MASTER="$(cd "$FW_SRC" && pwd)"
  if [[ -e "$TARGET_DIR/fw" && ! -L "$TARGET_DIR/fw" ]]; then
    # a real folder: replace it only if it is empty (rmdir refuses otherwise)
    rmdir "$TARGET_DIR/fw" 2>/dev/null || {
      echo "ERROR: $TARGET_DIR/fw is a non-empty folder; move its files away or drop --fw-dir." >&2
      exit 1
    }
  fi
  ln -sfn "$(rel_link "$MASTER")" "$TARGET_DIR/fw"
else
  mkdir -p "$TARGET_DIR/fw"
  MASTER="$(cd "$TARGET_DIR/fw" && pwd)"
fi

# An older version of this script kept the helper files in a separate real folder
# TARGET_DIR/firmware. Move its files into the master folder (never overwriting:
# mv -n), then remove the old folder if it is empty now.
if [[ -d "$TARGET_DIR/firmware" && ! -L "$TARGET_DIR/firmware" ]]; then
  for f in "$TARGET_DIR/firmware"/* "$TARGET_DIR/firmware"/.[!.]*; do
    [[ -e "$f" ]] || continue
    if [[ -e "$MASTER/$(basename "$f")" ]]; then
      echo "NOTE: $MASTER/$(basename "$f") exists already; the copy in $TARGET_DIR/firmware is left there." >&2
    else
      mv -n "$f" "$MASTER/"
      echo "Moved $(basename "$f") into $MASTER"
    fi
  done
  rmdir "$TARGET_DIR/firmware" 2>/dev/null || {
    echo "ERROR: $TARGET_DIR/firmware still holds files (see NOTE above); remove them by hand, then run again." >&2
    exit 1
  }
fi
ln -sfn "$(rel_link "$MASTER")" "$TARGET_DIR/firmware"
echo "Firmware folder (fw/ and firmware/): $MASTER"

# ---- 5. generator for "releases" and the local config.json -------------------
# Written to a file so it can also be run by hand:
#   python3 TARGET_DIR/local-setup.py TARGET_DIR
cat > "$TARGET_DIR/local-setup.py" <<'PYEOF'
#!/usr/bin/env python3
"""Generate the local "releases" file and adapt config.json for offline use.

Input : <root>/fw/**   firmware files (see header of setup-local-flasher.sh)
        <root>/config.json  the flasher's device/firmware catalog
Output: <root>/releases     JSON list read by the flasher instead of the
                            (missing) /releases backend endpoint
        <root>/config.json  rewritten in place (idempotent):
          1. entries with fixed file names (no "github*" key) are removed,
             because their files are not available locally;
          2. the update pattern "-<7 hex>.bin" also accepts ".<TAG>N.bin"
             (TAG = argv[2], default "mups");
          3. a "WiFi" twin is added to every Bluetooth companion entry.
Prints a report of what is offered and of files that are not.
"""
import copy
import json
import os
import re
import sys
import urllib.parse

ROOT = sys.argv[1]
OWN_TAG = sys.argv[2] if len(sys.argv) > 2 else "mups"   # marks the user's own builds
FW_DIR = os.path.join(ROOT, "fw")
CONFIG = os.path.join(ROOT, "config.json")
RELEASES = os.path.join(ROOT, "releases")

# <env>-<version>[-merged].<ext>; <env> ends with the first role keyword that
# is followed by "-" (environment names themselves may contain "-", e.g.
# LilyGo_T-Echo_companion_radio_ble).
NAME_RE = re.compile(
    r"^(?P<env>.+?(?:companion_radio_(?:ble|usb|wifi)|[Rr]epeater|room_server))"
    r"-(?P<version>.+?)(?:-merged)?\.(?P<ext>bin|zip|uf2)$"
)
OLD_UPDATE = "-[a-f0-9]{7}\\.bin"                      # regex text, as in config.json
NEW_UPDATE = "(?:-[a-f0-9]{7}|\\." + re.escape(OWN_TAG) + "[0-9]+)\\.bin"   # also accepts ...<TAG>1.bin


def source_key(firmware):
    """Name of the release source key ("github", "github-ripple", ...) or None."""
    return next((k for k in firmware if k.startswith("github")), None)


def role_of(env):
    if "companion_radio" in env:
        return "companion"
    if env.lower().endswith("repeater"):
        return "repeater"
    return "room-server"


# ---------------------------------------------------------------- config.json
with open(CONFIG, encoding="utf-8") as fh:
    cfg = json.load(fh)

for device in cfg["device"]:
    # 1. drop static entries (fixed file names under /firmware/, not available here)
    device["firmware"] = [fw for fw in device["firmware"] if source_key(fw)]
    # 2. widen the update pattern
    for fw in device["firmware"]:
        files = fw[source_key(fw)].get("files", {})
        for kind, rx in files.items():
            files[kind] = rx.replace(OLD_UPDATE, NEW_UPDATE)
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

with open(CONFIG, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent="\t", ensure_ascii=False)

# per role: all file-name patterns of all devices (to report unmatched files)
patterns = {"companion": [], "repeater": [], "room-server": []}
for device in cfg["device"]:
    for fw in device["firmware"]:
        src = fw[source_key(fw)]
        if src.get("type") in patterns:
            patterns[src["type"]].extend(re.compile(rx) for rx in src.get("files", {}).values())

# ------------------------------------------------------------------- releases
groups = {}      # (version, role) -> {"files": [...], "mtime": float, "dir": str}
skipped = []     # not following the naming scheme
unmatched = []   # recognised, but no device pattern accepts them

for dirpath, _dirs, names in os.walk(FW_DIR, followlinks=True):
    for name in sorted(names):
        if name.startswith("."):
            continue
        path = os.path.join(dirpath, name)
        rel = os.path.relpath(path, FW_DIR).replace(os.sep, "/")
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
#   v1.18.0.mups1         -> "MUPS v1.18.0.mups1"     (own release; "mups" = OWN_TAG)
#   v1.18.0.dev-abc1234   -> "MUPS-dev v1.18.0.dev-abc1234"  (own test build)
#   v1.18.0-81dbae5       -> "Original v1.18.0-81dbae5"  (official release: base
#                            version + "-" + 7-digit git hash)
#   anything else         -> "Custom <version>"        (other modified firmware)
# Order in the drop-down (the first entry is pre-selected): own releases, own
# test builds, Custom, Original. Inside a kind, higher version numbers come
# first (test builds: newest file first).
KINDS = [  # (regex on version, label, sort rank)
    (re.compile(r"\." + re.escape(OWN_TAG) + r"\d+$"), OWN_TAG.upper(), 0),
    (re.compile(r"\.dev-[0-9a-f]+$"), OWN_TAG.upper() + "-dev", 1),
    (re.compile(r"^v?\d+(?:\.\d+)*-[0-9a-f]{7}$"), "Original", 3),
]


def kind_of(version):
    """Returns (label, rank) for a version string, see KINDS above."""
    for rx, label, rank in KINDS:
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
with open(RELEASES, "w", encoding="utf-8") as fh:
    json.dump(releases, fh, indent="\t", ensure_ascii=False)

# ----------------------------------------------------- static nRF52 files
# nRF52 devices also need fixed-name files that are NOT part of the flasher
# repository and are served by the official site from /firmware/: the
# "Erase device" image (device["erase"], a .zip; the download menu also offers
# its .uf2 twin) and the bootloader update files (device["bootloader"]).
# Only devices that have at least one offered firmware file are checked.
STATIC_DIR = os.path.join(ROOT, "firmware")
os.makedirs(STATIC_DIR, exist_ok=True)
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
                  if not os.path.isfile(os.path.join(STATIC_DIR, n))}

# --------------------------------------------------------------------- report
print(f"fw/: {sum(len(r['files']) for r in releases)} file(s) offered in "
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
    print(f"nRF52 helper files in firmware/: {len(static_needed) - len(static_missing)} of "
          f"{len(static_needed)} present")
if static_missing:
    print("MISSING in " + STATIC_DIR + " (Erase device / bootloader links do not work without them).")
    print("Download them once from the official flasher (https://flasher.meshcore.io; the device's")
    print("download menu lists the .uf2/.zip files; the erase .zip is not linked there) and")
    print("save them in that folder:")
    for name, devs in sorted(static_missing.items()):
        print(f"  {name}   <- {', '.join(devs)}")
PYEOF

python3 -I "$TARGET_DIR/local-setup.py" "$TARGET_DIR" "$OWN_TAG"

# ---- 6. serve ----------------------------------------------------------------
if [[ "$SERVE" -eq 0 ]]; then
  echo "Prepared in $TARGET_DIR (not serving, --no-serve given)."
  echo "Start later with:  cd '$TARGET_DIR' && python3 -m http.server $PORT --bind 127.0.0.1"
  exit 0
fi

echo
echo "Serving on http://localhost:$PORT  (flasher)  and  http://localhost:$PORT/config/  (repeater setup)"
echo "Open it in Chrome or Edge. Stop with Ctrl+C."
cd "$TARGET_DIR"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
