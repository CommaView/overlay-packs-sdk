"""
The overlay pack host: what loads, checks, times, sandboxes and drives an overlay pack's runtime half (its
export.py), and the context and helpers a pack sees (docs/extensions.md, "export.py").

It's one module with no imports beyond Python's standard library (orjson where it's installed). Two programs use
it, so a pack is checked off the comma exactly as it runs on one:

- the comma's exporter, which runs in the fork UI's process and carries this module word for word (the exporter is
  installed as one file, so this module is copied into it, never imported from beside it);
- check-pack.py, which imports it and drives it with PackHost (below) against a stand-in UI state.

PackHost is the host as check-pack uses it: _Extensions(PackHost(), folder) reads the packs in [folder] as the comma
reads its packs folder, and load_pending(), observe(ui_state) and payloads(ui_state) run them as the UI's frames do.
"""

import faulthandler
import importlib.util
import json
import math
import os
import random
import stat
import sys
import threading
import time

try:
  import orjson as _orjson
except (ImportError, OSError):
  _orjson = None


# Params are files. The exporter reads only what the UI has already parsed, except the CameraOffset param,
# which the UI's model renderer itself re-reads every 3 s; the exporter reads it no more often than that.
COMMAVIEW_PARAMS_REFRESH_SEC = 3.0
# A pack's ctx.param values each expire up to this share of COMMAVIEW_PARAMS_REFRESH_SEC later, at random, so a
# pack reading several params doesn't read every file again in the same frame.
COMMAVIEW_PARAMS_REFRESH_STAGGER = 0.2
# Where openpilot's HARDWARE.get_device_type() reads which comma this is (ctx.device).
COMMAVIEW_DEVICE_MODEL_FILE = "/sys/firmware/devicetree/base/model"
# The param values held at once (_param_value) in each cache: the exporter's own few, and what packs ask for.
COMMAVIEW_PARAM_CACHE_MAX = 256
# uiStateOnroad.wallTimeMs is sent only while the comma's clock reads after this (2024-01-01 UTC, in ms): before
# the comma has set its clock it reads 2015 or so, and a stamp from then would say the drive happened then.
COMMAVIEW_WALL_CLOCK_SET_MS = 1_704_067_200_000


def _encode_json(payload: dict) -> bytes:
  if _orjson is not None:
    return _orjson.dumps(payload)
  return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _checked_payload(payload) -> tuple:
  """A pack's [payload] as the worker would send it, (bytes, a copy of it): raises TypeError, ValueError, OverflowError
  or RecursionError for one it couldn't (NaN and infinity included). The copy is what's sent: the pack's own dict
  may change before the worker encodes the frame."""
  if _orjson is not None:
    # orjson writes NaN and infinity as null, and refuses more than json does: checked by both.
    json.dumps(payload, allow_nan=False, separators=(",", ":"))
    encoded = _encode_json(payload)
  else:
    encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
  return encoded, json.loads(encoded)


def _utf8_text(text: str) -> str:
  """[text] as UTF-8 can carry it: a lone surrogate (a pack's exception text, a hand-copied manifest) as "?". The
  worker encodes uiStateOnroad whole, so one such character would fail every frame of the run."""
  return text.encode("utf-8", "replace").decode("utf-8")


def _safe_int(value) -> int:
  try:
    if hasattr(value, "raw"):
      value = value.raw
    return int(value)
  except (TypeError, ValueError):
    return 0


def _safe_str(value) -> str:
  return "" if value is None else str(value)


# _read_param's default for "the file has no value": the caller's own default applies.
_PARAM_MISSING = object()


def _read_param(params, key: str, kind: str, default):
  if params is None:
    return default
  try:
    if kind == "bool":
      return bool(params.get_bool(key))
    try:
      value = params.get(key, return_default=True)
    except TypeError:
      value = params.get(key)
  except Exception:
    return default
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="replace")
  return default if value is None else value


def _coerce_param(value, kind: str, default):
  try:
    if kind == "bool":
      if isinstance(value, str):
        return value.strip().lower() in ("1", "true")
      return bool(value)
    if kind == "int":
      return int(float(value))
    if kind == "float":
      result = float(value)
      return result if math.isfinite(result) else default
  except (TypeError, ValueError, OverflowError):  # OverflowError: int() of "inf" or "1e999"
    return default
  return value


def _device_type(path: str) -> str | None:
  """Which comma this is, as the daemon's device_type() and openpilot's HARDWARE.get_device_type() tell: the
  devicetree model after "comma ", "tici" (comma 3), "tizi" (comma 3X) or "mici" (comma four); None otherwise.

  The runtime's settings API parses the same file: the two are kept in step, so a pack's ctx.device and the app's
  settings name the same comma."""
  try:
    with open(path, "rb") as f:
      model = f.read(256).replace(b"\0", b"").decode("utf-8", "replace").strip()
  except OSError:
    return None
  at = model.rfind("comma ")
  name = (model if at < 0 else model[at + 6:]).rstrip()
  return name if name in ("tici", "tizi", "mici") else None


_STATUS_MODE_NAMES = {
  "disengaged": "disengaged",
  "engaged": "engaged",
  "override": "override",
  "lat_only": "latOnly",
  "long_only": "longOnly",
}


def _status_mode_name(status) -> str:
  status_value = str(status.value if hasattr(status, "value") else status)
  return _STATUS_MODE_NAMES.get(status_value, "unknown")


def _unpin() -> None:
  # Free to run on any core: the UI process is pinned to its own, and a thread inherits that.
  try:
    os.sched_setaffinity(0, range(os.cpu_count() or 1))
  except Exception:
    pass


def _received_this_drive(ui_state, service: str) -> bool:
  """Whether [service] has a message from after the drive started (openpilot's recv_frame > started_frame)."""
  try:
    return ui_state.sm.recv_frame[service] > _safe_int(getattr(ui_state, "started_frame", 0))
  except (AttributeError, KeyError, TypeError):
    return False


def _services_fresh(ui_state, services, now: float, timeout: float) -> bool:
  """Whether every one of [services] is valid, alive, received this drive and no older than [timeout] s."""
  sm = getattr(ui_state, "sm", None)
  try:
    for service in services:
      if (not sm.valid[service] or not sm.alive[service] or not _received_this_drive(ui_state, service) or
          not 0 <= now - sm.recv_time[service] <= timeout):
        return False
  except (AttributeError, KeyError, TypeError):
    return False
  return True


def _drop_realtime() -> None:
  # openpilot's UI process runs SCHED_FIFO on its own core, and a thread inherits that. The export
  # worker only encodes and sends, so like openpilot's own helper threads (common.realtime.drop_realtime)
  # it runs as an ordinary thread and never competes with the UI's frame.
  try:
    if os.sched_getscheduler(0) != os.SCHED_OTHER:
      os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  except Exception:
    pass


