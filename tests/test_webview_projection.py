"""DOM projection, page geometry, and the refusals that keep it honest.

These tests exercise the pure projection layer: no device, no debugger. What they
pin is that a projected node is either a faithful, provably-sourced
representation of one measured page or is refused outright.

Pinned findings from §3.3 of the audit:

- coordinates cannot be recovered from a native rectangle alone, so a zoomed or
  letterboxed view must be refused rather than approximated;
- a page longer than its viewport was never offered as scrollable, so the
  candidate builder's existing `scrollable == "true"` gate could not produce a
  DOM scroll and a long document was explored only as far as its first screen;
- a scroll that had already reached the bottom and a scroll that did nothing are
  indistinguishable without the page's own progress; and
- the reserved `valordroid.webview:` prefix is what marks a node's coordinates as
  measured by the debugger, so a native dump carrying it is forged provenance.
"""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from valordroid.android.observe import AndroidObserver
from valordroid.android.webview import (
    DOM_CONTAINER_RESOURCE_ID,
    DOM_PROVENANCE,
    DOM_SCROLL_AT_END_ATTRIBUTE,
    DOM_SCROLL_OFFSET_ATTRIBUTE,
    DOM_SCROLL_PROGRESS_ATTRIBUTE,
    DOM_TARGET_URL_ATTRIBUTE,
    RESERVED_DOM_RESOURCE_PREFIX,
    WebViewObservationError,
    append_dom_nodes,
    reserved_dom_nodes,
    scroll_geometry,
)
from valordroid.models import stable_hash

PACKAGE = "com.example.app"
URL = "https://app.example/library"


def _webview(bounds: str = "[0,300][400,900]") -> ET.Element:
    return ET.Element(
        "node",
        {
            "class": "android.webkit.WebView",
            "package": PACKAGE,
            "resource-id": PACKAGE + ":id/web",
            "bounds": bounds,
            "text": "",
            "content-desc": "",
            "enabled": "true",
        },
    )


def _element(key: str, **overrides) -> dict:
    item = {
        "key": key,
        "tag": "button",
        "type": "",
        "text": "Play",
        "label": "play",
        "enabled": True,
        "checked": False,
        "selected": False,
        "password": False,
        "rect": [10.0, 10.0, 200.0, 60.0],
    }
    item.update(overrides)
    return item


def _snapshot(**overrides) -> dict:
    snapshot = {
        "url": URL,
        "width": 400,
        "height": 600,
        "elements": [_element("id:play")],
        "controlsSeen": 1,
        "scrollX": 0,
        "scrollY": 0,
        "scrollWidth": 400,
        "scrollHeight": 600,
    }
    snapshot.update(overrides)
    return snapshot


