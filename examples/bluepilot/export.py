"""
The runtime half of the BluePilot pack: what BluePilot (BluePilotDev/bluepilot, bp-7.0) adds to sunnypilot's onroad
screen that CommaView doesn't export, read the way BluePilot's own widgets read it. overlay.json
draws it. Each element follows the BluePilot setting that shows it on the comma's screen, so the app shows what the
driver chose there.

- Hybrid battery (selfdrive/ui/bp/onroad/hybrid_battery_gauge.py) and power flow (power_flow_gauge.py,
  bp/mici/onroad/powerflow_gauge.py): carStateBP.hybridBattery and .hybridDrive, from the Ford's cluster and battery
  CAN messages. FordPrefHybridBatteryStatus, FordPrefHybridPowerFlow.
- Brake lights (hud_renderer_bp.py, _brakes_on): carStateBP.brakeLightStatus. ShowBrakeStatus.
- The lateral mode letter, A / C / OP (_draw_lateral_control_overlay): controllerStateBP.activeLateralMode, sent
  only on Ford BluePilot cars. BpShowLateralControl.
- The confidence ball (bp/mici/onroad/confidence_ball_bp.py): the stock ConfidenceBall's filter over
  modelV2.meta.disengagePredictions, kept here frame by frame as the widget keeps it. BPShowConfidenceBall (comma 3X;
  the comma four always draws it).
- The blind spot screen edges (blindspot_renderer.py): their setting only; overlay.json reads the app's blindspot.
  ShowBlindspotOverlay.
- The radar lead overlay (chevron_metrics_bp.py): the lead's distance, speed and time gap, whether radar or vision
  sees it, and its close-range layout (under 100 ft, until 125 ft). FordPrefShowRadarLeadOverlay (comma 3X).
- The comma four's lower-right complication (bp/mici/onroad/complication.py): mici_complication picks lead speed,
  speed, lead distance or time to lead, and it fades over 3 s after the lead goes.

requiresModules names BluePilot's blindspot_renderer, which both of its road views import, so the pack costs nothing
on another fork.

SDK 1 (docs/extensions.md).
"""

import math
from typing import Any

MS_TO_KPH = 3.6
MS_TO_MPH = 2.236936
M_TO_FT = 3.28084
# BluePilot's power flow scale: throttleDemandPercent over this is a full bar.
POWER_FLOW_FULL = 102.0
# The confidence ball's filter (selfdrive/ui/mici/onroad/confidence_ball.py): from -0.5, rc 0.5 s.
CONFIDENCE_START = -0.5
CONFIDENCE_RC = 0.5
# ChevronMetricsBP's close-range layout: on under 100 ft, off above 125 ft.
CLOSE_ON = 30.48
CLOSE_OFF = 38.10
# MiciComplication: a value fades out over this long after the lead goes.
COMPLICATION_FADE = 3.0
# The lateral modes controllerStateBP.activeLateralMode names, as BluePilot letters them.
LATERAL_LETTERS = {"angle": "A", "curvature": "C", "openpilot": "OP"}
# ctx.status as the runtime names it (latOnly, longOnly: _status_mode_name); the UI's own spelling too.
LATERAL_STATUSES = ("latOnly", "lat_only")
LONGITUDINAL_STATUSES = ("longOnly", "long_only")


def _number(value, default=0.0):
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if math.isfinite(number) else default


def _max(values) -> float:
  # As the stock ball's max(probs or [1]): no predictions is no confidence.
  try:
    return max((_number(value) for value in values), default=1.0)
  except TypeError:
    return 1.0


