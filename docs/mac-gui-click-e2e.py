#!/usr/bin/env python3
"""End-to-end GUI probe for the macOS client click path.

Real clicks must be posted from inside the same System Events script that
activates the app. A separate cliclick process loses focus to the full-screen
terminal window, so its events never reach the WebView.

Usage: python3 docs/mac-gui-click-e2e.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

WT = "/Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013"
R8 = "/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009"
APP = os.path.join(WT, "apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac")
FIXTURE = os.path.join(WT, "target/release/examples/device_fixture")
BIND_IP = "192.168.0.3"
LOG = "/tmp/mac-gui-e2e-bridge.log"

log = lambda *a: print(*a, flush=True)


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


def click_named(pid, name):
    """Activate the app and click the named AX button, in one System Events run."""
    return osa(f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set frontmost of p to true
  delay 0.4
  perform action "AXRaise" of window 1 of p
  delay 0.8
  set fm to frontmost of p
  set es to entire contents of window 1 of p
  repeat with e in es
    try
      if role of e is "AXButton" and name of e is "{name}" then
        set pos to position of e
        set sz to size of e
        set cx to (item 1 of pos) + ((item 1 of sz) / 2) as integer
        set cy to (item 2 of pos) + ((item 2 of sz) / 2) as integer
        click at {{cx, cy}}
        return "OK " & cx & "," & cy & " front=" & (fm as text)
      end if
    end try
  end repeat
  return "MISSING"
end tell
''')


def press_named(pid, name):
    return osa(f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set frontmost of p to true
  delay 0.4
  perform action "AXRaise" of window 1 of p
  delay 0.8
  set fm to frontmost of p
  set es to entire contents of window 1 of p
  repeat with e in es
    try
      if role of e is "AXButton" and name of e is "{name}" then perform action "AXPress" of e
    end try
  end repeat
  return "DONE"
end tell
''')


def click_point(pid, x, y):
    return osa(f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set frontmost of p to true
  delay 0.4
  perform action "AXRaise" of window 1 of p
  delay 0.8
  click at {{{x}, {y}}}
  return "OK"
end tell
''')


def window_frame(pid):
    out = osa(f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set w to window 1 of p
  set pos to position of w
  set sz to size of w
  return ((item 1 of pos) as text) & "," & ((item 2 of pos) as text) & "," & ((item 1 of sz) as text) & "," & ((item 2 of sz) as text)
end tell
''')
    return tuple(int(float(v)) for v in out.split(","))


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
        time.sleep(12)
        r = subprocess.run(["osascript", "-e", f"""
tell application "System Events"
  set p to first process whose unix id is {app.pid}
  try
    set position of window 1 of p to {{2560, 100}}
    return "moved"
  on error e
    return "MOVE_ERR " & e
  end try
end tell
"""], capture_output=True, text=True)
        log("  move:", (r.stdout or r.stderr).strip())
        time.sleep(1)
        log("window:", window_frame(app.pid))

        log("== connect ==")
        press_named(app.pid, "打开菜单")
        time.sleep(1.5)
        subprocess.run(["screencapture", "-x", "/tmp/gui-menu.png"])
        subprocess.run(["screencapture", "-x", "/tmp/gui-menu.png"])
        log("  click 连接:", click_named(app.pid, "连接"))
        for _ in range(40):
            if '"connectionState":"connected"' in open(LOG, errors="ignore").read():
                break
            time.sleep(0.5)
        states = re.findall(r'"connectionState":"[a-z]+"', open(LOG, errors="ignore").read())
        log("  connectionState:", states[-1] if states else "none")

        log("== takeover ==")
        press_named(app.pid, "打开菜单")
        time.sleep(1.5)
        log("  click 接管:", click_named(app.pid, "接管"))
        time.sleep(3)
        log("  control:", fixture.call("status").get("control"))

        log("== page click ==")
        wx, wy, ww, wh = window_frame(app.pid)
        px, py = wx + 90, wy + 28 + 56 + 50
        log("  point:", px, py)
        click_point(app.pid, px, py)
        time.sleep(3)
        log("  sends:", send_counts())

        log("== release ==")
        press_named(app.pid, "打开菜单")
        time.sleep(1.5)
        log("  click 返回观察:", click_named(app.pid, "返回观察"))
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
