"""The staged sample-file corpus, and the boundaries it must not cross.

A screen that needs a file is terminal until one exists, so the corpus is a reach
mechanism rather than a convenience. That makes two things worth pinning: that the
real checked-in corpus is internally consistent, and that provisioning cannot be
turned into a way to push arbitrary host files to arbitrary device paths.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valordroid.media_corpus import (
    CATEGORY_DIRECTORIES,
    MediaCorpus,
    MediaCorpusError,
    MediaProvisioner,
)


class FakeResult:
    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.exit_code = exit_code


class FakeAdb:
    """Records what would have been sent to a device."""

    def __init__(self, *, digest: str | None = None) -> None:
        self.shell_calls: list[tuple[str, ...]] = []
        self.run_calls: list[list[str]] = []
        self.digest = digest

    def shell(self, *arguments, timeout=None, check=True):
        self.shell_calls.append(tuple(str(item) for item in arguments))
        if arguments and arguments[0] == "sha256sum" and self.digest:
            return FakeResult(f"{self.digest}  {arguments[1]}\n")
        return FakeResult()

    def run(self, arguments, timeout=None):
        self.run_calls.append([str(item) for item in arguments])
        return FakeResult()


class RealCorpusTest(unittest.TestCase):
    """The checked-in corpus must be usable exactly as committed."""

    def setUp(self) -> None:
        self.corpus = MediaCorpus.locate()

    def test_every_manifest_entry_matches_its_bytes(self) -> None:
        self.corpus.verify()

    def test_the_corpus_covers_the_formats_apps_actually_need(self) -> None:
        mimes = {item.mime for item in self.corpus.files}
        for required in (
            "application/pdf",
            "image/png",
            "image/jpeg",
            "audio/mpeg",
            "video/mp4",
            "text/plain",
            "text/csv",
            "application/zip",
            "application/epub+zip",
            "application/x-zim",
        ):
            self.assertIn(required, mimes, f"corpus lacks {required}")

    def test_every_destination_is_a_shared_media_folder(self) -> None:
        allowed = set(CATEGORY_DIRECTORIES.values()) | {"/sdcard/Download"}
        for item in self.corpus.files:
            directory = item.destination.rsplit("/", 1)[0]
            self.assertIn(directory, allowed, item.name)
            self.assertTrue(item.destination.startswith("/sdcard/"))

    def test_an_offline_archive_is_placed_where_such_readers_scan(self) -> None:
        """A per-file override exists because category placement is not enough."""

        archive = self.corpus.get("valor-fixture-wiki.zim")
        self.assertEqual(archive.destination, "/sdcard/Download/valor-fixture-wiki.zim")

    def test_the_catalogue_names_a_purpose_for_every_file(self) -> None:
        catalogue = self.corpus.catalogue()
        for item in self.corpus.files:
            self.assertIn(item.name, catalogue)
            self.assertTrue(item.purpose.strip(), item.name)


class CorpusRefusalTest(unittest.TestCase):
    """A malformed or unsafe corpus is refused rather than partly honoured."""

    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        (self.root / "files").mkdir()

    def _write(self, entries: list[dict], *, create: str | None = "a.txt") -> None:
        if create:
            (self.root / "files" / create).write_text("body", encoding="utf-8")
        (self.root / "manifest.json").write_text(
            json.dumps({"schema": 1, "corpus": "t", "files": entries}),
            encoding="utf-8",
        )

    @staticmethod
    def _entry(**overrides) -> dict:
        entry = {
            "name": "a.txt",
            "category": "document",
            "mime": "text/plain",
            "purpose": "a file",
            "sha256": "0" * 64,
            "size_bytes": 4,
        }
        entry.update(overrides)
        return entry

    def test_a_missing_file_is_refused(self) -> None:
        self._write([self._entry(name="absent.txt")])
        with self.assertRaises(MediaCorpusError):
            MediaCorpus(self.root)

    def test_a_traversing_name_is_refused(self) -> None:
        self._write([self._entry(name="../escape.txt")])
        with self.assertRaises(MediaCorpusError):
            MediaCorpus(self.root)

    def test_an_unknown_key_is_refused(self) -> None:
        self._write([self._entry(extra="surprise")])
        with self.assertRaises(MediaCorpusError):
            MediaCorpus(self.root)

    def test_a_directory_outside_shared_storage_is_refused(self) -> None:
        self._write([self._entry(directory="/data/local/tmp")])
        with self.assertRaises(MediaCorpusError):
            MediaCorpus(self.root)

    def test_a_wrong_digest_is_refused_on_verify(self) -> None:
        self._write([self._entry()])
        corpus = MediaCorpus(self.root)
        with self.assertRaises(MediaCorpusError):
            corpus.verify()


class ProvisioningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = MediaCorpus.locate()

    def test_provisioning_pushes_verifies_and_indexes(self) -> None:
        item = self.corpus.get("valor-fixture-document.pdf")
        adb = FakeAdb(digest=item.sha256)
        record = MediaProvisioner(adb, self.corpus).provision(item.name)

        self.assertEqual(record.destination, item.destination)
        self.assertTrue(record.scanned)
        self.assertEqual(
            adb.run_calls, [["push", str(item.path), item.destination]]
        )
        self.assertTrue(
            any(call[0] == "mkdir" for call in adb.shell_calls),
            "the destination folder must be created before the push",
        )
        self.assertTrue(
            any("MEDIA_SCANNER_SCAN_FILE" in " ".join(call) for call in adb.shell_calls),
            "a pushed file MediaStore cannot see is invisible to pickers",
        )

    def test_a_device_digest_mismatch_is_an_error_not_a_warning(self) -> None:
        adb = FakeAdb(digest="f" * 64)
        with self.assertRaises(MediaCorpusError):
            MediaProvisioner(adb, self.corpus).provision("valor-fixture-document.pdf")

    def test_an_unknown_file_cannot_be_requested(self) -> None:
        with self.assertRaises(MediaCorpusError):
            MediaProvisioner(FakeAdb(), self.corpus).provision("/etc/passwd")

    def test_provisioning_is_idempotent(self) -> None:
        adb = FakeAdb()
        provisioner = MediaProvisioner(adb, self.corpus)
        first = provisioner.provision("valor-fixture-image.png")
        second = provisioner.provision("valor-fixture-image.png")
        self.assertIs(first, second)
        self.assertEqual(len(adb.run_calls), 1, "the second request must not re-push")

    def test_staging_everything_reports_each_file_once(self) -> None:
        adb = FakeAdb()
        staged = MediaProvisioner(adb, self.corpus).provision_all()
        self.assertEqual(len(staged), len(self.corpus.files))
        self.assertEqual(
            len({item.name for item in staged}), len(self.corpus.files)
        )
        self.assertEqual(len(adb.run_calls), len(self.corpus.files))


if __name__ == "__main__":
    unittest.main()
