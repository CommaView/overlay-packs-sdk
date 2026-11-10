#!/usr/bin/env bash
# Checks every example overlay pack (examples/) with tools/check-pack.py and writes its
# bundle, <id>.cvpack, into dist/extensions/ (ignored by git), ready to send to a phone and install from a file.
#
#   tools/build-examples.sh [OUT_DIR [ID ...]]
#
# With IDs, only those examples.
#
# Each pack is checked against the stand-in UI it's written for (the arguments below); the safety-limit test packs
# are expected to end turned off. Exits 1 if any pack fails its check.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO_ROOT/dist/extensions}"
shift || true
CHECK="$REPO_ROOT/tools/check-pack.py"
EXAMPLES="$REPO_ROOT/examples"

# The check-pack arguments each example is checked with: the fork modules and settings it needs, the services'
# messages it reads, and the state it should end in.
pack_args() {
  case "$1" in
    bluepath)
      echo --module openpilot.selfdrive.ui.sunnypilot.onroad.blue_path --attr rainbow_path=true --attr rainbow_mode_style=1 ;;
    carrot-hud)
      echo --module openpilot.selfdrive.ui.carrot_param_cache \
        --service 'longitudinalPlan={"desiredDistance":24.6}' \
        --service 'carrotMan={"xSpdLimit":50,"xSignType":1,"nRoadLimitSpeed":60}' ;;
    bluepilot)
      echo --module openpilot.selfdrive.ui.bp.onroad.blindspot_renderer \
        --service 'carStateBP={"hybridDrive":{"dataAvailable":true,"throttleDemandPercent":51,"powerFlowMode":"Idle"},"hybridBattery":{"dataAvailable":true,"socActual":63,"voltActual":272,"ampsActual":-12}}' \
        --service 'controllerStateBP={"activeLateralMode":"curvature"}' ;;
    dragonpilot-hud)
      echo --module dragonpilot.selfdrive.ui.dashy_qr --attr dp_ui_lead=3 --attr dp_ui_rainbow=true --attr dp_ui_hide_hud_speed_ms=0 \
        --service 'liveTracks={"points":[{"trackId":3,"dRel":18.0,"yRel":3.1,"vRel":0.4}]}' \
        --service 'carOutput={"actuatorsOutput":{"torque":-0.4}}' ;;
    pack-test-slow|pack-test-broken)
      echo --expect disabled ;;
    *)
      echo ;;
  esac
}

packs=()
if (($#)); then
  for id in "$@"; do
    [[ "$id" =~ ^[a-z0-9][a-z0-9-]{0,31}$ && -f "$EXAMPLES/$id/manifest.json" ]] || {
      echo "No example pack $id in $EXAMPLES" >&2
      exit 2
    }
    packs+=("$EXAMPLES/$id/")
  done
else
  packs=("$EXAMPLES"/*/)
fi

mkdir -p "$OUT"
failed=()
for pack in "${packs[@]}"; do
  id="$(basename "$pack")"
  read -r -a args <<<"$(pack_args "$id")"
  echo "== $id"
  # ${args[@]+...}: an empty array is unbound to bash before 4.4 under set -u (macOS's bash 3.2).
  if ! python3 "$CHECK" "$pack" ${args[@]+"${args[@]}"} --bundle "$OUT/$id.cvpack"; then
    failed+=("$id")
  fi
done
if ((${#failed[@]})); then
  echo "FAIL: ${failed[*]}" >&2
  exit 1
fi
echo "PASS: the example packs' bundles are in $OUT"