class Extension:
  def __init__(self):
    self._confidence = CONFIDENCE_START
    self._last_time = None
    self._close = False
    self._complication_seen = None
    self._complication_value = None
    self._complication_kind = 0
    self._cluster_seen = False

  def observe(self, ctx) -> None:
    # The confidence ball's filter, every frame as the widget steps it.
    dt = 0.0 if self._last_time is None else max(0.0, min(ctx.now - self._last_time, 0.5))
    self._last_time = ctx.now
    target = CONFIDENCE_START
    # The latest model message this drive, as the stock ball follows it (valid or not).
    model = ctx.service("modelV2")
    # As the stock ball: down while disengaged; engaged and override (which only paints it white) both follow the model.
    if model is not None and ctx.status not in ("disengaged", "unknown"):
      predictions = getattr(getattr(model, "meta", None), "disengagePredictions", None)
      steer = 1.0 - _max(getattr(predictions, "steerOverrideProbs", ()))
      brake = 1.0 - _max(getattr(predictions, "brakeDisengageProbs", ()))
      target = (
        steer if ctx.status in LATERAL_STATUSES else brake if ctx.status in LONGITUDINAL_STATUSES else steer * brake
      )
    if dt > 0.0:
      alpha = dt / (CONFIDENCE_RC + dt)
      self._confidence += alpha * (target - self._confidence)

  def _hybrid(self, ctx) -> dict:
    # As BluePilot's gauges: received this drive and dataAvailable (carStateBP's valid follows CAN's, which they don't
    # wait on).
    bp = ctx.service("carStateBP")
    battery = getattr(bp, "hybridBattery", None)
    drive = getattr(bp, "hybridDrive", None)
    brake = getattr(bp, "brakeLightStatus", None)
    out: dict[str, Any] = {
      "battery": None,
      "flow": None,
      "brakeLights": bool(getattr(brake, "dataAvailable", False) and getattr(brake, "brakeLightsOn", False)),
    }
    if battery is not None and getattr(battery, "dataAvailable", False):
      soc = max(0.0, min(_number(getattr(battery, "socActual", 0.0)), 100.0))
      amps = _number(getattr(battery, "ampsActual", 0.0))
      # Its figures as BluePilot prints them, truncated: f"{int(soc)}%", f"{int(volts)}V", f"{int(amps):+d}A"; the
      # fill's and the amps' colours from the values themselves (19.96 % is still under 20, -0.6 A still drawing).
      out["battery"] = {
        "soc": round(soc, 2),
        "socText": f"{int(soc)}%",
        "voltsText": f"{int(_number(getattr(battery, 'voltActual', 0.0)))}V",
        "amps": round(amps, 2),
        "ampsText": f"{int(amps):+d}A",
      }
    if drive is not None and getattr(drive, "dataAvailable", False):
      mode = str(getattr(drive, "powerFlowMode", "") or "")
      reason = str(getattr(drive, "engineOnReason", "") or "")
      out["flow"] = {
        # -1 (regen) to 1 (full demand), as the gauge's bar.
        "value": round(max(-1.0, min(_number(getattr(drive, "throttleDemandPercent", 0.0)) / POWER_FLOW_FULL, 1.0)), 3),
        # In electric drive, the demand at which the engine starts, 0-1 of the bar.
        "threshold": round(max(0.0, min(_number(getattr(drive, "throttleThresholdPercent", 0.0)) / 100.0, 1.0)), 3),
        "ev": "Electric" in mode,
        "hybrid": "Hybrid" in mode,
        "text": "  |  ".join(part for part in (mode, reason) if part),
        # The comma four's alternate style: a ring round the wheel in place of the bar up the left edge.
        "ring": ctx.param("FordPrefHybridPowerFlowAlternate", kind="bool", default=False),
      }
    return out

  def _lateral(self, ctx):
    if not ctx.fresh("controllerStateBP", timeout=1.0):
      return None
    mode = getattr(ctx.service("controllerStateBP"), "activeLateralMode", None)
    return LATERAL_LETTERS.get(str(mode), "OP") if mode is not None else None

  def _lead(self, ctx, metric: bool):
    radar = ctx.service("radarState") if ctx.fresh("radarState") else None
    leads = [
      lead
      for lead in (getattr(radar, "leadOne", None), getattr(radar, "leadTwo", None))
      if getattr(lead, "status", False)
    ]
    # ChevronMetricsBP's close-range layout: by the closer of the two leads, kept while there's none.
    if leads:
      closest = min(_number(getattr(lead, "dRel", 0.0)) for lead in leads)
      self._close = closest < CLOSE_ON or (self._close and closest <= CLOSE_OFF)
    lead = getattr(radar, "leadOne", None)
    if lead is None or not getattr(lead, "status", False):
      return None
    car = ctx.service("carState")
    v_ego = _number(getattr(car, "vEgo", 0.0)) if car is not None else 0.0
    distance = _number(getattr(lead, "dRel", 0.0))
    # As ChevronMetricsBP: speed and distance not below 0, the time gap only between 0 and 200 s, else "---".
    speed = max(0.0, (v_ego + _number(getattr(lead, "vRel", 0.0))) * (MS_TO_KPH if metric else MS_TO_MPH))
    gap = distance / v_ego if distance > 0 and v_ego > 0 else 0.0
    gap = gap if 0 < gap < 200 else None
    return {
      "distance": round(max(0.0, distance) if metric else max(0.0, distance) * M_TO_FT),
      "speed": round(speed),
      "gapText": f"{gap:.1f} s" if gap is not None else "---",
      "radar": bool(getattr(lead, "radar", False)),
      "close": self._close,
    }

  def _complication(self, ctx, kind: int, metric: bool, hide_speed: bool):
    # MiciComplication: 1 lead speed, 2 speed, 3 lead distance, 4 time to lead.
    car = ctx.service("carState")
    if kind != self._complication_kind:
      # Another complication picked: the last one's value and fade go with it.
      self._complication_kind, self._complication_seen, self._complication_value = kind, None, None
    if kind not in (1, 2, 3, 4) or car is None:
      return None
    # MiciComplication reads vEgoCluster alone.
    cluster = _number(getattr(car, "vEgoCluster", 0.0))
    speed_unit = MS_TO_KPH if metric else MS_TO_MPH
    if kind == 2:
      # BluePilot respects "Speedometer: Hide from Onroad Screen" (HideVEgoUI) here too.
      if hide_speed:
        return None
      return {
        "text": str(round(max(0.0, cluster * speed_unit))),
        "unit": "km/h" if metric else "mph",
        "color": "#FFFFFFDC",
        "alpha": 1.0,
      }
    radar = ctx.service("radarState") if ctx.fresh("radarState") else None
    lead = getattr(radar, "leadOne", None)
    gear = str(getattr(car, "gearShifter", ""))
    present = lead is not None and getattr(lead, "status", False) and gear not in ("park", "reverse")
    v_rel = _number(getattr(lead, "vRel", 0.0)) if present else 0.0
    distance = _number(getattr(lead, "dRel", 0.0)) if present else 0.0
    # The time to the lead shows only while the lead pulls away, fading 3 s after it stops; the others while there's
    # a lead, fading 3 s after it goes.
    if present and (kind != 4 or v_rel > 0):
      if kind == 1:
        # Orange when the lead is 4.5 m/s or more slower, green when as much faster.
        t = max(-1.0, min(v_rel / 4.5, 1.0))
        r, g, b = (
          (255, int(160 + 95 * (1 + t)), int(255 * (1 + t))) if t < 0 else (int(255 * (1 - t)), 255, int(255 * (1 - t)))
        )
        self._complication_value = {
          "text": str(round(max(0.0, (cluster + v_rel) * speed_unit))),
          "unit": "km/h" if metric else "mph",
          "color": f"#{r:02X}{g:02X}{b:02X}DC",
        }
      elif kind == 3:
        self._complication_value = {
          "text": str(round(distance if metric else distance * M_TO_FT)),
          "unit": "m" if metric else "ft",
          "color": "#FFFFFFDC",
        }
      else:
        ttc = distance / cluster if cluster > 0 else 0.0
        self._complication_value = {
          "text": f"{ttc:.1f}" if 0 < ttc < 200 else "---",
          "unit": "sec",
          "color": "#FFFFFFDC",
        }
      self._complication_seen = ctx.now
    if self._complication_seen is None or self._complication_value is None:
      return None
    alpha = 1.0 - (ctx.now - self._complication_seen) / COMPLICATION_FADE
    if alpha <= 0.0:
      self._complication_value = None
      return None
    return {**self._complication_value, "alpha": round(alpha, 2)}

  def payload(self, ctx) -> dict:
    # Both of BluePilot's screens' figures: overlay.json draws the comma 3X's or the comma four's by the design on
    # screen (overlay level 2's `screen`), which isn't always the comma's own. Only what overlay.json reads is sent.
    metric = bool(getattr(ctx.ui_state, "is_metric", True))
    show_battery = ctx.param("FordPrefHybridBatteryStatus", kind="bool", default=False)
    show_flow = ctx.param("FordPrefHybridPowerFlow", kind="bool", default=False)
    show_brake = ctx.param("ShowBrakeStatus", kind="bool", default=False)
    hybrid = self._hybrid(ctx) if show_battery or show_flow or show_brake else None
    kind = ctx.param("mici_complication", kind="int", default=0)
    # sunnypilot's "Speedometer: Hide from Onroad Screen", which BluePilot's speed readouts respect.
    hide_speed = ctx.param("HideVEgoUI", kind="bool", default=False)
    lead_overlay = ctx.param("FordPrefShowRadarLeadOverlay", kind="bool", default=True)
    car = ctx.service("carState")
    # The speed the comma 3X design shows, as sunnypilot's HUD picks it: vEgo with "Always Display True Speed"
    # (TrueVEgoUI), else the cluster's where the car reports one; never below 0.
    true_speed = ctx.param("TrueVEgoUI", kind="bool", default=False)
    v_ego = _number(getattr(car, "vEgo", 0.0)) if car is not None else 0.0
    cluster = _number(getattr(car, "vEgoCluster", 0.0)) if car is not None else 0.0
    # Once the car has reported a cluster speed, sunnypilot keeps showing it, 0 included (a Ford's below 1.5 km/h).
    self._cluster_seen = self._cluster_seen or cluster != 0.0
    shown = v_ego if true_speed or not self._cluster_seen else cluster
    return {
      "show": {
        # The comma 3X's strip follows its setting; the comma four's ball is always drawn.
        "ball": ctx.param("BPShowConfidenceBall", kind="bool", default=True),
        "blindspot": ctx.param("ShowBlindspotOverlay", kind="bool", default=True),
      },
      "battery": hybrid["battery"] if hybrid and show_battery else None,
      "flow": hybrid["flow"] if hybrid and show_flow else None,
      "brakeLights": bool(hybrid and show_brake and hybrid["brakeLights"]),
      # The speed the comma 3X design shows, the cluster's where the car reports it, for the red one in its place.
      "clusterSpeed": round(max(0.0, shown) * (MS_TO_KPH if metric else MS_TO_MPH)),
      "lateral": self._lateral(ctx) if ctx.param("BpShowLateralControl", kind="bool", default=False) else None,
      # -0.5 to 1, as the ball's filter: its height in its strip is (confidence + 0.5) / 1.5.
      "confidence": round(self._confidence, 3),
      "lead": self._lead(ctx, metric) if lead_overlay else None,
      "complication": self._complication(ctx, kind, metric, hide_speed),
      "hideSpeed": hide_speed,
      # Minimal Driving View (cameraview_bp.py): the camera painted black onroad; and the border band hidden.
      "hideCamera": ctx.param("BPHideCameraView", kind="bool", default=False),
      "hideBorder": ctx.param("BPHideOnroadBorder", kind="bool", default=False),
      # BluePilot's lane lines on the comma 3X: its current lanes white rather than the status colour.
      "laneWhite": ctx.param("BPDisableLaneLineStatusColor", kind="bool", default=False),
      "distanceUnit": "m" if metric else "ft",
    }