def _log_exception(message: str) -> None:
  # From an except block cloudlog records the exception being handled; outside one, the message alone.
  try:
    from openpilot.common.swaglog import cloudlog
    if sys.exc_info()[1] is None:
      cloudlog.error(message)
    else:
      cloudlog.exception(message)
  except Exception:
    try:
      print(message, file=sys.stderr)
    except Exception:
      pass


# ---- Overlay pack extensions (docs/extensions.md)
#
# A pack's runtime half: a folder in COMMAVIEW_EXTENSIONS_DIR with manifest.json and the Python its manifest
# names, which the comma's owner put there (by hand, or the app on their say). It is their code on their comma,
# as the fork is, and it runs here, in the fork UI's process, so it is held to a budget: what it returns goes to
# the app as uiStateOnroad.ext[<id>], and a pack that raises, runs slow, or returns what isn't small JSON is
# turned off for the rest of the UI's run, leaving the rest of the export as it was.
COMMAVIEW_EXTENSIONS_DIR_DEFAULT = "/data/commaview/extensions"
# One file turns every pack off (a pack of its own: a file named "disabled" in its folder).
COMMAVIEW_EXTENSIONS_OFF_FILE_DEFAULT = "/data/commaview/config/extensions-disabled"
COMMAVIEW_EXTENSION_SDK = 1
COMMAVIEW_EXTENSION_MAX_PACKS = 8
COMMAVIEW_EXTENSION_MAX_MANIFEST_BYTES = 16 * 1024
COMMAVIEW_EXTENSION_MAX_SOURCE_BYTES = 256 * 1024
COMMAVIEW_EXTENSION_MAX_PAYLOAD_BYTES = 4 * 1024
# Every pack's block together, in one uiStateOnroad: over it, the largest blocks are left out of that frame, so a
# recording's snapshot stays under the bridge's 64 KiB.
COMMAVIEW_EXTENSION_MAX_TOTAL_PAYLOAD_BYTES = 16 * 1024
COMMAVIEW_EXTENSION_MAX_SERVICES = 8
# observe() and payload() share this much of a UI frame (60 Hz: 16.7 ms); this many frames over it in a row, or
# one frame over the hard limit, and the pack is off.
COMMAVIEW_EXTENSION_FRAME_BUDGET_NS = 2_000_000
COMMAVIEW_EXTENSION_HARD_LIMIT_NS = 20_000_000
COMMAVIEW_EXTENSION_SLOW_FRAMES = 30
COMMAVIEW_EXTENSION_MAX_FAILURES = 10
# The control API rewrites this file in the packs' folder after each install, removal or switch; offroad, the
# exporter looks at it this often and reads the packs again when it changed. Onroad it never does: a pack never
# loads, or goes, mid-drive.
COMMAVIEW_EXTENSIONS_GENERATION_FILE = ".generation"
COMMAVIEW_EXTENSIONS_RELOAD_CHECK_SEC = 2.0
# A pack's import and Extension() share this much; over it, the pack is off (loading happens offroad).
COMMAVIEW_EXTENSION_LOAD_LIMIT_NS = 250_000_000
# One call into a pack (its import, observe, payload, applies) running this long is a hang: a watchdog thread
# interrupts it and counts the run in the pack's .overruns. One run turns the pack off for that run; the
# COMMAVIEW_EXTENSION_OVERRUN_RUNS-th writes .hung, off until it's installed again.
COMMAVIEW_EXTENSION_HANG_SEC = 1.0
# Loading (the import and Extension()) gets longer before it's a hang: a cold boot's first import of a big module
# can be slow. Over COMMAVIEW_EXTENSION_LOAD_LIMIT_NS it's off for the run all the same.
COMMAVIEW_EXTENSION_LOAD_HANG_SEC = 5.0
COMMAVIEW_EXTENSION_WATCHDOG_SEC = 0.1
COMMAVIEW_EXTENSION_HUNG_FILE = ".hung"
# The runs a pack has hung in (a call over its limit, a load hung), counted in its folder so it survives reboots
# (a reinstall or an update writes a new folder, counting from 0): one turns it off for that run, the
# COMMAVIEW_EXTENSION_OVERRUN_RUNS-th for good (.hung) until it's installed again.
COMMAVIEW_EXTENSION_OVERRUNS_FILE = ".overruns"
COMMAVIEW_EXTENSION_OVERRUN_RUNS = 3
# Loading a pack (its import and Extension()), Python's faulthandler writes the stacks here if it goes on past
# COMMAVIEW_EXTENSION_LOAD_HANG_SEC + COMMAVIEW_EXTENSION_C_HANG_GRACE_SEC: its watchdog thread needs no GIL, so it
# marks a load stuck in C (holding the GIL) that ours can't interrupt, and the pack stays off as for .hung.
COMMAVIEW_EXTENSION_HUNG_TRACE_FILE = ".hung-trace"
COMMAVIEW_EXTENSION_C_HANG_GRACE_SEC = 1.0
# Signing: the control API writes a pack's .reviewed when CommaView's
# catalog signed it, and the folder's .allow-unreviewed while the comma's owner allows unreviewed packs.
COMMAVIEW_EXTENSION_REVIEWED_FILE = ".reviewed"
# A pack's .reviewed set aside by the runtime at its start: signed by a key this runtime doesn't accept (it holds the
# key's id), so the pack is unreviewed.
COMMAVIEW_EXTENSION_KEY_NOT_ACCEPTED_FILE = ".key-not-accepted"
# The control API's id for each install: even the same bundle again is a new one.
COMMAVIEW_EXTENSION_INSTALL_FILE = ".install"
# The control API's record of the bundle's SHA-256 (written at install).
COMMAVIEW_EXTENSION_DIGEST_FILE = ".digest"
COMMAVIEW_EXTENSIONS_ALLOW_UNREVIEWED_FILE = ".allow-unreviewed"
# The counters _Extensions adds to in its host's _stats: the exporter's stats carry them, in this order, as PackHost's
# do.
COMMAVIEW_EXTENSION_STATS = ("extensionExceptions", "extensionsDisabled", "extensionHangs", "extensionPayloadsDropped")
_EXTENSION_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_EXTENSION_SERVICE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _wall_time_field() -> dict:
  """{"wallTimeMs": now} while the comma's clock is plausibly set (COMMAVIEW_WALL_CLOCK_SET_MS), else {}."""
  wall_ms = int(time.time() * 1000)
  return {"wallTimeMs": wall_ms} if wall_ms >= COMMAVIEW_WALL_CLOCK_SET_MS else {}


def _extension_id_ok(value) -> bool:
  return isinstance(value, str) and 0 < len(value) <= 32 and value[0] != "-" and set(value) <= _EXTENSION_ID_CHARS


