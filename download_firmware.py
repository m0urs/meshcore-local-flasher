#!/usr/bin/env python3
# =============================================================================
# download_firmware.py
#
# Downloads the CURRENT original MeshCore firmware for the devices you use into
# your firmware folder, so the local web flasher (setup_local_flasher.py) can
# offer it. It replaces the manual "open GitHub, find the release, click each
# file" routine.
#
# Pure Python 3.8+ (standard library only): runs the same on Windows, Linux,
# macOS and WSL. Needs internet access to api.github.com and github.com only
# while it runs.
#
# QUICK START
#     python3 download_firmware.py --dry-run     (show what would be downloaded)
#     python3 download_firmware.py               (download it)
#     python3 download_firmware.py --refresh     (download, then rebuild the flasher's list)
# Run it from the folder that contains "meshcore-local-flasher" (the target
# folder of setup_local_flasher.py); it finds your firmware folder through
# that folder's "local-flasher.json". Or name the folder: --fw-dir PATH.
#
# HOW IT WORKS
# ------------
# * The official firmware is published as GitHub releases of
#   github.com/meshcore-dev/MeshCore, one release per role (tags like
#   "repeater-v1.18.0", "companion-v1.18.0", "room-server-v1.18.0"). Every
#   release contains one file set per device, named
#       <Device>_<role>-<version>-<7 hex digits>[-merged].<bin|uf2|zip>
#   for example  Heltec_v3_repeater-v1.18.0-81dbae5.bin  and
#                Heltec_v3_repeater-v1.18.0-81dbae5-merged.bin.
#   Everything before "-v<version>" is called the STEM here
#   ("Heltec_v3_repeater"); the stem identifies device + role.
# * The script asks the GitHub API (read-only, no login) for the release list.
# * Which devices do you want? By default: the ones you already have. It scans
#   your firmware folder (all sub folders) for original files named like the
#   above and remembers every stem together with the file kinds you have of it
#   (plain .bin, -merged.bin, .uf2, .zip). For each stem it looks for the
#   newest version in the releases and downloads the same kinds. Your own
#   builds (e.g. "...-v1.18.0.mups1.bin") do not match this naming and are
#   never touched or replaced.
# * New devices: --add TEXT picks every stem that contains TEXT (upper/lower
#   case does not matter), e.g. --add heltec_v4 --role repeater. All file
#   kinds of the newest version are downloaded. --list shows what exists.
# * Files go to  <firmware folder>/<stem>/<file name>  (or to the folder where
#   you keep that stem already). A file whose name already exists anywhere in
#   the firmware folder is skipped, so running the script again is harmless.
# * Safety: each file is first saved as "<name>.part", checked against the size
#   and SHA-256 checksum that GitHub reports for it, and only then renamed. A
#   broken download therefore never ends up as a firmware file.
# * --helpers: nRF52 devices need a few fixed-name helper files (erase image,
#   bootloader) in the TOP level of the firmware folder. With this option the
#   script reads the target folder's config.json, works out which helper files
#   your nRF52 devices need and downloads missing ones from
#   https://flasher.meshcore.io/firmware/ (no checksum is published for them).
# * GitHub allows 60 unauthenticated API requests per hour; this script uses
#   one to three per run. For more, set the environment variable GITHUB_TOKEN
#   (any token, no permissions needed).
#
# OPTIONS
#   --fw-dir PATH     firmware folder (default: read from <target>/local-flasher.json)
#   --target DIR      target folder of setup_local_flasher.py (default: ./meshcore-local-flasher)
#   --add TEXT        also fetch stems containing TEXT (repeatable)
#   --role ROLE       limit --add and --list to repeater, companion or room-server
#   --list            only list the devices available in the newest releases
#   --dry-run         show what would be downloaded, change nothing
#   --helpers         also fetch missing nRF52 helper files
#   --prerelease      also consider pre-releases (default: only full releases)
#   --refresh         afterwards run "setup_local_flasher.py --refresh --no-serve"
#   --repo OWNER/NAME release source (default: meshcore-dev/MeshCore)
# =============================================================================
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "meshcore-dev/MeshCore"
API = "https://api.github.com"                  # replaced by the hidden --api-url in tests
HELPER_BASE = "https://flasher.meshcore.io/firmware/"
SETTINGS_NAME = "local-flasher.json"            # written by setup_local_flasher.py
DEFAULT_TARGET = "meshcore-local-flasher"

