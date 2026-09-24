"""Prepared bundle and instrumenter provenance regressions.

Pinned findings:
- review 1 #6: receipt values compared loosely, so JSON 1 satisfied a boolean.
- review 2 #12: config, receipt, and invocation were discarded after the run.
- review 3 #7: the retained invocation was not cross-bound to its config.
- review 5 #3: the executable symlink refusal broke the shipped example.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.instrumentation import (
    INSTRUMENTER_CONFIG_SCHEMA_VERSION,
    INSTRUMENTER_PROTOCOL,
    InstrumenterConfig,
    check_instrumenter_conformance,
)
from valordroid.prepared import (
    PREPARED_SCHEMA_VERSION,
    import_prepared_bundle,
    verify_prepared_bundle,
)

from . import support

REPO = Path(__file__).resolve().parents[1] / "app"


class PreparedBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-prepared."))
        self.tools = support.fake_tools(self._root / "tools")
        self.work = self._root / "work"

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def test_bundle_without_provenance_verifies(self) -> None:
        bundle_path = support.build_prepared(self.work, self.tools["aapt"])
        bundle = verify_prepared_bundle(bundle_path, aapt=self.tools["aapt"])
        self.assertEqual(bundle.manifest.schema_version, PREPARED_SCHEMA_VERSION)
        self.assertNotIn("instrumenter_invocation", bundle.manifest.artifacts)

    def test_bundle_with_provenance_verifies(self) -> None:
        """Review 2 #12: provenance must survive into the bundle."""

        bundle_path = support.build_prepared(
            self.work, self.tools["aapt"], name="prepared-prov", with_provenance=True
        )
        bundle = verify_prepared_bundle(bundle_path, aapt=self.tools["aapt"])
        for key in (
            "instrumenter_config",
            "instrumenter_receipt",
            "instrumenter_invocation",
        ):
            self.assertIn(key, bundle.manifest.artifacts)

    def test_tampered_invocation_is_rejected_by_artifact_hash(self) -> None:
        bundle_path = support.build_prepared(
            self.work, self.tools["aapt"], name="prepared-prov", with_provenance=True
        )
        target = bundle_path / "instrumenter-invocation.json"
        value = json.loads(target.read_text())
        value["returncode"] = 1
        target.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            verify_prepared_bundle(bundle_path, aapt=self.tools["aapt"])

    def test_partial_provenance_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            import_prepared_bundle(
                original_apk=self.work / "original.apk",
                instrumented_apk=self.work / "instrumented.apk",
                units_path=self.work / "units.json",
                proof_path=self.work / "static-unit-proof.json",
                output=self.work / "partial",
                backend_version="1.0",
                inclusion_policy="all-app-methods",
                log_tag="AndroLog",
                method_prefix="METHOD=",
                aapt=self.tools["aapt"],
                instrumenter_config={"protocol": INSTRUMENTER_PROTOCOL},
            )

    def test_invocation_must_describe_its_configuration(self) -> None:
        """Review 3 #7: command, environment, and digests were unbound."""

        for field in ("command", "environment", "artifacts"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    support.build_prepared(
                        self.work,
                        self.tools["aapt"],
                        name=f"prepared-broken-{field}",
                        with_provenance=True,
                        break_field=field,
                    )


class InstrumenterConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-instrumenter."))

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def test_shipped_example_parses(self) -> None:
        value = json.loads((REPO / "instrumenter-config.example.json").read_text())
        config = InstrumenterConfig.from_mapping(value)
        self.assertEqual(config.protocol, INSTRUMENTER_PROTOCOL)
        self.assertIn("PATH", config.environment)

    def test_child_environment_is_explicit_and_not_inherited(self) -> None:
        """Review 1 #5: the instrumenter inherited the caller's environment."""

        value = json.loads((REPO / "instrumenter-config.example.json").read_text())
        config = InstrumenterConfig.from_mapping(value)
        environment = config.child_environment()
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["TZ"], "UTC")
        self.assertEqual(
            set(environment), {"LC_ALL", "LANG", "TZ", *config.environment}
        )

    def test_alternatives_managed_executable_is_accepted(self) -> None:
        """Review 5 #3: rejecting a PATH symlink broke every JDK install."""

        import shutil as shutil_module

        interpreter = shutil_module.which("python3")
        self.assertIsNotNone(interpreter)
        tool = self._root / "tool.jar"
        tool.write_bytes(b"tool")
        import hashlib

        config = {
            "schema_version": INSTRUMENTER_CONFIG_SCHEMA_VERSION,
            "protocol": INSTRUMENTER_PROTOCOL,
            "producer": "example",
            "producer_version": "1",
            "source_revision": "0" * 40,
            "command": ["python3", "{tool:tool}"],
            "executable_sha256": hashlib.sha256(
                Path(interpreter).resolve().read_bytes()
            ).hexdigest(),
            "toolchain": [
                {
                    "name": "tool",
                    "path": str(tool),
                    "sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
                }
            ],
            "timeout_seconds": 30.0,
            "backend_version": "1.0",
            "inclusion_policy": "all",
            "log_tag": "AndroLog",
            "method_prefix": "METHOD=",
            "environment": {"PATH": "/usr/bin:/bin"},
        }
        path = self._root / "config.json"
        path.write_text(json.dumps(config))
        loaded = InstrumenterConfig.load(path)
        self.assertTrue(Path(loaded.command[0]).is_absolute())
        self.assertEqual(loaded.verify_artifacts()["tool"], config["toolchain"][0]["sha256"])

    def test_symlinked_tool_path_is_refused(self) -> None:
        import hashlib
        import shutil as shutil_module

        interpreter = shutil_module.which("python3")
        real = self._root / "real.jar"
        real.write_bytes(b"tool")
        link = self._root / "link.jar"
        link.symlink_to(real)
        config = {
            "schema_version": INSTRUMENTER_CONFIG_SCHEMA_VERSION,
            "protocol": INSTRUMENTER_PROTOCOL,
            "producer": "example",
            "producer_version": "1",
            "source_revision": "0" * 40,
            "command": ["python3", "{tool:tool}"],
            "executable_sha256": hashlib.sha256(
                Path(interpreter).resolve().read_bytes()
            ).hexdigest(),
            "toolchain": [
                {
                    "name": "tool",
                    "path": str(link),
                    "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
                }
            ],
            "timeout_seconds": 30.0,
            "backend_version": "1.0",
            "inclusion_policy": "all",
            "log_tag": "AndroLog",
            "method_prefix": "METHOD=",
            "environment": {},
        }
        path = self._root / "symlink.json"
        path.write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            InstrumenterConfig.load(path)


