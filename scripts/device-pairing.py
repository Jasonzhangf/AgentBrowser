"""Install/remove this task's ephemeral pairing in the debug app's private files."""
import argparse
import os
import pathlib
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


def remote(arguments, data=None):
    return subprocess.run([adb, "-s", serial, "shell", "-T", shlex.join(["run-as", app] + arguments)],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

files = ["endpoint.txt", "ca.der", "client.der", "key.der"]
if args.action == "install":
    # mkdir fails if an existing pairing is present; never replace user trust.
    remote(["mkdir", "-m", "700", "files/pairing"])
    remote(["sh", "-c", "umask 077; cat > files/pairing/owner"], fixture.name.encode())
    for name in files:
        remote(["sh", "-c", f"umask 077; cat > files/pairing/{name}"], (fixture / name).read_bytes())
else:
    owner = remote(["cat", "files/pairing/owner"]).decode().strip()
    if owner != fixture.name:
        raise RuntimeError("Refuse cleanup: pairing belongs to another fixture")
    remote(["rm"] + [f"files/pairing/{name}" for name in files + ["owner"]])
    remote(["rmdir", "files/pairing"])
print(f"Task pairing {args.action} completed; credentials not logged")
