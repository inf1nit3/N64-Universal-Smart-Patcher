"""The shipped OoT 640x240p recipe: the JSON and the BPS must agree.

This exists because they once did not. The recipe pair sat in the tree
for four days carrying the 2026-09-12 build - VI_X_SCALE 0x400, no
z-buffer relocation - while the builder had already been corrected
twice. Anyone installing the pair got exactly the ROM the hardware round
had rejected, and nothing in the repository said so: a stale binary
looks identical to a current one.

No ROM is needed to catch that. A BPS footer carries the CRC32 of the
source it was built from and of the target it produces, so pinning both
here ties the binary to a specific build. Rebuild the BPS and this test
fails until the four constants below and the recipe's ``outputs`` are
updated together - which is the point.
"""

import json
import os
import struct
import unittest
import zlib

from n64patcher import n64_core as core
from n64patcher import patchdb

_RECIPE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(core.__file__), "..", "..", "scripts", "oot_hires")
)
_RECIPE = os.path.join(_RECIPE_DIR, "recipe.oot-hires-exp.json")

# Zelda - Ocarina of Time (USA) Rev 0, the dump the recipe matches.
_CLEAN_CRC32 = 0xCD16C529
# The 640p --zrel build of 2026-09-16 (makeoot.py, VI_WIDTH 0x280 /
# VI_X_SCALE 0x100, gZBuffer at 0x8056A000).
_TARGET_CRC32 = 0x449C3C31
# ... and the N64 header checksums that build restamps into itself.
_TARGET_CRC1 = 0xEC701F37
_TARGET_CRC2 = 0x76D875D0


@unittest.skipUnless(os.path.isfile(_RECIPE), "source checkout only")
class TestOotRecipe(unittest.TestCase):
    def setUp(self):
        problems = []
        db = patchdb.load_patch_db([_RECIPE_DIR], on_error=problems.append)
        self.assertEqual(problems, [], "the recipe must load without complaints")
        entries = [e for slots in db.values() for e in slots]
        self.assertEqual(len(entries), 1)
        self.entry = entries[0]

    def test_entry_shape(self):
        self.assertEqual(self.entry["id"], "zelda-oot-usa-rev0-640x240p-exp")
        self.assertEqual(self.entry["flavor"], "640x240")
        self.assertIn("hires", self.entry["provides"])
        self.assertEqual(self.entry["operations"][0]["type"], "bps")

    def test_patch_file_resolves_next_to_its_recipe(self):
        """A user recipe ships its patch beside the JSON, not inside the
        installed package - that is what origin_dir is for."""
        path = core.get_flavor_patch(
            self.entry["crc1"], self.entry["crc2"], self.entry["flavor"], op_type="bps"
        )
        # get_flavor_patch reads the process-wide PATCH_DB, which this
        # test does not install into; resolve directly instead.
        candidate = os.path.join(self.entry["origin_dir"], self.entry["operations"][0]["file"])
        self.assertTrue(os.path.isfile(candidate), f"missing patch file: {candidate}")
        self.assertGreater(os.path.getsize(candidate), 0)
        if path is not None:  # only when the recipe is also installed
            self.assertEqual(os.path.realpath(path), os.path.realpath(candidate))

    def test_bps_is_the_build_the_recipe_describes(self):
        path = os.path.join(self.entry["origin_dir"], self.entry["operations"][0]["file"])
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertEqual(data[:4], b"BPS1")
        source_crc, target_crc, patch_crc = struct.unpack("<III", data[-12:])
        self.assertEqual(
            zlib.crc32(data[:-4]) & 0xFFFFFFFF, patch_crc, "the BPS is corrupt (footer self-check)"
        )
        self.assertEqual(
            source_crc, _CLEAN_CRC32, "the BPS was not built from the clean dump it claims to match"
        )
        self.assertEqual(
            target_crc,
            _TARGET_CRC32,
            "the BPS does not produce the pinned build - rebuild it with "
            "makeoot.py (640p + --zrel) and update this test AND the "
            "recipe's outputs together",
        )

    def test_declared_outputs_match_the_pinned_build(self):
        with open(_RECIPE, encoding="utf-8") as fh:
            raw = json.load(fh)["patches"][0]
        self.assertEqual(int(raw["outputs"]["crc1"], 16), _TARGET_CRC1)
        self.assertEqual(int(raw["outputs"]["crc2"], 16), _TARGET_CRC2)
        # The normalized entry carries the same pair as ints.
        self.assertEqual(self.entry["outputs"]["crc1"], _TARGET_CRC1)
        self.assertEqual(self.entry["outputs"]["crc2"], _TARGET_CRC2)

    def test_outputs_do_not_key_the_clean_dump(self):
        """A recipe whose output CRCs equal its match CRCs would make the
        patched ROM look like a fresh candidate for the same patch."""
        self.assertNotEqual(
            (self.entry["outputs"]["crc1"], self.entry["outputs"]["crc2"]),
            (self.entry["crc1"], self.entry["crc2"]),
        )