class ConformanceTest(unittest.TestCase):
    """The instrumenter-check command against a deliberately wrong instrumenter."""

    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-conformance."))
        self.tools = support.fake_tools(self._root / "tools")
        self.apk = self._root / "app.apk"
        self.apk.write_bytes(b"apk-bytes")

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _config_for(self, script_body: str) -> Path:
        import hashlib
        import stat as stat_module

        script = self._root / "instrumenter.py"
        script.write_text(script_body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat_module.S_IXUSR)
        interpreter = Path(__import__("shutil").which("python3")).resolve()
        config = {
            "schema_version": INSTRUMENTER_CONFIG_SCHEMA_VERSION,
            "protocol": INSTRUMENTER_PROTOCOL,
            "producer": "stub",
            "producer_version": "1",
            "source_revision": "0" * 40,
            "command": ["python3", "{tool:stub}"],
            "executable_sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
            "toolchain": [
                {
                    "name": "stub",
                    "path": str(script),
                    "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                }
            ],
            "timeout_seconds": 60.0,
            "backend_version": "1.0",
            "inclusion_policy": "all",
            "log_tag": "AndroLog",
            "method_prefix": "METHOD=",
            "environment": {"PATH": "/usr/bin:/bin"},
        }
        path = self._root / "instrumenter-config.json"
        path.write_text(json.dumps(config))
        return path

    def test_nonconformant_instrumenter_is_reported_per_check(self) -> None:
        config_path = self._config_for("import sys\nsys.exit(3)\n")
        report = check_instrumenter_conformance(
            original_apk=self.apk,
            config_path=config_path,
            aapt=self.tools["aapt"],
        )
        self.assertFalse(report["conformant"])
        statuses = {item["check"]: item["status"] for item in report["findings"]}
        self.assertEqual(statuses["invocation"], "fail")
        self.assertEqual(statuses["produced_instrumented_apk"], "fail")
        # Digest verification still happened before the invocation.
        self.assertEqual(statuses["toolchain_digests_before"], "pass")

    def test_unpinned_executable_is_reported_not_raised(self) -> None:
        """Review 6 #12: a digest mismatch produced no report at all."""

        config_path = self._config_for("import sys\nsys.exit(0)\n")
        value = json.loads(config_path.read_text())
        value["executable_sha256"] = "f" * 64
        config_path.write_text(json.dumps(value))
        report = check_instrumenter_conformance(
            original_apk=self.apk,
            config_path=config_path,
            aapt=self.tools["aapt"],
        )
        self.assertFalse(report["conformant"])
        statuses = {item["check"]: item["status"] for item in report["findings"]}
        self.assertEqual(statuses["toolchain_digests_before"], "fail")
        self.assertIn("verification", report)
        self.assertIsNone(report["returncode"], "the instrumenter must not have run")

    def test_conformant_instrumenter_is_accepted(self) -> None:
        """Review 6 #16: `conformant: true` was asserted nowhere."""

        body = (
            "import hashlib,json,sys\n"
            "args=dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
            "original=open(args['--input-apk'],'rb').read()\n"
            "instrumented=original+b'-probes'\n"
            "open(args['--output-apk'],'wb').write(instrumented)\n"
            "units=['com.example.app.A.a()V','com.example.app.A.b()V']\n"
            "json.dump(units, open(args['--units-output'],'w'))\n"
            "def digest(value):\n"
            "    return hashlib.sha256(value).hexdigest()\n"
            "canonical=json.dumps(sorted(units), separators=(',',':'), sort_keys=True)\n"
            "json.dump({'schema_version':1,'protocol':args['--protocol'],"
            "'producer':'stub','producer_version':'1','source_revision':'0'*40,"
            "'backend':args['--backend'],'backend_version':args['--backend-version'],"
            "'inclusion_policy':args['--inclusion-policy'],'log_tag':args['--log-tag'],"
            "'method_prefix':args['--method-prefix'],"
            "'original_apk_sha256':digest(original),"
            "'instrumented_apk_sha256':digest(instrumented),"
            "'unit_count':len(units),"
            "'unit_ids_sha256':digest(canonical.encode()),"
            "'instrumentation_complete':True,'enumeration_complete':True,"
            "'one_probe_per_unit':True}, open(args['--receipt-output'],'w'))\n"
        )
        report = check_instrumenter_conformance(
            original_apk=self.apk,
            config_path=self._config_for(body),
            aapt=self.tools["aapt"],
        )
        failures = [item for item in report["findings"] if item["status"] != "pass"]
        self.assertEqual(failures, [], failures)
        self.assertTrue(report["conformant"])
        self.assertEqual(report["unit_count"], 2)

    def test_integer_one_does_not_satisfy_a_boolean_assertion(self) -> None:
        """Review 1 #6: JSON 1 passed a required boolean receipt field."""

        body = (
            "import json,sys\n"
            "args=dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
            "open(args['--output-apk'],'wb').write(b'x')\n"
            "json.dump(['com.example.app.A.a()V'], open(args['--units-output'],'w'))\n"
            "json.dump({'schema_version':1,'protocol':args['--protocol'],"
            "'producer':'stub','producer_version':'1','source_revision':'0'*40,"
            "'backend':args['--backend'],'backend_version':args['--backend-version'],"
            "'inclusion_policy':args['--inclusion-policy'],'log_tag':args['--log-tag'],"
            "'method_prefix':args['--method-prefix'],'original_apk_sha256':'0'*64,"
            "'instrumented_apk_sha256':'0'*64,'unit_count':1,'unit_ids_sha256':'0'*64,"
            "'instrumentation_complete':1,'enumeration_complete':1,"
            "'one_probe_per_unit':1}, open(args['--receipt-output'],'w'))\n"
        )
        report = check_instrumenter_conformance(
            original_apk=self.apk,
            config_path=self._config_for(body),
            aapt=self.tools["aapt"],
        )
        statuses = {item["check"]: item["status"] for item in report["findings"]}
        self.assertEqual(statuses["invocation"], "pass")
        for name in (
            "receipt_instrumentation_complete",
            "receipt_enumeration_complete",
            "receipt_one_probe_per_unit",
        ):
            self.assertEqual(statuses[name], "fail", name)
        self.assertFalse(report["conformant"])


if __name__ == "__main__":
    unittest.main()
