#!/usr/bin/env python3
"""End-to-end GUI probe for the macOS client click path.

Two environment facts drive this script:

1. System Events' `first process whose unix id is N` returns the wrong
   process when two instances of the app run. Resolve the process with an
   explicit loop over `every process` instead.
2. `cliclick` does not deliver mouse events here. Post clicks with
   System Events' `click at {x, y}` inside the same script that activates
   the app.

Usage: python3 docs/mac-gui-click-e2e.py
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

WT = "/Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013"
R8 = "/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009"
APP = os.path.join(WT, "apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac")
FIXTURE = os.path.join(WT, "target/release/examples/device_fixture")
BIND_IP = "192.168.0.3"
LOG = "/tmp/mac-gui-e2e-bridge.log"

log = lambda *a: print(*a, flush=True)


def osa(body, pid):
    """Run `body` inside the matching process iteration.

    A saved `target` reference is not stable across the repeat loop, so the
    body must run inside the matching iteration and use `pr` directly.
    """
    script = f'''
tell application "System Events"
  repeat with pr in (every process)
    try
      if (unix id of pr) is {pid} then
        {body}
      end if
    end try
  end repeat
  return "NO_PROCESS"
end tell
'''
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True).stdout.strip()


def window_frame(pid):
    out = osa('''
  set w to window 1 of pr
  set pos to position of w
  set sz to size of w
  return ((item 1 of pos) as text) & "," & ((item 2 of pos) as text) & "," & ((item 1 of sz) as text) & "," & ((item 2 of sz) as text)''', pid)
    try:
        return tuple(int(float(v)) for v in out.split(","))
    except Exception:
        return None


def move_window(pid, x, y):
    return osa(f'''
  try
    set position of window 1 of pr to {{{x}, {y}}}
    return "moved"
  on error e
    return "MOVE_ERR " & e
  end try''', pid)


# Panel geometry relative to the window origin, read from the live AX tree of
# a running instance. The WebView does not always expose DOM buttons to
# accessibility, so drive the panel by coordinate.
MENU_TOGGLE = (1149, 61)
MENU_CLOSE = (1121, 70)
PANEL_CONNECT = (890, 323)
PANEL_TAKEOVER = (890, 376)
PANEL_RELEASE = (1075, 376)
PAGE_ORIGIN = (0, 86)


def activate(pid):
    """Bring the app and its window forward. Kept separate from clicking so the
    click script stays short and does not time out with error -25208."""
    return osa('''
  set frontmost of pr to true
  perform action "AXRaise" of window 1 of pr
  return "OK"''', pid)


def click_at(pid, x, y, attempts=3):
    """Post a real click at a screen point, retrying transient AX errors."""
    last = ""
    for _ in range(attempts):
        last = osa(f'''
  try
    click at {{{x}, {y}}}
    return "OK"
  on error e
    return "ERR " & e
  end try''', pid)
        if last == "OK":
            return last
        time.sleep(1.5)
    return last


def panel_point(pid, offset):
    frame = window_frame(pid)
    if frame is None:
        return None
    return (frame[0] + offset[0], frame[1] + offset[1])


def send_counts():
    text = open(LOG, errors="ignore").read()
    counts = {}
    for s in re.findall(r"MAC_NATIVE_SEND op=[a-z_]+", text):
        counts[s] = counts.get(s, 0) + 1
    return counts


class Fixture:
    def __init__(self):
        env = dict(os.environ)
        env["OBSCURA_BIN_DIR"] = os.path.join(R8, "target/release")
        env["OBSCURA_ENDPOINT_BIND_IP"] = BIND_IP
        self.proc = subprocess.Popen([FIXTURE], cwd=WT, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.info = json.loads(self.proc.stdout.readline())

    def call(self, command):
        self.proc.stdin.write((command + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line) if line.strip() else {"__empty__": True}


def main():
    tmp = tempfile.mkdtemp(prefix="macguiprobe-")
    fixture = Fixture()
    log("endpoint:", fixture.info["endpoint"])

    pairing = os.path.join(tmp, "pairing")
    os.makedirs(pairing, mode=0o700)
    bundle = json.load(open(os.path.join(fixture.info["fixture"], "pairing.abconnection")))
    open(os.path.join(pairing, "endpoint.txt"), "w").write(bundle["endpoint"] + "\n")
    fd = os.open(os.path.join(pairing, "token"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, (bundle["token"] + "\n").encode())
    os.close(fd)

    env = dict(os.environ)
    env["AGENTBROWSER_MAC_PAIRING"] = pairing
    logfile = open(LOG, "w")
    app = subprocess.Popen([APP, "--pairing-dir", pairing], env=env,
                           stdout=logfile, stderr=subprocess.STDOUT)

    try:
        time.sleep(16)
        log("  window:", window_frame(app.pid))

        log("== connect ==")
        log("  activate:", activate(app.pid))
        time.sleep(1.5)
        pt = panel_point(app.pid, MENU_TOGGLE)
        log("  toggle:", click_at(app.pid, *pt))
        time.sleep(1.5)
        subprocess.run(["screencapture", "-x", "/tmp/gui-menu.png"])
        pt = panel_point(app.pid, PANEL_CONNECT)
        log("  click 连接:", click_at(app.pid, *pt))
        for _ in range(40):
            if '"connectionState":"connected"' in open(LOG, errors="ignore").read():
                break
            time.sleep(0.5)
        states = re.findall(r'"connectionState":"[a-z]+"', open(LOG, errors="ignore").read())
        log("  connectionState:", states[-1] if states else "none")

        log("== takeover ==")
        activate(app.pid)
        time.sleep(1.0)
        pt = panel_point(app.pid, MENU_TOGGLE)
        log("  toggle:", click_at(app.pid, *pt))
        time.sleep(1.5)
        pt = panel_point(app.pid, PANEL_TAKEOVER)
        log("  click 接管:", click_at(app.pid, *pt))
        time.sleep(3)
        log("  control:", fixture.call("status").get("control"))

        log("== page click ==")
        activate(app.pid)
        time.sleep(1.0)
        pt = panel_point(app.pid, MENU_CLOSE)
        log("  close menu:", click_at(app.pid, *pt))
        time.sleep(1.5)
        pt = panel_point(app.pid, PAGE_ORIGIN)
        px, py = pt[0] + 90, pt[1] + 50
        log("  point:", px, py)
        subprocess.run(["screencapture", "-x", "/tmp/gui-before-click.png"])
        origin = panel_point(app.pid, PAGE_ORIGIN)
        for dx, dy, label in [(90, 50, "red-button"), (100, 119, "input"), (400, 220, "blue"), (600, 520, "blue-low")]:
            before = send_counts()
            log(f"  click {label} ({dx},{dy}):", click_at(app.pid, origin[0] + dx, origin[1] + dy))
            time.sleep(2)
            after = send_counts()
            delta = {k: after[k] - before.get(k, 0) for k in after if after[k] - before.get(k, 0) > 0}
            log(f"    delta: {delta}")
        log("  sends:", send_counts())
        log("  dom:", fixture.call("inspect"))

        log("== release ==")
        activate(app.pid)
        time.sleep(1.0)
        pt = panel_point(app.pid, MENU_TOGGLE)
        log("  toggle:", click_at(app.pid, *pt))
        time.sleep(1.5)
        pt = panel_point(app.pid, PANEL_RELEASE)
        log("  click 返回观察:", click_at(app.pid, *pt))
        time.sleep(3)
        log("  control:", fixture.call("status").get("control"))
        log("  dom:", fixture.call("inspect"))
    finally:
        for proc in (app, fixture.proc):
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                pass
        logfile.close()
        shutil.rmtree(tmp, ignore_errors=True)
        log("== done ==")


if __name__ == "__main__":
    main()
