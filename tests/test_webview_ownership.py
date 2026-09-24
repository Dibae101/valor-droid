"""Ownership and pre-dispatch freshness for one attached WebView target.

Driven through the real `AndroidObserver`, `AndroidActionExecutor` and
`AndroidOutcomeVerifier` against the scriptable fake device: a real `adb`
subprocess, a real `/proc/net/unix`, a real loopback `/json/list` and
`/json/version`, and a stub `websocket` speaking the CDP subset the adapter uses.
Nothing about the attach, forward, discovery or refusal logic is mocked out.

§3.3 of the audit asks for proof of ownership and freshness before any DOM action
is credited. The load-bearing case is the security regression at the end of this
file: two different URLs whose controls share a structural signature produce the
same `state_id`, so the ordinary staleness check passes and a tap would land on a
different document at coordinates measured for the first one. That tap must not
be dispatched, and the assertion is made against the fake `adb` call log rather
than against a return value, because what matters is that no input reached the
device.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import fake_device
from valordroid.android.actions import AndroidActionExecutor
from valordroid.android.adb import AdbClient
from valordroid.android.observe import AndroidObserver
from valordroid.android.outcomes import AndroidOutcomeVerifier
from valordroid.android.session import AndroidSession
from valordroid.android.webview import (
    DOM_CONTAINER_RESOURCE_ID,
    DOM_PROVENANCE,
    RESERVED_DOM_RESOURCE_PREFIX,
)
from valordroid.models import ExecutionStatus, OutcomeKind
from valordroid.runtime_config import RuntimeConfig

PACKAGE = fake_device.PACKAGE
URL = "https://app.example/library"
OTHER_URL = "https://app.example/settings"
PATTERNS = ("https://app.example/*",)


def _runtime(**overrides) -> RuntimeConfig:
    values = {
        "schema_version": RuntimeConfig.SCHEMA_VERSION,
        # Distinct from other fake-device suites: the ADB serial is what the
        # device lock is keyed on.
        "serial": "fake-webview-device",
        "launcher": fake_device.LAUNCHER,
        "max_actions": 8,
        "max_seconds": 120.0,
        "action_timeout_seconds": 10.0,
        "adb_timeout_seconds": 10.0,
        "capture_screenshots": False,
        "install_timeout_seconds": 10.0,
        "launch_timeout_seconds": 10.0,
        "observation_timeout_seconds": 10.0,
        "route_foreground_timeout_seconds": 3.0,
        "per_field_input_enabled": True,
        "pid_poll_seconds": 0.05,
        "post_action_delay_seconds": 0.01,
        "reset_seed_enabled": True,
        "suppress_soft_keyboard": False,
        "route_discovery_sha256": None,
        "component_extras": {},
        "text_input_value": "valordroid",
        "uninstall_after_run": False,
        "deep_links": [],
        "exported_components": [],
        "forced_components": [],
        "webview_enabled": True,
        "webview_url_patterns": list(PATTERNS),
    }
    values.update(overrides)
    return RuntimeConfig.from_mapping(values)


class _Session(AndroidSession):
    """A real session's device behavior without a prepared bundle.

    Subclassed rather than faked so foreground resolution, resumed-activity
    parsing and ownership comparison are the production implementations: the
    package-prefix defect this change removes lived in exactly that comparison.
    """

    def __init__(self, adb: AdbClient, config: RuntimeConfig) -> None:
        self.adb = adb
        self.config = config
        self.package = PACKAGE


class _Harness:
    def __init__(self, test: unittest.TestCase, **page) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = fake_device.install_fake_device(self.root / "tools")
        os.environ["FAKE_DEVICE_STATE"] = self.paths["state"]
        test.addCleanup(os.environ.pop, "FAKE_DEVICE_STATE", None)
        fake_device.write_state(
            self.paths,
            screen="web",
            installed=True,
            unix_sockets=[fake_device.WEBVIEW_SOCKET],
        )
        self.page: dict = {
            "url": URL,
            "elements": [fake_device.dom_element("id:play", text="Play")],
        }
        self.page.update(page)
        self.snapshot = fake_device.dom_snapshot(**self.page)
        self.devtools = fake_device.FakeDevTools(
            self.root / "tools", fake_device.devtools_document(self.snapshot)
        )
        test.addCleanup(self.devtools.stop)
        self.websocket = fake_device.install_fake_websocket(self.devtools)
        test.addCleanup(fake_device.remove_fake_websocket)
        self.config = _runtime()
        self.adb = AdbClient(
            self.config.serial,
            executable=self.paths["adb"],
            default_timeout=self.config.adb_timeout_seconds,
        )
        self.session = _Session(self.adb, self.config)
        self.observer = AndroidObserver(
            self.session, self.config, self.root / "artifacts"
        )
        test.addCleanup(self.observer.close)
        self.executor = AndroidActionExecutor(
            self.session, self.observer, self.config
        )
        self.verifier = AndroidOutcomeVerifier(self.observer, self.config)

    # -- device shaping -------------------------------------------------
    def set_page(self, **overrides) -> dict:
        """Replace what the debugger serves, as a navigation or scroll would."""

        document_overrides = {}
        for name in ("target_id", "target_type", "listed_url", "android_package"):
            if name in overrides:
                document_overrides[name] = overrides.pop(name)
        self.page.update(overrides)
        self.snapshot = fake_device.dom_snapshot(**self.page)
        self.devtools.set_document(
            fake_device.devtools_document(self.snapshot, **document_overrides)
        )
        return self.snapshot

    def set_document(self, document: dict) -> None:
        self.devtools.set_document(document)

    def state(self, **values: object) -> None:
        fake_device.write_state(self.paths, **values)

    def calls(self) -> tuple[tuple[str, ...], ...]:
        return fake_device.read_adb_calls(self.paths)

    def input_calls(self) -> tuple[tuple[str, ...], ...]:
        return tuple(
            call for call in self.calls() if call[:2] == ("shell", "input")
        )

    def dom_nodes(self, captured) -> tuple:
        return tuple(
            node
            for node in captured.nodes
            if node.attributes.get("source") == DOM_PROVENANCE
        )

    def tap_candidate(self, captured):
        matches = [
            candidate
            for candidate in captured.candidates
            if candidate.action.kind == "tap"
            and candidate.action.parameters["selector"]["resource_id"].startswith(
                RESERVED_DOM_RESOURCE_PREFIX
            )
        ]
        assert len(matches) == 1, matches
        return matches[0]


class AttachmentTest(unittest.TestCase):
    """A correctly owned, correctly reported target is observed and usable."""

    def setUp(self) -> None:
        self.harness = _Harness(self)

    def test_an_owned_target_is_observed_and_projected(self) -> None:
        captured = self.harness.observer.capture()
        details = captured.webview_details
        assert details is not None
        self.assertEqual(details["status"], "observed")
        self.assertEqual(details["socket"], fake_device.WEBVIEW_SOCKET)
        self.assertEqual(details["owner_pid"], fake_device.APP_PID)
        self.assertEqual(details["debugger_android_package"], PACKAGE)
        self.assertIs(details["package_match"], True)
        self.assertEqual(details["dom_controls"], 1)
        self.assertEqual(len(self.harness.dom_nodes(captured)), 1)

    def test_the_forward_is_allocated_and_released_around_the_snapshot(self) -> None:
        self.harness.observer.capture()
        forwards = [call for call in self.harness.calls() if call[0] == "forward"]
        self.assertEqual(
            forwards,
            [
                ("forward", "tcp:0", "localabstract:" + fake_device.WEBVIEW_SOCKET),
                ("forward", "--remove", f"tcp:{self.harness.devtools.port}"),
            ],
        )

    def test_a_dom_tap_is_dispatched_at_the_projected_centre(self) -> None:
        captured = self.harness.observer.capture()
        candidate = self.harness.tap_candidate(captured)
        record = self.harness.executor.execute(candidate.action)
        self.assertIs(record.status, ExecutionStatus.EXECUTED, record.error)
        # 400x600 CSS over the WebView's [0,300][400,900]: the control's
        # (10,10)-(200,60) rectangle projects to a centre of (105, 335).
        self.assertIn(
            ("shell", "input", "tap", "105", "335"), self.harness.input_calls()
        )

    def test_the_outcome_retains_the_pre_input_freshness_proof(self) -> None:
        captured = self.harness.observer.capture()
        candidate = self.harness.tap_candidate(captured)
        record = self.harness.executor.execute(candidate.action)
        outcome = self.harness.verifier.verify(captured.observation, record)
        self.assertEqual(outcome.details["webview_source"], DOM_PROVENANCE)
        freshness = outcome.details["webview_pre_input_freshness"]
        self.assertIs(freshness["verified"], True)
        self.assertEqual(freshness["socket"], fake_device.WEBVIEW_SOCKET)
        self.assertEqual(freshness["owner_pid"], fake_device.APP_PID)
        self.assertEqual(freshness["debugger_android_package"], PACKAGE)
        self.assertIsNone(freshness["reason"])


class OwnershipRefusalTest(unittest.TestCase):
    """Each ownership fact is independently necessary."""

    def setUp(self) -> None:
        self.harness = _Harness(self)

    def _failed_details(self) -> dict:
        captured = self.harness.observer.capture()
        details = captured.webview_details
        assert details is not None
        # The native frame survives; only the enrichment is refused.
        self.assertEqual(details["status"], "failed")
        self.assertEqual(self.harness.dom_nodes(captured), ())
        return dict(details)

    def test_a_lookalike_foreground_package_is_refused(self) -> None:
        """`com.example.app2` starts with `com.example.app` and is another app.

        The check this replaces was `activity.startswith(package + "/")`, which is
        a string relationship rather than a parsed component owner. A look-alike
        package is the whole reason ownership is resolved with `component_owner`.
        """

        self.harness.state(foreground_package=PACKAGE + "2")
        details = self._failed_details()
        self.assertEqual(details["error_type"], "WebViewObservationError")
        self.assertIn("foreground", details["error"])
        # Nothing was attached, so no ownership fact is claimed.
        self.assertNotIn("socket", details)

    def test_a_socket_owned_by_another_process_is_refused(self) -> None:
        self.harness.state(unix_sockets=["webview_devtools_remote_9999"])
        details = self._failed_details()
        self.assertIn("AUT-owned", details["error"])

    def test_two_owned_sockets_are_refused_rather_than_guessed(self) -> None:
        """A multi-process app can own two debug sockets; neither is implied."""

        self.harness.state(
            extra_processes=[[4243, PACKAGE + ":remote"]],
            unix_sockets=[
                fake_device.WEBVIEW_SOCKET,
                "webview_devtools_remote_4243",
            ],
        )
        details = self._failed_details()
        self.assertIn("one AUT-owned", details["error"])

    def test_a_second_socket_from_a_foreign_process_is_ignored(self) -> None:
        """Only the AUT's own processes contribute, so this stays unambiguous."""

        self.harness.state(
            unix_sockets=[
                fake_device.WEBVIEW_SOCKET,
                "webview_devtools_remote_9999",
            ]
        )
        details = self.harness.observer.capture().webview_details
        assert details is not None
        self.assertEqual(details["status"], "observed")
        self.assertEqual(details["owner_pid"], fake_device.APP_PID)

    def test_a_debugger_reporting_another_package_is_refused(self) -> None:
        """A socket named after an AUT pid is not proof of the endpoint's owner.

        The abstract socket namespace is not owned by the process whose id names
        the socket, so the debugger's own `Android-Package` has to agree before
        any page is read.
        """

        self.harness.set_page(android_package="com.example.other")
        details = self._failed_details()
        self.assertIn("another package", details["error"])
        # The refusal keeps the ownership facts it did establish.
        self.assertEqual(details["socket"], fake_device.WEBVIEW_SOCKET)
        self.assertEqual(details["owner_pid"], fake_device.APP_PID)
        self.assertEqual(details["debugger_android_package"], "com.example.other")
        self.assertIs(details["package_match"], False)

    def test_a_lookalike_debugger_package_is_refused(self) -> None:
        self.harness.set_page(android_package=PACKAGE + ".debug")
        details = self._failed_details()
        self.assertIs(details["package_match"], False)

    def test_a_navigation_during_the_observation_is_refused(self) -> None:
        """The frame tree and the evaluated document must describe one page.

        A disagreement means the page moved between the two debugger calls, so
        neither reading can be attributed to the document the controls came from.
        """

        self.harness.set_document(
            fake_device.devtools_document(
                fake_device.dom_snapshot(
                    URL, [fake_device.dom_element("id:play", text="Play")]
                ),
                frame_url=OTHER_URL,
            )
        )
        details = self._failed_details()
        self.assertIn("disagree", details["error"])

    def test_a_page_outside_the_url_allowlist_is_refused(self) -> None:
        self.harness.set_page(url="https://tracker.example/beacon")
        details = self._failed_details()
        self.assertIn("allowlisted", details["error"])

    def test_a_target_that_is_not_a_page_is_refused(self) -> None:
        self.harness.set_page(target_type="service_worker")
        details = self._failed_details()
        self.assertIn("allowlisted", details["error"])

    def test_a_screen_with_no_webview_claims_nothing(self) -> None:
        self.harness.state(screen="main")
        captured = self.harness.observer.capture()
        details = captured.webview_details
        assert details is not None
        self.assertEqual(details["status"], "no_native_webview")
        self.assertNotIn("socket", details)
        self.assertEqual(self.harness.dom_nodes(captured), ())


