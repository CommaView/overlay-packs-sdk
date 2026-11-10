"""
A pack that tests the runtime's time limit (docs/extensions.md, "The rules the runtime enforces"): it
spends 3 ms of every UI frame, over the 2 ms budget, so after 30 frames in a row (half a second at 60 Hz) the
exporter turns it off for the rest of the UI's run and reports it disabled, "over 2 ms a frame for 30 frames".
Meanwhile the fork's UI and the rest of the export carry on. Don't keep it installed: until it's off, it slows the
car's screen.
"""

import time

SPEND_SEC = 0.003


class Extension:
  def __init__(self):
    self._frames = 0

  def observe(self, ctx) -> None:
    self._frames += 1
    time.sleep(SPEND_SEC)

  def payload(self, ctx) -> dict:
    return {"frames": self._frames}