# <stem>-v<version>-<7 hex>[-merged].<ext>   (no mups/dev builds: they have no hash part)
ORIGINAL_RE = re.compile(
    r"^(?P<stem>[A-Za-z0-9_.+-]+?)-(?P<version>v\d+(?:\.\d+)+)-(?P<hash>[0-9a-f]{7})"
    r"(?P<merged>-merged)?\.(?P<ext>bin|uf2|zip)$"
)
ROLES = ("repeater", "companion", "room-server")


class Fail(Exception):
    """A problem that ends the run with a readable message (no traceback)."""


def info(text):
    print(text)


def warn(text):
    print("WARNING: " + text)


def role_of(stem):
    """Role of a stem, same rule as setup_local_flasher.py."""
    if "companion_radio" in stem:
        return "companion"
    if stem.lower().endswith("repeater"):
        return "repeater"
    return "room-server"


def kind_of(match):
    """File kind of a parsed name: 'bin', 'merged.bin', 'uf2' or 'zip'."""
    return ("merged." if match["merged"] else "") + match["ext"]


def version_key(text):
    """'v1.18.0' -> (1, 18, 0), so versions sort numerically."""
    return tuple(int(p) for p in text.lstrip("v").split("."))


def http_get(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "meshcore-local-flasher", **(headers or {})})
    try:
        return urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (403, 429):
            hint = " (GitHub rate limit? wait an hour or set GITHUB_TOKEN)"
        raise Fail(f"{url}: HTTP {exc.code}{hint}") from exc
    except Exception as exc:
        raise Fail(f"{url}: {exc}") from exc


# ----------------------------------------------------------------------------
# what you have, what exists
# ----------------------------------------------------------------------------
def scan_local(fw_dir):
    """Returns (names, wanted): all file names in the firmware folder, and
    {stem: {"kinds": set, "dir": folder, "version": newest local version}} for
    the original files found there."""
    names, wanted = set(), {}
    for folder, _dirs, files in os.walk(fw_dir):
        for name in files:
            names.add(name)
            m = ORIGINAL_RE.match(name)
            if not m:
                continue
            entry = wanted.setdefault(m["stem"], {"kinds": set(), "dir": folder, "version": ()})
            entry["kinds"].add(kind_of(m))
            if version_key(m["version"]) >= entry["version"]:
                entry["version"] = version_key(m["version"])
                entry["dir"] = folder
    return names, wanted


