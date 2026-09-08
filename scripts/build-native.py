"""Build the shared native owner for Android arm64; no Browser ABI mirroring."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

TARGET_TRIPLE = "aarch64-linux-android"
NATIVE_ARTIFACT = pathlib.Path("release/libagentbrowser_android.so")


def resolve_cargo_target_root(root, configured):
    target_root = pathlib.Path(configured) if configured else root / "target"
    if not target_root.is_absolute():
        target_root = root / target_root
    return target_root.resolve()


def native_artifact_path(target_root):
    return target_root / TARGET_TRIPLE / NATIVE_ARTIFACT


def copy_native_artifact(source, output):
    if not source.is_file():
        raise FileNotFoundError(f"Cargo did not produce expected native library: {source}")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output / source.name)


def build():
    root = pathlib.Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    target_root = resolve_cargo_target_root(root, env.get("CARGO_TARGET_DIR"))
    env["CARGO_TARGET_DIR"] = str(target_root)

    protocol = pathlib.Path(env["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
    ndk = pathlib.Path(env["ANDROID_NDK_HOME"]).resolve(strict=True)
    prebuilt = ndk / "toolchains/llvm/prebuilt"
    hosts = [path for path in prebuilt.iterdir() if (path / "bin/aarch64-linux-android30-clang").is_file()]
    if len(hosts) != 1:
        raise RuntimeError("Expected one installed NDK host compiler")
    compiler = hosts[0] / "bin/aarch64-linux-android30-clang"
    env["CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"] = str(compiler)
    env["CC_aarch64_linux_android"] = str(compiler)
    env["AR_aarch64_linux_android"] = str(hosts[0] / "bin/llvm-ar")
    patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
    subprocess.run(
        ["cargo", "build", "--release", "--target", TARGET_TRIPLE, "-p", "agentbrowser-android", "--config", patch],
        cwd=root,
        env=env,
        check=True,
    )
    source = native_artifact_path(target_root)
    output = root / "apps/android/app/build/generated/nativeLibs/arm64-v8a"
    copy_native_artifact(source, output)


def self_check():
    with tempfile.TemporaryDirectory(prefix="build-native-self-check-") as directory:
        root = pathlib.Path(directory) / "repo"
        root.mkdir()
        assert resolve_cargo_target_root(root, None) == (root / "target").resolve()
        assert resolve_cargo_target_root(root, "configured-target") == (root / "configured-target").resolve()
        configured = pathlib.Path(directory) / "absolute-target"
        assert resolve_cargo_target_root(root, str(configured)) == configured.resolve()

        stale = native_artifact_path(root / "target")
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        destination = root / "generated"
        try:
            copy_native_artifact(native_artifact_path(root / "configured-target"), destination)
        except FileNotFoundError as error:
            assert str(root / "configured-target") in str(error)
        else:
            raise AssertionError("missing configured artifact must fail closed")

        configured_artifact = native_artifact_path(root / "configured-target")
        configured_artifact.parent.mkdir(parents=True)
        configured_artifact.write_bytes(b"configured")
        copy_native_artifact(configured_artifact, destination)
        assert (destination / configured_artifact.name).read_bytes() == b"configured"

    print("build-native self-check: PASS")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    else:
        build()