class ProjectionTest(unittest.TestCase):
    def test_one_control_projects_into_the_measured_native_rectangle(self) -> None:
        view = _webview()
        projection = append_dom_nodes(view, _snapshot(), PACKAGE)
        self.assertEqual(projection.controls, 1)
        self.assertEqual(projection.controls_seen, 1)
        node = list(view)[0]
        # 400x600 of CSS viewport over 400x600 of native surface is 1:1, so the
        # rectangle is the DOM rectangle offset by the view's own origin.
        self.assertEqual(node.get("bounds"), "[10,310][200,360]")
        self.assertEqual(node.get("class"), "android.widget.Button")
        self.assertEqual(node.get("clickable"), "true")
        self.assertEqual(node.get("text"), "Play")

    def test_every_projected_node_carries_its_page_provenance(self) -> None:
        """Provenance is on the node, not in the selector.

        The selector is the pre-image of `target_id` and a frozen six-field
        contract, so it cannot carry the page digest without changing the
        identity of every DOM action. The node can, and it is the same capture the
        executor resolves against.
        """

        view = _webview()
        append_dom_nodes(view, _snapshot(scrollHeight=1800), PACKAGE)
        for node in view:
            self.assertEqual(node.get("source"), DOM_PROVENANCE)
            self.assertTrue(
                node.get("resource-id", "").startswith(RESERVED_DOM_RESOURCE_PREFIX)
            )
            self.assertRegex(node.get(DOM_TARGET_URL_ATTRIBUTE, ""), r"^[0-9a-f]{64}$")

    def test_a_password_value_never_leaves_the_renderer(self) -> None:
        view = _webview()
        append_dom_nodes(
            view,
            _snapshot(
                elements=[
                    _element(
                        "id:secret",
                        tag="input",
                        kind="password",
                        text="",
                        password=True,
                    )
                ]
            ),
            PACKAGE,
        )
        node = list(view)[0]
        self.assertEqual(node.get("text"), "")
        self.assertEqual(node.get("password"), "true")

    def test_a_zoomed_viewport_is_refused_rather_than_approximated(self) -> None:
        with self.assertRaises(WebViewObservationError):
            append_dom_nodes(
                _webview(), {"unsupported": "zoomed_visual_viewport", "url": URL}, PACKAGE
            )

    def test_mismatched_aspect_ratios_are_refused(self) -> None:
        # 400x600 of CSS over a 400x300 native surface is letterboxed or cropped,
        # and one rectangle cannot say which.
        with self.assertRaises(WebViewObservationError):
            append_dom_nodes(_webview("[0,300][400,600]"), _snapshot(), PACKAGE)

    def test_an_invalid_viewport_is_refused(self) -> None:
        for broken in ({"width": 0}, {"height": -5}, {"width": "400"}):
            with self.subTest(broken=broken):
                with self.assertRaises(WebViewObservationError):
                    append_dom_nodes(_webview(), _snapshot(**broken), PACKAGE)

    def test_a_snapshot_without_a_control_list_is_refused(self) -> None:
        snapshot = _snapshot()
        del snapshot["elements"]
        with self.assertRaises(WebViewObservationError):
            append_dom_nodes(_webview(), snapshot, PACKAGE)

    def test_empty_native_bounds_are_refused(self) -> None:
        with self.assertRaises(WebViewObservationError):
            append_dom_nodes(_webview("[0,300][0,900]"), _snapshot(), PACKAGE)

    def test_offscreen_and_malformed_controls_are_dropped_not_guessed(self) -> None:
        view = _webview()
        projection = append_dom_nodes(
            view,
            _snapshot(
                elements=[
                    _element("id:ok"),
                    _element("id:offscreen", rect=[500.0, 10.0, 700.0, 60.0]),
                    _element("id:degenerate", rect=[10.0, 10.0, 10.0, 60.0]),
                    _element("id:nonfinite", rect=[float("nan"), 1.0, 2.0, 3.0]),
                    {"not": "a control"},
                ],
                controlsSeen=5,
            ),
            PACKAGE,
        )
        self.assertEqual(projection.controls, 1)
        self.assertEqual(projection.controls_seen, 5)

    def test_control_text_from_the_renderer_cannot_break_the_xml(self) -> None:
        view = _webview()
        append_dom_nodes(
            view, _snapshot(elements=[_element("id:x", text="a\x00b\x08c")]), PACKAGE
        )
        rendered = ET.tostring(view, encoding="unicode")
        self.assertNotIn("\x00", rendered)
        self.assertEqual(ET.fromstring(rendered).find("node").get("text"), "abc")


