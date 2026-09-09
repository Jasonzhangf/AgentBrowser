"""Build/test the connection kernel against an explicit Obscura protocol owner.

OBSCURA_PROTOCOL_ROOT must identify protocol/browser in the owner checkout.
No protocol source is copied, and no published crate availability is implied.
"""
import argparse
import json
import os
import pathlib
import subprocess
import shutil

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["build", "test", "check"])
parser.add_argument("--protocol-root", default=os.environ.get("OBSCURA_PROTOCOL_ROOT"))
args = parser.parse_args()
if not args.protocol_root:
    parser.error("Set OBSCURA_PROTOCOL_ROOT or --protocol-root to the Obscura protocol/browser owner")
protocol = pathlib.Path(args.protocol_root).resolve(strict=True)
if not (protocol / "src/lib.rs").is_file():
    parser.error("Protocol source entry is missing")
patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
command = ["cargo"]
command += ["nextest", "run", "--release"] if args.command == "test" else [args.command, "--release"]
command += ["--config", patch]
command += ["-p", "agentbrowser-connection"]
root = pathlib.Path(__file__).resolve().parent.parent
if args.command != "build":
    raise SystemExit(subprocess.run(command, cwd=root).returncode)

# Preserve the exact linked real-entrypoint consumer beside the native library
# for admission replay. This is a development artifact, not a published SDK.
result = subprocess.run(command + ["--lib", "--tests", "--message-format=json"], cwd=root, stdout=subprocess.PIPE, text=True)
if result.returncode:
    raise SystemExit(result.returncode)
artifacts = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
library = [path for item in artifacts if item.get("reason") == "compiler-artifact"
           and item["target"]["name"] == "agentbrowser_connection"
           for path in item["filenames"] if path.endswith(".rlib")]
consumer = [item["executable"] for item in artifacts if item.get("reason") == "compiler-artifact"
            and item["target"]["name"] == "endpoint" and item.get("executable")]
relay = [item["executable"] for item in artifacts if item.get("reason") == "compiler-artifact"
         and item["target"]["name"] == "relay" and item.get("executable")]
relay_connection = [item["executable"] for item in artifacts if item.get("reason") == "compiler-artifact"
                    and item["target"]["name"] == "relay_connection" and item.get("executable")]
webrtc = [item["executable"] for item in artifacts if item.get("reason") == "compiler-artifact"
          and item["target"]["name"] == "webrtc" and item.get("executable")]
if len(library) != 1 or len(consumer) != 1 or len(relay) != 1 or len(relay_connection) != 1 or len(webrtc) != 1:
    raise RuntimeError("Expected one native library and one consumer for each direct, WebRTC, Relay, and Relay Connection path")
output = root / "generated/modules/client-connection/lib"
output.mkdir(parents=True, exist_ok=True)
shutil.copy2(library[0], output / "libagentbrowser_connection.rlib")
shutil.copy2(consumer[0], output / "connection-acceptance")
shutil.copy2(webrtc[0], output / "webrtc-acceptance")
shutil.copy2(relay[0], output / "relay-acceptance")
shutil.copy2(relay_connection[0], output / "relay-connection-acceptance")