class _ExtensionContext:
  """
  What a pack's observe(ctx) and payload(ctx) are handed (docs/extensions.md): the UI's state this frame, its
  status as the app names it, the frame's clock, and a few helpers that keep a pack's reads as cheap and as
  forgiving as the exporter's own.
  """

  def __init__(self, exporter):
    self._exporter = exporter
    self.ui_state = None
    self.status = "unknown"
    self.now = 0.0
    self._device = ...

  @property
  def engaged(self) -> bool:
    return self.status in ("engaged", "lat_only", "latOnly")

  @property
  def device(self) -> str | None:
    """The comma's hardware: "mici" (comma four), "tizi" (comma 3X) or "tici" (comma three); None when unknown.
    Read once."""
    if self._device is ...:
      self._device = _device_type(COMMAVIEW_DEVICE_MODEL_FILE)
    return self._device

  @staticmethod
  def module(name: str):
    """The fork UI's module [name] if the UI has loaded it, else None (never imports one)."""
    return sys.modules.get(name)

  def service(self, name: str):
    """The latest [name] message received this drive, else None: received this drive as fresh() reads it."""
    if not _received_this_drive(self.ui_state, name):
      return None
    return self._exporter._service_msg(self.ui_state, name)

  def fresh(self, *services: str, timeout: float = 0.5) -> bool:
    """Whether every one of [services] is valid, alive, received this drive and no older than [timeout] s."""
    return _services_fresh(self.ui_state, services, self.now, timeout)

  def param(self, key: str, kind: str = "str", default=None, attr: str | None = None):
    """A param as [kind] ("bool", "int", "float", "str"): the UI's own [attr] when it has one, else the file,
    read at most every few seconds (each value's wait a little longer at random, so they aren't all read in one
    frame)."""
    return self._exporter._param_value(
      self.ui_state, key, kind, default, attr, cache=self._exporter._pack_param_cache, stagger=True
    )


class _Extension:
  def __init__(self, ext_id: str, folder: str, manifest: dict):
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    self.id = ext_id
    self.folder = folder
    self.version = _safe_str(manifest.get("version"))[:32]
    self.entry = _safe_str(runtime.get("entry") or "export.py")
    services = runtime.get("services") if isinstance(runtime.get("services"), list) else []
    self.services = tuple(service for service in services[:COMMAVIEW_EXTENSION_MAX_SERVICES]
                          if isinstance(service, str) and 0 < len(service) <= 64 and set(service) <= _EXTENSION_SERVICE_CHARS)
    modules = runtime.get("requiresModules") if isinstance(runtime.get("requiresModules"), list) else []
    self.requires_modules = tuple(name for name in modules if isinstance(name, str) and name)
    self.instance = None
    # "pending" until the first frame loads it; "active"; "inactive" (not this UI: a required module isn't
    # loaded, or applies() said no); "disabled" (off for this run, [reason] says why).
    self.state = "pending"
    self.reason = ""
    # Exceptions in a row, by hook (applies, observe, payload): one hook that works never hides another that fails.
    self.failures: dict = {}
    # Whether its module was put in sys.modules (a reload then has something to collect).
    self.loaded = False
    self.slow_frames = 0
    self.spent_ns = 0
    # Whether its payload was built since the frame's budget was last looked at: a frame without one (the export
    # builds payloads less often than the UI draws) measured only observe's part, so it doesn't end a slow run.
    self.payload_built = False
    # Its own _ExtensionContext (made at its first call): packs never share one.
    self.ctx = None
    self.applies_checked = False
    self.has_observe = False
    # Its load's own time (the import and Extension()), for the load budget.
    self.load_ns = 0
    # Inactive only because a module it needs isn't loaded yet.
    self.awaiting_modules = False
    # The runs it hung in (.overruns), and, off for a hang, until when: "next-start" or "reinstall".
    self.overruns = 0
    self.off_until = ""
    # The install's digest ("" by hand), and whether the runtime turned it off this run (not a file saying so).
    self.digest = ""
    # Its folder's identity: each install writes a new folder, so the same bundle installed again is a new one.
    self.folder_id = None
    self.runtime_disabled = False

  def status(self) -> dict:
    status = {"version": _utf8_text(self.version), "state": self.state}
    if self.reason:
      # At most 200 characters, whatever a pack put in it (its manifest's sdk, a class's name), as UTF-8 can carry.
      status["reason"] = _utf8_text(self.reason[:200])
    # Its hangs: the runs it overran in, of how many allowed, and while off for one, until when.
    if self.overruns:
      status["overruns"], status["overrunLimit"] = self.overruns, COMMAVIEW_EXTENSION_OVERRUN_RUNS
    if self.state == "disabled" and self.off_until:
      status["offUntil"] = self.off_until
    return status


def _extensions_dir() -> str:
  return os.environ.get("COMMAVIEW_EXTENSIONS_DIR") or COMMAVIEW_EXTENSIONS_DIR_DEFAULT


def _extensions_generation(folder: str):
  """What the packs' .generation file is now (None without one): a change means the packs changed."""
  try:
    st = os.stat(os.path.join(folder, COMMAVIEW_EXTENSIONS_GENERATION_FILE))
  except OSError:
    return None
  return st.st_mtime_ns, st.st_ino, st.st_size


class _ExtensionHang(BaseException):
  """Raised into the UI thread by the watchdog when a pack's call runs past COMMAVIEW_EXTENSION_HANG_SEC. A
  BaseException, so a pack's own `except Exception` doesn't swallow it."""


def _read_small_at(folder_fd: int | None, name: str) -> str:
  """The small file [name] (in the folder open as [folder_fd], or a path when None), stripped: "" without one.
  The one reader of the control API's small files (.install, .digest), so every check reads them alike."""
  try:
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=folder_fd)
  except OSError:
    return ""
  try:
    return os.read(fd, 128).decode("utf-8", "replace").strip()
  except OSError:
    return ""
  finally:
    os.close(fd)


def _arm_load_trace(folder: str):
  """Arms faulthandler to write the stacks into the pack's .hung-trace should its load go on past the limit and the
  grace: the file's descriptor, or None where it can't. faulthandler's timer is the process's one: a timer a UI set
  of its own would be cancelled for the load; none of the UIs CommaView serves sets one."""
  try:
    fd = os.open(os.path.join(folder, COMMAVIEW_EXTENSION_HUNG_TRACE_FILE),
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o644)
  except OSError:
    return None
  try:
    faulthandler.dump_traceback_later(COMMAVIEW_EXTENSION_LOAD_HANG_SEC + COMMAVIEW_EXTENSION_C_HANG_GRACE_SEC,
                                      repeat=False, file=fd, exit=False)
  except Exception:
    os.close(fd)
    return None
  return fd


def _unlink_load_trace(folder: str) -> None:
  """The pack's .hung-trace gone (faulthandler left as it is): the load step came back, or the watchdog counted it
  (the next start would count it again)."""
  try:
    os.unlink(os.path.join(folder, COMMAVIEW_EXTENSION_HUNG_TRACE_FILE))
  except OSError:
    pass


