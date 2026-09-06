"""Build the shared native owner for Android arm64; no Browser ABI mirroring."""
import json
import os
import pathlib
import shutil
import subprocess

root = pathlib.Path(__file__).resolve().parent.parent
protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
ndk = pathlib.Path(os.environ["ANDROID_NDK_HOME"]).resolve(strict=True)
prebuilt = ndk / "toolchains/llvm/prebuilt"
hosts = [path for path in prebuilt.iterdir() if (path / "bin/aarch64-linux-android30-clang").is_file()]
if len(hosts) != 1:
    raise RuntimeError("Expected one installed NDK host compiler")
compiler = hosts[0] / "bin/aarch64-linux-android30-clang"
env = os.environ.copy()
env["CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"] = str(compiler)
env["CC_aarch64_linux_android"] = str(compiler)
env["AR_aarch64_linux_android"] = str(hosts[0] / "bin/llvm-ar")
patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
subprocess.run(["cargo", "build", "--release", "--target", "aarch64-linux-android", "-p", "agentbrowser-android", "--config", patch], cwd=root, env=env, check=True)
source = root / "target/aarch64-linux-android/release/libagentbrowser_android.so"
output = root / "apps/android/app/build/generated/nativeLibs/arm64-v8a"
output.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, output / source.name)
