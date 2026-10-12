#!/usr/bin/env python3
"""Black-box wrong-token slice for the §7.2 connection path.

The token comparison is the only check left on the connection path, so it needs
one binding test. This slice drives the LIVE endpoint with a token that is not
the configured one, and asserts the two outcomes a refusal must produce:

  1. the refusal frame carries the code ``REMOTE_TOKEN_INVALID``;
  2. no session is created -- the connection never delivers the ``ready`` frame
     that opens a session.

Both assertions are proven sensitive by the counterexample in
``evidence/task47-ime-raise/wrong-token-negative-control/``: run with
``WRONG_TOKEN_CONTROL=configured`` the slice sends the driven Host's own token,
the comparison accepts it, a session opens, and both assertions go red.

Environment:
  HOST_SERVICE_ROOT          private service state root (required)
  HOST_SERVICE_LAUNCHER      path to the host-service entrypoint (required)
  WRONG_TOKEN_EVIDENCE_DIR   output directory (default: evidence/wrong-token-slice)
  WRONG_TOKEN_CONTROL        set to ``configured`` for the counterexample run only
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import ssl
import subprocess
import sys

import websockets.sync.client

ROOT = pathlib.Path(__file__).resolve().parent.parent
launcher = pathlib.Path(os.environ["HOST_SERVICE_LAUNCHER"]).resolve()
service_root = pathlib.Path(os.environ["HOST_SERVICE_ROOT"]).expanduser()
evidence = pathlib.Path(
    os.environ.get("WRONG_TOKEN_EVIDENCE_DIR", str(ROOT / "evidence" / "wrong-token-slice"))
)
evidence.mkdir(parents=True, exist_ok=True)

gates: dict[str, dict[str, object]] = {}


def gate(name: str, ok: bool, detail: str) -> bool:
    gates[name] = {"result": "PASS" if ok else "FAIL", "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    return ok


status = json.loads(
    subprocess.run(
        [str(launcher), "trust", "status", "--root", str(service_root)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
)
endpoint = f"{status['endpoint_host']}:{int(status['endpoint_port'])}"
assert status["endpoint_state"] == "refreshed", status

configured = (service_root / "trust" / "token").read_text().strip()
# Negative control (docs/m1/test-audit.md 3.2/6): with WRONG_TOKEN_CONTROL=configured
# the slice sends the driven Host's OWN configured token. The comparison must then
# accept it, so the slice MUST go red. This proves the assertions are sensitive to the
# comparison outcome. It is a counterexample run, not a normal mode.
control = os.environ.get("WRONG_TOKEN_CONTROL", "")
if control == "configured":
    wrong = configured
    print("NEGATIVE CONTROL: sending the Host's own configured token", flush=True)
elif control:
    raise SystemExit(f"unknown WRONG_TOKEN_CONTROL={control!r}")
else:
    wrong = "wrong-" + secrets.token_urlsafe(24)
    assert wrong != configured, "the wrong token must differ from the configured one"

# The endpoint's own server certificate is the only trusted CA for this endpoint.
context = ssl.create_default_context()
context.load_verify_locations(
    cadata=ssl.DER_cert_to_PEM_cert((service_root / "trust" / "server.der").read_bytes())
)
context.check_hostname = True

receipt: dict[str, object] = {
    "endpoint": endpoint,
    "negativeControl": control or None,
    "wrongTokenSha256Prefix": hashlib.sha256(wrong.encode()).hexdigest()[:16],
    "configuredTokenSha256": status["token_sha256"],
}

refusal_line = None
frames: list[str] = []
closed = False

with websockets.sync.client.connect(
    f"wss://{endpoint}/control",
    ssl=context,
    additional_headers={"Authorization": f"Bearer {wrong}"},
    open_timeout=15,
) as socket:
    try:
        refusal_line = socket.recv(timeout=15)
        frames.append(str(refusal_line))
    except Exception as error:  # noqa: BLE001 - the raw outcome is the evidence
        refusal_line = f"<no frame: {type(error).__name__}: {error}>"
    try:
        extra = socket.recv(timeout=5)
        if extra is not None:
            frames.append(str(extra))
    except Exception as error:  # noqa: BLE001 - closure is the expected outcome
        closed = True
        receipt["closeReason"] = f"{type(error).__name__}: {error}"

# A session opens with `{"type":"ready",...}`. It can be the first frame, so every
# frame counts -- otherwise an accepted token would look like a refusal.
ready_seen = any('"ready"' in frame for frame in frames)

receipt["refusalLine"] = str(refusal_line)
receipt["frames"] = frames
receipt["closed"] = closed

gate(
    "wrong-token-refused-with-code",
    isinstance(refusal_line, str) and "REMOTE_TOKEN_INVALID" in refusal_line,
    f"raw={refusal_line}",
)
gate("wrong-token-no-session-stream", not ready_seen, f"readyFrame={ready_seen} closed={closed}")

receipt["gates"] = gates
(evidence / "wrong-token.json").write_text(json.dumps(receipt, indent=2) + "\n")
(evidence / "wrong-token-raw.txt").write_text(
    f"endpoint={endpoint}\nnegative_control={control or 'off'}\n"
    f"refusal_line={refusal_line}\nframes={frames}\n"
)
failed = [name for name, value in gates.items() if value["result"] != "PASS"]
print(json.dumps({"failed": failed, "gates": gates}, indent=2), flush=True)
sys.exit(1 if failed else 0)
