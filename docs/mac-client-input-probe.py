#!/usr/bin/env python3
"""Headless probe for the macOS client input path.

Starts a real Obscura host+endpoint fixture and the AgentBrowser macOS
connection bridge, then drives the full input prerequisite chain:

    connect -> viewport -> takeover -> frame -> ack_frame -> click / gesture

It reports, for both the bridge `click` op and the bridge `editing` gesture op,
whether the remote DOM recorded the click.

Usage: python3 docs/mac-client-input-probe.py
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

WT = "/Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013"
R8 = "/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009"
BRIDGE = os.path.join(WT, "apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMacBridge")
FIXTURE = os.path.join(WT, "target/release/examples/device_fixture")
BIND_IP = "192.168.0.3"

log = lambda *a: print(*a, flush=True)


class Bridge:
    """Reads the bridge binary stdout stream: kind byte, u32 header, u32 body."""

    def __init__(self, proc):
        self.proc = proc
        self.responses = {}
        self.condition = threading.Condition()
        self.frames = []
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        stream = self.proc.stdout
        while True:
            head = stream.read(1)
            if not head:
                break
            kind = head[0]
            raw = stream.read(4)
            if len(raw) < 4:
                break
            header = stream.read(struct.unpack(">I", raw)[0])
            raw = stream.read(4)
            if len(raw) < 4:
                break
            body = stream.read(struct.unpack(">I", raw)[0])
            try:
                meta = json.loads(header)
            except Exception:
                continue
            with self.condition:
                if kind == 0 and "id" in meta:
                    value = meta.get("value")
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except Exception:
                            pass
                    self.responses[meta["id"]] = value
                elif kind == 1:
                    self.frames.append(meta)
                self.condition.notify_all()

    def send(self, payload):
        self.next_id += 1
        rid = self.next_id
        line = json.dumps({"id": rid, "command": json.dumps(payload)})
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()
        return rid

    def call(self, payload, timeout=25.0):
        rid = self.send(payload)
        deadline = time.time() + timeout
        with self.condition:
            while rid not in self.responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {"__timeout__": True}
                self.condition.wait(remaining)
            return self.responses[rid]

    def wait_frames(self, count, timeout=25.0):
        deadline = time.time() + timeout
        with self.condition:
            while len(self.frames) < count:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return list(self.frames)
                self.condition.wait(remaining)
            return list(self.frames)


def start_fixture(tmp):
    env = dict(os.environ)
    env["OBSCURA_BIN_DIR"] = os.path.join(R8, "target/release")
    env["OBSCURA_ENDPOINT_BIND_IP"] = BIND_IP
    proc = subprocess.Popen(
        [FIXTURE], cwd=WT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    line = proc.stdout.readline()
    if not line.strip():
        log("FIXTURE_FAILED:", proc.stderr.read().decode("utf-8", "replace")[:800])
        sys.exit(1)
    return proc, json.loads(line)


def fixture_call(proc, command, timeout=25.0):
    proc.stdin.write((command + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line.strip() else {"__empty__": True}


def stage_pairing(bundle_path, tmp):
    pairing = os.path.join(tmp, "pairing")
    os.makedirs(pairing, mode=0o700, exist_ok=True)
    bundle = json.load(open(bundle_path))
    open(os.path.join(pairing, "endpoint.txt"), "w").write(bundle["endpoint"] + "\n")
    fd = os.open(os.path.join(pairing, "token"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, (bundle["token"] + "\n").encode())
    os.close(fd)
    return pairing


def epoch_of(status):
    control = status.get("control") if isinstance(status, dict) else None
    return control.get("epoch") if isinstance(control, dict) else None


def main():
    tmp = tempfile.mkdtemp(prefix="macprobe-")
    fixture, info = start_fixture(tmp)
    log("fixture:", info["fixture"])
    log("endpoint:", info["endpoint"])

    pairing = stage_pairing(os.path.join(info["fixture"], "pairing.abconnection"), tmp)
    env = dict(os.environ)
    env["AGENTBROWSER_MAC_PAIRING"] = pairing
    bridge_proc = subprocess.Popen(
        [BRIDGE], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    bridge = Bridge(bridge_proc)
    time.sleep(2)

    def report(tag, value):
        if not isinstance(value, dict):
            log(f"  {tag}: {value!r}")
            return
        keep = {k: value.get(k) for k in
                ("connectionState", "state", "controlMode", "epoch", "inputReady",
                 "renderedFrames", "rejection", "error") if k in value}
        log(f"  {tag}: {json.dumps(keep, ensure_ascii=False)}")

    try:
        log("== connect ==")
        report("connect", bridge.call({"op": "connect"}))
        for _ in range(40):
            st = bridge.call({"op": "status"})
            if isinstance(st, dict) and st.get("connectionState") == "connected":
                break
            time.sleep(0.5)
        report("status", bridge.call({"op": "status"}))

        log("== viewport ==")
        report("viewport", bridge.call(
            {"op": "viewport", "width": 1180, "height": 704, "orientation": "landscape"}))
        time.sleep(1)
        status = bridge.call({"op": "status"})
        report("status", status)

        log("== takeover ==")
        ep = epoch_of(fixture_call(fixture, "status"))
        log("  fixture epoch:", ep)
        report("takeover", bridge.call({"op": "takeover", "epoch": ep}))
        time.sleep(1)
        status = bridge.call({"op": "status"})
        report("status", status)
        ep = status.get("epoch") if isinstance(status, dict) else ep

        log("== frame + ack until inputReady ==")
        acked = set()
        status = {}
        for _ in range(30):
            frames = bridge.wait_frames(1, timeout=20)
            fresh = [f for f in frames if f["ticket"] not in acked]
            if fresh:
                ticket = fresh[-1]["ticket"]
                acked.add(ticket)
                bridge.call({"op": "ack_frame", "ticket": ticket})
            status = bridge.call({"op": "status"})
            log("  ticket=%s renderedFrames=%s viewport=%s viewport_pending=%s inputReady=%s" % (
                fresh[-1]["ticket"] if fresh else None,
                status.get("renderedFrames"), status.get("viewport"),
                status.get("viewport_pending"), status.get("inputReady")))
            if status.get("inputReady") is True:
                break
            time.sleep(1)
        report("status", status)
        log("  inputReady:", status.get("inputReady"))

        log("== click op ==")
        report("click", bridge.call({"op": "click", "epoch": ep, "x": 90, "y": 50}))
        time.sleep(2)

        log("== release ==")
        report("release", bridge.call({"op": "release", "epoch": ep}))
        time.sleep(1)
        log("  dom:", fixture_call(fixture, "inspect"))

        log("== gesture path (editing) ==")
        ep = epoch_of(fixture_call(fixture, "status"))
        report("takeover", bridge.call({"op": "takeover", "epoch": ep}))
        time.sleep(1)
        status = bridge.call({"op": "status"})
        ep = status.get("epoch") if isinstance(status, dict) else ep
        binding = {"type": "gesture", "gesture_session_id": "probe-gesture"}
        report("pointer_down", bridge.call({
            "op": "editing", "epoch": ep, "operation_id": "probe-gesture",
            "event_sequence": 0, "binding": binding,
            "event": {"type": "pointer_down", "pointer_id": 1, "x": 90, "y": 50,
                      "button": "left", "buttons": 1, "modifiers": 0}}))
        time.sleep(1)
        report("pointer_up", bridge.call({
            "op": "editing", "epoch": ep, "operation_id": "probe-gesture",
            "event_sequence": 1, "binding": binding,
            "event": {"type": "pointer_up", "pointer_id": 1, "x": 90, "y": 50,
                      "button": "left", "buttons": 0, "modifiers": 0}}))
        time.sleep(2)
        report("release", bridge.call({"op": "release", "epoch": ep}))
        time.sleep(1)
        log("  dom:", fixture_call(fixture, "inspect"))

        err = bridge_proc.stderr.read(200) if bridge_proc.stderr else b""
        if err:
            log("bridge stderr:", err.decode("utf-8", "replace")[:400])
    finally:
        for proc in (bridge_proc, fixture):
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        log("== done ==")


if __name__ == "__main__":
    main()
