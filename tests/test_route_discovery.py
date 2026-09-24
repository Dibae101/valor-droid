"""Manifest route discovery: the gap-8 fix that fills the recovery ladder.

Gap 8 measured 627 of 987 declared activities as never reached, and the earlier
implementation could dispatch deep links but shipped empty target lists. These
tests pin the parser, the URI synthesis rules, the bounds, and the checksum that
binds an inventory to one exact APK.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from valordroid.android.manifest import (
    DISCOVERY_RULE,
    ManifestParseError,
    RouteDiscovery,
    discover_apk_routes,
    discover_routes,
    parse_xmltree,
    route_discovery_from_mapping,
)

PACKAGE = "com.example.app"
APK_SHA = "b" * 64

_DATASET = Path(__file__).resolve().parents[1] / "dataset" / "apks"


def _manifest(body: str, package: str = PACKAGE) -> str:
    return (
        "N: android=http://schemas.android.com/apk/res/android\n"
        "  E: manifest (line=2)\n"
        f"    A: package=\"{package}\" (Raw: \"{package}\")\n"
        "    E: application (line=10)\n" + body
    )


def _activity(
    name: str,
    *,
    exported: bool | None = None,
    enabled: bool | None = None,
    permission: str | None = None,
    filters: str = "",
    tag: str = "activity",
) -> str:
    lines = [f"      E: {tag} (line=11)"]
    lines.append(f'        A: android:name(0x01010003)="{name}" (Raw: "{name}")')
    if exported is not None:
        flag = "0xffffffff" if exported else "0x0"
        lines.append(f"        A: android:exported(0x01010010)=(type 0x12){flag}")
    if enabled is not None:
        flag = "0xffffffff" if enabled else "0x0"
        lines.append(f"        A: android:enabled(0x0101000e)=(type 0x12){flag}")
    if permission is not None:
        lines.append(
            f'        A: android:permission(0x01010006)="{permission}" (Raw: "{permission}")'
        )
    if filters:
        lines.append(filters)
    return "\n".join(lines) + "\n"


def _view_filter(*data_lines: str) -> str:
    lines = [
        "        E: intent-filter (line=20)",
        "          E: action (line=21)",
        '            A: android:name(0x01010003)="android.intent.action.VIEW" (Raw: "x")',
        "          E: category (line=22)",
        '            A: android:name(0x01010003)="android.intent.category.BROWSABLE" (Raw: "x")',
    ]
    lines.extend(data_lines)
    return "\n".join(lines)


def _data(**attributes: str) -> str:
    lines = ["          E: data (line=30)"]
    identifiers = {
        "scheme": "0x01010027",
        "host": "0x01010028",
        "port": "0x01010029",
        "path": "0x0101002a",
        "pathPrefix": "0x0101002b",
        "pathPattern": "0x0101002c",
        "pathSuffix": "0x0101149b",
        "mimeType": "0x01010026",
    }
    for name, value in attributes.items():
        lines.append(
            f'            A: android:{name}({identifiers[name]})="{value}" (Raw: "{value}")'
        )
    return "\n".join(lines)


def _discover(body: str, **bounds: int) -> RouteDiscovery:
    return discover_routes(
        _manifest(body), package_name=PACKAGE, apk_sha256=APK_SHA, **bounds
    )


class ParserTest(unittest.TestCase):
    def test_string_boolean_and_reference_attributes_are_decoded(self) -> None:
        manifest = parse_xmltree(
            _manifest(_activity(".Main", exported=True, enabled=False))
        )
        application = manifest.find("application")[0]
        activity = application.find("activity")[0]
        self.assertEqual(activity.text("android:name"), ".Main")
        self.assertIs(activity.flag("android:exported"), True)
        self.assertIs(activity.flag("android:enabled"), False)

    def test_resource_references_are_dropped_rather_than_guessed(self) -> None:
        manifest = parse_xmltree(
            _manifest(
                "      E: activity (line=11)\n"
                '        A: android:name(0x01010003)=".Main" (Raw: ".Main")\n'
                "        A: android:theme(0x01010000)=@0x7f15000b\n"
            )
        )
        activity = manifest.find("application")[0].find("activity")[0]
        self.assertIsNone(activity.text("android:theme"))

    def test_unknown_line_is_refused(self) -> None:
        with self.assertRaises(ManifestParseError):
            parse_xmltree(_manifest("      X: something odd\n"))

    def test_missing_manifest_element_is_refused(self) -> None:
        with self.assertRaises(ManifestParseError):
            parse_xmltree("N: android=http://example\n")

    def test_package_mismatch_is_refused(self) -> None:
        text = _manifest(_activity(".Main"), package="com.other.app")
        with self.assertRaises(ValueError) as caught:
            discover_routes(text, package_name=PACKAGE, apk_sha256=APK_SHA)
        self.assertIn("differs from prepared package", str(caught.exception))


class ComponentClassificationTest(unittest.TestCase):
    def test_exported_non_launcher_becomes_an_intent_target(self) -> None:
        discovery = _discover(_activity(".Share", exported=True))
        self.assertEqual(
            discovery.exported_components, (f"{PACKAGE}/{PACKAGE}.Share",)
        )
        self.assertEqual(discovery.forced_components, ())

    def test_non_exported_becomes_a_forced_target(self) -> None:
        discovery = _discover(_activity(".Detail", exported=False))
        self.assertEqual(discovery.forced_components, (f"{PACKAGE}/{PACKAGE}.Detail",))
        self.assertEqual(discovery.exported_components, ())

    def test_launcher_is_not_offered_as_an_intent_target(self) -> None:
        launcher_filter = "\n".join(
            [
                "        E: intent-filter (line=20)",
                "          E: action (line=21)",
                '            A: android:name(0x01010003)="android.intent.action.MAIN" (Raw: "x")',
                "          E: category (line=22)",
                '            A: android:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "x")',
            ]
        )
        discovery = _discover(
            _activity(".Main", exported=True, filters=launcher_filter)
        )
        self.assertEqual(discovery.launch_candidates, (f"{PACKAGE}/{PACKAGE}.Main",))
        self.assertEqual(discovery.exported_components, ())

    def test_action_main_without_the_launcher_category_is_an_intent_target(self) -> None:
        # MAIN alone does not put an activity in the launcher. It stays a public
        # intent target, which is what Android's own resolution would do.
        main_only = "\n".join(
            [
                "        E: intent-filter (line=20)",
                "          E: action (line=21)",
                '            A: android:name(0x01010003)="android.intent.action.MAIN" (Raw: "x")',
            ]
        )
        discovery = _discover(_activity(".Main", exported=True, filters=main_only))
        self.assertEqual(discovery.launch_candidates, ())
        self.assertEqual(
            discovery.exported_components, (f"{PACKAGE}/{PACKAGE}.Main",)
        )

    def test_permission_guarded_component_is_reported_not_targeted(self) -> None:
        discovery = _discover(
            _activity(".Admin", exported=True, permission="com.example.PRIVATE")
        )
        self.assertEqual(
            discovery.permission_guarded_components, (f"{PACKAGE}/{PACKAGE}.Admin",)
        )
        self.assertEqual(discovery.exported_components, ())
        self.assertEqual(discovery.forced_components, ())

    def test_disabled_component_is_excluded_from_every_target_list(self) -> None:
        discovery = _discover(_activity(".Legacy", exported=True, enabled=False))
        self.assertEqual(discovery.disabled_components, (f"{PACKAGE}/{PACKAGE}.Legacy",))
        self.assertEqual(discovery.exported_components, ())
        self.assertEqual(discovery.forced_components, ())

    def test_absent_exported_flag_with_a_filter_counts_as_exported(self) -> None:
        # Pre-31 manifests may omit android:exported; an intent filter still
        # makes the component externally startable.
        discovery = _discover(_activity(".Legacy", filters=_view_filter(_data(scheme="app"))))
        self.assertEqual(
            discovery.exported_components, (f"{PACKAGE}/{PACKAGE}.Legacy",)
        )

    def test_activity_alias_is_treated_as_a_startable_component(self) -> None:
        discovery = _discover(
            _activity(".Alias", exported=True, tag="activity-alias")
        )
        self.assertEqual(
            discovery.exported_components, (f"{PACKAGE}/{PACKAGE}.Alias",)
        )


class DeepLinkSynthesisTest(unittest.TestCase):
    def test_scheme_and_host_produce_a_concrete_uri(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(_data(scheme="https", host="example.com")),
            )
        )
        self.assertEqual(discovery.deep_links, ("https://example.com",))
        self.assertEqual(
            discovery.deep_link_owners["https://example.com"],
            f"{PACKAGE}/{PACKAGE}.Deep",
        )

    def test_wildcard_host_is_replaced_with_a_reserved_name(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(_data(scheme="https", host="*")),
            )
        )
        self.assertEqual(discovery.deep_links, ("https://valordroid.test",))
        self.assertIn("* -> valordroid.test", discovery.host_substitutions)

    def test_wildcard_subdomain_keeps_the_declared_domain(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(_data(scheme="https", host="*.example.com")),
            )
        )
        self.assertEqual(discovery.deep_links, ("https://example.com",))

    def test_missing_host_is_substituted_and_recorded(self) -> None:
        discovery = _discover(
            _activity(".Deep", exported=True, filters=_view_filter(_data(scheme="app")))
        )
        self.assertEqual(discovery.deep_links, ("app://valordroid.test",))
        self.assertIn("no host declared -> valordroid.test", discovery.host_substitutions)

    def test_path_prefix_is_used_verbatim_because_a_prefix_already_matches(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(
                    _data(scheme="https", host="example.com", pathPrefix="/users")
                ),
            )
        )
        self.assertEqual(discovery.deep_links, ("https://example.com/users",))

    def test_path_pattern_wildcard_is_filled_deterministically(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(
                    _data(scheme="https", host="example.com", pathPattern="/item/.*")
                ),
            )
        )
        self.assertEqual(discovery.deep_links, ("https://example.com/item/valordroid",))

    def test_path_suffix_is_prefixed_with_a_filler_segment(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(
                    _data(scheme="https", host="example.com", pathSuffix="/edit")
                ),
            )
        )
        self.assertEqual(
            discovery.deep_links, ("https://example.com/valordroid/edit",)
        )

    def test_data_elements_pool_into_a_cross_product(self) -> None:
        # Android pools scheme/host/path across every data element in one filter.
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(
                    _data(scheme="https", host="a.example.com"),
                    _data(scheme="http", host="b.example.com"),
                ),
            )
        )
        self.assertEqual(
            discovery.deep_links,
            (
                "http://a.example.com",
                "http://b.example.com",
                "https://a.example.com",
                "https://b.example.com",
            ),
        )

    def test_filter_without_a_scheme_yields_no_uri(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(_data(mimeType="text/plain")),
            )
        )
        self.assertEqual(discovery.deep_links, ())

    def test_non_view_filter_yields_no_uri(self) -> None:
        send_filter = "\n".join(
            [
                "        E: intent-filter (line=20)",
                "          E: action (line=21)",
                '            A: android:name(0x01010003)="android.intent.action.SEND" (Raw: "x")',
                _data(scheme="https", host="example.com"),
            ]
        )
        discovery = _discover(_activity(".Deep", exported=True, filters=send_filter))
        self.assertEqual(discovery.deep_links, ())


class BoundsAndChecksumTest(unittest.TestCase):
    def _many_deep_links(self, count: int) -> str:
        return "".join(
            _activity(
                f".Deep{index}",
                exported=True,
                filters=_view_filter(_data(scheme="https", host=f"h{index}.example.com")),
            )
            for index in range(count)
        )

    def test_deep_links_are_truncated_by_sorted_order_and_flagged(self) -> None:
        discovery = _discover(self._many_deep_links(6), max_deep_links=3)
        self.assertEqual(len(discovery.deep_links), 3)
        self.assertTrue(discovery.truncated)
        self.assertEqual(discovery.deep_links, tuple(sorted(discovery.deep_links)))

    def test_truncation_keeps_owners_consistent_with_retained_links(self) -> None:
        discovery = _discover(self._many_deep_links(6), max_deep_links=2)
        self.assertEqual(set(discovery.deep_link_owners), set(discovery.deep_links))

    def test_component_bounds_are_enforced(self) -> None:
        body = "".join(_activity(f".E{index}", exported=True) for index in range(5))
        discovery = _discover(body, max_exported_components=2)
        self.assertEqual(len(discovery.exported_components), 2)
        self.assertTrue(discovery.truncated)

    def test_discovery_is_reproducible_and_self_checksumming(self) -> None:
        body = self._many_deep_links(4)
        first = _discover(body)
        second = _discover(body)
        self.assertEqual(first.discovery_sha256, second.discovery_sha256)
        self.assertEqual(first.rule, DISCOVERY_RULE)

    def test_tampered_checksum_is_refused(self) -> None:
        discovery = _discover(_activity(".Main", exported=True))
        payload = discovery.to_dict()
        payload["deep_links"] = ["https://injected.example.com"]
        payload["deep_link_owners"] = {
            "https://injected.example.com": f"{PACKAGE}/{PACKAGE}.Main"
        }
        with self.assertRaises(ValueError) as caught:
            route_discovery_from_mapping(payload)
        self.assertIn("checksum mismatch", str(caught.exception))

    def test_round_trip_through_json_preserves_identity(self) -> None:
        discovery = _discover(
            _activity(
                ".Deep",
                exported=True,
                filters=_view_filter(_data(scheme="https", host="example.com")),
            )
        )
        restored = route_discovery_from_mapping(discovery.to_dict())
        self.assertEqual(restored, discovery)


@unittest.skipUnless(
    (_DATASET / "Kore.apk").is_file(), "campaign APK dataset is not present"
)
class RealApkTest(unittest.TestCase):
    """Discovery must work on the actual campaign APKs, not only fixtures."""

    def test_kore_yields_usable_targets_where_configuration_had_none(self) -> None:
        apk = _DATASET / "Kore.apk"
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
        discovery = discover_apk_routes(
            apk, package_name="org.xbmc.kore", apk_sha256=digest
        )
        self.assertEqual(discovery.package_name, "org.xbmc.kore")
        total = (
            len(discovery.deep_links)
            + len(discovery.exported_components)
            + len(discovery.forced_components)
        )
        self.assertGreater(total, 0)
        self.assertTrue(
            all(uri.count("://") == 1 for uri in discovery.deep_links),
            discovery.deep_links,
        )
        for component in discovery.exported_components + discovery.forced_components:
            self.assertTrue(component.startswith("org.xbmc.kore/"), component)

    def test_discovery_is_byte_identical_across_two_runs(self) -> None:
        apk = _DATASET / "Kore.apk"
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
        first = discover_apk_routes(apk, package_name="org.xbmc.kore", apk_sha256=digest)
        second = discover_apk_routes(apk, package_name="org.xbmc.kore", apk_sha256=digest)
        self.assertEqual(first.discovery_sha256, second.discovery_sha256)


if __name__ == "__main__":
    unittest.main()
