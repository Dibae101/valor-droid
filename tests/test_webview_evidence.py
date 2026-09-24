"""The WebView observation evidence contract, v1 and v2.

Three layers are pinned here:

- the producer's normalization, which must either publish a complete v2 record or
  say plainly that it could not;
- the offline validator's schema conjunction, which must reject a record that
  claims more than it carries; and
- the summary aggregation, which must not present a v1 record's silence as proof
  of the ownership and freshness the v1 schema never recorded.

The bump from v1 to v2 exists because v1 could not distinguish an observation of
the application's own WebView from one of any other debug socket, and recorded no
page geometry at all. A v1 record therefore stays readable and is reported as
what it is.
"""

from __future__ import annotations

import unittest

from tests import fake_device
from valordroid.android.observe import (
    WEBVIEW_OBSERVATION_EVIDENCE_RULE,
    WEBVIEW_OBSERVATION_EVIDENCE_RULE_V1,
    AndroidObserver,
)
from valordroid.summary import _webview_diagnostics, summarize_run
from valordroid.validator import (
    _WEBVIEW_V1_KEYS,
    _WEBVIEW_V2_KEYS,
    _webview_v1_details_valid,
    _webview_v2_details_valid,
    validate_run,
)

STATE_SHA = "a" * 64
HIERARCHY_SHA = "b" * 64
URL_SHA = "c" * 64
TARGET_SHA = "d" * 64
SOCKET = "webview_devtools_remote_4242"


def _observed_details(**overrides) -> dict:
    details = {
        "status": "observed",
        "dom_controls": 3,
        "dom_controls_seen": 5,
        "dom_scroll_containers": 1,
        "socket": SOCKET,
        "owner_pid": 4242,
        "forward_port": 41235,
        "debugger_android_package": "com.example.app",
        "package_match": True,
        "source": "webview_dom",
        "target_url_sha256": URL_SHA,
        "target_id_sha256": TARGET_SHA,
        "main_frame_url_sha256": URL_SHA,
        "viewport_width": 400,
        "viewport_height": 600,
        "scroll_x": 0,
        "scroll_y": 600,
        "scroll_width": 400,
        "scroll_height": 1800,
        "scroll_progress_permille": 500,
        "scroll_at_end": False,
    }
    details.update(overrides)
    return details


def _evidence(details: dict, *, sequence: int = 1) -> dict:
    return AndroidObserver._webview_evidence(
        details,
        sequence=sequence,
        state_id=STATE_SHA,
        hierarchy_sha256=HIERARCHY_SHA,
    )


def _v2_record(**overrides) -> dict:
    record = _evidence(_observed_details())
    record.update(overrides)
    return record