def _disarm_load_trace(folder: str, fd) -> bool:
  """The load step is over: faulthandler disarmed and the trace file gone (it's for a load that never came back).
  Whether faulthandler had written it: the step hung past its limit and the grace."""
  if fd is None:
    return False
  try:
    faulthandler.cancel_dump_traceback_later()  # first: nothing is written after it's looked at
  except Exception:
    pass
  try:
    written = os.fstat(fd).st_size > 0
  except OSError:
    written = False
  os.close(fd)
  _unlink_load_trace(folder)
  return written


def _load_traced(folder: str) -> bool:
  """Whether faulthandler wrote the stacks of a load that hung (a .hung-trace with something in it)."""
  try:
    st = os.lstat(os.path.join(folder, COMMAVIEW_EXTENSION_HUNG_TRACE_FILE))
  except OSError:
    return False
  return stat.S_ISREG(st.st_mode) and st.st_size > 0


def _write_at(folder_fd: int | None, name: str, text: str) -> None:
  """[text] as the file [name] (in the folder open as [folder_fd], or a path when None), never through a link."""
  fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644, dir_fd=folder_fd)
  try:
    os.write(fd, text.encode())
  finally:
    os.close(fd)


def _overruns_at(folder_fd) -> int:
  """The runs the pack has overrun in (.overruns in the folder open as [folder_fd]), 0 without one."""
  return _parse_overruns(_read_small_at(folder_fd, COMMAVIEW_EXTENSION_OVERRUNS_FILE))


def _overruns(folder: str) -> int:
  return _parse_overruns(_read_small_at(None, os.path.join(folder, COMMAVIEW_EXTENSION_OVERRUNS_FILE)))


def _parse_overruns(text: str) -> int:
  # As the control API reads it: up to 4 digits, else none.
  return int(text) if text.isascii() and text.isdigit() and len(text) <= 4 else 0


def _hung_for_good_reason(overruns: int) -> str:
  return (f"hung in {overruns} runs (a call over its limit, or a load): off until it's installed again")


def _folder_id(folder: str):
  """Which install [folder] holds: the control API's id for it (each install has its own, even the same bundle
  again), with the folder's (device, inode) for a pack copied by hand; None when it's gone."""
  try:
    st = os.stat(folder)
  except OSError:
    return None
  return (st.st_dev, st.st_ino, _read_small_at(None, os.path.join(folder, COMMAVIEW_EXTENSION_INSTALL_FILE)))


_NO_HOOK = object()


def _call_hook(instance, name: str, ctx):
  """[instance].[name](ctx), or _NO_HOOK when it has none. Run guarded: the lookup can be the pack's own code too
  (its __getattr__)."""
  hook = getattr(instance, name, None)
  if hook is None:
    return _NO_HOOK
  return hook(ctx)


def _applies(instance, ctx) -> bool:
  """Whether the pack serves this UI: its applies(ctx) as a bool (its own __bool__ run guarded too), True without one."""
  verdict = _call_hook(instance, "applies", ctx)
  return True if verdict is _NO_HOOK else bool(verdict)


def _describe(error: BaseException) -> str:
  """'Type: message' for what a pack raised: its own __str__ (or a class's __name__) raising mustn't escape."""
  try:
    return f"{type(error).__name__}: {error}"
  except BaseException:  # noqa: BLE001 - the pack's code, not the exporter's
    try:
      return f"{type(error).__name__}: (its message can't be read)"
    except BaseException:  # noqa: BLE001
      return "an exception (it can't be described)"


def _type_name(value) -> str:
  try:
    return str(type(value).__name__)
  except BaseException:  # noqa: BLE001 - a metaclass of the pack's own
    return "an object"


def _known_offroad(exporter, ui_state) -> bool:
  """Whether the car is known to be off: not started, with deviceState received (a UI just restarted mid-drive
  reads started as False until it is) and openpilot's IsOnroad param not set."""
  if getattr(ui_state, "started", False):
    return False
  sm = getattr(ui_state, "sm", None)
  try:
    if sm.recv_frame["deviceState"] <= 0:
      return False
  except (AttributeError, KeyError, TypeError):
    return False
  return not exporter._param_value(ui_state, "IsOnroad", "bool", False)


class _ExtensionCall:
  def __init__(self, pack, thread_id: int, limit: float, load: bool = False):
    self.pack = pack
    # A step of the pack's load: faulthandler is armed for it too (_guarded_load).
    self.load = load
    self.thread_id = thread_id
    self.started = time.monotonic()
    self.limit = limit
    self.fired = False
    # When the watchdog last raised into it: again every [limit] while the call goes on (a pack's bare `except:`
    # can swallow one).
    self.last_fired = 0.0


def _pack_digest(folder: str) -> str:
  """The install's digest (the control API's .digest), "" for a pack copied by hand: it tells one version from another."""
  return _read_small_at(None, os.path.join(folder, COMMAVIEW_EXTENSION_DIGEST_FILE))


def _raise_in_thread(thread_id: int, exc) -> None:
  """[exc] raised in [thread_id] at its next bytecode (None: a pending one cleared). Best effort: CPython only."""
  try:
    import ctypes
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None if exc is None else ctypes.py_object(exc))
  except Exception:
    pass