def fetch_releases(repo, include_prerelease):
    """Newest asset per stem: {stem: {"version": tuple, "assets": {kind: asset}}}.

    The API lists releases newest first. Draft releases are ignored, and so are
    pre-releases unless asked for."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    found = {}
    for page in range(1, 6):
        with http_get(f"{API}/repos/{repo}/releases?per_page=100&page={page}", headers) as resp:
            releases = json.load(resp)
        if not releases:
            break
        for rel in releases:
            if rel.get("draft") or (rel.get("prerelease") and not include_prerelease):
                continue
            for asset in rel.get("assets", []):
                m = ORIGINAL_RE.match(asset.get("name", ""))
                if not m:
                    continue
                ver = version_key(m["version"])
                cur = found.setdefault(m["stem"], {"version": ver, "assets": {}})
                if ver > cur["version"]:           # a newer version replaces the older set
                    cur["version"], cur["assets"] = ver, {}
                if ver == cur["version"]:
                    cur["assets"][kind_of(m)] = asset
        if len(releases) < 100:
            break
    return found


# ----------------------------------------------------------------------------
# download
# ----------------------------------------------------------------------------
def download_file(url, dest, size=None, digest=None):
    """Saves url to dest via dest+'.part'; checks size and sha256 when known."""
    part = dest + ".part"
    sha = hashlib.sha256()
    total = 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with http_get(url) as resp, open(part, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                total += len(chunk)
        if size is not None and total != size:
            raise Fail(f"{os.path.basename(dest)}: size {total} instead of {size}")
        if digest and digest.startswith("sha256:") and sha.hexdigest() != digest[7:]:
            raise Fail(f"{os.path.basename(dest)}: SHA-256 checksum does not match")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)
        try:
            os.rmdir(os.path.dirname(dest))      # remove a folder we created if nothing was saved
        except OSError:
            pass


def newest_per_role(found):
    """Highest version among all stems of each role, e.g. {'repeater': (1, 18, 0)}."""
    top = {}
    for stem, rel in found.items():
        role = role_of(stem)
        top[role] = max(top.get(role, ()), rel["version"])
    return top


def fmt_version(key):
    return "v" + ".".join(str(n) for n in key) if key else "-"


def plan_firmware(wanted, found, names, fw_dir, top):
    """List of (file name, asset, dest path, note) to download."""
    todo = []
    for stem, entry in sorted(wanted.items()):
        release = found.get(stem)
        if not release:
            warn(f"{stem}: not in the releases (renamed or removed?) - nothing to do")
            continue
        if release["version"] < top[role_of(stem)]:
            warn(f"{stem}: newest file under this name is {fmt_version(release['version'])}, but the "
                 f"{role_of(stem)} releases are at {fmt_version(top[role_of(stem)])} - the device was "
                 f"probably renamed or dropped (see --list); skipped")
            continue
        kinds = entry["kinds"] or set(release["assets"])
        folder = entry["dir"] or os.path.join(fw_dir, stem)
        missing = [k for k in sorted(kinds) if k in release["assets"] and release["assets"][k]["name"] not in names]
        absent = [k for k in sorted(kinds) if k not in release["assets"]]
        if absent:
            warn(f"{stem}: the newest release has no '{', '.join(absent)}' file")
        if not missing:
            info(f"  up to date   {stem} {fmt_version(release['version'])}")
            continue
        old = fmt_version(entry["version"]) if entry["version"] else "new"
        for kind in missing:
            asset = release["assets"][kind]
            todo.append((asset["name"], asset, os.path.join(folder, asset["name"]),
                         f"{stem}: {old} -> {fmt_version(release['version'])}"))
    return todo


# ----------------------------------------------------------------------------
# nRF52 helper files
# ----------------------------------------------------------------------------
def helper_names(target, names):
    """File names of erase images/bootloaders that the nRF52 devices NEED, i.e.
    devices for which at least one firmware file is present, according to the
    target folder's config.json (written by setup_local_flasher.py)."""
    path = os.path.join(target, "config.json")
    if not os.path.isfile(path):
        raise Fail(f"{path} not found - run setup_local_flasher.py first (needed for --helpers)")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    needed = set()
    for device in cfg["device"]:
        if device.get("type") != "nrf52":
            continue
        in_use = False
        for fw in device["firmware"]:
            key = next((k for k in fw if k.startswith("github")), None)
            for rx in (fw[key].get("files", {}).values() if key else []):
                if any(re.search(rx, n) for n in names):
                    in_use = True
        if in_use:
            needed.update(device.get("bootloader", []))
            if device.get("erase"):
                needed.update([device["erase"], device["erase"].replace(".zip", ".uf2")])
    return sorted(n for n in needed if re.fullmatch(r"[A-Za-z0-9_.+-]+", n))


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Download the current original MeshCore firmware for your devices.")
    p.add_argument("--fw-dir", metavar="PATH", help="firmware folder (default: from <target>/local-flasher.json)")
    p.add_argument("--target", default=DEFAULT_TARGET, metavar="DIR", help="folder created by setup_local_flasher.py")
    p.add_argument("--add", action="append", default=[], metavar="TEXT", help="also fetch devices whose name contains TEXT")
    p.add_argument("--role", choices=ROLES, help="limit --add and --list to one role")
    p.add_argument("--list", action="store_true", help="list devices in the newest releases and exit")
    p.add_argument("--dry-run", action="store_true", help="show what would be downloaded, change nothing")
    p.add_argument("--helpers", action="store_true", help="also fetch missing nRF52 helper files")
    p.add_argument("--prerelease", action="store_true", help="also consider pre-releases")
    p.add_argument("--refresh", action="store_true", help="afterwards run setup_local_flasher.py --refresh --no-serve")
    p.add_argument("--repo", default=DEFAULT_REPO, metavar="OWNER/NAME", help="release source")
    p.add_argument("--api-url", help=argparse.SUPPRESS)   # tests only
    return p.parse_args()


