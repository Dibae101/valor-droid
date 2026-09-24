"""A scriptable fake Android device, driven entirely through a fake `adb`.

The exploration engine had no end-to-end coverage: every mechanism that decides
what to tap was verified only by unit tests over its own data structures. This
module supplies a real subprocess-level device so `AndroidRunner` can be driven
through a whole run without an emulator, including the paths that only appear
under stall and recovery.

The simulated app is deliberately shaped to exercise the four gap fixes:

- a clock element whose text changes on every dump, so an implementation that
  hashes the raw hierarchy would mint a new screen identity every observation
  (gap 2);
- a screen with no actionable element, which forces the recovery ladder (gap 7);
- an email field and a quantity field, which one constant value cannot satisfy
  (gap 4);
- a deep-link entry point that reaches a screen ordinary taps cannot (gap 8).

For the WebView adapter (§3.3) the device also supplies the evidence that
adapter's ownership and freshness checks read: `/proc/net/unix` with an abstract
debug socket named after a process id, `adb forward` allocation and removal, a
real loopback `/json/list` + `/json/version` endpoint, and a stub `websocket`
module speaking the small CDP subset the adapter uses. Every `adb` invocation is
appended to a log so a test can assert that no input was ever dispatched.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PACKAGE = "com.example.app"
LAUNCHER = f"{PACKAGE}/{PACKAGE}.MainActivity"
LOG_TAG = "AndroLog"
METHOD_PREFIX = "METHOD="
# The process the fake device runs the app as, and therefore the process id its
# WebView debug socket is named after.
APP_PID = 4242
WEBVIEW_SOCKET = f"webview_devtools_remote_{APP_PID}"

# Every method the fake app can report, which becomes the frozen universe.
UNITS = (
    f"{PACKAGE}.Deep.onCreate()V",
    f"{PACKAGE}.Detail.onCreate()V",
    f"{PACKAGE}.Form.type()V",
    f"{PACKAGE}.Leaf.onCreate()V",
    f"{PACKAGE}.Main.onCreate()V",
)

_DEVICE_SCRIPT = r'''#!/usr/bin/env python3
"""Fake adb backed by a JSON state file. Simulates one app on one device."""
import json, os, sys, time
from pathlib import Path

STATE = Path(os.environ["FAKE_DEVICE_STATE"])
PACKAGE = "com.example.app"
PID = 4242

# screen -> (methods emitted on entry, {element key: attributes})
SCREENS = {
    "main": ["com.example.app.Main.onCreate()V"],
    "detail": ["com.example.app.Detail.onCreate()V"],
    "leaf": ["com.example.app.Leaf.onCreate()V"],
    "deep": ["com.example.app.Deep.onCreate()V"],
    # A screen whose only content is a native WebView. It emits no method of its
    # own so the frozen universe is unchanged by its existence.
    "web": [],
}
ACTIVITIES = {
    "main": "MainActivity", "detail": "DetailActivity",
    "leaf": "LeafActivity", "deep": "DeepActivity", "web": "WebActivity",
}


def load():
    if not STATE.is_file():
        return {
            "screen": "main", "stack": [], "installed": False, "seen": [],
            "typed": {}, "dumps": 0, "boot": "11111111-2222-3333-4444-555555555555",
        }
    return json.loads(STATE.read_text())


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE)


def emit(state, methods):
    """Append method lines for screens entered for the first time."""
    lines = []
    for method in methods:
        if method in state["seen"]:
            continue
        state["seen"].append(method)
        lines.append(
            "%.3f %5d %5d I AndroLog: METHOD=%s\n" % (time.time(), PID, PID, method)
        )
    if lines:
        with (STATE.parent / "emitted.log").open("a") as handle:
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())


def enter(state, screen):
    if state["screen"] != screen:
        state["stack"].append(state["screen"])
    state["screen"] = screen
    emit(state, SCREENS[screen])


def node(index, cls, bounds, **extra):
    attributes = {
        "index": str(index), "text": "", "resource-id": "", "class": cls,
        "package": PACKAGE, "content-desc": "", "checkable": "false",
        "checked": "false", "clickable": "false", "enabled": "true",
        "focusable": "true", "focused": "false", "scrollable": "false",
        "long-clickable": "false", "password": "false", "selected": "false",
        "bounds": bounds,
    }
    attributes.update(extra)
    return "<node " + " ".join(
        '%s="%s"' % (key, value) for key, value in attributes.items()
    ) + "/>"


def hierarchy(state):
    dumps = state["dumps"]
    children = [
        # The clock text changes on every dump. Screen identity must ignore it.
        node(0, "android.widget.TextView", "[0,0][200,40]",
             **{"resource-id": PACKAGE + ":id/clock", "text": "tick-%d" % dumps}),
    ]
    screen = state["screen"]
    if screen == "main":
        children += [
            node(1, "android.widget.Button", "[0,60][200,120]", clickable="true",
                 **{"resource-id": PACKAGE + ":id/go", "text": "Open detail"}),
            node(2, "android.widget.EditText", "[0,140][200,200]", clickable="true",
                 **{"resource-id": PACKAGE + ":id/email_input",
                    "text": state["typed"].get("email", "")}),
            node(3, "android.widget.EditText", "[0,220][200,280]", clickable="true",
                 **{"resource-id": PACKAGE + ":id/item_quantity",
                    "text": state["typed"].get("quantity", "")}),
        ]
    elif screen == "detail":
        children += [
            node(1, "android.widget.Button", "[0,60][200,120]", clickable="true",
                 **{"resource-id": PACKAGE + ":id/deeper", "text": "Go deeper"}),
        ]
    elif screen == "leaf":
        # Deliberately actionless: the ladder is the only way forward.
        children += [
            node(1, "android.widget.TextView", "[0,60][200,120]",
                 **{"resource-id": PACKAGE + ":id/dead_end", "text": "Nothing here"}),
        ]
    elif screen == "deep":
        children += [
            node(1, "android.widget.Button", "[0,60][200,120]", clickable="true",
                 **{"resource-id": PACKAGE + ":id/deep_action", "text": "Deep action"}),
        ]
    elif screen == "web":
        # 400x600 of native surface, so a 400x600 CSS viewport projects 1:1 and
        # the adapter's aspect-ratio guard is satisfied by real geometry.
        children += [
            node(1, "android.webkit.WebView", "[0,300][400,900]",
                 **{"resource-id": PACKAGE + ":id/web"}),
        ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<hierarchy rotation="0">'
        + "".join(children)
        + "</hierarchy>"
    )


def tap(state, x, y):
    screen = state["screen"]
    if screen == "main":
        if 60 <= y <= 120:
            enter(state, "detail")
        elif 140 <= y <= 200:
            state["pending_field"] = "email"
        elif 220 <= y <= 280:
            state["pending_field"] = "quantity"
    elif screen == "detail" and 60 <= y <= 120:
        enter(state, "leaf")
    elif screen == "deep" and 60 <= y <= 120:
        emit(state, ["com.example.app.Form.type()V"])


def log_call(args):
    """Append every invocation so a test can assert what was never dispatched.

    Deliberately not fsynced: one O_APPEND write of a short line is atomic
    between the separate processes that share this file, and syncing every adb
    call cost enough wall clock to push whole runs past their time budget.
    """
    with (STATE.parent / "adb-calls.log").open("a") as handle:
        handle.write(json.dumps(list(args)) + "\n")


def main():
    args = sys.argv[1:]
    if args[:1] == ["-s"]:
        args = args[2:]
    if not args:
        return 1
    log_call(args)
    state = load()
    command = args[0]

    if command == "forward":
        rest = args[1:]
        if rest[:1] == ["--remove"]:
            # A device that refuses to release a forward is a real failure mode,
            # and the adapter must not be permanently disabled by it.
            if (STATE.parent / "forward-remove-fails").is_file():
                sys.stderr.write("error: listener 'tcp:0' not found\n")
                return 1
            return 0
        if len(rest) == 2 and rest[0] == "tcp:0" and rest[1].startswith("localabstract:"):
            port_file = STATE.parent / "devtools-port"
            if not port_file.is_file():
                sys.stderr.write("error: cannot bind listener\n")
                return 1
            print(port_file.read_text().strip())
            return 0
        sys.stderr.write("fake adb: unsupported forward %r\n" % (rest,))
        return 1
    if command == "get-state":
        print("device"); return 0
    if command == "devices":
        print("List of devices attached")
        print("fake-device\tdevice product:fake model:fake device:fake")
        return 0
    if command == "install":
        state["installed"] = True
        save(state)
        print("Success"); return 0
    if command == "uninstall":
        state["installed"] = False
        save(state)
        print("Success"); return 0
    if command == "logcat":
        # Follow the emitted log until killed, like a real logcat stream.
        path = STATE.parent / "emitted.log"
        path.touch(exist_ok=True)
        with path.open() as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.01)
    if command == "exec-out":
        rest = args[1:]
        if rest[:1] == ["cat"]:
            state["dumps"] += 1
            save(state)
            sys.stdout.write(hierarchy(state))
            return 0
        if rest[:1] == ["screencap"]:
            sys.stdout.write("PNG")
            return 0
        return 1
    if command != "shell":
        sys.stderr.write("fake adb: unsupported command %r\n" % command)
        return 1

    rest = args[1:]
    if rest[:2] == ["sh", "-c"] and len(rest) == 3 and "VALORDROID_URI" in rest[2]:
        # A real device does not behave this way, and modelling it as if it did
        # is why 862 failed deep-link launches across two campaigns went unseen.
        # adb joins the remote argv with spaces, so the device shell reads
        # `sh -c IFS= read -r VALORDROID_PACKAGE; ...`: `sh -c` takes `IFS=` as
        # its entire command and everything after the first `;` runs in the outer
        # shell, where the first `read` consumes the package into VALORDROID_URI
        # and VALORDROID_PACKAGE is never set. Reproduce the failure instead.
        sys.stderr.write(
            "Starting: Intent { act=android.intent.action.VIEW dat=%s pkg= }\n"
            "Error: Activity not started, unable to resolve Intent "
            "{ act=android.intent.action.VIEW dat=%s flg=0x10000000 pkg= }\n"
            % (PACKAGE, PACKAGE)
        )
        return 0
    if len(rest) == 1 and "VALORDROID_URI" in rest[0]:
        # The shape that works: the script is the whole remote command, so the
        # device shell runs it and both stdin lines reach its own `read`.
        package = sys.stdin.readline().rstrip("\n")
        uri = sys.stdin.readline().rstrip("\n")
        if package != PACKAGE or not uri:
            sys.stderr.write("fake adb: malformed stdin VIEW launch\n")
            return 64
        enter(state, "deep")
        save(state)
        print("Status: ok")
        return 0
    if rest[:1] == ["getprop"]:
        values = {"ro.build.version.sdk": "34", "sys.boot_completed": "1"}
        print(values.get(rest[1] if len(rest) > 1 else "", "")); return 0
    if rest[:2] == ["pm", "path"]:
        if not state["installed"]:
            return 1
        print("package:/data/app/com.example.app/base.apk"); return 0
    if rest[:2] == ["pm", "clear"]:
        state.update({"screen": "main", "stack": [], "typed": {}})
        save(state)
        print("Success"); return 0
    if rest[:1] == ["date"]:
        print(int(time.time()) - 5); return 0
    if rest[:1] == ["ps"]:
        print("PID NAME")
        if state["installed"]:
            print("%d %s" % (PID, PACKAGE))
            # An app may legitimately run several processes, and each one can own
            # its own WebView debug socket.
            for pid, name in state.get("extra_processes", []):
                print("%d %s" % (pid, name))
        return 0
    if rest[:1] == ["cat"]:
        target = rest[1] if len(rest) > 1 else ""
        if target == "/proc/net/unix":
            # Abstract socket names appear with a leading '@' and no filesystem
            # path, exactly as Android reports a WebView devtools socket.
            print("Num       RefCount Protocol Flags    Type St Inode Path")
            for index, name in enumerate(state.get("unix_sockets", [])):
                print("%016x: 00000002 00000000 00010000 0001 01 %d @%s"
                      % (index + 1, 100000 + index, name))
            return 0
        if target == "/proc/sys/kernel/random/boot_id":
            print(state["boot"]); return 0
        if target == "/proc/%d/stat" % PID:
            # Fields up to 22 (starttime); only the shape matters.
            fields = ["0"] * 49
            fields[19] = "9999"
            print("%d (%s) S %s" % (PID, PACKAGE, " ".join(fields))); return 0
        return 1
    if rest[:1] == ["rm"]:
        return 0
    if rest[:1] == ["test"]:
        # The observer probes that the dump produced a nonempty file before
        # reading it back. This device always has a hierarchy available.
        return 0
    if rest[:1] == ["uiautomator"]:
        # The dump itself is a no-op; `exec-out cat` renders the current screen.
        return 0
    if rest[:2] == ["am", "start"]:
        if "-d" in rest:
            enter(state, "deep")
        elif "-n" in rest:
            component = rest[rest.index("-n") + 1]
            if component.endswith("DeepActivity"):
                enter(state, "deep")
            elif component.endswith("DetailActivity"):
                enter(state, "detail")
            else:
                state.update({"screen": "main", "stack": []})
                emit(state, SCREENS["main"])
        save(state)
        print("Status: ok"); return 0
    if rest[:2] == ["am", "force-stop"]:
        return 0
    if rest[:1] == ["dumpsys"]:
        activity = ACTIVITIES[state["screen"]]
        owner = state.get("foreground_package", PACKAGE)
        print("  topResumedActivity=ActivityRecord{abc u0 %s/.%s t1}" % (owner, activity))
        return 0
    if rest[:2] == ["input", "tap"]:
        tap(state, int(rest[2]), int(rest[3]))
        save(state)
        return 0
    if rest[:2] == ["input", "text"]:
        field = state.pop("pending_field", None)
        if field is not None:
            state["typed"][field] = rest[2]
            emit(state, ["com.example.app.Form.type()V"])
        save(state)
        return 0
    if rest[:2] == ["input", "swipe"]:
        return 0
    if rest[:2] == ["input", "keyevent"]:
        if state["stack"]:
            state["screen"] = state["stack"].pop()
        save(state)
        return 0
    sys.stderr.write("fake adb: unsupported shell %r\n" % (rest,))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

_AAPT_SCRIPT = f'''#!/usr/bin/env python3
import sys
if "badging" in sys.argv:
    print("package: name='{PACKAGE}' versionCode='7' versionName='1.2.3'")
    print("launchable-activity: name='{PACKAGE}.MainActivity'  label='' icon=''")
    raise SystemExit(0)
if "xmltree" in sys.argv:
    print("N: android=http://schemas.android.com/apk/res/android")
    print("  E: manifest (line=2)")
    print("    A: package=\\"{PACKAGE}\\" (Raw: \\"{PACKAGE}\\")")
    print("    E: application (line=10)")
    # A manifest Android would actually accept: the launcher carries both MAIN
    # and LAUNCHER, the activity owning the deep link is exported because an
    # implicit VIEW intent cannot start one that is not, and a separate
    # non-exported activity supplies the forced-route target.
    for name, exported in (
        ("MainActivity", True),
        ("DetailActivity", True),
        ("DeepActivity", True),
        ("InternalActivity", False),
    ):
        print("      E: activity (line=11)")
        print("        A: android:name(0x01010003)=\\"{PACKAGE}.%s\\" (Raw: \\"x\\")" % name)
        print("        A: android:exported(0x01010010)=(type 0x12)0x%s"
              % ("ffffffff" if exported else "0"))
        if name == "MainActivity":
            print("        E: intent-filter (line=20)")
            print("          E: action (line=21)")
            print("            A: android:name(0x01010003)=\\"android.intent.action.MAIN\\" (Raw: \\"x\\")")
            print("          E: category (line=22)")
            print("            A: android:name(0x01010003)=\\"android.intent.category.LAUNCHER\\" (Raw: \\"x\\")")
        if name == "DeepActivity":
            print("        E: intent-filter (line=30)")
            print("          E: action (line=31)")
            print("            A: android:name(0x01010003)=\\"android.intent.action.VIEW\\" (Raw: \\"x\\")")
            print("          E: data (line=32)")
            print("            A: android:scheme(0x01010027)=\\"fakeapp\\" (Raw: \\"fakeapp\\")")
            print("            A: android:host(0x01010028)=\\"deep\\" (Raw: \\"deep\\")")
    raise SystemExit(0)
raise SystemExit(1)
'''


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def install_fake_device(root: Path) -> dict[str, str]:
    """Write a fake adb and aapt, and return the paths plus the state file."""

    root.mkdir(parents=True, exist_ok=True)
    adb = _executable(root / "adb", _DEVICE_SCRIPT)
    aapt = _executable(root / "aapt", _AAPT_SCRIPT)
    state = root / "device-state.json"
    return {"adb": str(adb), "aapt": str(aapt), "state": str(state)}


def device_environment(paths: dict[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment["FAKE_DEVICE_STATE"] = paths["state"]
    return environment


def read_state(paths: dict[str, str]) -> dict:
    path = Path(paths["state"])
    return json.loads(path.read_text()) if path.is_file() else {}


def write_state(paths: dict[str, str], **values: object) -> dict:
    """Merge values into the device state, creating a plausible default first."""

    state = read_state(paths) or {
        "screen": "main",
        "stack": [],
        "installed": True,
        "seen": [],
        "typed": {},
        "dumps": 0,
        "boot": "11111111-2222-3333-4444-555555555555",
    }
    state.update(values)
    Path(paths["state"]).write_text(json.dumps(state))
    return state


def read_adb_calls(paths: dict[str, str]) -> tuple[tuple[str, ...], ...]:
    """Every `adb` invocation the fake device saw, serial already stripped."""

    path = Path(paths["state"]).parent / "adb-calls.log"
    if not path.is_file():
        return ()
    calls: list[tuple[str, ...]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            calls.append(tuple(json.loads(line)))
    return tuple(calls)


def clear_adb_calls(paths: dict[str, str]) -> None:
    path = Path(paths["state"]).parent / "adb-calls.log"
    if path.is_file():
        path.unlink()


def refuse_forward_removal(paths: dict[str, str], refuse: bool = True) -> None:
    """Make `adb forward --remove` fail, as a wedged adb server does."""

    marker = Path(paths["state"]).parent / "forward-remove-fails"
    if refuse:
        marker.write_text("1")
    elif marker.is_file():
        marker.unlink()


# --------------------------------------------------------------------------
# WebView debugger endpoints
# --------------------------------------------------------------------------

WEB_TARGET_ID = "PAGE-0123456789ABCDEF"


def dom_element(
    key: str,
    *,
    tag: str = "button",
    kind: str = "",
    text: str = "",
    label: str = "",
    rect: tuple[float, float, float, float] = (10.0, 10.0, 200.0, 60.0),
    enabled: bool = True,
    checked: bool = False,
    selected: bool = False,
    password: bool = False,
) -> dict:
    """One control shaped exactly as the in-page snapshot expression returns it."""

    return {
        "key": key,
        "tag": tag,
        "type": kind,
        "text": text,
        "label": label,
        "enabled": enabled,
        "checked": checked,
        "selected": selected,
        "password": password,
        "rect": list(rect),
    }


def dom_snapshot(
    url: str,
    elements: list[dict],
    *,
    width: int = 400,
    height: int = 600,
    scroll_x: int = 0,
    scroll_y: int = 0,
    scroll_width: int | None = None,
    scroll_height: int | None = None,
    controls_seen: int | None = None,
    unsupported: str | None = None,
) -> dict:
    """The value `DOM_SNAPSHOT` evaluates to, including page scroll extent."""

    if unsupported is not None:
        return {"unsupported": unsupported, "url": url}
    return {
        "url": url,
        "width": width,
        "height": height,
        "elements": list(elements),
        "controlsSeen": len(elements) if controls_seen is None else controls_seen,
        "scrollX": scroll_x,
        "scrollY": scroll_y,
        "scrollWidth": width if scroll_width is None else scroll_width,
        "scrollHeight": height if scroll_height is None else scroll_height,
    }


def dom_freshness(snapshot: dict) -> dict:
    """The smaller value `DOM_FRESHNESS` evaluates to for the same page."""

    if snapshot.get("unsupported"):
        return {"unsupported": snapshot["unsupported"], "url": snapshot.get("url", "")}
    return {
        "url": snapshot["url"],
        "width": snapshot["width"],
        "height": snapshot["height"],
        "scrollX": snapshot["scrollX"],
        "scrollY": snapshot["scrollY"],
        "scrollWidth": snapshot["scrollWidth"],
        "scrollHeight": snapshot["scrollHeight"],
        "controlsSeen": snapshot["controlsSeen"],
    }


def devtools_document(
    snapshot: dict,
    *,
    android_package: str = PACKAGE,
    target_id: str = WEB_TARGET_ID,
    target_type: str = "page",
    listed_url: str | None = None,
    extra_targets: list[dict] | None = None,
    freshness: dict | None = None,
    websocket_url: str | None = None,
    frame_url: str | None = None,
) -> dict:
    """One debugger view of the device: what /json serves and what the page is.

    `listed_url` and `frame_url` exist so a test can make the target list, the
    frame tree and the evaluated document disagree with each other, which is what
    a navigation part-way through an observation looks like.
    """

    url = snapshot.get("url", "")
    target = {
        "id": target_id,
        "type": target_type,
        "url": url if listed_url is None else listed_url,
        "webSocketDebuggerUrl": (
            f"ws://127.0.0.1:9222/devtools/page/{target_id}"
            if websocket_url is None
            else websocket_url
        ),
    }
    return {
        "android_package": android_package,
        "targets": [target] + list(extra_targets or []),
        "pages": {
            target_id: {
                "url": url if frame_url is None else frame_url,
                "snapshot": snapshot,
                "freshness": dom_freshness(snapshot)
                if freshness is None
                else freshness,
            }
        },
    }


class FakeDevTools:
    """A loopback `/json/version` + `/json/list` endpoint for one fake WebView.

    Bound to 127.0.0.1 on an ephemeral port, and the port is published where the
    fake `adb forward` reads it, so the adapter's own forwarding, discovery and
    redirect handling all run for real.
    """

    def __init__(self, root: Path, document: dict) -> None:
        self.root = root
        self.document_path = root / "devtools.json"
        self.set_document(document)
        handler = _handler_class(self.document_path)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self._thread.start()
        (root / "devtools-port").write_text(str(self.port))

    def set_document(self, document: dict) -> None:
        temporary = self.document_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document))
        os.replace(temporary, self.document_path)

    def document(self) -> dict:
        return json.loads(self.document_path.read_text())

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)


def _handler_class(document_path: Path):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, payload: object, code: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server contract
            document = json.loads(document_path.read_text())
            if self.path == "/json/version":
                self._send(
                    {
                        "Browser": "Chrome/120.0.0.0",
                        "Protocol-Version": "1.3",
                        "Android-Package": document["android_package"],
                    }
                )
            elif self.path in ("/json/list", "/json"):
                self._send(document["targets"])
            else:
                self._send({"error": "not found"}, code=404)

        def log_message(self, *_: object) -> None:
            return

    return _Handler


class _FakeWebSocket:
    """The CDP subset the adapter uses, answered from the debugger document."""

    def __init__(self, url: str, document_path: Path) -> None:
        from valordroid.android.webview import DOM_FRESHNESS, DOM_SNAPSHOT

        self.url = url
        self.document_path = document_path
        self.sent: list[dict] = []
        self.closed = False
        self._pending: list[str] = []
        self._expressions = {DOM_SNAPSHOT: "snapshot", DOM_FRESHNESS: "freshness"}
        self._target_id = url.rsplit("/", 1)[-1]

    def settimeout(self, value: float) -> None:
        self._timeout = value

    def _page(self) -> dict:
        document = json.loads(self.document_path.read_text())
        pages = document.get("pages", {})
        if self._target_id not in pages:
            raise AssertionError(f"no fake page for target {self._target_id!r}")
        return pages[self._target_id]

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        identifier = message["id"]
        method = message["method"]
        page = self._page()
        if method == "Page.getFrameTree":
            result: object = {
                "frameTree": {
                    "frame": {"id": "FRAME-" + self._target_id, "url": page["url"]}
                }
            }
        elif method == "Page.createIsolatedWorld":
            result = {"executionContextId": 7}
        elif method == "Runtime.evaluate":
            name = self._expressions.get(message["params"]["expression"])
            if name is None:
                self._pending.append(
                    json.dumps(
                        {
                            "id": identifier,
                            "error": {"code": -32000, "message": "unknown expression"},
                        }
                    )
                )
                return
            result = {"result": {"type": "object", "value": page[name]}}
        else:
            self._pending.append(
                json.dumps(
                    {
                        "id": identifier,
                        "error": {"code": -32601, "message": "unsupported"},
                    }
                )
            )
            return
        self._pending.append(json.dumps({"id": identifier, "result": result}))

    def recv(self) -> str:
        if not self._pending:
            raise AssertionError("fake debugger has no queued response")
        return self._pending.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeWebSocketModule:
    """Stands in for the optional `websocket` dependency."""

    def __init__(self, document_path: Path) -> None:
        self.document_path = document_path
        self.connections: list[_FakeWebSocket] = []

    def create_connection(self, url: str, **kwargs: object) -> _FakeWebSocket:
        connection = _FakeWebSocket(url, self.document_path)
        self.connections.append(connection)
        return connection


def install_fake_websocket(devtools: FakeDevTools) -> FakeWebSocketModule:
    """Register the stub as the importable `websocket` module."""

    module = FakeWebSocketModule(devtools.document_path)
    sys.modules["websocket"] = module  # type: ignore[assignment]
    return module


def remove_fake_websocket() -> None:
    sys.modules.pop("websocket", None)
