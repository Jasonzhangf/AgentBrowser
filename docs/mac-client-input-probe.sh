#!/usr/bin/env bash
# Headless probe: does the macOS client's input path reach the remote page?
# Runs fixture + Mac bridge in one process tree so neither dies between steps.
set -u

WT=/Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013
R8=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009
OUT=/tmp/macprobe
rm -rf "$OUT"; mkdir -p "$OUT"

echo "== start fixture =="
mkfifo "$OUT/fix.fifo"
( sleep 900 > "$OUT/fix.fifo" ) &
HOLDER=$!
cd "$WT"
OBSCURA_BIN_DIR="$R8/target/release" OBSCURA_ENDPOINT_BIND_IP=192.168.0.3 \
  target/release/examples/device_fixture < "$OUT/fix.fifo" > "$OUT/fix.out" 2> "$OUT/fix.err" &
FIX=$!

for _ in $(seq 1 60); do
  [ -s "$OUT/fix.out" ] && break
  sleep 0.5
done
head -1 "$OUT/fix.out" > "$OUT/fix.json"
if [ ! -s "$OUT/fix.json" ]; then echo "FIXTURE_FAILED"; cat "$OUT/fix.err"; exit 1; fi
FIXDIR=$(python3 -c 'import json,sys;print(json.load(open("/tmp/macprobe/fix.json"))["fixture"])')
echo "fixture=$FIXDIR"

PAIR="$OUT/pairing"; mkdir -p "$PAIR"; chmod 700 "$PAIR"
python3 - "$FIXDIR/pairing.abconnection" "$PAIR" <<'PY'
import json,pathlib,sys
b=json.load(open(sys.argv[1]));p=pathlib.Path(sys.argv[2])
(p/'endpoint.txt').write_text(b['endpoint']+'\n')
(p/'token').write_text(b['token']+'\n')
PY

send_fix() { printf '%s\n' "$1" > "$OUT/fix.fifo"; }
last_fix() { tail -1 "$OUT/fix.out"; }

echo "== start bridge =="
mkfifo "$OUT/br.fifo"
( sleep 900 > "$OUT/br.fifo" ) &
BHOLDER=$!
AGENTBROWSER_MAC_PAIRING="$PAIR" \
  "$WT/apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMacBridge" \
  < "$OUT/br.fifo" > "$OUT/br.out" 2> "$OUT/br.err" &
BR=$!
sleep 3

send_br() { printf '%s\n' "$1" > "$OUT/br.fifo"; }

echo "== connect =="
send_br '{"id":1,"command":"{\"op\":\"connect\"}"}'
for _ in $(seq 1 60); do
  grep -aq '"connectionState":"connected"' "$OUT/br.out" && break
  sleep 0.5
done
grep -ao '"connectionState":"[a-z]*"' "$OUT/br.out" | tail -1

echo "== fixture status =="
send_fix status; sleep 1; last_fix

read -r EPOCH < <(python3 -c 'import json,sys;print(json.load(open("/tmp/macprobe/fix.json"))["control"]["epoch"])' 2>/dev/null || echo 1)
EPOCH=$(tail -1 "$OUT/fix.out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["control"]["epoch"])')
echo "epoch=$EPOCH"

echo "== takeover =="
send_br "{\"id\":2,\"command\":\"{\\\"op\\\":\\\"takeover\\\",\\\"epoch\\\":$EPOCH}\"}"
sleep 3
send_fix status; sleep 1
EPOCH=$(tail -1 "$OUT/fix.out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["control"]["epoch"])')
tail -1 "$OUT/fix.out" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("control",d["control"])'

echo "== click =="
send_br "{\"id\":3,\"command\":\"{\\\"op\\\":\\\"click\\\",\\\"epoch\\\":$EPOCH,\\\"x\\\":90,\\\"y\\\":50}\"}"
sleep 3
grep -ao '"error":"[A-Z_]*"' "$OUT/br.err" "$OUT/br.out" 2>/dev/null | tail -3

echo "== release =="
send_br "{\"id\":4,\"command\":\"{\\\"op\\\":\\\"release\\\",\\\"epoch\\\":$EPOCH}\"}"
sleep 3
send_fix status; sleep 1
tail -1 "$OUT/fix.out" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("control",d["control"])'
send_fix inspect; sleep 1; last_fix

echo "== bridge stderr =="
tail -5 "$OUT/br.err"
echo "== done =="
kill -TERM "$FIX" "$BR" "$HOLDER" "$BHOLDER" 2>/dev/null