def find_fw_dir(args):
    if args.fw_dir:
        return os.path.abspath(os.path.expanduser(args.fw_dir))
    settings = os.path.join(args.target, SETTINGS_NAME)
    if os.path.isfile(settings):
        with open(settings, encoding="utf-8") as fh:
            value = json.load(fh).get("firmware_dir")
        if value:
            return os.path.normpath(os.path.join(args.target, value))
    raise Fail("firmware folder unknown: use --fw-dir PATH, or run this from the folder that contains "
               f"'{DEFAULT_TARGET}' (or use --target)")


def run(args):
    global API
    if args.api_url:
        API = args.api_url
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        raise Fail("--repo must look like OWNER/NAME")

    found = fetch_releases(args.repo, args.prerelease)
    if not found:
        raise Fail("no firmware files found in the releases of " + args.repo)

    top = newest_per_role(found)
    if args.list:
        for role in ROLES:
            if args.role and args.role != role:
                continue
            stems = sorted(s for s in found if role_of(s) == role)
            print(f"{role}  ({len(stems)})")
            for s in stems:
                old = "   <- OLD, newest release is " + fmt_version(top[role]) if found[s]["version"] < top[role] else ""
                print(f"  {s:<50} {fmt_version(found[s]['version'])}{old}")
        return

    fw_dir = find_fw_dir(args)
    if not os.path.isdir(fw_dir):
        raise Fail("firmware folder does not exist: " + fw_dir)
    names, wanted = scan_local(fw_dir)
    info(f"Firmware folder: {fw_dir}")
    info(f"Devices you have: {len(wanted)}")

    for text in args.add:
        hits = [s for s in found if text.lower() in s.lower() and (not args.role or role_of(s) == args.role)]
        if not hits:
            warn(f"--add {text}: no matching device (see --list)")
        for s in hits:
            wanted.setdefault(s, {"kinds": set(), "dir": os.path.join(fw_dir, s), "version": ()})

    todo = plan_firmware(wanted, found, names, fw_dir, top)
    for name, asset, dest, note in todo:
        info(f"  {'would get' if args.dry_run else 'downloading'}  {name}   ({note})")
    changed = False
    failures = []
    if not args.dry_run:
        for name, asset, dest, _note in todo:
            try:
                download_file(asset["browser_download_url"], dest, asset.get("size"), asset.get("digest"))
                changed = True
            except Fail as exc:                   # keep going; report at the end
                failures.append(str(exc))
                warn("not saved: " + str(exc))

    if args.helpers:
        for name in helper_names(args.target, names | {t[0] for t in todo}):
            if os.path.isfile(os.path.join(fw_dir, name)):
                continue
            info(f"  {'would get' if args.dry_run else 'downloading'}  helper file {name}")
            if not args.dry_run:
                download_file(HELPER_BASE + name, os.path.join(fw_dir, name))
                changed = True

    info(f"Done: {len(todo) - len(failures)} firmware file(s) {'to download' if args.dry_run else 'downloaded'}"
         + (f", {len(failures)} FAILED." if failures else "."))
    if args.refresh and not args.dry_run:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_local_flasher.py")
        if not os.path.isfile(script):
            raise Fail("setup_local_flasher.py not found next to this script")
        info("Rebuilding the flasher's firmware list ...")
        subprocess.run([sys.executable, script, args.target, "--refresh", "--no-serve", "--fw-dir", fw_dir], check=True)
    elif changed:
        info("Next: run setup_local_flasher.py --refresh (or use --refresh here), then reload the page.")
    if failures:
        raise Fail(f"{len(failures)} download(s) failed (see above); run again to retry")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        run(parse_args())
    except Fail as exc:
        print("ERROR: " + str(exc))
        return 1
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