class ForwardCleanupTest(unittest.TestCase):
    """A refused `adb forward --remove` must not disable DOM observation."""

    def setUp(self) -> None:
        self.harness = _Harness(self)

    def test_a_failed_forward_removal_is_recorded_and_then_survived(self) -> None:
        """The self-poisoning close, and the shape of its fix.

        The previous implementation retained the port whose removal failed and
        refused to observe until it succeeded. The next observation retried the
        identical failing command, failed identically, and refused again -- so one
        transient adb failure disabled the adapter for the rest of the run. The
        port is now released from the observer's own state, the leak is recorded
        as evidence, and the next attach allocates a fresh port.
        """

        fake_device.refuse_forward_removal(self.harness.paths)
        first = self.harness.observer.capture().webview_details
        assert first is not None
        self.assertEqual(first["status"], "observed")
        self.assertEqual(first["cleanup_errors"], ["adb_forward_remove_failed"])
        second = self.harness.observer.capture().webview_details
        assert second is not None
        self.assertEqual(second["status"], "observed")
        self.assertEqual(second["dom_controls"], 1)
        self.assertEqual(second["leaked_forward_ports"], 1)

    def test_a_recovered_device_stops_reporting_cleanup_errors(self) -> None:
        fake_device.refuse_forward_removal(self.harness.paths)
        self.harness.observer.capture()
        fake_device.refuse_forward_removal(self.harness.paths, refuse=False)
        details = self.harness.observer.capture().webview_details
        assert details is not None
        self.assertEqual(details["status"], "observed")
        self.assertNotIn("cleanup_errors", details)