class NormalizationTest(unittest.TestCase):
    def test_an_observed_diagnostic_becomes_a_complete_v2_record(self) -> None:
        record = _evidence(_observed_details())
        self.assertEqual(record["rule"], WEBVIEW_OBSERVATION_EVIDENCE_RULE)
        self.assertEqual(set(record), set(_WEBVIEW_V2_KEYS))
        self.assertEqual(record["status"], "observed")
        self.assertEqual(record["socket"], SOCKET)
        self.assertEqual(record["owner_pid"], 4242)
        self.assertIs(record["package_match"], True)
        self.assertEqual(record["scroll_progress_permille"], 500)
        self.assertIs(record["scroll_at_end"], False)
        self.assertEqual(record["scroll_containers"], 1)
        self.assertTrue(_webview_v2_details_valid(record, previous_sequence=0))

    def test_the_v2_schema_extends_v1_rather_than_replacing_it(self) -> None:
        self.assertTrue(_WEBVIEW_V1_KEYS < _WEBVIEW_V2_KEYS)
        self.assertEqual(
            _WEBVIEW_V2_KEYS - _WEBVIEW_V1_KEYS,
            {
                "dom_controls_seen",
                "socket",
                "owner_pid",
                "debugger_android_package",
                "package_match",
                "forward_port",
                "target_id_sha256",
                "main_frame_url_sha256",
                "viewport_width",
                "viewport_height",
                "scroll_x",
                "scroll_y",
                "scroll_width",
                "scroll_height",
                "scroll_progress_permille",
                "scroll_at_end",
                "scroll_containers",
            },
        )

    def test_an_incomplete_observation_is_downgraded_not_published(self) -> None:
        """A record must never claim an ownership fact the attempt never got.

        Publishing `status="observed"` with a missing debugger package would make
        the retained evidence assert exactly the property the v2 bump exists to
        establish.
        """

        for missing in (
            "socket",
            "owner_pid",
            "debugger_android_package",
            "forward_port",
            "target_id_sha256",
            "viewport_width",
            "scroll_progress_permille",
        ):
            with self.subTest(missing=missing):
                details = _observed_details()
                del details[missing]
                record = _evidence(details)
                self.assertEqual(record["status"], "failed")
                self.assertEqual(
                    record["error_type"], "IncompleteWebViewObservationEvidence"
                )
                self.assertEqual(record["dom_controls"], 0)
                self.assertIsNone(record["target_url_sha256"])
                self.assertTrue(
                    _webview_v2_details_valid(record, previous_sequence=0)
                )

    def test_a_package_mismatch_cannot_be_recorded_as_an_observation(self) -> None:
        record = _evidence(_observed_details(package_match=False))
        self.assertEqual(record["status"], "failed")
        self.assertIs(record["package_match"], False)

    def test_a_failed_attempt_keeps_the_ownership_it_did_establish(self) -> None:
        record = _evidence(
            {
                "status": "failed",
                "error_type": "WebViewObservationError",
                "socket": SOCKET,
                "owner_pid": 4242,
                "forward_port": 41235,
                "debugger_android_package": "com.example.other",
                "package_match": False,
            }
        )
        self.assertEqual(record["socket"], SOCKET)
        self.assertEqual(record["debugger_android_package"], "com.example.other")
        self.assertIs(record["package_match"], False)
        # But nothing about the page, which was never read.
        self.assertIsNone(record["main_frame_url_sha256"])
        self.assertIsNone(record["scroll_progress_permille"])
        self.assertTrue(_webview_v2_details_valid(record, previous_sequence=0))

    def test_a_screen_with_no_webview_records_no_ownership_at_all(self) -> None:
        record = _evidence({"status": "no_native_webview"})
        for name in (
            "socket",
            "owner_pid",
            "debugger_android_package",
            "package_match",
            "forward_port",
            "error_type",
            "target_url_sha256",
            "scroll_at_end",
        ):
            self.assertIsNone(record[name], name)
        self.assertEqual(record["dom_controls_seen"], 0)
        self.assertTrue(_webview_v2_details_valid(record, previous_sequence=0))

    def test_a_boolean_is_not_accepted_where_a_count_belongs(self) -> None:
        record = _evidence(_observed_details(dom_controls=True))
        self.assertEqual(record["status"], "failed")

    def test_unknown_and_oversized_values_are_bounded(self) -> None:
        record = _evidence(
            {
                "status": "invented",
                "error_type": "x" * 500,
                "cleanup_errors": ["e"] * 50,
            }
        )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["error_type"]), 128)
        self.assertEqual(record["cleanup_error_count"], 10)
        self.assertTrue(_webview_v2_details_valid(record, previous_sequence=0))

    def test_a_seen_count_below_the_projected_count_is_refused(self) -> None:
        record = _v2_record(dom_controls=5, dom_controls_seen=2)
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=0))


