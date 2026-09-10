# Patch database

Patch recipes live in data files, not in Python. Adding support for a dump
means dropping a `.json` file in a directory — no fork, no rebuild, no
release.

## Where files are read from

Later directories win, so a personal entry can override a bundled one for
the same dump.

| Order | Path | Use |
|---|---|---|
| 1 | `<package>/patches/*.json` | Shipped with the tool |
| 2 | `~/.n64patcher/patches/*.json` | Yours; survives reinstalls |
| 3 | `$N64PATCHER_PATCHES/*.json` | Explicit override (testing, CI) |

List what is loaded and from where:

```bash
n64patcher --list-patches
```

`.yaml` / `.yml` are also read **if** PyYAML happens to be installed. JSON is
canonical because the engine installs with zero third-party dependencies and
that is worth keeping — never make a recipe that only works with PyYAML
present.

## Format

```json
{
  "schema_version": 1,
  "patches": [
    {
      "id": "super-mario-64-usa-640x480",
      "name": "Super Mario 64 (USA) - 640x480 No-AA",
      "source": "SubDrag",
      "notes": "Optional free text shown by --list-patches",
      "match": { "crc1": "635A2BFF", "crc2": "8B022326" },
      "provides": ["hires", "noaa"],
      "operations": [
        { "type": "xdelta", "file": "Super Mario 64 (U) [!] 640 x 480i No AA[SubDrag].xdelta" }
      ]
    }
  ]
}
```

### `match`

`crc1` and `crc2` are the boot checksums at header `0x10`/`0x14`, as hex
strings (integers also accepted). **Both are required.**

This is the key because a delta only applies to the exact dump it was built
against. Matching on title was tried and failed twice: the internal titles
are `Banjo-Kazooie` (hyphen) and `Forsaken` (no "64"), so those two games
silently never got their patch — and title matching cannot tell revisions
apart, which matters because the Banjo delta targets Rev A only.

Read a ROM's checksums with:

```bash
n64patcher "your rom.z64" --inspect-only
```

### `provides`

What the recipe delivers. Known values: `hires`, `noaa`, `nodither`,
`widescreen`, `misc`. An unknown value rejects the entry.

`hires` is the one with teeth: it is what makes the 640x480 option available
for that dump. **Do not claim it unless you have confirmed the result renders
correctly on hardware.** Widening VI mode tables alone does not work — see
the hi-res section in the README.

### `operations`

Exactly **one** operation per entry - the pipeline applies one patch per
recipe, and entries with more are rejected up front rather than silently
half-applied (chain additional steps as separate entries). Unknown types
reject the **whole entry** rather than being skipped, because applying
some steps and not others leaves a corrupt ROM.

| Type | Fields | Meaning |
|---|---|---|
| `xdelta` | `file` | Apply a delta from the bundled patches directory |
| `bps` | `file` | Apply a BPS patch from the bundled patches directory (source and target CRC32 verified) |
| `poke` | `offset` (int), `bytes` (hex string) | Write bytes at a ROM offset |

### `flavor`

Optional, default `"640x480"`. A dump may offer alternative hi-res builds:
same `match`, different flavor, selected by the caller (`--hires` means the
default flavor, `--h2x` means `640x240`). Two entries with the same dump
**and** the same flavor is an error - the later one replaces the earlier
with a problem reported, exactly like the directory override.

### `outputs`

Optional: `{ "crc1": "...", "crc2": "..." }` - the boot checksums of the
ROM this recipe *produces*. Declaring them lets the inspector recognise a
already-patched ROM as `native` with the recipe's name, instead of
misclassifying it (an H2X image keeps 320-space VI tables for some modes
and would otherwise read as mixed-resolution `unsupported`).

## Adding an entry

1. Get the checksums: `n64patcher "rom.z64" --inspect-only`
2. Put your `.xdelta` where the tool can find it (alongside the bundled ones)
3. Write `~/.n64patcher/patches/mine.json` using the shape above
4. Check it loaded: `n64patcher --list-patches`
5. Confirm on hardware before claiming `hires`

A malformed entry is reported by id and skipped; the rest of the database
still loads. `--list-patches` exits non-zero when anything failed to parse.

## Versioning

`schema_version` must be `1`. A file declaring anything else is skipped with
a warning rather than guessed at, so an older tool will not half-read a newer
format.