class FreshnessTest(unittest.TestCase):
    """Ownership and page identity are re-proved immediately before dispatch."""

    def setUp(self) -> None:
        self.harness = _Harness(self)
        self.captured = self.harness.observer.capture()
        self.candidate = self.harness.tap_candidate(self.captured)
        # The check under test guards dispatch, so the pre-dispatch device state
        # is what each case changes. The reuse window is widened so `resolve`
        # deterministically serves the cached capture: that is the path where a
        # page swap is most likely, because no new dump is taken.
        self.harness.observer.REUSE_LATEST_WITHIN_SECONDS = 3600.0
        fake_device.clear_adb_calls(self.harness.paths)

    def _refused(self) -> str:
        record = self.harness.executor.execute(self.candidate.action)
        self.assertIs(record.status, ExecutionStatus.NOT_EXECUTED)
        assert record.error is not None
        # The whole point: no input reached the device.
        self.assertEqual(self.harness.input_calls(), ())
        return record.error

    def test_a_page_swap_behind_one_structure_is_not_tapped(self) -> None:
        """The security regression, stated as a device fact.

        The replacement page carries the same control, so its projected node ids,
        its structural signature and therefore its `state_id` are identical. The
        ordinary staleness comparison is satisfied and the executor would tap
        coordinates measured on the previous document.
        """

        before = self.captured.observation
        replacement = self.harness.set_page(url=OTHER_URL)
        self.assertEqual(
            [item["key"] for item in replacement["elements"]],
            [item["key"] for item in self.harness.page["elements"]],
        )
        self.assertIn("different URL", self._refused())
        # And the reason the refusal has to exist: a fresh observation of the
        # replacement page is the same state, with the same candidate, at the same
        # coordinates. Nothing in native identity distinguishes the two documents.
        after = self.harness.observer.capture()
        self.assertEqual(after.observation.state_id, before.state_id)
        self.assertEqual(
            after.observation.structure_sha256, before.structure_sha256
        )
        self.assertEqual(
            self.harness.tap_candidate(after).stable_id, self.candidate.stable_id
        )

    def test_the_cached_capture_path_is_the_one_being_verified(self) -> None:
        def fail(*_: object, **__: object):
            raise AssertionError("dispatch must not re-dump; the cache is in use")

        self.harness.observer.capture = fail  # type: ignore[method-assign]
        self.harness.set_page(url=OTHER_URL)
        self.assertIn("stale WebView DOM target", self._refused())

    def test_a_scroll_after_observation_is_refused(self) -> None:
        """Projected coordinates are only valid at the scroll offset measured."""

        self.harness.set_page(scroll_height=1800, scroll_y=600)
        self.assertIn("scrolled", self._refused())

    def test_a_replaced_debug_target_is_refused(self) -> None:
        self.harness.set_page(target_id="PAGE-REPLACED-0000")
        self.assertIn("replaced", self._refused())

    def test_a_replaced_app_process_is_refused(self) -> None:
        self.harness.state(unix_sockets=["webview_devtools_remote_9999"])
        self.assertIn("socket", self._refused())

    def test_a_debugger_package_change_before_dispatch_is_refused(self) -> None:
        self.harness.set_page(android_package="com.example.other")
        self.assertIn("stale WebView DOM target", self._refused())

    def test_a_target_list_that_disagrees_with_its_page_is_refused(self) -> None:
        """Navigation between discovery and evaluation is still a page swap."""

        self.harness.set_document(
            fake_device.devtools_document(
                fake_device.dom_snapshot(
                    OTHER_URL, [fake_device.dom_element("id:play", text="Play")]
                ),
                listed_url=URL,
            )
        )
        self.assertIn("main frame", self._refused())

    def test_a_dead_debugger_is_refused_rather_than_assumed_fresh(self) -> None:
        self.harness.devtools.stop()
        self.assertIn("stale WebView DOM target", self._refused())

    def test_a_refusal_retains_the_proof_that_produced_it(self) -> None:
        """Withheld input is only useful evidence if it says what it saw."""

        self.harness.set_page(url=OTHER_URL)
        record = self.harness.executor.execute(self.candidate.action)
        outcome = self.harness.verifier.verify(self.captured.observation, record)
        self.assertIs(outcome.kind, OutcomeKind.NOT_EXECUTED)
        freshness = outcome.details["webview_pre_input_freshness"]
        self.assertIs(freshness["verified"], False)
        self.assertIn("different URL", freshness["reason"])
        self.assertEqual(freshness["socket"], fake_device.WEBVIEW_SOCKET)
        self.assertNotEqual(
            freshness["live_main_frame_url_sha256"],
            freshness["main_frame_url_sha256"],
        )

    def test_an_unchanged_page_is_dispatched(self) -> None:
        record = self.harness.executor.execute(self.candidate.action)
        self.assertIs(record.status, ExecutionStatus.EXECUTED, record.error)
        self.assertEqual(len(self.harness.input_calls()), 1)

    def test_a_dom_node_without_a_page_digest_is_refused(self) -> None:
        """Unverifiable provenance is refused, not trusted by default."""

        captured = self.captured
        node = next(
            item
            for item in captured.nodes
            if item.attributes.get("source") == DOM_PROVENANCE
        )
        stripped = dict(node.attributes)
        stripped.pop("valordroid-webview-target-url-sha256")
        object.__setattr__(node, "attributes", stripped)
        record = self.harness.executor.execute(self.candidate.action)
        self.assertIs(record.status, ExecutionStatus.NOT_EXECUTED)
        assert record.error is not None
        self.assertIn("no page digest", record.error)
        self.assertEqual(self.harness.input_calls(), ())

    def test_a_native_target_needs_no_debugger_round_trip(self) -> None:
        """Only DOM-sourced targets pay for the check."""

        self.harness.state(screen="main")
        captured = self.harness.observer.capture()
        self.assertEqual(self.harness.dom_nodes(captured), ())
        candidate = next(
            item
            for item in captured.candidates
            if item.action.kind == "tap"
            and item.action.parameters["selector"]["resource_id"].endswith(":id/go")
        )
        fake_device.clear_adb_calls(self.harness.paths)
        record = self.harness.executor.execute(candidate.action)
        self.assertIs(record.status, ExecutionStatus.EXECUTED, record.error)
        self.assertEqual(
            [call for call in self.harness.calls() if call[0] == "forward"], []
        )


