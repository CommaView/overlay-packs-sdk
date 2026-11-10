"""
The runtime half of the pack test: what every fork's UI has (openpilot and sunnypilot, the comma 3X and the comma
four), so the pipeline from the comma's UI to the app can be tried on any comma. It reads the UI's status (through
ctx), carState's speed and acceleration, radarState's lead and counts the UI's frames; overlay.json draws them.

No requiresModules and no applies(): it runs on every UI. SDK 1 (docs/extensions.md).
"""


def _finite(value):
  """value as a float rounded for the app, or None when it isn't a finite number."""
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return round(number, 2) if number == number and abs(number) != float("inf") else None


class Extension:
  def __init__(self):
    self._frames = 0

  def observe(self, ctx) -> None:
    # Every UI frame (60 Hz): the count says the pack runs with the UI.
    self._frames += 1

  def payload(self, ctx) -> dict:
    car = ctx.service("carState")
    radar = ctx.service("radarState")
    lead = getattr(radar, "leadOne", None) if radar is not None else None
    has_lead = bool(getattr(lead, "status", False))
    lead_distance = _finite(getattr(lead, "dRel", None)) if has_lead else None
    return {
      "frames": self._frames,
      "status": ctx.status,
      "engaged": ctx.engaged,
      "vEgo": _finite(getattr(car, "vEgo", None)) if car is not None else None,
      "aEgo": _finite(getattr(car, "aEgo", None)) if car is not None else None,
      # The radar's lead one, metres ahead and m/s; null without one.
      "leadDistance": lead_distance,
      "leadSpeed": _finite(getattr(lead, "vLead", None)) if lead_distance is not None else None,
    }
