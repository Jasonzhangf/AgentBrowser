"""Stage/remove this task's ephemeral pairing bundle in the debug app's private files.

The bundle is staged as a plain file the installed app reads through its own
import owner. This script never writes the app's live pairing directory; it only
removes the generation it can prove the fixture created.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["install", "remove"])
parser.add_argument("fixture")
parser.add_argument("--adb", default=None)
args = parser.parse_args()
fixture = pathlib.Path(args.fixture).resolve(strict=args.action == "install")
if fixture.parent != pathlib.Path("/tmp").resolve() or not fixture.name.startswith("an-"):
    parser.error("Expected the owned /tmp/an-* fixture directory")
serial = os.environ["ANDROID_SERIAL"]
app = "com.agentbrowser.probe"
configured_adb = args.adb or os.environ.get("ADB") or "adb"
adb_path = pathlib.Path(configured_adb).expanduser()
if adb_path.is_absolute():
    adb = str(adb_path.resolve(strict=False))
else:
    found = shutil.which(configured_adb)
    adb = str(pathlib.Path(found).resolve(strict=False)) if found else configured_adb


def remote(arguments, data=None, check=True):
    return subprocess.run([adb, "-s", serial, "shell", "-T", shlex.join(["run-as", app] + arguments)],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check).stdout


STAGING = "files/network-replay-input"
BUNDLE = "pairing.abconnection"
OWNER = "owner"
EXPECTED = "expected.json"
ACTIVE = "files/pairing/active-generation.json"
GENERATIONS = "files/pairing/.generations"
GENERATION_ID = re.compile(r"^[A-Za-z0-9._-]+$")

if args.action == "install":
    bundle = (fixture / BUNDLE).read_bytes()
    client_key = (fixture / "key.der").read_bytes()
    expected = {
        "fixture": fixture.name,
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "client_key_sha256": hashlib.sha256(client_key).hexdigest(),
    }
    # mkdir fails when an unremoved staging directory is present; never replace
    # a pairing this task did not stage.
    remote(["mkdir", "-m", "700", STAGING])
    remote(["sh", "-c", f"umask 077; cat > {STAGING}/{BUNDLE}"], bundle)
    remote(["sh", "-c", f"umask 077; cat > {STAGING}/{OWNER}"], fixture.name.encode())
    remote(["sh", "-c", f"umask 077; cat > {STAGING}/{EXPECTED}"], json.dumps(expected).encode())
    print(f"Task pairing install staged {STAGING}/{BUNDLE} "
          f"bundle_sha256={expected['bundle_sha256']}; bundle contents not logged")
else:
    owner = remote(["cat", f"{STAGING}/{OWNER}"]).decode().strip()
    if owner != fixture.name:
        raise RuntimeError("Refuse cleanup: staged pairing belongs to another fixture")
    expected = json.loads(remote(["cat", f"{STAGING}/{EXPECTED}"]).decode())
    if expected.get("fixture") != fixture.name:
        raise RuntimeError("Refuse cleanup: staged expectation belongs to another fixture")
    active_raw = remote(["sh", "-c", f"cat {ACTIVE} 2>/dev/null || true"]).decode().strip()
    if active_raw:
        active = json.loads(active_raw)
        generation_id = active.get("generation_id")
        materials = {item.get("name"): item for item in active.get("materials", [])}
        installed_key = materials.get("key.der", {}).get("sha256")
        if installed_key != expected.get("client_key_sha256"):
            raise RuntimeError("Refuse cleanup: active pairing generation belongs to another pairing")
        if not isinstance(generation_id, str) or not GENERATION_ID.match(generation_id) or ".." in generation_id:
            raise RuntimeError("Refuse cleanup: active pairing generation id is not removable")
        remote(["rm", "-rf", f"{GENERATIONS}/{generation_id}"])
        remote(["rm", "-f", ACTIVE])
        print(f"Task pairing remove dropped generation {generation_id}; key material not logged")
    remote(["rm", "-f", f"{STAGING}/{BUNDLE}", f"{STAGING}/{OWNER}", f"{STAGING}/{EXPECTED}"])
    remote(["rmdir", STAGING])
    print("Task pairing remove completed; credentials not logged")
