"""Run the real JNI/TLS consumer against an owned temporary Host fixture."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--library-dir", type=Path, required=True)
parser.add_argument("--fixture-bin", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
java = Path(os.environ["JAVA_HOME"]) / "bin"
source = root / "apps/android/app/src/main/java/com/agentbrowser/probe"
sources = [source / name for name in ("NativeConnection.java", "NetworkFrame.java", "AccessUnit.java", "HostCommandException.java")]
sources.append(Path(__file__).with_name("NativeSmoke.java"))
with tempfile.TemporaryDirectory(prefix="m1-jni-smoke-") as classes:
    subprocess.run([str(java / "javac"), "-d", classes, *map(str, sources)], check=True)
    fixture = subprocess.Popen([str(args.fixture_bin.resolve())], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    try:
        if not select.select([fixture.stdout], [], [], 30)[0]:
            raise TimeoutError("Owned Host fixture did not become ready")
        ready = json.loads(fixture.stdout.readline())
        subprocess.run([str(java / "java"), "-Djava.library.path=" + str(args.library_dir.resolve()),
                        "-cp", classes, "com.agentbrowser.probe.NativeSmoke", ready["fixture"]],
                       check=True, timeout=30)
    finally:
        fixture.communicate("quit\n", timeout=20)
        if fixture.returncode:
            raise RuntimeError(f"Owned fixture cleanup failed: {fixture.returncode}")
