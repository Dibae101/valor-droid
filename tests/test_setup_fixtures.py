"""The checked-in setup fixtures have to be real objects, not plausible ones.

`gaps_and_fixes.md` §3.4 is specific about why setup matters: a generic
valid-looking string does not create the particular form, note or file a later
screen requires, so the feature never becomes available and the run spends its
budget on a shell. §3.5 adds the other half: a component launched without the
state or intent context it reads is the same dead shell reached a different way.

Both failures are silent. They show up as a coverage number, not an error. So
every property that would make these fixtures quietly wrong is asserted here:

* the profile parses under the schema the runtime actually enforces;
* its canonical digest is pinned in `fixtures.json`, because comparison arms must
  be able to prove they used the same fixture bytes;
* the payload digests in the profile are the digests of the checked-in payloads,
  and the APK digests in the index are the ones `dataset/apps.tsv` records;
* no fixture carries a literal credential, a bearer token, or a `user:pass@` URI;
* a profile whose secret environment variable is absent or blank stops before it
  touches the device, with the variable and slot named, and substitutes nothing;
* a declared component prerequisite is refused unless the steps that establish it
  are required, ordered before the launch, and actually verify device state; and
* the components and resource ids the fixtures name exist in the APK they claim,
  read back out of that APK with `aapt`.

`device_witness` is false for every fixture, and these tests deliberately prove
nothing about coverage: no fixture here has been executed on a device.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from valordroid.android.manifest import discover_apk_routes
from valordroid.coverage_context import (
    DEEP_LINK_TARGET,
    EXPORTED_COMPONENT_TARGET,
    FORCED_COMPONENT_TARGET,
    derive_coverage_context,
    validate_component_prerequisites,
)
from valordroid.runtime_config import EXTRA_TYPES, RuntimeConfig
from valordroid.setup_profile import (
    SETUP_INTENT_EXTRA_KINDS,
    SetupProfile,
)
from valordroid.universe import CoverageUniverse

_REPOSITORY = Path(__file__).resolve().parents[1]
_FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "setup-profiles"
_INDEX = _FIXTURES / "fixtures.json"
_APKS = _REPOSITORY / "dataset" / "apks"
_APPS = _REPOSITORY / "dataset" / "apps.tsv"

# Substrings that have no business appearing in a retained fixture. A profile
# records slot names and environment variable names; a value that looks like a
# credential is either a real one or a placeholder pretending to be one, and both
# are refused.
_CREDENTIAL_MARKERS = (
    "password",
    "passwd",
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "refresh_token",
    "bearer ",
    "private_key",
    "begin rsa",
    "begin openssh",
    "aws_secret",
)
_USERINFO_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/?#@\s]*:[^/?#@\s]*@")


def _index() -> dict:
    return json.loads(_INDEX.read_text(encoding="utf-8"))


def _profiles() -> list[Path]:
    return sorted(
        path for path in _FIXTURES.glob("*.json") if path.name != _INDEX.name
    )


def _dataset_rows() -> dict[str, dict[str, str]]:
    with _APPS.open(encoding="utf-8", newline="") as stream:
        return {
            row["slug"]: row for row in csv.DictReader(stream, delimiter="\t")
        }


def _strings(value: object, path: str = "") -> list[tuple[str, str]]:
    """Every string value in a JSON document, with the path that reached it."""

    if isinstance(value, dict):
        found: list[tuple[str, str]] = []
        for key, item in value.items():
            found.extend(_strings(item, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_strings(item, f"{path}[{index}]"))
        return found
    if isinstance(value, str):
        return [(path, value)]
    return []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aapt() -> str | None:
    return shutil.which("aapt")


def _resource_ids(apk: Path, aapt: str) -> set[str]:
    """Every `package:id/name` the APK's own resource table declares."""

    completed = subprocess.run(
        [aapt, "dump", "resources", str(apk)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
        timeout=120.0,
    )
    pattern = re.compile(r"spec resource 0x[0-9a-f]{8} ([^:\s]+):id/(\S+?):")
    return {
        f"{match.group(1)}:id/{match.group(2)}"
        for match in (
            pattern.search(line) for line in completed.stdout.splitlines()
        )
        if match is not None
    }


def _selector_resource_ids(profile: SetupProfile) -> set[str]:
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if "resource_id" in value and isinstance(value["resource_id"], str):
                found.add(value["resource_id"])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(profile.to_dict())
    return found


class FixtureInventoryTest(unittest.TestCase):
    """The index and the directory have to describe the same set of files."""

    def test_the_index_lists_every_profile_exactly_once(self) -> None:
        index = _index()
        listed = [entry["profile"] for entry in index["fixtures"]]
        self.assertEqual(sorted(listed), sorted(path.name for path in _profiles()))
        self.assertEqual(len(set(listed)), len(listed))

    def test_every_indexed_payload_exists_with_the_recorded_digest(self) -> None:
        for entry in _index()["fixtures"]:
            for payload in entry["payloads"]:
                with self.subTest(profile=entry["profile"], payload=payload["path"]):
                    path = _FIXTURES / payload["path"]
                    self.assertTrue(path.is_file(), f"missing payload: {path}")
                    self.assertEqual(_sha256(path), payload["sha256"])

    def test_no_fixture_claims_a_coverage_number(self) -> None:
        """`device_witness` is false, so a percentage here would be invented."""

        for entry in _index()["fixtures"]:
            with self.subTest(profile=entry["profile"]):
                self.assertIsInstance(entry["device_witness"], bool)
                if not entry["device_witness"]:
                    self.assertNotIn("%", entry["notes"])
                    self.assertIsNone(
                        re.search(r"\d+(\.\d+)?\s*(percent|pp)\b", entry["notes"])
                    )


class FixtureSchemaAndDigestTest(unittest.TestCase):
    """Schema validity and a digest that does not move between loads."""

    def test_every_fixture_parses_under_the_runtime_schema(self) -> None:
        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                self.assertEqual(profile.schema, 1)
                self.assertEqual(profile.package, profile.package.strip())
                self.assertTrue(profile.steps)
                self.assertTrue(profile.success_predicates)

    def test_every_fixture_digest_matches_the_pinned_value(self) -> None:
        pinned = {entry["profile"]: entry for entry in _index()["fixtures"]}
        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                entry = pinned[path.name]
                self.assertEqual(profile.profile_sha256, entry["profile_sha256"])
                self.assertEqual(profile.package, entry["package"])
                self.assertEqual(
                    sorted(profile.component_intent_context),
                    sorted(entry["component_context"]),
                )
                self.assertEqual(
                    sorted(profile.secret_environment), sorted(entry["secret_slots"])
                )

    def test_the_digest_survives_a_canonical_round_trip(self) -> None:
        """Reparsing the canonical form must not change a single byte."""

        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                again = SetupProfile.from_mapping(json.loads(profile.canonical_json))
                self.assertEqual(again.canonical_json, profile.canonical_json)
                self.assertEqual(again.profile_sha256, profile.profile_sha256)
                self.assertEqual(
                    SetupProfile.load(path).profile_sha256, profile.profile_sha256
                )

    def test_a_reordered_document_produces_the_same_digest(self) -> None:
        """The digest is over canonical content, not over file key order."""

        for path in _profiles():
            with self.subTest(profile=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                shuffled = {key: document[key] for key in reversed(list(document))}
                self.assertEqual(
                    SetupProfile.from_mapping(shuffled).profile_sha256,
                    SetupProfile.load(path).profile_sha256,
                )

    def test_a_changed_step_changes_the_digest(self) -> None:
        """Otherwise pinning it would prove nothing about the bytes used."""

        path = _FIXTURES / "Amaze.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        original = SetupProfile.from_mapping(copy.deepcopy(document)).profile_sha256
        document["steps"][0]["timeout_seconds"] = 21.0
        self.assertNotEqual(
            SetupProfile.from_mapping(document).profile_sha256, original
        )


class FixturePayloadTest(unittest.TestCase):
    """A pushed payload is only a fixture if the bytes are the declared bytes."""

    def test_resolution_verifies_every_payload_against_the_repository(self) -> None:
        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                resolved = profile.resolve({})
                pushed = {
                    step.step_id: step
                    for step in profile.steps
                    if step.kind == "push_file"
                }
                self.assertEqual(
                    sorted(step_id for step_id, _ in resolved.fixture_items),
                    sorted(pushed),
                )
                for step_id, source in resolved.fixture_items:
                    self.assertEqual(
                        _sha256(source), pushed[step_id].parameters["sha256"]
                    )

    def test_a_payload_whose_bytes_changed_fails_resolution(self) -> None:
        """The digest is a gate, not documentation."""

        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            shutil.copytree(_FIXTURES, work / "setup-profiles")
            copied = work / "setup-profiles"
            payload = copied / "payloads" / "amaze-notes.txt"
            payload.write_text(
                payload.read_text(encoding="utf-8") + "drift\n", encoding="utf-8"
            )
            profile = SetupProfile.load(copied / "Amaze.json")
            with self.assertRaises(ValueError) as caught:
                profile.resolve({})
            self.assertIn("checksum mismatch", str(caught.exception))

    def test_a_missing_payload_fails_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            shutil.copytree(_FIXTURES, work / "setup-profiles")
            copied = work / "setup-profiles"
            (copied / "payloads" / "odk-collect-single-field.xml").unlink()
            profile = SetupProfile.load(copied / "ODK-Collect.json")
            with self.assertRaises(ValueError) as caught:
                profile.resolve({})
            self.assertIn("missing", str(caught.exception))


class FixtureCredentialTest(unittest.TestCase):
    """Secrets stay logical slots plus environment variable names."""

    def test_no_fixture_contains_a_credential_shaped_literal(self) -> None:
        for path in _profiles():
            document = json.loads(path.read_text(encoding="utf-8"))
            for where, value in _strings(document, path.name):
                lowered = value.lower()
                for marker in _CREDENTIAL_MARKERS:
                    with self.subTest(profile=path.name, at=where, marker=marker):
                        self.assertNotIn(marker, lowered)
                with self.subTest(profile=path.name, at=where):
                    self.assertIsNone(_USERINFO_URI.search(value))

    def test_these_fixtures_declare_no_secret_at_all(self) -> None:
        """They are the account-free set; a slot here would contradict that."""

        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                self.assertEqual(dict(profile.secret_environment), {})
                self.assertFalse(
                    [step for step in profile.steps if step.kind == "input_secret"]
                )

    def test_every_secret_environment_value_is_a_variable_name(self) -> None:
        """Across every profile in the repository, not just the fixtures."""

        roots = [_FIXTURES, _REPOSITORY / "app" / "setup-profiles"]
        seen = 0
        for root in roots:
            for path in sorted(root.glob("*.json")):
                if path.name == _INDEX.name:
                    continue
                profile = SetupProfile.load(path)
                for slot, variable in profile.secret_environment.items():
                    seen += 1
                    with self.subTest(profile=path.name, slot=slot):
                        # An environment variable name, and nothing that could be
                        # mistaken for the value it names.
                        self.assertRegex(variable, r"\A[A-Z][A-Z0-9_]*\Z")
                        self.assertNotIn(":", variable)
        self.assertGreater(seen, 0, "no profile exercises the secret path")

    def test_a_retained_input_text_step_cannot_carry_a_userinfo_uri(self) -> None:
        """The loader refuses it, so a credential cannot arrive by that route."""

        document = json.loads((_FIXTURES / "Amaze.json").read_text(encoding="utf-8"))
        document["steps"].append(
            {
                "id": "smuggle-a-credential",
                "kind": "input_text",
                "required": True,
                "timeout_seconds": 10.0,
                "parameters": {
                    "clear": True,
                    "value": "https://name:value@example.invalid/",
                    "selector": {
                        "package": "com.amaze.filemanager",
                        "class_name": "android.widget.EditText",
                    },
                },
            }
        )
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("literal credential", str(caught.exception))


class SecretResolutionTest(unittest.TestCase):
    """An absent variable must stop the run, not run with a placeholder."""

    PROFILE = {
        "schema": 1,
        "package": "com.example.app",
        "profile": "sign-in-with-an-environment-secret",
        "persona": "disposable-staging-user",
        "timeout_seconds": 120.0,
        "allowed_foreign_packages": [],
        "secret_environment": {"login-name": "VALORDROID_FIXTURE_TEST_LOGIN"},
        "steps": [
            {
                "id": "enter-login",
                "kind": "input_secret",
                "required": True,
                "timeout_seconds": 20.0,
                "parameters": {
                    "clear": True,
                    "secret_slot": "login-name",
                    "selector": {
                        "package": "com.example.app",
                        "resource_id": "com.example.app:id/login",
                    },
                },
            }
        ],
        "success_predicates": [
            {
                "kind": "package_foreground",
                "timeout_seconds": 20.0,
                "parameters": {"package": "com.example.app"},
            }
        ],
    }

    def setUp(self) -> None:
        self.profile = SetupProfile.from_mapping(copy.deepcopy(self.PROFILE))

    def test_an_absent_variable_names_the_variable_and_the_slot(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.profile.resolve({})
        message = str(caught.exception)
        self.assertIn("VALORDROID_FIXTURE_TEST_LOGIN", message)
        self.assertIn("login-name", message)
        self.assertIn("no placeholder", message)

    def test_a_blank_variable_is_refused_like_an_absent_one(self) -> None:
        for value in ("", " ", "\t"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError) as caught:
                    self.profile.resolve({"VALORDROID_FIXTURE_TEST_LOGIN": value})
                self.assertIn("missing or", str(caught.exception))

    def test_a_control_character_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.profile.resolve({"VALORDROID_FIXTURE_TEST_LOGIN": "a\nb"})

    def test_a_present_variable_resolves_without_entering_the_evidence(self) -> None:
        resolved = self.profile.resolve(
            {"VALORDROID_FIXTURE_TEST_LOGIN": "fixture-value"}
        )
        self.assertEqual(resolved.secret("login-name"), "fixture-value")
        self.assertNotIn("fixture-value", repr(resolved))
        self.assertNotIn("fixture-value", self.profile.canonical_json)
        self.assertNotIn(
            "fixture-value", json.dumps(self.profile.verification_details())
        )
        self.assertEqual(resolved.redact("saw fixture-value here"), "saw [REDACTED] here")

    def test_the_digest_does_not_depend_on_the_resolved_value(self) -> None:
        first = self.profile.resolve({"VALORDROID_FIXTURE_TEST_LOGIN": "one"})
        second = self.profile.resolve({"VALORDROID_FIXTURE_TEST_LOGIN": "two"})
        self.assertEqual(
            first.profile.profile_sha256, second.profile.profile_sha256
        )

    def test_every_repository_profile_with_a_slot_fails_closed(self) -> None:
        """The shipped ownCloud/Jellyfin profiles included."""

        root = _REPOSITORY / "app" / "setup-profiles"
        checked = 0
        for path in sorted(root.glob("*.json")):
            profile = SetupProfile.load(path)
            if not profile.secret_environment:
                continue
            checked += 1
            with self.subTest(profile=path.name):
                with self.assertRaises(ValueError) as caught:
                    profile.resolve({})
                message = str(caught.exception)
                self.assertIn("no placeholder", message)
                self.assertTrue(
                    any(
                        variable in message
                        for variable in profile.secret_environment.values()
                    )
                )
        self.assertGreater(checked, 0)


class ComponentContextDeclarationTest(unittest.TestCase):
    """A declared prerequisite has to be established, checked, and delivered."""

    def setUp(self) -> None:
        self.path = _FIXTURES / "Amaze.json"
        self.document = json.loads(self.path.read_text(encoding="utf-8"))
        self.profile = SetupProfile.load(self.path)

    def test_the_declared_uri_is_a_path_a_required_step_writes(self) -> None:
        pushed = {
            str(step.parameters["destination"])
            for step in self.profile.steps
            if step.kind == "push_file"
        }
        for entry in self.profile.component_context:
            for key, kind, value in entry.intent_context:
                if kind == "uri" and value.startswith("file://"):
                    with self.subTest(component=entry.component):
                        self.assertIn(value.removeprefix("file://"), pushed)

    def test_every_prerequisite_runs_before_the_launch_and_is_required(self) -> None:
        order = {step.step_id: index for index, step in enumerate(self.profile.steps)}
        by_id = {step.step_id: step for step in self.profile.steps}
        for entry in self.profile.component_context:
            launch = [
                step.step_id
                for step in self.profile.steps
                if step.kind == "launch_component"
                and step.parameters["component"] == entry.component
            ]
            self.assertEqual(len(launch), 1)
            for step_id in entry.requires:
                with self.subTest(component=entry.component, step=step_id):
                    self.assertLess(order[step_id], order[launch[0]])
                    self.assertTrue(by_id[step_id].required)

    def test_a_prerequisite_after_the_launch_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        document["steps"].append(
            {
                "id": "verify-too-late",
                "kind": "wait_for",
                "required": True,
                "timeout_seconds": 10.0,
                "parameters": {
                    "predicate": {
                        "kind": "package_foreground",
                        "timeout_seconds": 10.0,
                        "parameters": {"package": "com.amaze.filemanager"},
                    }
                },
            }
        )
        document["component_context"][0]["requires"].append("verify-too-late")
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("does not run before", str(caught.exception))

    def test_an_optional_prerequisite_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        for step in document["steps"]:
            if step["id"] == "verify-fixture-notes-on-device":
                step["required"] = False
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("may be skipped is an assumption", str(caught.exception))

    def test_a_prerequisite_that_verifies_nothing_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        document["component_context"][0]["requires"] = ["grant-write-storage"]
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("never verifies", str(caught.exception))

    def test_a_file_uri_no_step_writes_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        document["component_context"][0]["intent_context"] = [
            ["", "uri", "file:///sdcard/valordroid/not-pushed.txt"]
        ]
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("no required push_file step", str(caught.exception))

    def test_an_undeclared_launch_is_refused_once_any_context_exists(self) -> None:
        document = copy.deepcopy(self.document)
        document["steps"].append(
            {
                "id": "launch-something-else",
                "kind": "launch_component",
                "required": True,
                "timeout_seconds": 30.0,
                "parameters": {
                    "component": (
                        "com.amaze.filemanager/"
                        "com.amaze.filemanager.activities.DbViewer"
                    )
                },
            }
        )
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("without a declared component context", str(caught.exception))

    def test_a_context_for_a_component_no_step_launches_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        document["component_context"].append(
            {
                "component": (
                    "com.amaze.filemanager/"
                    "com.amaze.filemanager.activities.PreferencesActivity"
                ),
                "requires": ["verify-fixture-notes-on-device"],
            }
        )
        with self.assertRaises(ValueError) as caught:
            SetupProfile.from_mapping(document)
        self.assertIn("which no launch_component step launches", str(caught.exception))

    def test_a_foreign_component_is_refused(self) -> None:
        document = copy.deepcopy(self.document)
        document["component_context"][0]["component"] = (
            "com.other.app/com.other.app.Activity"
        )
        with self.assertRaises(ValueError):
            SetupProfile.from_mapping(document)


class ComponentExtrasDeliveryTest(unittest.TestCase):
    """The declared context must reach `am start`, or the run must not start."""

    def setUp(self) -> None:
        self.profile = SetupProfile.load(_FIXTURES / "Amaze.json")
        self.declared = self.profile.component_intent_context
        self.assertTrue(self.declared, "the Amaze fixture must declare a context")

    def test_intent_kinds_are_exactly_the_runtime_extra_types(self) -> None:
        """`setup_profile` copies this set; drift would silently accept junk."""

        self.assertEqual(SETUP_INTENT_EXTRA_KINDS, frozenset(EXTRA_TYPES))

    def test_a_configuration_without_the_context_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.profile.validate_component_extras({})
        message = str(caught.exception)
        self.assertIn("omit the context declared for", message)
        self.assertIn("TextReader", message)

    def test_a_configuration_carrying_the_context_is_accepted(self) -> None:
        self.profile.validate_component_extras(
            {
                component: [list(entry) for entry in entries]
                for component, entries in self.declared.items()
            }
        )

    def test_the_short_component_spelling_is_accepted(self) -> None:
        """A manifest spells its own activity without the package prefix."""

        self.profile.validate_component_extras(
            {
                component.split("/", 1)[-1]: [list(entry) for entry in entries]
                for component, entries in self.declared.items()
            }
        )

    def test_a_configuration_with_a_different_uri_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.profile.validate_component_extras(
                {
                    component: [["", "uri", "file:///sdcard/other.txt"]]
                    for component in self.declared
                }
            )

    def test_the_declared_context_is_retained_in_the_verification_details(self) -> None:
        """The run's own evidence has to say which launch was promised context."""

        details = self.profile.verification_details()
        self.assertEqual(
            details["component_context"],
            [entry.to_dict() for entry in self.profile.component_context],
        )
        self.assertEqual(
            details["setup_profile_sha256"], self.profile.profile_sha256
        )

    def test_a_profile_without_a_context_keeps_its_historical_detail_shape(self) -> None:
        """Adding a key to published runs' details would break their validation."""

        profile = SetupProfile.load(_FIXTURES / "Omni-Notes-Alpha.json")
        self.assertEqual(profile.component_context, ())
        self.assertEqual(
            sorted(profile.verification_details()),
            [
                "package",
                "persona",
                "profile",
                "schema",
                "secret_slots",
                "setup_profile_sha256",
                "step_count",
                "success_predicate_count",
            ],
        )

    def test_the_declared_context_is_a_valid_runtime_configuration(self) -> None:
        """Derived, not hand-written, so the two cannot drift."""

        values = {
            "schema_version": RuntimeConfig.SCHEMA_VERSION,
            "serial": "127.0.0.1:5555",
            "launcher": f"{self.profile.package}/.MainActivity",
            "max_actions": 4,
            "max_seconds": 60.0,
            "action_timeout_seconds": 5.0,
            "adb_timeout_seconds": 5.0,
            "capture_screenshots": False,
            "install_timeout_seconds": 10.0,
            "launch_timeout_seconds": 5.0,
            "observation_timeout_seconds": 5.0,
            "route_foreground_timeout_seconds": 2.0,
            "per_field_input_enabled": True,
            "pid_poll_seconds": 0.2,
            "post_action_delay_seconds": 0.1,
            "reset_seed_enabled": False,
            "suppress_soft_keyboard": False,
            "route_discovery_sha256": None,
            "component_extras": {
                component: [list(entry) for entry in entries]
                for component, entries in self.declared.items()
            },
            "text_input_value": "valordroid",
            "uninstall_after_run": True,
            "exported_components": sorted(self.declared),
            "forced_components": [],
            "deep_links": [],
        }
        config = RuntimeConfig.from_mapping(values)
        self.profile.validate_component_extras(config.component_extras)
        # And the same after a serialize/parse cycle, which is how a frozen
        # configuration actually reaches a run.
        again = RuntimeConfig.from_mapping(json.loads(json.dumps(config.to_dict())))
        self.profile.validate_component_extras(again.component_extras)


class ComponentPrerequisiteAuthorizationTest(unittest.TestCase):
    """A prerequisite path to an unreachable component is not a valid path.

    The universe here is synthetic: `com.example.app` with two obviously
    made-up unit IDs. No dataset app's methods are involved, because a method ID
    can only come from instrumentation output.
    """

    PACKAGE = "com.example.app"
    EXPORTED = "com.example.app/com.example.app.ExportedActivity"
    FORCED = "com.example.app/com.example.app.InternalActivity"

    def _context(self):
        universe = CoverageUniverse.create(
            package_name=self.PACKAGE,
            metric="method",
            backend="regression-suite",
            apk_sha256="a" * 64,
            unit_ids=(
                "<com.example.app.ExportedActivity: void onCreate(android.os.Bundle)>",
                "<com.example.app.InternalActivity: void onCreate(android.os.Bundle)>",
            ),
        )
        return derive_coverage_context(
            universe,
            discovery=None,
            deep_links=(),
            exported_components=(self.EXPORTED,),
            forced_components=(self.FORCED,),
        )

    def test_an_exported_component_is_authorized(self) -> None:
        self.assertEqual(
            validate_component_prerequisites(self._context(), [self.EXPORTED]),
            (self.EXPORTED,),
        )

    def test_a_forced_only_component_is_refused_by_default(self) -> None:
        with self.assertRaises(ValueError) as caught:
            validate_component_prerequisites(self._context(), [self.FORCED])
        message = str(caught.exception)
        self.assertIn("only reachable as", message)
        self.assertIn(FORCED_COMPONENT_TARGET, message)

    def test_a_forced_component_is_authorized_only_when_asked_for(self) -> None:
        self.assertEqual(
            validate_component_prerequisites(
                self._context(),
                [self.FORCED],
                authorized_kinds=(FORCED_COMPONENT_TARGET,),
            ),
            (self.FORCED,),
        )

    def test_an_unknown_component_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            validate_component_prerequisites(
                self._context(), ["com.example.app/com.example.app.Renamed"]
            )
        self.assertIn("not a retained route target", str(caught.exception))

    def test_an_unsupported_authorized_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_component_prerequisites(
                self._context(), [self.EXPORTED], authorized_kinds=("teleport",)
            )

    def test_the_default_authorization_excludes_forced_activation(self) -> None:
        context = self._context()
        self.assertEqual(
            validate_component_prerequisites(context, [self.EXPORTED]),
            (self.EXPORTED,),
        )
        kinds = {
            target.kind for target in context.route_targets
        }
        self.assertEqual(
            kinds,
            {EXPORTED_COMPONENT_TARGET, FORCED_COMPONENT_TARGET},
        )
        self.assertNotIn(DEEP_LINK_TARGET, kinds)


@unittest.skipUnless(_APKS.is_dir(), "dataset APKs are not present")
@unittest.skipUnless(_aapt() is not None, "aapt is not available")
class FixtureMatchesTheApkTest(unittest.TestCase):
    """Every name a fixture uses is read back out of the APK it names."""

    def setUp(self) -> None:
        self.index = _index()
        self.rows = _dataset_rows()
        self.aapt = _aapt()

    def test_the_index_apk_digest_is_the_dataset_digest(self) -> None:
        for entry in self.index["fixtures"]:
            with self.subTest(slug=entry["slug"]):
                row = self.rows[entry["slug"]]
                self.assertEqual(entry["apk_file"], row["apk_file"])
                self.assertEqual(entry["apk_sha256"], row["sha256"])
                self.assertEqual(entry["package"], row["package"])
                self.assertEqual(entry["apk_version_name"], row["version_name"])
                apk = _APKS / entry["apk_file"]
                self.assertTrue(apk.is_file())
                self.assertEqual(_sha256(apk), entry["apk_sha256"])

    def test_every_declared_component_is_exported_in_that_apk(self) -> None:
        """A forced-only component cannot be launched by a setup profile."""

        for entry in self.index["fixtures"]:
            if not entry["component_context"]:
                continue
            with self.subTest(slug=entry["slug"]):
                discovery = discover_apk_routes(
                    _APKS / entry["apk_file"],
                    package_name=entry["package"],
                    apk_sha256=entry["apk_sha256"],
                    aapt=self.aapt,
                )
                for component in entry["component_context"]:
                    self.assertIn(component, discovery.declared_activities)
                    self.assertIn(component, discovery.exported_components)

    def test_no_profile_launches_a_component_it_did_not_declare(self) -> None:
        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                launched = {
                    str(step.parameters["component"])
                    for step in profile.steps
                    if step.kind == "launch_component"
                }
                self.assertEqual(
                    launched, {entry.component for entry in profile.component_context}
                )

    def test_every_resource_id_exists_in_that_apk(self) -> None:
        by_profile = {entry["profile"]: entry for entry in self.index["fixtures"]}
        for path in _profiles():
            entry = by_profile[path.name]
            profile = SetupProfile.load(path)
            wanted = _selector_resource_ids(profile)
            if not wanted:
                continue
            declared = _resource_ids(_APKS / entry["apk_file"], self.aapt)
            for resource_id in sorted(wanted):
                with self.subTest(profile=path.name, resource_id=resource_id):
                    package, _, name = resource_id.partition(":id/")
                    if package != entry["package"]:
                        # Framework ids (`android:id/...`) are not in this table.
                        self.assertEqual(package, "android")
                        continue
                    self.assertTrue(
                        resource_id in declared,
                        f"{resource_id} is not declared by {entry['apk_file']} "
                        f"({len(declared)} ids read from its resource table)",
                    )
                    self.assertTrue(name)

    def test_every_granted_permission_is_declared_by_that_apk(self) -> None:
        """`pm grant` fails for a permission the manifest never requested."""

        by_profile = {entry["profile"]: entry for entry in self.index["fixtures"]}
        for path in _profiles():
            entry = by_profile[path.name]
            profile = SetupProfile.load(path)
            wanted = {
                str(step.parameters["permission"])
                for step in profile.steps
                if step.kind == "permission"
            } | {
                str(predicate.parameters["permission"])
                for predicate in profile.success_predicates
                if predicate.kind == "permission_granted"
            }
            if not wanted:
                continue
            completed = subprocess.run(
                [self.aapt, "dump", "badging", str(_APKS / entry["apk_file"])],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=120.0,
            )
            declared = {
                line.split("name='", 1)[1].split("'", 1)[0]
                for line in completed.stdout.splitlines()
                if line.startswith("uses-permission")
            }
            for permission in sorted(wanted):
                with self.subTest(profile=path.name, permission=permission):
                    self.assertIn(permission, declared)

    def test_every_selector_stays_inside_the_app_package(self) -> None:
        for path in _profiles():
            with self.subTest(profile=path.name):
                profile = SetupProfile.load(path)
                self.assertEqual(profile.allowed_foreign_packages, ())
                for where, value in _strings(profile.to_dict(), path.name):
                    if where.endswith(".package"):
                        self.assertEqual(value, profile.package)


if __name__ == "__main__":
    unittest.main()
