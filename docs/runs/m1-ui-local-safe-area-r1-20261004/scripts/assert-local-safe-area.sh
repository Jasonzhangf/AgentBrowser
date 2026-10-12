#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
raw="$root/raw"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

pass() {
  printf 'PASS: %s\n' "$*"
}

node_bounds() {
  local xml=$1
  local xpath=$2
  local bounds
  bounds=$(xmllint --xpath "string($xpath/@bounds)" "$xml")
  if [[ ! $bounds =~ ^\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]$ ]]; then
    fail "invalid bounds for $xpath in $xml: $bounds"
  fi
  printf '%s %s %s %s\n' \
    "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" \
    "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
}

inset_bounds() {
  local dumpsys=$1
  local type=$2
  local line
  line=$(grep -m1 "type=${type} frame=" "$dumpsys" || true)
  if [[ ! $line =~ frame=\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\] ]]; then
    fail "missing ${type} frame in $dumpsys"
  fi
  printf '%s %s %s %s\n' \
    "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" \
    "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
}

assert_local_shell_bounds() {
  local label=$1
  local xml=$2
  local dumpsys=$3
  local expected_rotation=$4
  local rotation
  local toolbar address menu status nav
  local tl tt tr tb al at ar ab ml mt mr mb sl st sr sb nl nt nr nb

  rotation=$(xmllint --xpath "string(/hierarchy/@rotation)" "$xml")
  [[ $rotation == "$expected_rotation" ]] || \
    fail "$label rotation: expected $expected_rotation, got $rotation"

  read -r toolbar < <(node_bounds "$xml" "//node[@text='浏览器工具栏']")
  read -r tl tt tr tb <<<"$toolbar"
  read -r address < <(node_bounds "$xml" "//node[@resource-id='address']")
  read -r al at ar ab <<<"$address"
  read -r menu < <(node_bounds "$xml" "//node[@resource-id='drawer-toggle']")
  read -r ml mt mr mb <<<"$menu"
  read -r status < <(inset_bounds "$dumpsys" statusBars)
  read -r sl st sr sb <<<"$status"
  read -r nav < <(inset_bounds "$dumpsys" navigationBars)
  read -r nl nt nr nb <<<"$nav"

  (( at >= sb )) || fail "$label address overlaps status bar: address=$at status_bottom=$sb"
  (( tt >= st )) || fail "$label toolbar starts above status bar: toolbar=$tt status_top=$st"
  (( tb <= nt )) || fail "$label toolbar extends below navigation top: toolbar_bottom=$tb nav_top=$nt"
  (( mt >= sb )) || fail "$label menu overlaps status bar: menu_top=$mt status_bottom=$sb"
  (( mb <= nt )) || fail "$label menu extends below navigation top: menu_bottom=$mb nav_top=$nt"
  (( ar <= nr && ab <= nt )) || fail "$label address exceeds available bounds"

  printf '%s toolbar=%s address=%s menu=%s statusBars=%s navigationBars=%s\n' \
    "$label" "$toolbar" "$address" "$menu" "$status" "$nav"
}

portrait_xml="$raw/10-installed-green-portrait-full.xml"
portrait_dumpsys="$raw/11-installed-green-window-displays.txt"
landscape_xml="$raw/40-installed-green-landscape.xml"
landscape_dumpsys="$raw/41-installed-green-landscape-window-displays.txt"
restored_xml="$raw/50-installed-green-restored-portrait.xml"
restored_dumpsys="$raw/51-installed-green-restored-window-displays.txt"

assert_local_shell_bounds portrait "$portrait_xml" "$portrait_dumpsys" 0
assert_local_shell_bounds landscape "$landscape_xml" "$landscape_dumpsys" 1
assert_local_shell_bounds restored "$restored_xml" "$restored_dumpsys" 0

portrait_address=$(node_bounds "$portrait_xml" "//node[@resource-id='address']")
restored_address=$(node_bounds "$restored_xml" "//node[@resource-id='address']")
[[ $portrait_address == "$restored_address" ]] || \
  fail "portrait restore changed address bounds: $portrait_address -> $restored_address"
pass "portrait restore preserved address bounds: $portrait_address"

drawer_open="$raw/20-installed-green-drawer-open.xml"
drawer_closed="$raw/30-installed-green-back-closed.xml"
drawer_activities="$raw/31-installed-green-back-activities.txt"

read -r drawer < <(node_bounds "$drawer_open" "//node[@resource-id='browser-drawer']")
read -r dl dt dr db <<<"$drawer"
read -r drawer_status < <(inset_bounds "$portrait_dumpsys" statusBars)
read -r dsl dst dsr dsb <<<"$drawer_status"
read -r drawer_nav < <(inset_bounds "$portrait_dumpsys" navigationBars)
read -r dnl dnt dnr dnb <<<"$drawer_nav"
(( dt >= dsb )) || fail "drawer overlaps status bar: drawer_top=$dt status_bottom=$dsb"
(( db <= dnb )) || fail "drawer exceeds display bottom: drawer_bottom=$db display_bottom=$dnb"
[[ $(xmllint --xpath "string(//node[@resource-id='drawer-toggle']/@text)" "$drawer_open") == "关闭菜单" ]] || \
  fail "visible menu control did not report the open state"
pass "drawer open bounds=$drawer status_bottom=$dsb navigation_bottom=$dnb"

[[ $(xmllint --xpath "count(//node[@resource-id='browser-drawer'])" "$drawer_closed") == "0" ]] || \
  fail "system Back left the drawer attached"
[[ $(xmllint --xpath "string(//node[@resource-id='drawer-toggle']/@text)" "$drawer_closed") == "打开菜单" ]] || \
  fail "system Back did not restore the closed menu control"
grep -q 'topResumedActivity=.*com\.agentbrowser\.probe/\.MainActivity' "$drawer_activities" || \
  fail "probe activity was not resumed after system Back"
pass "system Back closed the drawer and kept the probe activity resumed"

instrument_log="$raw/62-installed-green-instrument.log"
grep -q '^OK (2 tests)$' "$instrument_log" || fail "instrumentation did not report OK (2 tests)"
if grep -q 'FAILURES!!!' "$instrument_log"; then
  fail "instrumentation reported failures"
fi
pass "installed LayoutDeviceTest reported OK (2 tests)"

binding="$raw/64-installed-green-base-binding.txt"
grep -q '^byte_compare=match$' "$binding" || fail "installed APK bytes do not match candidate"
installed_hash=$(grep '^installed_base_sha256=' "$binding" | cut -d= -f2)
candidate_hash=$(grep '^candidate_apk_sha256=' "$binding" | cut -d= -f2)
[[ $installed_hash == "$candidate_hash" ]] || fail "installed/candidate hashes differ"
pass "installed APK bytes match candidate: $installed_hash"
