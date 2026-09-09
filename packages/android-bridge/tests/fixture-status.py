"""Real fixture Status stays read-only while a human owns browser control."""
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile

fixture_binary = Path(sys.argv[1]).resolve(strict=True)
evidence = Path(tempfile.mkdtemp(prefix="ab-fixture-status-"))
print(f"Evidence: {evidence}", flush=True)


def record(stream):
    readable, _, _ = select.select([stream], [], [], 5)
    if not readable:
        raise TimeoutError("Fixture response deadline")
    line = stream.readline()
    if not line:
        raise RuntimeError("Fixture ended before receipt")
    return json.loads(line)


with (evidence / "fixture.log").open("wb") as log:
    fixture = subprocess.Popen([str(fixture_binary)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=log, bufsize=0)
    try:
        ready = record(fixture.stdout)

        def status():
            fixture.stdin.write(b"status\n")
            fixture.stdin.flush()
            return record(fixture.stdout)

        before = status()
        assert before["session_id"] == ready["session"]
        assert before["attachments"] == 1 and before["agent_attached"]
        with socket.socket(socket.AF_UNIX) as human:
            human.settimeout(5)
            human.connect(str(Path(ready["fixture"]) / "host/host.sock"))
            with human.makefile("rwb", buffering=0) as wire:
                assert json.loads(wire.readline())["session_id"] == ready["session"]

                def call(request_id, command):
                    wire.write(json.dumps({"id": request_id, "command": command}).encode() + b"\n")
                    response = json.loads(wire.readline())
                    assert response["type"] == "result" and response["id"] == request_id, response
                    return response["value"]

                attached = call(1, {"type": "attach", "mode": "observe"})
                taken = call(2, {"type": "request_takeover", "epoch": attached["control"]["epoch"]})
                during = status()
                assert during["control"]["phase"]["type"] == "human", during
                assert during["control"]["phase"]["attachment_id"] == taken["attachment_id"]
                assert during["attachments"] == 2
                assert during["attachment_id"] == before["attachment_id"]
                for key in ["session_id", "document_revision", "viewport_revision", "viewport", "next_sequence"]:
                    assert during[key] == before[key], key
                assert status() == during, "Status must not mutate Host control or operation state"
                released = call(3, {"type": "release_control", "epoch": during["control"]["epoch"]})
                after = status()
                assert after["control"] == released["control"]
                assert after["control"]["phase"]["type"] == "agent"
                (evidence / "status.json").write_text(json.dumps(
                    {"before": before, "during": during, "after": after}, indent=2) + "\n")
        print("FIXTURE_READONLY_STATUS_PASS", flush=True)
    finally:
        if fixture.poll() is None:
            fixture.stdin.write(b"quit\n")
            fixture.stdin.flush()
            try:
                fixture.wait(timeout=10)
            except subprocess.TimeoutExpired:
                fixture.terminate()
                fixture.wait(timeout=5)
                raise RuntimeError("Fixture shutdown deadline")
        if fixture.returncode != 0:
            raise RuntimeError(f"Fixture failed: {fixture.returncode}")