class ValidatorSchemaTest(unittest.TestCase):
    def test_a_complete_record_is_accepted(self) -> None:
        self.assertTrue(_webview_v2_details_valid(_v2_record(), previous_sequence=0))

    def test_the_sequence_must_advance(self) -> None:
        record = _v2_record(observation_sequence=3)
        self.assertTrue(_webview_v2_details_valid(record, previous_sequence=2))
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=3))
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=9))

    def test_a_missing_or_unknown_key_is_refused(self) -> None:
        short = _v2_record()
        del short["socket"]
        self.assertFalse(_webview_v2_details_valid(short, previous_sequence=0))
        wide = _v2_record()
        wide["dom_nodes_are_fine"] = True
        self.assertFalse(_webview_v2_details_valid(wide, previous_sequence=0))

    def test_an_observation_must_prove_its_ownership(self) -> None:
        for name, value in (
            ("socket", None),
            ("socket", "some_other_socket"),
            ("owner_pid", None),
            ("owner_pid", 0),
            ("debugger_android_package", None),
            ("forward_port", None),
            ("forward_port", 70000),
            ("package_match", None),
            ("package_match", False),
        ):
            with self.subTest(name=name, value=value):
                self.assertFalse(
                    _webview_v2_details_valid(
                        _v2_record(**{name: value}), previous_sequence=0
                    )
                )

    def test_an_observation_must_bind_its_page(self) -> None:
        self.assertFalse(
            _webview_v2_details_valid(
                _v2_record(main_frame_url_sha256="e" * 64), previous_sequence=0
            )
        )
        self.assertFalse(
            _webview_v2_details_valid(
                _v2_record(target_id_sha256=None), previous_sequence=0
            )
        )

    def test_an_observation_must_carry_consistent_geometry(self) -> None:
        for name, value in (
            ("viewport_width", None),
            ("scroll_at_end", None),
            ("scroll_progress_permille", 1001),
            ("scroll_height", 100),  # smaller than the viewport it was measured in
            ("scroll_y", -1),
        ):
            with self.subTest(name=name, value=value):
                self.assertFalse(
                    _webview_v2_details_valid(
                        _v2_record(**{name: value}), previous_sequence=0
                    )
                )

    def test_a_non_observation_cannot_carry_page_geometry(self) -> None:
        record = _evidence({"status": "no_native_webview"})
        record["scroll_progress_permille"] = 1000
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=0))

    def test_a_screen_with_no_webview_cannot_name_a_socket(self) -> None:
        record = _evidence({"status": "no_native_webview"})
        record["socket"] = SOCKET
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=0))

    def test_a_v1_record_is_not_accepted_as_v2(self) -> None:
        """The point of the bump: v1 silence is not v2 proof."""

        legacy = {
            "rule": WEBVIEW_OBSERVATION_EVIDENCE_RULE_V1,
            "observation_sequence": 1,
            "state_id": STATE_SHA,
            "hierarchy_sha256": HIERARCHY_SHA,
            "status": "observed",
            "dom_controls": 3,
            "target_url_sha256": URL_SHA,
            "error_type": None,
            "cleanup_error_count": 0,
        }
        self.assertTrue(_webview_v1_details_valid(legacy, previous_sequence=0))
        self.assertFalse(_webview_v2_details_valid(legacy, previous_sequence=0))
        # And a v1 record cannot be relabelled as v2 to acquire the properties.
        relabelled = dict(legacy, rule=WEBVIEW_OBSERVATION_EVIDENCE_RULE)
        self.assertFalse(_webview_v2_details_valid(relabelled, previous_sequence=0))

    def test_a_v2_record_is_not_accepted_as_v1(self) -> None:
        self.assertFalse(_webview_v1_details_valid(_v2_record(), previous_sequence=0))

    def test_an_unknown_rule_is_refused_by_both(self) -> None:
        record = _v2_record(rule="webview_observation_lifecycle_v3")
        self.assertFalse(_webview_v2_details_valid(record, previous_sequence=0))
        self.assertFalse(_webview_v1_details_valid(record, previous_sequence=0))


class _FakeLedger:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def payloads(self) -> list[dict]:
        return [
            {
                "event": {
                    "record_id": f"l-{index}",
                    "observed_at": 1.0 + index,
                    "phase": "device",
                    "event": "webview_observation",
                    "status": "info",
                    "details": record,
                    "error": None,
                }
            }
            for index, record in enumerate(self.records)
        ]


class _FakeStore:
    def __init__(self, records: list[dict]) -> None:
        self._ledger = _FakeLedger(records)

    def ledger(self, name: str) -> _FakeLedger:
        assert name == "lifecycle"
        return self._ledger