class ScrollProgressTest(unittest.TestCase):
    """A page already at its bottom must be distinguishable from a dead scroll."""

    def setUp(self) -> None:
        self.harness = _Harness(self, scroll_height=1800, scroll_y=0)

    def _scroll_candidate(self, captured):
        matches = [
            candidate
            for candidate in captured.candidates
            if candidate.action.kind == "scroll_forward"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].action.parameters["selector"]["resource_id"],
            DOM_CONTAINER_RESOURCE_ID,
        )
        return matches[0]

    def test_a_long_page_reports_its_progress_in_the_observation_evidence(
        self,
    ) -> None:
        details = self.harness.observer.capture().webview_details
        assert details is not None
        self.assertEqual(details["scroll_height"], 1800)
        self.assertEqual(details["viewport_height"], 600)
        self.assertEqual(details["scroll_progress_permille"], 0)
        self.assertIs(details["scroll_at_end"], False)
        self.assertEqual(details["dom_scroll_containers"], 1)

    def test_a_scroll_that_advanced_the_page_is_recorded_as_advanced(self) -> None:
        captured = self.harness.observer.capture()
        candidate = self._scroll_candidate(captured)
        record = self.harness.executor.execute(candidate.action)
        self.assertIs(record.status, ExecutionStatus.EXECUTED, record.error)
        # The fake renderer does not move on a swipe, so the new position is
        # supplied directly; what is under test is the recorded evidence.
        self.harness.set_page(scroll_y=1200)
        outcome = self.harness.verifier.verify(captured.observation, record)
        self.assertEqual(outcome.details["webview_scroll_outcome"], "advanced")
        self.assertEqual(
            outcome.details["webview_scroll_before"]["scroll_progress_permille"], 0
        )
        self.assertEqual(
            outcome.details["webview_scroll_after"]["scroll_progress_permille"], 1000
        )
        self.assertIs(
            outcome.details["webview_scroll_after"]["scroll_at_end"], True
        )

    def test_a_scroll_at_the_bottom_is_not_reported_as_a_dead_gesture(self) -> None:
        """`NO_EFFECT` is the same label for both; the page's progress is not.

        Without this the two cases are identical evidence, and a fully-read
        document looks exactly like a broken gesture -- which is what retires a
        usable control.
        """

        self.harness.set_page(scroll_y=1200)
        captured = self.harness.observer.capture()
        candidate = self._scroll_candidate(captured)
        record = self.harness.executor.execute(candidate.action)
        self.assertIs(record.status, ExecutionStatus.EXECUTED, record.error)
        outcome = self.harness.verifier.verify(captured.observation, record)
        self.assertIs(outcome.kind, OutcomeKind.NO_EFFECT)
        self.assertEqual(outcome.details["webview_scroll_outcome"], "at_end_unchanged")
        self.assertIs(
            outcome.details["webview_scroll_before"]["scroll_at_end"], True
        )

    def test_a_scroll_that_changed_nothing_at_the_top_is_reported_unchanged(
        self,
    ) -> None:
        captured = self.harness.observer.capture()
        candidate = self._scroll_candidate(captured)
        record = self.harness.executor.execute(candidate.action)
        outcome = self.harness.verifier.verify(captured.observation, record)
        self.assertEqual(outcome.details["webview_scroll_outcome"], "unchanged")
        self.assertIs(
            outcome.details["webview_scroll_after"]["scroll_at_end"], False
        )


class ArtifactTest(unittest.TestCase):
    def test_the_retained_diagnostic_names_its_ownership_evidence(self) -> None:
        harness = _Harness(self)
        harness.observer.capture()
        artifacts = sorted((harness.root / "artifacts").glob("*/webview.json"))
        self.assertEqual(len(artifacts), 1)
        document = json.loads(artifacts[0].read_text())
        self.assertEqual(document["status"], "observed")
        self.assertEqual(document["socket"], fake_device.WEBVIEW_SOCKET)
        self.assertIs(document["package_match"], True)
        self.assertEqual(document["main_frame_url_sha256"], document["target_url_sha256"])


if __name__ == "__main__":
    unittest.main()