class _Extensions:
  """The comma's overlay packs, each held to the budget above (docs/extensions.md)."""

  def __init__(self, exporter, folder: str | None = None, off_file: str | None = None):
    self._exporter = exporter
    # This frame's (ui_state, status, now): each pack's own context is set from it before each of its calls.
    self._frame = (None, "unknown", 0.0)
    self.packs = []
    # Set when a pack stops running, or may not run (disabled, inactive): what its services add is rebuilt.
    self.services_changed = False
    self._lock = threading.Lock()
    self._current = None
    self._counting = False
    # Notified once the watchdog's count of an overrun is settled (taken back too): _count_load_overrun waits on it.
    self._settled = threading.Condition(self._lock)
    self._closed = False
    self._watchdog = None
    folder = folder or _extensions_dir()
    off_file = off_file or os.environ.get("COMMAVIEW_EXTENSIONS_OFF_FILE") or COMMAVIEW_EXTENSIONS_OFF_FILE_DEFAULT
    try:
      if os.path.exists(off_file) or not os.path.isdir(folder):
        return
      names = sorted(os.listdir(folder))
    except OSError:
      return
    try:
      # As the control API reads it (lstat): a regular file, never a link to one.
      allow_unreviewed = stat.S_ISREG(os.lstat(os.path.join(folder, COMMAVIEW_EXTENSIONS_ALLOW_UNREVIEWED_FILE)).st_mode)
    except OSError:
      allow_unreviewed = False
    for name in names:
      if len(self.packs) >= COMMAVIEW_EXTENSION_MAX_PACKS:
        break
      pack = self._read(folder, name, allow_unreviewed)
      if pack is not None:
        self.packs.append(pack)
    # A thread of its own only while some pack may run: it sleeps, and steps in only for a hang.
    if any(pack.state == "pending" for pack in self.packs):
      self._ensure_watchdog()

  def _ensure_watchdog(self) -> None:
    """The watchdog running, started again if it had stopped (no pack could run then). Called with a pack already
    pending, so a watchdog deciding (under the lock) whether to stop sees it."""
    with self._lock:
      if self._closed or self._watchdog is not None:
        return
      self._watchdog = threading.Thread(target=self._watch, name="commaview-packs-watchdog", daemon=True)
      self._watchdog.start()

  def keep_disabled(self, earlier) -> None:
    """Each pack [earlier] (the packs before a reload) turned off this run stays off, the same version of it."""
    # Keyed by the folder too: installed again, even the same bundle, a pack gets another chance.
    off = {(pack.id, pack.digest, pack.version, pack.folder_id): (pack.reason, pack.off_until)
           for pack in earlier.packs if pack.runtime_disabled}
    for pack in self.packs:
      kept = off.get((pack.id, pack.digest, pack.version, pack.folder_id))
      if kept is not None and pack.state == "pending":
        pack.state, (pack.reason, pack.off_until), pack.runtime_disabled = "disabled", kept, True

  def _offroad(self, ui_state) -> bool:
    return _known_offroad(self._exporter, ui_state)

  def load_pending(self) -> None:
    """Loads every pack not loaded yet, now, as the first offroad frame would (check-pack, the tests)."""
    for pack in self.packs:
      if pack.state == "pending":
        self._load(pack)

  def close(self) -> None:
    """Stops the watchdog and lets go of the packs' modules: these packs were read again into another _Extensions."""
    self._closed = True
    for pack in self.packs:
      pack.instance = None
      self._unload(pack)

  def _watch(self) -> None:
    # Started from the UI thread, it inherits SCHED_FIFO and the UI's core, where a spinning pack would keep it from
    # ever running: an ordinary thread, free to run on any core.
    _drop_realtime()
    _unpin()
    wait = COMMAVIEW_EXTENSION_WATCHDOG_SEC
    while not self._closed:
      time.sleep(wait)
      # Every pack off or not for this UI: none runs now, so nothing is left to watch. Decided under the lock, as
      # _ensure_watchdog decides to start it again (a pack whose module came, waiting to load anew).
      with self._lock:
        if not any(pack.state in ("pending", "active") for pack in self.packs):
          self._watchdog = None
          return
      with self._lock:
        call = self._current
        now = time.monotonic()
        # Asleep until the call there could be overdue, and never longer than half the shortest limit: a call
        # starting meanwhile (the next after a load's, with its 5 s) is then caught within one and a half of its
        # limit. Never more often than COMMAVIEW_EXTENSION_WATCHDOG_SEC.
        due = None if call is None else (call.last_fired if call.fired else call.started) + call.limit
        if due is None or now < due:
          longest = min(COMMAVIEW_EXTENSION_HANG_SEC, COMMAVIEW_EXTENSION_LOAD_HANG_SEC) / 2
          wait = max(COMMAVIEW_EXTENSION_WATCHDOG_SEC, min(longest, due - now) if due is not None else longest)
          continue
        wait = COMMAVIEW_EXTENSION_WATCHDOG_SEC
        first = not call.fired
        # Counting it (outside the lock, below): _load's own count of a load stuck in C waits for this one.
        self._counting = first
      # The first time, the overrun is counted (and .hung written on the last allowed) before the call is
      # interrupted, and outside the lock: the UI thread finishing the call meanwhile mustn't wait on the file system.
      try:
        counted = self._count_overrun(call.pack) if first else None
        with self._lock:
          returned = self._current is not call
          if not returned:
            if counted is not None:
              call.pack.overruns = counted[2]
            if first:
              self._exporter._stats["extensionHangs"] += 1
            call.fired = True
            call.last_fired = time.monotonic()
            _raise_in_thread(call.thread_id, _ExtensionHang)
        # Outside the lock: it returned meanwhile, so it isn't interrupted nor counted (taken back); or a load step
        # counted here, whose faulthandler trace, should it come, is this run's, not the next start's to count again.
        # Only its file goes: faulthandler is left armed, as the UI thread may have moved on to the next load and
        # armed it for that one meanwhile; the step's own disarm cancels it once it comes back.
        if counted is not None:
          if returned:
            self._take_back_overrun(counted)
          else:
            os.close(counted[0])
            if call.load:
              _unlink_load_trace(call.pack.folder)
      finally:
        # Settled whatever happened above: _count_load_overrun waits for it.
        if first:
          with self._settled:
            self._counting = False
            self._settled.notify_all()

  @staticmethod
  def _count_overrun(pack):
    """Counts a run [pack] overran in (.overruns), and on the COMMAVIEW_EXTENSION_OVERRUN_RUNS-th writes .hung, so
    it stays off across restarts: (the folder's descriptor, the count before, the count now, whether .hung was
    written), to take it back by, or None."""
    try:
      folder = os.open(pack.folder, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
      return None
    try:
      # Only into the install that hung, checked and written through one descriptor: an install swapping another
      # folder in meanwhile (the same bundle too) never gets it.
      st = os.fstat(folder)
      install = _read_small_at(folder, COMMAVIEW_EXTENSION_INSTALL_FILE)
      if (st.st_dev, st.st_ino, install) != pack.folder_id or _read_small_at(folder, COMMAVIEW_EXTENSION_DIGEST_FILE) != pack.digest:
        os.close(folder)
        return None
      before = _overruns_at(folder)
      now = before + 1
      _write_at(folder, COMMAVIEW_EXTENSION_OVERRUNS_FILE, f"{now}\n")
      permanent = now >= COMMAVIEW_EXTENSION_OVERRUN_RUNS
      if permanent:
        _write_at(folder, COMMAVIEW_EXTENSION_HUNG_FILE, _hung_for_good_reason(now) + "\n")
    except OSError:
      os.close(folder)
      return None
    return folder, before, now, permanent

  @staticmethod
  def _take_back_overrun(counted) -> None:
    folder, before, _, permanent = counted
    try:
      if permanent:
        os.unlink(COMMAVIEW_EXTENSION_HUNG_FILE, dir_fd=folder)
      if before:
        _write_at(folder, COMMAVIEW_EXTENSION_OVERRUNS_FILE, f"{before}\n")
      else:
        os.unlink(COMMAVIEW_EXTENSION_OVERRUNS_FILE, dir_fd=folder)
    except OSError:
      pass
    os.close(folder)

  def _guarded_load(self, pack, fn, *args):
    """One step of a pack's load (its import, its Extension()), watched as a call is, and by faulthandler too, armed
    for this step alone (its limit and the grace): a step stuck in C past them leaves .hung-trace."""
    trace = _arm_load_trace(pack.folder)
    before = _overruns(pack.folder)
    started = time.monotonic_ns()
    try:
      result = self._guarded(pack, fn, *args, limit=COMMAVIEW_EXTENSION_LOAD_HANG_SEC, load=True)
    except BaseException:
      if _disarm_load_trace(pack.folder, trace):
        # Stuck past the limit, then raising: a hang all the same (counted, and reported as one).
        self._count_load_overrun(pack, before)
        raise _ExtensionHang() from None
      raise
    # The pack's own time, for its load budget: not the trace's bookkeeping around it.
    pack.load_ns += time.monotonic_ns() - started
    if _disarm_load_trace(pack.folder, trace):
      self._count_load_overrun(pack, before)
      raise _ExtensionHang()  # back, but stuck past the limit: a hang all the same
    return result

  def _count_load_overrun(self, pack, before: int) -> None:
    """A load stuck in C past the limit, then back: counted here unless our watchdog counted it (once its count,
    under way, is settled)."""
    with self._settled:
      # The watchdog settles its count within a few file operations; never waited on for good all the same.
      self._settled.wait_for(lambda: not self._counting, timeout=COMMAVIEW_EXTENSION_LOAD_HANG_SEC)
      if _overruns(pack.folder) == before:
        counted = self._count_overrun(pack)
        if counted is not None:
          os.close(counted[0])
      pack.overruns = max(pack.overruns, _overruns(pack.folder))

  def _hang_reason(self, pack, what: str) -> str:
    """Why a pack that hung this run is off: for this run, or (its last allowed overrun) until it's installed again."""
    pack.overruns = max(1, pack.overruns)  # its count, or (an install swapped in meanwhile) this run's one
    if pack.overruns >= COMMAVIEW_EXTENSION_OVERRUN_RUNS:
      pack.off_until = "reinstall"
      return _hung_for_good_reason(pack.overruns)
    pack.off_until = "next-start"
    return (f"{what}: off for this run, runs again at the next start (overrun {pack.overruns} of "
            f"{COMMAVIEW_EXTENSION_OVERRUN_RUNS}: the last turns it off until it's installed again)")

  def _guarded(self, pack, fn, *args, limit: float | None = None, load: bool = False):
    """[fn](*args) with the watchdog watching; raises _ExtensionHang when it interrupted the call."""
    call = _ExtensionCall(pack, threading.get_ident(), COMMAVIEW_EXTENSION_HANG_SEC if limit is None else limit, load)
    try:
      with self._lock:
        self._current = call
      return fn(*args)
    finally:
      while True:
        try:
          with self._lock:
            self._current = None
            if call.fired:
              # Interrupted after the call returned: the exception mustn't land in the exporter's own code.
              _raise_in_thread(call.thread_id, None)
          break
        except _ExtensionHang:
          # It landed here, before the call was cleared: clear it, or the watchdog goes on raising into the UI.
          continue
      if call.fired:
        raise _ExtensionHang()

  @staticmethod
  def _read(folder: str, name: str, allow_unreviewed: bool = False):
    pack_folder = os.path.join(folder, name)
    # A link isn't a pack: the control API neither lists, counts nor removes one (it reads folders with lstat).
    if (name.startswith(".") or os.path.islink(pack_folder) or not os.path.isdir(pack_folder) or
        os.path.lexists(os.path.join(pack_folder, "disabled"))):
      return None
    try:
      # As the control API reads it: never through a link, a regular file, at most the cap.
      fd = os.open(os.path.join(pack_folder, "manifest.json"), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
      with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > COMMAVIEW_EXTENSION_MAX_MANIFEST_BYTES:
          raise ValueError("manifest isn't a file, or is too large")
        manifest = json.loads(f.read(COMMAVIEW_EXTENSION_MAX_MANIFEST_BYTES + 1).decode("utf-8"))
    except (OSError, ValueError, RecursionError):  # RecursionError: nested past what json reads
      if _extension_id_ok(name):
        pack = _Extension(name, pack_folder, {})
        pack.state, pack.reason = "disabled", "manifest.json is missing or isn't JSON"
        return pack
      return None
    if not isinstance(manifest, dict):
      # As the control API lists it: a pack whose manifest is no object.
      if _extension_id_ok(name):
        pack = _Extension(name, pack_folder, {})
        pack.state, pack.reason = "disabled", "manifest.json isn't a JSON object"
        return pack
      return None
    ext_id = manifest.get("id")
    if not _extension_id_ok(ext_id) or ext_id != name:
      return None
    pack = _Extension(ext_id, pack_folder, manifest)
    pack.digest = _pack_digest(pack_folder)
    pack.folder_id = _folder_id(pack_folder)
    hung = os.path.join(pack_folder, COMMAVIEW_EXTENSION_HUNG_FILE)
    if _load_traced(pack_folder):
      # faulthandler wrote the stacks of a load stuck in C in a run before (the UI froze, then restarted): that
      # run's overrun, counted now, the stacks kept as .hung-trace.last.
      overruns = _overruns(pack_folder) + 1
      try:
        # Written never through a link, as the control API writes; the trace consumed, so it's counted once.
        _write_at(None, os.path.join(pack_folder, COMMAVIEW_EXTENSION_OVERRUNS_FILE), f"{overruns}\n")
        os.replace(os.path.join(pack_folder, COMMAVIEW_EXTENSION_HUNG_TRACE_FILE),
                   os.path.join(pack_folder, COMMAVIEW_EXTENSION_HUNG_TRACE_FILE + ".last"))
        if overruns >= COMMAVIEW_EXTENSION_OVERRUN_RUNS:
          _write_at(None, hung, _hung_for_good_reason(overruns) + "\n")
      except OSError:
        pass
    pack.overruns = _overruns(pack_folder)
    if os.path.lexists(hung):
      # Read as the other small files are: never through a link, any bytes.
      why = _read_small_at(None, hung)
      pack.state, pack.reason, pack.off_until = "disabled", why or "it hung: off until it's installed again", "reinstall"
    elif not allow_unreviewed and not os.path.lexists(os.path.join(pack_folder, COMMAVIEW_EXTENSION_REVIEWED_FILE)):
      set_aside = os.path.join(pack_folder, COMMAVIEW_EXTENSION_KEY_NOT_ACCEPTED_FILE)
      kid = _read_small_at(None, set_aside) or "no key recorded"
      if os.path.lexists(set_aside):
        pack.state, pack.reason = "disabled", (f"signed with a key this runtime doesn't accept ({kid[:32]}): it runs only "
                                               "while this comma allows unreviewed packs")
      else:
        pack.state, pack.reason = "disabled", "not reviewed by CommaView: it runs only while this comma allows unreviewed packs"
    elif type(manifest.get("sdk")) is not int or manifest.get("sdk") != COMMAVIEW_EXTENSION_SDK:
      # The integer 1, as the control API and check-pack read it (true and 1.0 aren't).
      pack.state, pack.reason = "disabled", f"needs SDK {manifest.get('sdk')!r}; this runtime has {COMMAVIEW_EXTENSION_SDK}"
    elif "runtime" not in manifest and not os.path.exists(os.path.join(pack_folder, pack.entry)):
      # A standalone pack (the app draws it alone) copied here by hand: nothing to run, and nothing wrong.
      pack.state, pack.reason = "inactive", "a standalone pack: the app draws it, nothing runs on the comma"
    return pack

  @property
  def services(self) -> tuple:
    # Only a pack that runs: one waiting to load (onroad, a UI restarted mid-drive), off or not for this UI has no
    # reason to rebuild uiStateOnroad.
    return tuple(dict.fromkeys(service for pack in self.packs if pack.state == "active" for service in pack.services))

  def _disable(self, pack, reason: str) -> None:
    pack.state, pack.reason, pack.instance = "disabled", reason, None
    pack.runtime_disabled = True
    self.services_changed = True
    self._unload(pack)
    self._exporter._stats["extensionsDisabled"] += 1
    _log_exception(f"commaview ui export: overlay pack {pack.id} turned off: {reason}")

  def _load(self, pack) -> None:
    missing = [name for name in pack.requires_modules if name not in sys.modules]
    if missing:
      # Looked at again each offroad frame (observe): a UI may import it later.
      pack.state, pack.reason = "inactive", f"this UI hasn't loaded {missing[0]}"
      pack.awaiting_modules = True
      self.services_changed = True
      return
    entry = os.path.realpath(os.path.join(pack.folder, pack.entry))
    if not entry.startswith(os.path.realpath(pack.folder) + os.sep) or not entry.endswith(".py"):
      self._disable(pack, "runtime.entry must be a .py file in the pack's folder")
      return
    try:
      if os.path.getsize(entry) > COMMAVIEW_EXTENSION_MAX_SOURCE_BYTES:
        self._disable(pack, "runtime.entry is too large")
        return
      spec = importlib.util.spec_from_file_location(self._module_name(pack), entry)
      if spec is None or spec.loader is None:
        self._disable(pack, "runtime.entry can't be loaded")
        return
      module = importlib.util.module_from_spec(spec)
      # In sys.modules while it runs, as an import would put it (dataclasses, typing and pickle look it up there).
      sys.modules[spec.name] = module
      pack.loaded = True
      pack.load_ns = 0
      self._guarded_load(pack, spec.loader.exec_module, module)
      # The lookups guarded too: a module's or instance's __getattr__ is the pack's code.
      pack.instance = self._guarded_load(pack, lambda: module.Extension())
      spent = pack.load_ns
      has_payload, pack.has_observe = self._guarded(
        pack,
        lambda: (callable(getattr(pack.instance, "payload", None)), callable(getattr(pack.instance, "observe", None))),
        limit=COMMAVIEW_EXTENSION_LOAD_HANG_SEC,
      )
    except _ExtensionHang:
      self._disable(pack, self._hang_reason(pack, f"hung while loading (over {COMMAVIEW_EXTENSION_LOAD_HANG_SEC:.0f} s)"))
      return
    except BaseException as e:  # noqa: BLE001 - whatever a pack raises stays in the pack (KeyboardInterrupt, its own)
      self._disable(pack, f"couldn't load: {self._guarded_text(pack, _describe, e)}"[:200])
      return
    if spent > COMMAVIEW_EXTENSION_LOAD_LIMIT_NS:
      self._disable(pack, f"took {spent / 1e6:.0f} ms to load, over {COMMAVIEW_EXTENSION_LOAD_LIMIT_NS / 1e6:.0f}")
      return
    if not has_payload:
      self._disable(pack, "its Extension has no payload(ctx)")
      return
    pack.state = "active"
    # Its services count from now (services).
    self.services_changed = True

  @staticmethod
  def _module_name(pack) -> str:
    return f"commaview_ext_{pack.id.replace('-', '_')}"

  def _guarded_text(self, pack, describe, value) -> str:
    """[describe](value), watched as any call into the pack is: its own __str__ (or a metaclass's __name__) is its
    code, and may hang as well as raise."""
    try:
      return self._guarded(pack, describe, value)
    except _ExtensionHang:
      # Counted as the pack's overrun: said so, as for any hang (its reason and offUntil agree with the list).
      return self._hang_reason(pack, "hung while its exception was described")
    except BaseException:  # noqa: BLE001 - what describing it raised
      return "(it can't be described)"

  def _unload(self, pack) -> None:
    sys.modules.pop(self._module_name(pack), None)

  def _run(self, pack, fn, hook: str) -> object:
    started = time.monotonic_ns()
    try:
      result = self._guarded(pack, fn, self._pack_ctx(pack))
    except _ExtensionHang:
      self._disable(pack, self._hang_reason(pack, f"hung (one call over {COMMAVIEW_EXTENSION_HANG_SEC:.0f} s)"))
      return None
    except BaseException as e:  # noqa: BLE001 - whatever a pack raises stays in the pack
      failures = pack.failures[hook] = pack.failures.get(hook, 0) + 1
      self._exporter._stats["extensionExceptions"] += 1
      if failures >= COMMAVIEW_EXTENSION_MAX_FAILURES:
        self._disable(pack, f"{hook}() raised {failures} times in a row: {self._guarded_text(pack, _describe, e)}"[:200])
      return None
    finally:
      pack.spent_ns += time.monotonic_ns() - started
    if result is not _NO_HOOK:  # a hook that isn't there ran nothing: no run of the pack's to count
      pack.failures.pop(hook, None)
    return result

  def _frame_ctx(self, ui_state) -> None:
    self._frame = (ui_state, _status_mode_name(getattr(ui_state, "status", "")), time.monotonic())

  def _pack_ctx(self, pack):
    """[pack]'s own context, its frame's fields set again before each call: what one pack writes to its ctx never
    reaches another pack."""
    ctx = pack.ctx
    if ctx is None:
      ctx = pack.ctx = _ExtensionContext(self._exporter)
    ctx.ui_state, ctx.status, ctx.now = self._frame
    return ctx

  def observe(self, ui_state, may_load: bool = True) -> None:
    """Every UI frame: each pack's observe(ctx), and the budget for the frame before. [may_load]: whether a pack
    waiting may load now (not while a reload is due)."""
    self._frame_ctx(ui_state)
    offroad = None  # asked only while a pack waits to load
    for pack in self.packs:
      if pack.awaiting_modules and pack.state == "inactive" and all(name in sys.modules for name in pack.requires_modules):
        # A module it needs, imported since: it loads as a pack waiting does (offroad), watched.
        pack.state, pack.reason, pack.awaiting_modules = "pending", "", False
        self._ensure_watchdog()
      if pack.state == "pending":
        # Never mid-drive, a UI restarted onroad included: a pack loads at the next offroad frame.
        if not may_load:
          continue
        if offroad is None:
          offroad = self._offroad(ui_state)
        if not offroad:
          continue
        self._load(pack)
      if pack.state != "active":
        continue
      spent, pack.spent_ns = pack.spent_ns, 0
      if spent > COMMAVIEW_EXTENSION_HARD_LIMIT_NS:
        self._disable(pack, f"took {spent / 1e6:.1f} ms in one frame")
        continue
      built, pack.payload_built = pack.payload_built, False
      if spent > COMMAVIEW_EXTENSION_FRAME_BUDGET_NS:
        pack.slow_frames += 1
      elif built:
        pack.slow_frames = 0
      if pack.slow_frames >= COMMAVIEW_EXTENSION_SLOW_FRAMES:
        self._disable(pack, f"over {COMMAVIEW_EXTENSION_FRAME_BUDGET_NS / 1e6:.0f} ms a frame for {pack.slow_frames} frames")
        continue
      instance = pack.instance
      if not pack.applies_checked:
        verdict = self._run(pack, lambda ctx: _applies(instance, ctx), "applies")
        if pack.state != "active" or "applies" in pack.failures:
          # Raised: asked again next frame, until it has raised COMMAVIEW_EXTENSION_MAX_FAILURES times.
          continue
        if not verdict:
          pack.state, pack.reason, pack.instance = "inactive", "applies() said this UI isn't one it serves", None
          self.services_changed = True
          self._unload(pack)
          continue
        pack.applies_checked = True
      if pack.has_observe:
        self._run(pack, lambda ctx: instance.observe(ctx), "observe")

  def payloads(self, ui_state) -> dict:
    """{id: payload} of each active pack's payload(ctx) that is a JSON object small enough to send."""
    self._frame_ctx(ui_state)
    result = {}
    sizes = {}
    for pack in self.packs:
      if pack.state != "active" or not pack.applies_checked:
        continue
      instance = pack.instance
      pack.payload_built = True
      payload = self._run(pack, lambda ctx: _call_hook(instance, "payload", ctx), "payload")
      if pack.state != "active" or payload is None:
        continue
      if payload is _NO_HOOK:
        self._disable(pack, "its Extension has no payload(ctx)")
        continue
      if not issubclass(type(payload), dict):  # type(): isinstance can ask the object's own __class__
        self._disable(pack, f"payload() returned {self._guarded_text(pack, _type_name, payload)}, not a dict")
        continue
      checking = time.monotonic_ns()
      try:
        # Guarded: a dict subclass's own items() runs in the check.
        encoded, sent = self._guarded(pack, _checked_payload, payload)
      except _ExtensionHang:
        self._disable(pack, self._hang_reason(pack, f"hung (one call over {COMMAVIEW_EXTENSION_HANG_SEC:.0f} s)"))
        continue
      except BaseException as e:  # noqa: BLE001 - JSON's refusals, or what a dict subclass's own code raised
        self._disable(pack, f"payload() isn't JSON: {self._guarded_text(pack, _describe, e)}"[:200])
        continue
      finally:
        # The check's cost is the pack's payload's: counted in its frame budget.
        pack.spent_ns += time.monotonic_ns() - checking
      if len(encoded) > COMMAVIEW_EXTENSION_MAX_PAYLOAD_BYTES:
        self._disable(pack, f"payload() is {len(encoded)} bytes, over {COMMAVIEW_EXTENSION_MAX_PAYLOAD_BYTES}")
        continue
      result[pack.id] = sent
      sizes[pack.id] = len(encoded)
    # Together over the cap: the largest block left out of this frame, again until they fit. Not the pack's fault
    # alone, so it isn't turned off: next frame it's weighed again.
    total = sum(sizes.values())
    while total > COMMAVIEW_EXTENSION_MAX_TOTAL_PAYLOAD_BYTES:
      largest = max(sizes, key=lambda ext_id: (sizes[ext_id], ext_id))
      total -= sizes.pop(largest)
      del result[largest]
      self._exporter._stats["extensionPayloadsDropped"] += 1
    return result

  def statuses(self) -> dict:
    return {pack.id: pack.status() for pack in self.packs}


class _PackHostBase:
  """What a pack's context reads through its host: a service's latest message, and a param, cached. The comma's
  exporter takes these two methods (its own reads go through them too); PackHost is the other host."""

  def _service_msg(self, ui_state, service: str):
    try:
      return ui_state.sm[service]
    except (KeyError, TypeError, IndexError):
      return None

  def _param_value(
    self, ui_state, key: str, kind: str, default, attr: str | None = None, cache: dict | None = None,
    stagger: bool = False,
  ):
    # The value the UI already parsed (sunnypilot refreshes these on its own param thread). Without such an
    # attribute the param file is read at most once per COMMAVIEW_PARAMS_REFRESH_SEC.
    if attr:
      value = getattr(ui_state, attr, None)
      if value is not None:
        return _coerce_param(value, kind, default)
    now = time.monotonic()
    # By kind: a pack's ctx.param("X") as a string never hands the exporter's own float read a string, nor the
    # other way round.
    # Packs' reads (ctx.param) are held apart from the exporter's own: they can ask for any key and default, and
    # never push the exporter's few out.
    cache = self._param_cache if cache is None else cache
    # What the file holds is cached, by key and kind (a bool is read another way); the default applies per call, so
    # a default (whatever its repr, a pack's object's included) never splits the cache nor runs code of its own.
    cache_key = (key, kind)
    cached = cache.get(cache_key)
    if cached is None or now >= cached[0]:
      if cached is None and len(cache) >= COMMAVIEW_PARAM_CACHE_MAX:
        # Never outgrows this: read again instead.
        cache.clear()
      # Kept until it expires: a pack's (stagger) a random 0-20% later than the exporter's own.
      wait = COMMAVIEW_PARAMS_REFRESH_SEC * (1 + random.random() * COMMAVIEW_PARAMS_REFRESH_STAGGER if stagger else 1)
      cached = (now + wait, _read_param(getattr(ui_state, "params", None), key, kind, _PARAM_MISSING))
      cache[cache_key] = cached
    raw = cached[1]
    return _coerce_param(default if raw is _PARAM_MISSING else raw, kind, default)


class PackHost(_PackHostBase):
  """The host's side of a pack's context off the comma (check-pack): the exporter's counters and caches that
  _Extensions and _ExtensionContext use, with nothing else of the exporter's."""

  def __init__(self):
    self._param_cache = {}
    self._pack_param_cache = {}
    self._stats = dict.fromkeys(COMMAVIEW_EXTENSION_STATS, 0)
