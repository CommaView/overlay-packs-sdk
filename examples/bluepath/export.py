"""
The runtime half of the blue path pack: the blue style of RainbowMode and the predicted stop marker that
TonyBinheWu/sunnypilot's hkg-enhanced draws (selfdrive/ui/sunnypilot/onroad/blue_path.py, predicted_stop_marker.py).

The fork already decides when the model predicts a stop (blue_path.predicted_stop); this pack only reads its
settings, asks the fork's own function, and holds the marker's distance from frame to frame the way the fork's
PredictedStopMarker does. The app's half (overlay.json) draws what payload() returns.

SDK 1 (docs/extensions.md): a class named Extension with
  applies(ctx) -> bool  optional, until it's True once: whether this UI is one the pack serves
  observe(ctx)          optional, every UI frame (60 Hz): keep whatever state the pack needs
  payload(ctx) -> dict  up to 20 times a second: what the app gets as uiStateOnroad.ext["bluepath"]
"""

import math

BLUE_PATH = "openpilot.selfdrive.ui.sunnypilot.onroad.blue_path"
# The comma four's UI draws the marker whenever engaged in any way; the comma 3X's only while engaged or steering
# only, as the blue path draws on both. Which one: the UI's application says (gui_app.big_ui() on a 3/3X); both
# layouts' modules are loaded on every comma, so a loaded module says nothing.
APPLICATION = "openpilot.system.ui.lib.application"
# DynamicBluePath: a stop has to hold this long before its own line and badge show.
STOP_HOLD = 0.3


class Extension:
  def __init__(self):
    self._marker_distance = None
    self._marker_time = None
    self._saw_standstill = False
    self._stop_since = None
    self._stop: tuple[bool, float | None] = (False, None)
    self._stop_message = None
    self._blue_drawn = False
    self._comma_four: bool | None = None

  def applies(self, ctx) -> bool:
    # The fork's UI state carries the blue style's setting.
    return hasattr(ctx.ui_state, "rainbow_mode_style")

  def _comma_four_ui(self, ctx) -> bool:
    if self._comma_four is None:
      gui_app = getattr(ctx.module(APPLICATION), "gui_app", None)
      try:
        self._comma_four = gui_app is not None and not bool(gui_app.big_ui())
      except Exception:
        self._comma_four = False
    return self._comma_four

  def _reset_marker(self) -> None:
    self._marker_distance = None
    self._marker_time = None
    self._saw_standstill = False

  def observe(self, ctx) -> None:
    ui = ctx.ui_state
    now = ctx.now
    marker_drawn = ctx.status != "disengaged" if self._comma_four_ui(ctx) else ctx.engaged
    # DynamicBluePath draws (and holds its stop) only with its style on and while engaged or steering only.
    self._blue_drawn = (
      ctx.engaged and bool(getattr(ui, "rainbow_path", False)) and int(getattr(ui, "rainbow_mode_style", 0) or 0) == 1
    )
    predicted_stop = getattr(ctx.module(BLUE_PATH), "predicted_stop", None)
    if predicted_stop is None or not marker_drawn or not ctx.fresh("modelV2", "carState"):
      self._reset_marker()
      self._stop_since = None
      self._stop = (False, None)
      self._stop_message = None
      return
    # The model's stop changes only with a new modelV2 message (20 Hz): read once per message, not per UI frame.
    message = ui.sm.recv_frame["modelV2"]
    if message != self._stop_message:
      self._stop_message = message
      # Until the call returns, no stop: one that raises leaves none, not the last message's.
      self._stop = (False, None)
      stopping, distance = predicted_stop(ui.sm["modelV2"])
      distance = None if distance is None else float(distance)
      # NaN or infinity is no distance: payload() must stay JSON.
      self._stop = (bool(stopping), distance if distance is not None and math.isfinite(distance) else None)
    stopping, distance = self._stop
    # The blue path's own hold starts over whenever it isn't drawn, as DynamicBluePath.reset does.
    held = stopping and self._blue_drawn
    self._stop_since = (self._stop_since if self._stop_since is not None else now) if held else None
    if not bool(getattr(ui, "predicted_stop_marker", False)):
      self._reset_marker()
      return
    speed = max(0.0, float(getattr(ui.sm["carState"], "vEgo", 0.0) or 0.0))
    dt = 0.0 if self._marker_time is None else max(0.0, min(now - self._marker_time, 0.25))
    self._marker_time = now
    if stopping and distance is not None:
      self._marker_distance = max(0.0, float(distance))
    elif self._marker_distance is not None and not self._saw_standstill:
      # The model's stop flickered out: the marker keeps closing in at the car's speed.
      self._marker_distance = max(0.0, self._marker_distance - speed * dt)
    if self._marker_distance is None:
      return
    if speed < 0.25:
      self._saw_standstill = True
    elif self._saw_standstill and speed > 0.8:
      self._reset_marker()

  def payload(self, ctx) -> dict:
    ui = ctx.ui_state
    marker_on = bool(getattr(ui, "predicted_stop_marker", False))
    held = self._blue_drawn and self._stop_since is not None and ctx.now - self._stop_since >= STOP_HOLD
    stopping, distance = self._stop
    return {
      # RainbowModeStyle 1 under RainbowMode: blue instead of the rainbow.
      "enabled": bool(getattr(ui, "rainbow_path", False)) and int(getattr(ui, "rainbow_mode_style", 0) or 0) == 1,
      # PredictedStopMarker's latched distance, metres ahead; null without one.
      "stopMarker": None if self._marker_distance is None else round(self._marker_distance, 2),
      # Without the marker setting, the blue path's own line and "Model predicts stop" badge: a stop held 0.3 s.
      "stopBar": round(distance, 2) if (not marker_on and held and stopping and distance is not None) else None,
      "stopBadge": bool(not marker_on and held),
    }