class ScrollGeometryTest(unittest.TestCase):
    def test_a_page_that_fits_reports_no_scrollable_extent(self) -> None:
        geometry = scroll_geometry(_snapshot())
        self.assertFalse(geometry.scrollable)
        self.assertTrue(geometry.at_end)
        self.assertEqual(geometry.progress_permille, 1000)

    def test_progress_runs_from_the_top_of_a_long_page_to_its_bottom(self) -> None:
        top = scroll_geometry(_snapshot(scrollHeight=1800, scrollY=0))
        middle = scroll_geometry(_snapshot(scrollHeight=1800, scrollY=600))
        bottom = scroll_geometry(_snapshot(scrollHeight=1800, scrollY=1200))
        self.assertEqual(
            [top.progress_permille, middle.progress_permille, bottom.progress_permille],
            [0, 500, 1000],
        )
        self.assertEqual(
            [top.at_end, middle.at_end, bottom.at_end], [False, False, True]
        )
        self.assertTrue(top.scrollable)

    def test_one_pixel_short_of_the_extent_still_counts_as_the_end(self) -> None:
        # Fractional device scaling routinely leaves a page one CSS pixel short.
        geometry = scroll_geometry(_snapshot(scrollHeight=1800, scrollY=1199))
        self.assertTrue(geometry.at_end)

    def test_negative_or_nonfinite_geometry_is_refused(self) -> None:
        for broken in (
            {"scrollY": -1},
            {"scrollY": float("inf")},
            {"scrollHeight": 0},
            {"scrollWidth": "400"},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(WebViewObservationError):
                    scroll_geometry(_snapshot(**broken))

    def test_a_reported_extent_smaller_than_the_viewport_is_clamped(self) -> None:
        geometry = scroll_geometry(_snapshot(scrollHeight=100))
        self.assertEqual(geometry.scroll_height, 600)
        self.assertFalse(geometry.scrollable)


class ScrollContainerTest(unittest.TestCase):
    def test_a_long_page_projects_a_container_that_reports_real_scrollability(
        self,
    ) -> None:
        view = _webview()
        projection = append_dom_nodes(
            view, _snapshot(scrollHeight=1800, scrollY=600), PACKAGE
        )
        self.assertTrue(projection.container_added)
        container = [
            node
            for node in view
            if node.get("resource-id") == DOM_CONTAINER_RESOURCE_ID
        ]
        self.assertEqual(len(container), 1)
        self.assertEqual(container[0].get("scrollable"), "true")
        self.assertEqual(container[0].get("clickable"), "false")
        self.assertEqual(container[0].get("bounds"), "[0,300][400,900]")
        self.assertEqual(container[0].get(DOM_SCROLL_PROGRESS_ATTRIBUTE), "500")
        self.assertEqual(container[0].get(DOM_SCROLL_AT_END_ATTRIBUTE), "false")
        self.assertEqual(container[0].get(DOM_SCROLL_OFFSET_ATTRIBUTE), "0,600")

    def test_a_page_that_fits_projects_no_container(self) -> None:
        view = _webview()
        projection = append_dom_nodes(view, _snapshot(), PACKAGE)
        self.assertFalse(projection.container_added)
        self.assertEqual(
            [
                node
                for node in view
                if node.get("resource-id") == DOM_CONTAINER_RESOURCE_ID
            ],
            [],
        )

    def test_the_container_is_appended_after_the_controls(self) -> None:
        """A page that stops scrolling must not renumber every control.

        `tree_path` is part of a selector, so inserting the container first would
        change the identity of every projected control the moment a document
        became short enough to fit.
        """

        short, long = _webview(), _webview()
        append_dom_nodes(short, _snapshot(), PACKAGE)
        append_dom_nodes(long, _snapshot(scrollHeight=1800), PACKAGE)
        self.assertEqual(
            list(short)[0].get("resource-id"), list(long)[0].get("resource-id")
        )
        self.assertEqual(
            list(long)[-1].get("resource-id"), DOM_CONTAINER_RESOURCE_ID
        )


class _Config:
    observation_timeout_seconds = 5.0
    adb_timeout_seconds = 5.0
    action_timeout_seconds = 5.0
    capture_screenshots = False
    text_input_value = "valordroid"
    per_field_input_enabled = False
    max_seconds = 600.0
    webview_enabled = False
    webview_url_patterns: tuple[str, ...] = ()
    semantic_state_enabled = False
    uiautomator2_fallback_enabled = False


class _AdbResult:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _Adb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *arguments: str, timeout=None, check: bool = True) -> _AdbResult:
        self.calls.append(arguments)
        return _AdbResult()

    def run(self, arguments, *, timeout=None, check: bool = True) -> _AdbResult:
        self.calls.append(tuple(arguments))
        return _AdbResult()


class _Session:
    package = PACKAGE

    def __init__(self) -> None:
        self.adb = _Adb()

    @staticmethod
    def foreground(*, deadline_monotonic=None) -> tuple[tuple[str, ...], str]:
        return ((f"{PACKAGE}/.WebActivity",), f"{PACKAGE}/.WebActivity")

    @staticmethod
    def repair_ui_automation(*, deadline_monotonic=None) -> str:
        return "not_required"


class _Observer(AndroidObserver):
    """The real capture path with only the device dump replaced."""

    def __init__(self, hierarchy: bytes) -> None:
        root = Path(tempfile.mkdtemp())
        super().__init__(_Session(), _Config(), root)
        self._hierarchy = hierarchy

    def _dump_once(self, *, compressed: bool, timeout=None, deadline_monotonic=None):
        return self._hierarchy, ET.fromstring(self._hierarchy)


