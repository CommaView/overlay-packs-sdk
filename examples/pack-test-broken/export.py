"""
A pack that tests the runtime's error limit (docs/extensions.md, "The rules the runtime enforces"): its
payload() raises every time, so after 10 exceptions in a row (half a second at 20 a second) the exporter turns it
off for the rest of the UI's run and reports it disabled, "payload() raised 10 times in a row: RuntimeError: ...".
It never sends a block, so its badge never draws; the fork's UI and the rest of the export carry on.
"""


class Extension:
  def payload(self, ctx) -> dict:
    raise RuntimeError("pack-test-broken raises on purpose")
