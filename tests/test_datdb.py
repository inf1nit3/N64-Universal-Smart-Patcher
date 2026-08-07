"""Unit tests for No-Intro / Redump DAT lookup.

DAT files key on hashes of the *file*, unrelated to the N64 boot checksums
the patch database matches on. A ROM can be a verified dump with no patch
recipe, and vice versa - these tests keep the two straight.
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from unittest import mock

from n64patcher import datdb
from n64patcher import n64_core as core
from tests.test_n64_core import make_synthetic_rom


def write_dat(directory, name="test.dat", games=(), header="Test DAT",
              version="1.0", root_tag="datafile"):
    root = ET.Element(root_tag)
    hdr = ET.SubElement(root, "header")
    ET.SubElement(hdr, "name").text = header
    ET.SubElement(hdr, "version").text = version
    for game_name, attrs in games:
        g = ET.SubElement(root, "game", name=game_name)
        ET.SubElement(g, "rom", **attrs)
    path = os.path.join(directory, name)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def hashes_of(data):
    import hashlib
    return {
        "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
        "md5": hashlib.md5(data).hexdigest().upper(),
        "sha1": hashlib.sha1(data).hexdigest().upper(),
    }


class DatTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Never touch the real user cache from a test run.
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        patcher = mock.patch.object(datdb, "CACHE_DIR", self.cache.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        # ...nor the real ~/.n64patcher/dats.
        p2 = mock.patch.object(datdb, "USER_DAT_DIR",
                               os.path.join(self.tmp.name, "no_such_dir"))
        p2.start()
        self.addCleanup(p2.stop)

    def _rom(self, name="game.z64", **kw):
        data = make_synthetic_rom(**kw)
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path, data


class TestFileHashes(DatTestBase):
    def test_single_pass_matches_reference(self):
        path, data = self._rom()
        got = datdb.file_hashes(path)
        self.assertEqual(got, hashes_of(data))

    def test_small_chunks_give_the_same_result(self):
        """CRC32 is chained across chunks; a wrong seed would show here."""
        path, data = self._rom()
        self.assertEqual(datdb.file_hashes(path, chunk_size=7), hashes_of(data))

    def test_core_hash_file_still_returns_md5_sha1(self):
        path, data = self._rom()
        md5, sha1 = core._hash_file(path)
        self.assertEqual((md5, sha1), (hashes_of(data)["md5"], hashes_of(data)["sha1"]))


class TestParseDat(DatTestBase):
    def test_parses_games_and_header(self):
        p = write_dat(self.tmp.name, games=[
            ("Super Mario 64 (USA)", {"name": "smb.z64", "size": "8388608",
                                      "crc": "4EAA3D0E", "md5": "AA" * 16,
                                      "sha1": "BB" * 20}),
        ])
        out = datdb.parse_dat(p)
        self.assertEqual(out["name"], "Test DAT")
        self.assertEqual(len(out["entries"]), 1)
        self.assertEqual(out["entries"][0]["game"], "Super Mario 64 (USA)")
        self.assertEqual(out["entries"][0]["size"], 8388608)

    def test_hashes_are_uppercased(self):
        p = write_dat(self.tmp.name, games=[
            ("G", {"name": "g.z64", "crc": "abcdef01", "md5": "aa" * 16,
                   "sha1": "bb" * 20}),
        ])
        entry = datdb.parse_dat(p)["entries"][0]
        self.assertEqual(entry["crc32"], "ABCDEF01")
        self.assertEqual(entry["md5"], "AA" * 16)

    def test_rom_without_any_hash_skipped(self):
        p = write_dat(self.tmp.name, games=[
            ("NoHash", {"name": "a.z64", "size": "1"}),
            ("HasHash", {"name": "b.z64", "crc": "11111111"}),
        ])
        entries = datdb.parse_dat(p)["entries"]
        self.assertEqual([e["game"] for e in entries], ["HasHash"])

    def test_wrong_root_element_rejected(self):
        p = write_dat(self.tmp.name, root_tag="mame",
                      games=[("G", {"name": "g", "crc": "1"})])
        with self.assertRaises(datdb.DatError) as ctx:
            datdb.parse_dat(p)
        self.assertIn("expected <datafile>", str(ctx.exception))

    def test_empty_dat_rejected(self):
        p = write_dat(self.tmp.name, games=[])
        with self.assertRaises(datdb.DatError):
            datdb.parse_dat(p)

    def test_malformed_xml_rejected(self):
        p = os.path.join(self.tmp.name, "bad.dat")
        with open(p, "w", encoding="utf-8") as f:
            f.write("<datafile><game>")
        with self.assertRaises(datdb.DatError):
            datdb.parse_dat(p)


class TestDatIndex(DatTestBase):
    def _index(self, data, game="A Game"):
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[
            (game, {"name": "g.z64", "size": str(len(data)), **{
                "crc": h["crc32"], "md5": h["md5"], "sha1": h["sha1"]}}),
        ])
        return datdb.load_dats([p]), h

    def test_lookup_by_each_hash(self):
        _, data = self._rom()
        index, h = self._index(data)
        for key in ("crc32", "md5", "sha1"):
            self.assertIsNotNone(index.lookup(**{key: h[key]}), key)

    def test_lookup_is_case_insensitive(self):
        _, data = self._rom()
        index, h = self._index(data)
        self.assertIsNotNone(index.lookup(sha1=h["sha1"].lower()))

    def test_unknown_hash_returns_none(self):
        _, data = self._rom()
        index, _ = self._index(data)
        self.assertIsNone(index.lookup(sha1="00" * 20))

    def test_sha1_wins_over_a_colliding_crc32(self):
        """CRC32 collides in 4 billion; the strongest hash must decide."""
        _, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[
            ("Right", {"name": "r.z64", "sha1": h["sha1"]}),
            ("Wrong", {"name": "w.z64", "crc": h["crc32"]}),
        ])
        index = datdb.load_dats([p])
        hit = index.lookup(crc32=h["crc32"], sha1=h["sha1"])
        self.assertEqual(hit["game"], "Right")

    def test_empty_index_is_falsy(self):
        self.assertFalse(datdb.DatIndex())

    def test_missing_file_reported_not_raised(self):
        problems = []
        index = datdb.load_dats([os.path.join(self.tmp.name, "nope.dat")],
                                on_error=problems.append)
        self.assertFalse(index)
        self.assertTrue(any("not found" in p for p in problems))

    def test_bad_dat_does_not_stop_a_good_one(self):
        _, data = self._rom()
        h = hashes_of(data)
        good = write_dat(self.tmp.name, "good.dat",
                         games=[("G", {"name": "g", "sha1": h["sha1"]})])
        bad = os.path.join(self.tmp.name, "bad.dat")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<datafile><broken")
        problems = []
        index = datdb.load_dats([bad, good], on_error=problems.append)
        self.assertIsNotNone(index.lookup(sha1=h["sha1"]))
        self.assertEqual(len(problems), 1)


class TestDatCache(DatTestBase):
    def test_cache_is_used_and_gives_the_same_result(self):
        _, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("G", {"name": "g", "sha1": h["sha1"]})])

        first = datdb.load_dats([p])
        self.assertEqual(len(os.listdir(self.cache.name)), 1)
        with mock.patch.object(datdb, "parse_dat",
                               side_effect=AssertionError("should not re-parse")):
            second = datdb.load_dats([p])
        self.assertEqual(len(first), len(second))
        self.assertIsNotNone(second.lookup(sha1=h["sha1"]))

    def test_editing_the_dat_invalidates_the_cache(self):
        _, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("Old", {"name": "g", "sha1": h["sha1"]})])
        datdb.load_dats([p])

        write_dat(self.tmp.name, os.path.basename(p),
                  games=[("New", {"name": "g", "sha1": h["sha1"]})])
        os.utime(p, (0, 0))  # force a different mtime
        index = datdb.load_dats([p])
        self.assertEqual(index.lookup(sha1=h["sha1"])["game"], "New")

    def test_corrupt_cache_falls_back_to_parsing(self):
        _, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("G", {"name": "g", "sha1": h["sha1"]})])
        datdb.load_dats([p])
        for name in os.listdir(self.cache.name):
            with open(os.path.join(self.cache.name, name), "w") as f:
                f.write("{garbage")
        index = datdb.load_dats([p])
        self.assertIsNotNone(index.lookup(sha1=h["sha1"]))


class TestInspectionIntegration(DatTestBase):
    def test_verified_dump_is_reported(self):
        path, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[
            ("Test Game (USA)", {"name": "g.z64", "sha1": h["sha1"]})])
        index = datdb.load_dats([p])
        info = core.inspect_rom_details(path, dat=index)
        self.assertEqual(info["dump_status"], "verified")
        self.assertEqual(info["dump_name"], "Test Game (USA)")

    def test_unlisted_dump_is_flagged(self):
        path, _ = self._rom()
        other = write_dat(self.tmp.name, games=[
            ("Something Else", {"name": "x.z64", "sha1": "00" * 20})])
        info = core.inspect_rom_details(path, dat=datdb.load_dats([other]))
        self.assertEqual(info["dump_status"], "unknown")
        self.assertEqual(info["dump_name"], "")

    def test_no_dat_means_no_status_and_no_hashing(self):
        """Without a DAT the lookup must not silently cost a full read."""
        path, _ = self._rom()
        with mock.patch.object(datdb, "file_hashes",
                               side_effect=AssertionError("hashed without need")):
            info = core.inspect_rom_details(path)
        self.assertEqual(info["dump_status"], "")

    def test_dat_lookup_supplies_hashes_without_with_hashes(self):
        path, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("G", {"name": "g", "sha1": h["sha1"]})])
        info = core.inspect_rom_details(path, dat=datdb.load_dats([p]))
        self.assertEqual(info["sha1"], h["sha1"])
        self.assertEqual(info["crc32"], h["crc32"])

    def test_export_carries_dump_columns(self):
        path, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("G", {"name": "g", "sha1": h["sha1"]})])
        info = core.inspect_rom_details(path, dat=datdb.load_dats([p]))
        out = os.path.join(self.tmp.name, "r.csv")
        core.export_report([info], out)
        with open(out, encoding="utf-8") as f:
            header = f.readline()
        for col in ("dump_status", "dump_name", "crc32"):
            self.assertIn(col, header)

    def test_dump_status_is_independent_of_patch_support(self):
        """A verified dump need not have a patch recipe, and vice versa."""
        path, data = self._rom()
        h = hashes_of(data)
        p = write_dat(self.tmp.name, games=[("G", {"name": "g", "sha1": h["sha1"]})])
        info = core.inspect_rom_details(path, dat=datdb.load_dats([p]))
        self.assertEqual(info["dump_status"], "verified")
        self.assertEqual(info["hires_support"], core.HIRES_UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