class SummaryAggregationTest(unittest.TestCase):
    def test_no_records_produce_no_section(self) -> None:
        self.assertIsNone(_webview_diagnostics(_FakeStore([])))

    def test_proven_observations_are_summarized_as_proven(self) -> None:
        summary = _webview_diagnostics(
            _FakeStore([_evidence(_observed_details(), sequence=1)])
        )
        assert summary is not None
        self.assertEqual(summary["observations"], 1)
        self.assertEqual(summary["statuses"], {"observed": 1})
        self.assertEqual(
            summary["evidence_rules"], {WEBVIEW_OBSERVATION_EVIDENCE_RULE: 1}
        )
        self.assertEqual(summary["dom_controls"], 3)
        self.assertEqual(summary["dom_controls_seen"], 5)
        self.assertEqual(summary["ownership_verified_observations"], 1)
        self.assertIs(summary["ownership_established"], True)
        self.assertEqual(summary["distinct_owned_sockets"], 1)
        self.assertEqual(summary["distinct_main_frame_pages"], 1)
        self.assertEqual(summary["max_scroll_progress_permille"], 500)
        self.assertEqual(summary["legacy_schema_records"], 0)
        self.assertEqual(summary["coverage_gain_vs_baseline"], "not_established")

    def test_a_package_mismatch_is_counted_as_a_refusal(self) -> None:
        summary = _webview_diagnostics(
            _FakeStore(
                [
                    _evidence(
                        {
                            "status": "failed",
                            "error_type": "WebViewObservationError",
                            "socket": SOCKET,
                            "owner_pid": 4242,
                            "debugger_android_package": "com.example.other",
                            "package_match": False,
                        }
                    )
                ]
            )
        )
        assert summary is not None
        self.assertEqual(summary["package_mismatch_refusals"], 1)
        self.assertIs(summary["ownership_established"], False)

    def test_one_legacy_record_withdraws_the_ownership_claim(self) -> None:
        legacy = {
            "rule": WEBVIEW_OBSERVATION_EVIDENCE_RULE_V1,
            "observation_sequence": 2,
            "state_id": STATE_SHA,
            "hierarchy_sha256": HIERARCHY_SHA,
            "status": "observed",
            "dom_controls": 1,
            "target_url_sha256": URL_SHA,
            "error_type": None,
            "cleanup_error_count": 0,
        }
        summary = _webview_diagnostics(
            _FakeStore([_evidence(_observed_details(), sequence=1), legacy])
        )
        assert summary is not None
        self.assertEqual(summary["observations"], 2)
        self.assertEqual(summary["legacy_schema_records"], 1)
        self.assertEqual(summary["dom_controls"], 4)
        # The v1 record contributes an observation with no ownership evidence, so
        # the run as a whole has not established it.
        self.assertIs(summary["ownership_established"], False)
        self.assertEqual(summary["ownership_verified_observations"], 1)


class EnabledRunEvidenceTest(unittest.TestCase):
    """A whole WebView-enabled run, checked by the independent validator.

    The fake app's reachable screens hold no WebView, so every observation is a
    truthful `no_native_webview`. That is exactly the case a producer could get
    wrong by inventing ownership fields, and the case the validator must accept.
    """

    class _Deferred:
        """Collects the harness's cleanups so one run can serve every test."""

        def __init__(self) -> None:
            self.cleanups: list[tuple] = []

        def addCleanup(self, function, *args, **kwargs) -> None:  # noqa: N802
            self.cleanups.append((function, args, kwargs))

        def run_cleanups(self) -> None:
            while self.cleanups:
                function, args, kwargs = self.cleanups.pop()
                try:
                    function(*args, **kwargs)
                except Exception:  # noqa: BLE001 - teardown is best effort
                    pass

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_android_loop import _Harness

        cls._deferred = cls._Deferred()
        cls._harness = _Harness(
            cls._deferred,
            runtime={
                "webview_enabled": True,
                "webview_url_patterns": ["https://app.example/*"],
                "max_actions": 6,
                # Its own device identity, so this run takes its own device lock
                # rather than queueing behind another fake-device run.
                "serial": "fake-webview-evidence-device",
            },
        )
        cls.state_path = cls._harness.tools["state"]
        cls.root, cls.result = cls._harness.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._deferred.run_cleanups()

    def test_the_run_validates_with_webview_evidence_present(self) -> None:
        validation = validate_run(self.root)
        self.assertTrue(
            validation.ok, [issue.__dict__ for issue in validation.issues]
        )
        self.assertEqual(
            [issue.code for issue in validation.warnings if "webview" in issue.code],
            [],
        )

    def test_every_observation_recorded_one_v2_record(self) -> None:
        summary = summarize_run(self.root)
        section = summary["webview_observation"]
        self.assertEqual(
            section["evidence_rules"],
            {WEBVIEW_OBSERVATION_EVIDENCE_RULE: section["observations"]},
        )
        self.assertEqual(section["statuses"], {"no_native_webview": section["observations"]})
        self.assertEqual(section["dom_controls"], 0)
        self.assertIs(section["ownership_established"], False)
        self.assertEqual(section["distinct_owned_sockets"], 0)

    def test_the_device_never_looked_for_a_debug_socket(self) -> None:
        """No WebView on screen means no attach, so no forwarding is attempted."""

        calls = fake_device.read_adb_calls({"state": self.state_path})
        self.assertNotEqual(calls, ())
        self.assertEqual([call for call in calls if call[0] == "forward"], [])
        self.assertEqual(
            [call for call in calls if "/proc/net/unix" in call], []
        )


if __name__ == "__main__":
    unittest.main()
