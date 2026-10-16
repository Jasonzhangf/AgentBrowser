#!/usr/bin/env python3
"""Drive the Android AgentBrowser UI through the WebView DevTools channel.

Usage:
    adb -s <serial> shell am start -n com.agentbrowser.probe/.MainActivity
    python3 docs/android-ui-drive.py --serial emulator-5554 --endpoint <ip:port>

Requires the fixture pairing bundle at /sdcard/Download/pairing.abconnection.
"""

import argparse
import json
import subprocess
import time

import websocket
from PIL import Image

DEVTOOLS_PORT = 19222


class Device:
    def __init__(self, serial):
        self.serial = serial

    def sh(self, args):
        return subprocess.run(["adb", "-s", self.serial] + args,
                              capture_output=True, text=True).stdout.strip()

    def tap(self, x, y):
        self.sh(["shell", "input", "tap", str(int(x)), str(int(y))])

    def screenshot(self, path):
        with open(path, "wb") as handle:
            subprocess.run(["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
                           stdout=handle)
        return path


class WebView:
    """Minimal client for the WebView DevTools Runtime.evaluate endpoint."""

    def __init__(self, serial, port=DEVTOOLS_PORT):
        pid = subprocess.run(["adb", "-s", serial, "shell", "pidof",
                              "com.agentbrowser.probe"],
                             capture_output=True, text=True).stdout.strip()
        subprocess.run(["adb", "-s", serial, "forward", "--remove", "tcp:%d" % port],
                       capture_output=True)
        subprocess.run(["adb", "-s", serial, "forward", "tcp:%d" % port,
                        "localabstract:webview_devtools_remote_%s" % pid],
                       capture_output=True)
        listing = subprocess.run(
            ["curl", "-s", "--max-time", "8", "http://127.0.0.1:%d/json" % port],
            capture_output=True, text=True).stdout
        for entry in json.loads(listing):
            if entry.get("type") == "page":
                self.url = entry["webSocketDebuggerUrl"]
                break
        else:
            raise SystemExit("no page target on the WebView DevTools endpoint")

    def js(self, expression):
        socket = websocket.create_connection(self.url, timeout=15, suppress_origin=True)
        try:
            socket.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": expression,
                                               "returnByValue": True}}))
            while True:
                message = json.loads(socket.recv())
                if message.get("id") == 1:
                    return message.get("result", {}).get("result", {}).get("value")
        finally:
            socket.close()

    def metrics(self):
        width, height = json.loads(self.js("JSON.stringify([innerWidth,innerHeight])"))
        return width, height, 1080.0 / width, 2400.0 / height

    def rect(self, selector):
        value = self.js("(()=>{const e=document.querySelector(%r);if(!e)return null;"
                        "const r=e.getBoundingClientRect();"
                        "return JSON.stringify([r.x,r.y,r.width,r.height]);})()" % selector)
        return json.loads(value) if value else None


def visible(rect, width, height):
    return bool(rect) and rect[0] >= -2 and rect[0] + rect[2] <= width + 2 \
        and rect[1] >= -2 and rect[1] + rect[3] <= height + 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--endpoint", required=True,
                        help="fixture endpoint, only used to report the LAN address")
    parser.add_argument("--clicks", type=int, default=3)
    args = parser.parse_args()

    device = Device(args.serial)
    view = WebView(args.serial)
    width, height, scale_x, scale_y = view.metrics()
    print("css viewport", (width, height), "scale", (round(scale_x, 3), round(scale_y, 3)))

    def tap_element(selector, label):
        rect = view.rect(selector)
        if not visible(rect, width, height):
            raise SystemExit("%s is not visible: %r" % (label, rect))
        x = (rect[0] + rect[2] / 2) * scale_x
        y = (rect[1] + rect[3] / 2) * scale_y
        device.tap(x, y)
        print("tap", label, "->", (round(x), round(y)))
        time.sleep(3)

    if not visible(view.rect("#drawer-close"), width, height):
        tap_element("#drawer-toggle", "drawer-toggle")
    tap_element("#connection-import", "import")
    device.tap(247, 884)
    time.sleep(6)
    if not visible(view.rect("#drawer-close"), width, height):
        tap_element("#drawer-toggle", "drawer-toggle")
    print("setup label:", view.js("(document.querySelector('#connection-setup')||{}).textContent"))

    tap_element("#connect", "connect")
    time.sleep(9)

    tap_element("#drawer-close", "drawer-close")
    image = Image.open(device.screenshot("/tmp/android-page.png")).convert("RGB")
    pixels = image.load()
    red = [x for y in range(0, image.height, 2) for x in range(0, image.width, 2)
           if pixels[x, y][0] > 190 and pixels[x, y][1] < 70 and pixels[x, y][2] < 70]
    print("red pixels before takeover:", len(red))

    tap_element("#drawer-toggle", "drawer-toggle")
    tap_element("#takeover", "takeover")
    tap_element("#drawer-close", "drawer-close")

    image = Image.open(device.screenshot("/tmp/android-page.png")).convert("RGB")
    pixels = image.load()
    xs = []
    ys = []
    for y in range(0, image.height, 3):
        for x in range(0, image.width, 3):
            r, g, b = pixels[x, y]
            if r > 190 and g < 70 and b < 70:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise SystemExit("remote page button is not on screen")
    cx = (min(xs) + max(xs)) // 2
    cy = (min(ys) + max(ys)) // 2
    print("remote button box", (min(xs), min(ys), max(xs), max(ys)))
    for _ in range(args.clicks):
        device.tap(cx, cy)
        time.sleep(3)

    tap_element("#drawer-toggle", "drawer-toggle")
    tap_element("#release", "release")
    print("release done")


if __name__ == "__main__":
    main()
