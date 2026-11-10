"""
The runtime half of the dragonpilot HUD pack: what dragonpilot (dragonpilot/dragonpilot, 0.11.1 and pre-build) adds
to the comma 3X's onroad screen that CommaView doesn't export, read the way dragonpilot's own UI reads it;
overlay.json draws it on the app's comma 3X design (overlay level 2's `screen`), whichever comma sends it.
dragonpilot's comma four screen adds only ALKA, which the runtime exports itself.

- The rainbow path (selfdrive/ui/onroad/model_renderer.py, _update_rainbow_gradient): dp_ui_rainbow. overlay.json
  turns its phase with the car's speed as dragonpilot does (integrate).
- The lead stats and radar tracks (model_renderer.py, dp_ui_lead): which of them the setting shows, and the
  nearest liveTracks points ahead, a list overlay.json repeats a group over. The lead's own speed and distance are the
  app's (lead.*): overlay.json reads them.
- Hide HUD above a speed (augmented_road_view.py, dp_ui_hide_hud_speed_kph): the speed in m/s, 0 when off.
- The border indicators (selfdrive/ui/onroad/augmented_road_view.py): always on; the car's own blinkers
  (carState.leftBlinker/rightBlinker), which overlay.json draws them from with the app's blindspot.* (the app's
  blinker.* also counts openpilot's lane change blinker, which dragonpilot's bars don't show).
- The torque bar (selfdrive/ui/onroad/hud_renderer.py, the comma four's TorqueBar at 4x): the torque it shows,
  -carOutput.actuatorsOutput.torque, and whether it shows (not on a car that steers by angle, and faded out while
  dragonpilot's status is disengaged, ALKA's too).

requiresModules names dragonpilot's dashy_qr, which its comma 3X sidebar imports and its UI loads on every device,
so the pack costs nothing on another fork.

SDK 1 (docs/extensions.md).
"""

import heapq

# dp_ui_lead: off, lead, radar, all.
LEAD_STATS = (1, 3)
RADAR_TRACKS = (2, 3)
# The radar points overlay.json repeats over (build-overlay.py reads this for the repeat's max): the nearest ahead.
# A repeat may make 64 copies, but each counts its 5 layers (the dot, dragonpilot's four lines) toward overlay.json's
# 128: 22 with the rest of the pack's 16.
TRACK_SLOTS = 22
# Where overlay.json's car pin can put a point: up to 250 m ahead and 20 m to either side.
TRACK_AHEAD = 250.0
TRACK_SIDE = 20.0


def _number(value, default=0.0):
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if number == number and abs(number) != float("inf") else default


def _status(ui_state) -> str:
  # dragonpilot's own UIStatus (ALKA steering a disengaged car is still disengaged here, as on its screen).
  status = getattr(ui_state, "status", "")
  return str(getattr(status, "value", status))


class Extension:
  def __init__(self):
    # The last liveTracks message's tracks: (its logMonoTime and the message, the tracks). liveTracks comes at 20 Hz,
    # the UI draws faster: each message is sorted once.
    self._tracks_cache: tuple[object, list] = (None, [])

  def applies(self, ctx) -> bool:
    # dragonpilot's UIState, on either comma: overlay.json draws on the app's comma 3X design, whichever it is.
    return hasattr(ctx.ui_state, "dp_ui_lead")

  def _tracks(self, ctx, shown: bool) -> list:
    tracks = ctx.service("liveTracks") if shown and ctx.fresh("liveTracks") else None
    if tracks is None:
      return []
    sm = getattr(ctx.ui_state, "sm", None)
    key = (getattr(sm, "logMonoTime", {}).get("liveTracks"), id(tracks))
    if self._tracks_cache[0] != key:
      self._tracks_cache = (key, self._sorted_tracks(tracks))
    return self._tracks_cache[1]

  @staticmethod
  def _sorted_tracks(tracks) -> list:
    # Each point read once: (id, d, y, v).
    points = [
      (
        int(_number(getattr(p, "trackId", 0))),
        _number(getattr(p, "dRel", -1.0), -1.0),
        _number(getattr(p, "yRel", 0.0)),
        _number(getattr(p, "vRel", 0.0)),
      )
      for p in (getattr(tracks, "points", None) or [])
    ]
    # Only points the pin can place (one outside its range would be drawn at the range's edge), the TRACK_SLOTS
    # nearest; sent farthest first, so the repeat draws the nearest, which matters most, on top of the labels behind it.
    ahead = heapq.nsmallest(
      TRACK_SLOTS, (p for p in points if 0.0 <= p[1] <= TRACK_AHEAD and abs(p[2]) <= TRACK_SIDE), key=lambda p: p[1]
    )
    return [
      {"id": track_id, "d": round(d, 2), "y": round(y, 2), "v": round(v, 2)} for track_id, d, y, v in reversed(ahead)
    ]

  def _torque(self, ctx) -> dict:
    controls = ctx.service("controlsState")
    lateral = getattr(controls, "lateralControlState", None) if controls is not None else None
    # A torque car only once controlsState says so: before its first message an angle car's bar would flash.
    try:
      torque_car = lateral is not None and lateral.which() != "angleState"
    except Exception:
      torque_car = False
    output = ctx.service("carOutput")
    actuators = getattr(output, "actuatorsOutput", None) if output is not None else None
    torque = -_number(getattr(actuators, "torque", 0.0)) if actuators is not None else 0.0
    status = _status(ctx.ui_state)
    return {
      # dragonpilot's comma 3X HUD draws it for torque cars, faded out while its status is disengaged.
      "torqueBar": torque_car and status != "disengaged",
      "torque": round(max(-1.0, min(1.0, torque)), 3),
      # Its colours: white to orange with torque while engaged, a faint white otherwise.
      "torqueEngaged": status == "engaged",
    }

  def _blinker(self, ctx) -> dict:
    # carState's own signals, which dragonpilot's border indicators read (not openpilot's lane change blinker).
    car = ctx.service("carState")
    return {
      "left": bool(getattr(car, "leftBlinker", False)) if car is not None else False,
      "right": bool(getattr(car, "rightBlinker", False)) if car is not None else False,
    }

  def payload(self, ctx) -> dict:
    ui = ctx.ui_state
    mode = int(_number(getattr(ui, "dp_ui_lead", 0)))
    payload = {
      "rainbow": bool(getattr(ui, "dp_ui_rainbow", False)),
      "leadStats": mode in LEAD_STATS,
      "blinker": self._blinker(ctx),
      "hideHudSpeed": round(max(0.0, _number(getattr(ui, "dp_ui_hide_hud_speed_ms", 0.0))), 3),
      **self._torque(ctx),
    }
    # No tracks while the setting doesn't show them: overlay.json repeats over none.
    payload["tracks"] = self._tracks(ctx, mode in RADAR_TRACKS)
    return payload
