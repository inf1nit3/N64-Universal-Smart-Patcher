"""Unit tests for the declarative patch database.

The point of the database is that the supported-dump list can grow without
a code change, so these cover both halves of that promise: a well-formed
community file is picked up, and a malformed one is rejected loudly without
taking the rest of the database down.
"""
import json
import os
import tempfile
import unittest

from n64patcher import n64_core as core
from n64patcher import patchdb

VALID = {
    "id": "example-640x480",
    "name": "Example Game (USA) - 640x480",
    "source": "test",
    "match": {"crc1": "AABBCCDD", "crc2": "11223344"},
    "provides": ["hires"],
    "operations": [{"type": "xdelta", "file": "example.xdelta"}],
}


def write_db(directory, name, patches, schema_version=patchdb.SCHEMA_VERSION):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": schema_version, "patches": patches}, f)
    return path


class TestValidateEntry(unittest.TestCase):
    def _invalid(self, **overrides):
        entry = dict(VALID)
        entry.update(overrides)
        with self.assertRaises(patchdb.PatchDBError) as ctx:
            patchdb.validate_entry(entry)
        return str(ctx.exception)

    def test_valid_entry_normalizes_crcs_to_ints(self):
        out = patchdb.validate_entry(VALID)
        self.assertEqual(out["crc1"], 0xAABBCCDD)
        self.assertEqual(out["crc2"], 0x11223344)

    def test_accepts_integer_crcs_too(self):
        entry = dict(VALID, match={"crc1": 0xAABBCCDD, "crc2": 0x11223344})
        self.assertEqual(patchdb.validate_entry(entry)["crc1"], 0xAABBCCDD)

    def test_missing_id_rejected(self):
        self.assertIn("id", self._invalid(id=""))

    def test_missing_match_rejected(self):
        self.assertIn("match", self._invalid(match=None))

    def test_partial_match_rejected(self):
        self.assertIn("crc1 and crc2", self._invalid(match={"crc1": "AABBCCDD"}))

    def test_non_hex_crc_rejected(self):
        self.assertIn("hex", self._invalid(match={"crc1": "zzz", "crc2": "1"}))

    def test_empty_operations_rejected(self):
        self.assertIn("operations", self._invalid(operations=[]))

    def test_unknown_operation_rejects_whole_entry(self):
        """Half-applying a recipe would leave a corrupt ROM, so an unknown
        step invalidates the entry rather than being skipped."""
        msg = self._invalid(operations=[{"type": "xdelta", "file": "a.xdelta"},
                                        {"type": "reticulate_splines"}])
        self.assertIn("unknown type", msg)

    def test_xdelta_without_file_rejected(self):
        self.assertIn("file", self._invalid(operations=[{"type": "xdelta"}]))

    def test_poke_requires_offset_and_hex_bytes(self):
        self.assertIn("offset", self._invalid(
            operations=[{"type": "poke", "bytes": "00"}]))
        self.assertIn("hex", self._invalid(
            operations=[{"type": "poke", "offset": 0, "bytes": "nothex"}]))

    def test_poke_accepted_when_wellformed(self):
        entry = dict(VALID, operations=[
            {"type": "poke", "offset": 0x1000, "bytes": "30422000"}])
        self.assertEqual(patchdb.validate_entry(entry)["operations"][0]["offset"],
                         0x1000)

    def test_unknown_capability_rejected(self):
        self.assertIn("unknown capability", self._invalid(provides=["raytracing"]))


class TestLoadPatchDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_loads_a_directory(self):
        write_db(self.tmp.name, "a.json", [VALID])
        db = patchdb.load_patch_db([self.tmp.name])
        self.assertIn((0xAABBCCDD, 0x11223344), db)

    def test_bad_entry_skipped_good_one_kept(self):
        """A broken community file must not stop the tool from patching
        the dumps it already understands."""
        bad = dict(VALID, id="bad", match={"crc1": "1", "crc2": "2"},
                   operations=[{"type": "nope"}])
        write_db(self.tmp.name, "a.json", [VALID, bad])
        problems = []
        db = patchdb.load_patch_db([self.tmp.name], on_error=problems.append)
        self.assertEqual(len(db), 1)
        self.assertTrue(any("unknown type" in p for p in problems), problems)

    def test_unsupported_schema_version_skips_file(self):
        write_db(self.tmp.name, "future.json", [VALID], schema_version=999)
        problems = []
        db = patchdb.load_patch_db([self.tmp.name], on_error=problems.append)
        self.assertEqual(db, {})
        self.assertTrue(any("schema_version" in p for p in problems), problems)

    def test_malformed_json_reported_not_raised(self):
        with open(os.path.join(self.tmp.name, "broken.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        problems = []
        db = patchdb.load_patch_db([self.tmp.name], on_error=problems.append)
        self.assertEqual(db, {})
        self.assertEqual(len(problems), 1)

    def test_later_directory_overrides_earlier(self):
        """A user entry can replace a bundled one for the same dump."""
        d2 = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d2, ignore_errors=True))
        write_db(self.tmp.name, "a.json", [VALID])
        write_db(d2, "b.json", [dict(VALID, id="override", name="Override")])
        db = patchdb.load_patch_db([self.tmp.name, d2])
        self.assertEqual(db[(0xAABBCCDD, 0x11223344)]["id"], "override")

    def test_missing_directory_is_not_an_error(self):
        db = patchdb.load_patch_db([os.path.join(self.tmp.name, "nope")])
        self.assertEqual(db, {})

    def test_non_recipe_files_ignored(self):
        with open(os.path.join(self.tmp.name, "notes.txt"), "w") as f:
            f.write("hello")
        problems = []
        patchdb.load_patch_db([self.tmp.name], on_error=problems.append)
        self.assertEqual(problems, [])

    def test_entries_providing_filters_by_capability(self):
        write_db(self.tmp.name, "a.json", [
            VALID,
            dict(VALID, id="other", match={"crc1": "1", "crc2": "2"},
                 provides=["noaa"]),
        ])
        db = patchdb.load_patch_db([self.tmp.name])
        self.assertEqual([e["id"] for e in patchdb.entries_providing(db, "hires")],
                         ["example-640x480"])


class TestShippedDatabase(unittest.TestCase):
    """Guards the bundled file, which is what users actually get."""

    def test_bundled_db_loads_clean(self):
        problems = []
        db = patchdb.load_patch_db([patchdb._bundled_patch_dir()],
                                   on_error=problems.append)
        self.assertEqual(problems, [])
        self.assertEqual(len(db), 8)

    def test_core_exposes_the_db_without_errors(self):
        self.assertEqual(core.patch_db_problems(), [])
        self.assertEqual(len(core.PATCH_DB), 8)

    def test_every_bundled_xdelta_file_exists(self):
        """A recipe naming a missing delta would fail only at patch time."""
        missing = []
        for entry in core.PATCH_DB.values():
            for op in entry["operations"]:
                if op["type"] == "xdelta":
                    path = os.path.join(core.HIRES_PATCHES_DIR, op["file"])
                    if not os.path.isfile(path):
                        missing.append(f"{entry['id']}: {op['file']}")
        self.assertEqual(missing, [])

    def test_compat_view_matches_the_db(self):
        self.assertEqual(set(core.SUBDRAG_PATCHES), set(core.PATCH_DB))

    def test_find_patch_entry_accepts_hex_and_int(self):
        key = next(iter(core.PATCH_DB))
        self.assertIsNotNone(core.find_patch_entry(*key))
        self.assertIsNotNone(core.find_patch_entry(f"{key[0]:08X}", f"{key[1]:08X}"))
        self.assertIsNone(core.find_patch_entry("bogus", "bogus"))

    def test_ids_are_unique(self):
        ids = [e["id"] for e in core.PATCH_DB.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_describe_mentions_every_entry(self):
        text = patchdb.describe(core.PATCH_DB)
        for entry in core.PATCH_DB.values():
            self.assertIn(entry["id"], text)


if __name__ == "__main__":
    unittest.main()