def _merged_hierarchy(**snapshot_overrides) -> bytes:
    root = ET.Element("hierarchy", {"rotation": "0"})
    frame = ET.SubElement(
        root,
        "node",
        {
            "class": "android.widget.FrameLayout",
            "package": PACKAGE,
            "resource-id": "",
            "text": "",
            "content-desc": "",
            "bounds": "[0,0][400,900]",
            "enabled": "true",
        },
    )
    view = ET.SubElement(
        frame,
        "node",
        {
            "class": "android.webkit.WebView",
            "package": PACKAGE,
            "resource-id": PACKAGE + ":id/web",
            "text": "",
            "content-desc": "",
            "bounds": "[0,300][400,900]",
            "enabled": "true",
        },
    )
    append_dom_nodes(view, _snapshot(**snapshot_overrides), PACKAGE)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class CandidateGateTest(unittest.TestCase):
    """The projection must reach the existing candidate builder unchanged.

    A merged tree cannot be fed through `_dump_once`: the reserved-prefix guard
    rejects it, correctly, because that path is the native dump. The real
    candidate builder is called on the merged tree's nodes instead.
    """

    def _candidates(self, **snapshot_overrides):
        observer = _Observer(b"<hierarchy/>")
        root = ET.fromstring(_merged_hierarchy(**snapshot_overrides))
        nodes = observer._nodes(root)
        state_id = observer.structure_signature(nodes)
        candidates, refused = observer._candidates(state_id, nodes)
        self.assertEqual(refused, ())
        return candidates

    def test_a_projected_control_becomes_a_tap_candidate(self) -> None:
        candidates = self._candidates()
        self.assertEqual(
            {candidate.action.kind for candidate in candidates}, {"tap"}
        )
        selector = candidates[0].action.parameters["selector"]
        self.assertTrue(
            selector["resource_id"].startswith(RESERVED_DOM_RESOURCE_PREFIX)
        )

    def test_target_id_remains_exactly_the_selector_digest(self) -> None:
        """Provenance must not move the identity of a DOM action.

        `transactions._check_android_gui_action` re-derives `target_id` from the
        selector and requires the parameter key set to be exactly
        `{expected_state_id, selector}`, so anything that changed either would be
        rejected at commit and again in offline validation.
        """

        for candidate in self._candidates(scrollHeight=1800):
            action = candidate.action
            self.assertEqual(
                action.target_id, stable_hash(action.parameters["selector"])
            )
            self.assertEqual(
                set(action.parameters), {"expected_state_id", "selector"}
            )

    def test_a_long_page_produces_scroll_candidates_through_the_existing_gate(
        self,
    ) -> None:
        candidates = self._candidates(scrollHeight=1800)
        self.assertEqual(
            {candidate.action.kind for candidate in candidates},
            {"tap", "scroll_forward", "scroll_backward"},
        )
        scrolls = [
            candidate
            for candidate in candidates
            if candidate.action.kind == "scroll_forward"
        ]
        self.assertEqual(len(scrolls), 1)
        self.assertEqual(
            scrolls[0].action.parameters["selector"]["resource_id"],
            DOM_CONTAINER_RESOURCE_ID,
        )

    def test_a_page_that_fits_offers_no_scroll(self) -> None:
        self.assertNotIn(
            "scroll_forward",
            {candidate.action.kind for candidate in self._candidates()},
        )

    def test_two_pages_with_one_structure_share_a_state_signature(self) -> None:
        """The collision the pre-dispatch check exists to catch.

        DOM node ids are derived from the control's own identity, not from the
        page, so two documents with the same controls hash to the same structural
        skeleton. Native state identity therefore cannot see a navigation, which
        is why freshness has to be re-proved from the device before input.
        """

        observer = _Observer(b"<hierarchy/>")
        first = observer._nodes(ET.fromstring(_merged_hierarchy()))
        second = observer._nodes(
            ET.fromstring(_merged_hierarchy(url="https://app.example/other"))
        )
        self.assertEqual(
            observer.structure_signature(first), observer.structure_signature(second)
        )


class ReservedPrefixTest(unittest.TestCase):
    def test_reserved_nodes_are_reported_from_a_hierarchy(self) -> None:
        root = ET.fromstring(
            '<hierarchy><node resource-id="valordroid.webview:abc" bounds="[0,0][1,1]"/>'
            '<node resource-id="com.example.app:id/ok" bounds="[0,0][1,1]"/></hierarchy>'
        )
        self.assertEqual(reserved_dom_nodes(root), ("valordroid.webview:abc",))

    def test_a_native_dump_claiming_dom_provenance_is_rejected(self) -> None:
        """Forged provenance ends the observation instead of being stripped.

        Silently removing the node would leave no evidence that an application
        tried to publish debugger-measured coordinates in its own hierarchy, and
        the whole point of the prefix is that only the adapter can mint it.
        """

        forged = (
            b'<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
            b'<node class="android.widget.Button" package="com.example.app"'
            b' resource-id="valordroid.webview:deadbeefdeadbeefdead" text="Tap"'
            b' content-desc="" bounds="[0,0][100,100]" clickable="true"'
            b' enabled="true"/></hierarchy>'
        )
        with self.assertRaises(ValueError) as caught:
            _Observer(forged).capture()
        self.assertIn("reserved", str(caught.exception))

    def test_an_ordinary_native_dump_is_unaffected(self) -> None:
        plain = (
            b'<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
            b'<node class="android.widget.Button" package="com.example.app"'
            b' resource-id="com.example.app:id/go" text="Go" content-desc=""'
            b' bounds="[0,0][100,100]" clickable="true" enabled="true"/></hierarchy>'
        )
        captured = _Observer(plain).capture()
        self.assertEqual(len(captured.candidates), 1)
        self.assertIsNone(captured.webview_details)


if __name__ == "__main__":
    unittest.main()
