"""
The runtime half of the carrotpilot HUD pack: two things carrotpilot (ajouatom/openpilot, carrot-wip) draws on the
comma 3X's onroad screen that CommaView doesn't export. Both are read the way carrotpilot's
own UI reads them; overlay.json draws them.

- The follow-distance line (selfdrive/ui/onroad/model_renderer.py, _draw_tf_marker_carrot): a white line across
  the path at longitudinalPlan.desiredDistance, the distance carrotpilot's planner wants behind the lead.
- The speed limit box (selfdrive/ui/onroad/hud_renderer.py, _draw_carrot_speed_limit_box): carrotMan's speed
  camera limit (xSpdLimit, "CAM", unless xSignType is 22) or else the road's limit (nRoadLimitSpeed, "LIMIT"), in
  the comma's units; blinking red and yellow for a camera (overlay.json, over the app's clock), and red when the
  car is more than 2 over the road's limit.

The lead's radar distance box is drawn by overlay.json alone, from the app's own lead.distance.

requiresModules names carrotpilot's carrot_param_cache, which its UIState imports on every device, so the pack
costs nothing on another fork. applies() leaves out the comma four: its carrotpilot HUD draws neither.

SDK 1 (docs/extensions.md).
"""

# Which screen: the UI's application says (gui_app.big_ui() on a 3/3X); both layouts' modules are loaded on every
# comma, so a loaded module says nothing.
APPLICATION = "openpilot.system.ui.lib.application"
KM_TO_MILE = 0.621371
MS_TO_KPH = 3.6
MS_TO_MPH = 2.236936
# carrotpilot's HUD shows xSpdLimit as a camera unless the sign is this type.
NOT_A_CAMERA_SIGN = 22
# Over the road's limit by more than this (in the comma's units), the box turns red.
OVER_LIMIT_MARGIN = 2


def _number(value, default=0.0):
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if number == number and abs(number) != float("inf") else default


class Extension:
  def __init__(self):
    self._cluster_speed_seen = False

  def applies(self, ctx) -> bool:
    # The comma 3X's carrotpilot HUD draws both; the comma four's draws neither.
    gui_app = getattr(ctx.module(APPLICATION), "gui_app", None)
    try:
      return gui_app is None or bool(gui_app.big_ui())
    except Exception:
      return True

  def _display_speed(self, ctx, metric: bool) -> float:
    # carrotpilot's HUD speed: the cluster's speed once the car has sent one, else vEgo.
    car = ctx.service("carState")
    if car is None:
      return 0.0
    cluster = _number(getattr(car, "vEgoCluster", 0.0))
    self._cluster_speed_seen = self._cluster_speed_seen or cluster != 0.0
    v_ego = cluster if self._cluster_speed_seen else _number(getattr(car, "vEgo", 0.0))
    return max(0.0, v_ego * (MS_TO_KPH if metric else MS_TO_MPH))

  def payload(self, ctx) -> dict:
    metric = bool(getattr(ctx.ui_state, "is_metric", True))

    follow = None
    if ctx.fresh("longitudinalPlan"):
      distance = _number(getattr(ctx.service("longitudinalPlan"), "desiredDistance", 0.0))
      follow = round(distance, 1) if distance > 0.0 else None

    carrot = ctx.service("carrotMan")
    camera_kph = int(_number(getattr(carrot, "xSpdLimit", 0))) if carrot is not None else 0
    sign = int(_number(getattr(carrot, "xSignType", 0))) if carrot is not None else 0
    road_kph = int(_number(getattr(carrot, "nRoadLimitSpeed", 0))) if carrot is not None else 0
    camera = camera_kph > 0 and sign != NOT_A_CAMERA_SIGN
    limit_kph = camera_kph if camera else road_kph
    limit = limit_kph if metric else int(limit_kph * KM_TO_MILE + 0.5)
    over = not camera and limit > 0 and self._display_speed(ctx, metric) > limit + OVER_LIMIT_MARGIN

    return {
      # Metres ahead; null while the planner has none.
      "followDistance": follow,
      # The box's number in the comma's units; null with no limit to show.
      "speedLimit": limit if limit > 0 else None,
      "camera": camera,
      "overLimit": over,
    }
