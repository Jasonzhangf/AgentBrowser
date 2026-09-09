"""Run the real WebRTC JNI consumer against an owned Host fixture."""
import argparse
import json
import os
import select
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--library-dir", type=Path, required=True)
parser.add_argument("--fixture-bin", type=Path, required=True)
parser.add_argument("--bind-ip", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
java = Path(os.environ["JAVA_HOME"]) / "bin"
source = root / "apps/android/app/src/main/java/com/agentbrowser/probe"
sources = [source / name for name in ("NativeConnection.java", "NetworkFrame.java", "AccessUnit.java", "HostCommandException.java")]
sources.append(Path(__file__).with_name("WebRtcSmoke.java"))

with tempfile.TemporaryDirectory(prefix="m1-webrtc-jni-smoke-") as classes:
    subprocess.run([str(java / "javac"), "-d", classes, *map(str, sources)], check=True)
    fixture_env = os.environ.copy()
    fixture_env["OBSCURA_ENABLE_WEBRTC"] = "1"
    fixture = subprocess.Popen([str(args.fixture_bin.resolve())], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, env=fixture_env)
    try:
        if not select.select([fixture.stdout], [], [], 30)[0]:
            raise TimeoutError("Owned Host fixture did not become ready")
        ready = json.loads(fixture.stdout.readline())
        subprocess.run([
            str(java / "java"), "-Djava.library.path=" + str(args.library_dir.resolve()),
            "-cp", classes, "com.agentbrowser.probe.WebRtcSmoke", ready["fixture"], args.bind_ip,
        ], check=True, timeout=90)
    finally:
        fixture.communicate("quit\n", timeout=20)
        if fixture.returncode:
            raise RuntimeError(f"Owned Host fixture cleanup failed: {fixture.returncode}")
