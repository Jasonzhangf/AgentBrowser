#!/usr/bin/env python3
"""End-to-end GUI probe for the macOS client click path.

Starts a real Obscura host+endpoint fixture, launches the macOS native app
against a staged pairing directory, then drives the WebKit connection menu with
real mouse clicks placed from the accessibility frames of the menu buttons.

It reports whether the GUI page click reaches the remote DOM.
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


def ax_frame(pid, name):
    script = f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set es to entire contents of window 1 of p
  repeat with e in es
    try
      if role of e is "AXButton" and name of e is "{name}" then
        set pos to position of e
        set sz to size of e
        return ((item 1 of pos) as text) & "," & ((item 2 of pos) as text) & "," & ((item 1 of sz) as text) & "," & ((item 2 of sz) as text)
      end if
    end try
  end repeat
  return "MISSING"
end tell
'''
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()
    if out == "MISSING" or "," not in out:
        return None
    x, y, w, h = (int(float(v)) for v in out.split(","))
    return x + w // 2, y + h // 2


def ax_press(pid, name):
    script = f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set es to entire contents of window 1 of p
  repeat with e in es
    try
      if role of e is "AXButton" and name of e is "{name}" then perform action "AXPress" of e
    end try
  end repeat
end tell
'''
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def window_frame(pid):
    script = f'''
tell application "System Events"
  set p to first process whose unix id is {pid}
  set w to window 1 of p
  set pos to position of w
  set sz to size of w
  return ((item 1 of pos) as text) & "," & ((item 2 of pos) as text) & "," & ((item 1 of sz) as text) & "," & ((item 2 of sz) as text)
end tell
'''
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()
    return tuple(int(float(v)) for v in out.split(","))


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
        subprocess.run(["osascript", "-e",
                        f'tell application "System Events" to set frontmost of (first process whose unix id is {app.pid}) to true'],
                       capture_output=True)
        time.sleep(2)
        wx, wy, ww, wh = window_frame(app.pid)
        log("window:", wx, wy, ww, wh)

        log("== open menu ==")
        ax_press(app.pid, "打开菜单")
        time.sleep(1.5)
        close_target = ax_frame(app.pid, "关闭菜单")
        log("close-menu button center:", close_target)
        if close_target:
            subprocess.run(["screencapture", "-x", "/tmp/mac-gui-e2e-shot.png"])
            subprocess.run(["cliclick", f"m:{close_target[0]},{close_target[1]}", "w:250", "c:2220,668"])
            time.sleep(1.5)
            still_open = ax_frame(app.pid, "关闭菜单") is not None
            log("menu still open after real click:", still_open)
            if not still_open:
                ax_press(app.pid, "打开菜单")
                time.sleep(1.5)
        target = ax_frame(app.pid, "连接")
        log("connect button center:", target)
        if not target:
            log("CONNECT_BUTTON_NOT_FOUND")
            return
        subprocess.run(["cliclick", f"m:{target[0]},{target[1]}", "w:250"])
        for _ in range(40):
            if re.search(r'"connectionState":"connected"', open(LOG, errors="ignore").read()):
                break
            time.sleep(0.5)
        tail = re.findall(r'"connectionState":"[a-z]+"', open(LOG, errors="ignore").read())
        log("connectionState:", tail[-1] if tail else "none")

        log("== takeover ==")
        ax_press(app.pid, "打开菜单")
        time.sleep(1.5)
        target = ax_frame(app.pid, "接管")
        log("takeover button center:", target)
        if target:
            subprocess.run(["cliclick", f"m:{target[0]},{target[1]}", "w:250"])
        time.sleep(3)
        status = fixture.call("status")
        log("control after takeover:", status.get("control"))

        log("== page click ==")
        page_x = wx + 90
        page_y = wy + 28 + 56 + 50
        log("page click at:", page_x, page_y)
        subprocess.run(["cliclick", f"c:{page_x},{page_y}"])
        time.sleep(3)
        sends = re.findall(r"MAC_NATIVE_SEND op=[a-z_]+", open(LOG, errors="ignore").read())
        counts = {}
        for s in sends:
            counts[s] = counts.get(s, 0) + 1
        log("send counts:", counts)

        log("== release via menu ==")
        ax_press(app.pid, "打开菜单")
        time.sleep(1.5)
        target = ax_frame(app.pid, "返回观察")
        log("release button center:", target)
        if target:
            subprocess.run(["cliclick", f"m:{target[0]},{target[1]}", "w:250"])
        time.sleep(3)
        status = fixture.call("status")
        log("control after release:", status.get("control"))
        log("dom:", fixture.call("inspect"))
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
