# Hi-Res-Kandidaten: Hardware-Testplan (SC64 / EverDrive)

Stand: 2026-09-16, vor Runde 3.

**Runde 1** (schwarzes Bild, mupen läuft): CRCs aller Kandidaten
verifiziert — der Boot-Fehler lag nicht an der CRC. Zwei dokumentierte
Risiken blieben: 8MB-Zwang und der gZBuffer-Overflow (640-breiter
Scissor schrieb 0x25800 Bytes über den 320x240-Z-Buffer hinaus in den
gGfxSPTaskOutputBuffer — mupen überlebte, Hardware nicht). `--zrel`
behebt den Overflow.

**Runde 2** (2026-09-13): Die Leiter hat funktioniert. L1 Baseline ✓,
L2 240pdbg ✓ — Cart, Console und alle Gameplay-Patches sind sauber, der
Ausfall war 640p-spezifisch. Ursache gefunden: `VI_X_SCALE` war auf
`0x400` gesetzt. Das Register ist der horizontale Upscale-Faktor
(Stock 0x200 = 2x für 320 breit), ein 640-breiter 1:1-Framebuffer
braucht `0x100`. Die VI des Emulators modelliert den Scaler kaum,
deshalb sahen alle Screenshots perfekt aus.

**Runde 3** (offen): Der korrigierte Build war nie auf der Console. Er
trägt beide Fixes — xScale `0x100` und die Z-Buffer-Relocation.

## Die Leiter für Runde 3 (OoT, nacheinander flashen)

Alle Dateien liegen fertig gebaut in `scripts/oot_hires/` (CRC- und
xScale-geprüft, Stand 2026-09-16):

| Stufe | Datei | Was sie zeigt |
|---|---|---|
| L1 | `cand_oot_baseline.z64` (= clean.z64, unmodifiziert) | Cart, Console, CIC/CRC, TV-Sync im Stock-Modus — in Runde 2 bereits ✓ |
| L2 | `cand_oot_640p_zrel_r3.z64` | **Der eigentliche Test**: 640p mit xScale-Fix + Z-Buffer-Fix, normales Spiel (kein Auto-Chain), braucht 8MB |
| L3 | `cand_oot_640pdbg_zrel.z64` | Derselbe Build mit Auto-Chain — nur falls L2 am File-Select hängt und du unbeaufsichtigt bis ins Gameplay willst |

Interpretation:

- **L2 läuft** → der xScale war der Killer. Das Rezept kann aus
  `EXPERIMENTAL` raus, sobald auch die Z-Relocation über mehrere Szenen
  sauber bleibt (Pausenmenü, Sonnenblendung, Raumwechsel).
- **L2 schwarz, aber Logo + Sound** → Signal läuft, TV synct nicht:
  640 aktive Pixel auf Composite/RGB prüfen, idealerweise CRT. Dann
  bleibt das 240p-Signal der Fallback.
- **L2 kein Logo** → Cart/Setup-Ebene, nicht der Patch.
- **L2 bootet, aber Grafikmüll in einer bestimmten Szene** → die
  Z-Buffer-Relocation, nicht die VI-Tabelle. Notieren: welche Szene,
  ab wann.
- **Expansion Pak** ist Pflicht. Ohne 8MB ist L2 by design nicht
  bootfähig.

Achtung bei den Altdateien im Ordner: `cand_oot_640p_zrel.z64` (ohne
`_r3`) trägt trotz des Namens die Debug-Edits in `ovl_file_choose` —
die liegen jenseits des ersten Megabytes und damit außerhalb des
CIC-Prüffensters, weshalb die CRC-Paare identisch sind. Für Runde 3
`_r3` nehmen.

Beim Boot auf **Nintendo-Logo und Sound achten**: Logo kommt aus dem
Cart-IPL3 und erscheint immer, wenn die Cart korrekt gelesen wird.
Logo da + danach schwarz = Spiel crasht nach Video-Init oder der TV
verliert Sync; nie ein Logo = Cart/Setup-Ebene. Logo-Musik/Title-Musik
hörbar mit schwarzem Bild = Spiel läuft, TV synct nicht.

## Kandidat 2: Majora's Mask (US) — 576x454p

Build (reproduzierbar verifiziert, byte-identisch):

    cd scripts/mm_hires
    python makemm.py --dbg > cand_mm_dbg.z64

1. Boot → Intro-Cutscene: vollbild 576x454 progressiv (der native
   „Notebook"-Modus, den Nintendo für die Bombers-Herz-Wolke gebaut
   hat — auf CRT ohne Interlace geprüft werden).
2. Titel/File-Select: derzeit bekannt corrupt im Emulator (320er-UI im
   Hi-Res-Framebuffer) — Hardware-Ergebnis interessiert trotzdem.

Hardware-spezifische Risiken:

- **Expansion Pak zwingend**: die Hi-Res-Buffer liegen fest bei
  0x807EA800. Ohne 8MB liefert der Modus Müll (MM braucht ohnehin das
  Paket) — schwarzes Bild auf einer 4MB-Console ist damit zu erklären.
- 454-Zeilen + yScale-Deflicker-Mathe auf echtem CRT: Zeilenbild,
  Flimmern, Bildhöhe.

## Berichtsschablone

Je Stufe: Logo ja/nein? Sound ja/nein? Bild ja/nein? Expansion Pak
ja/nein? Cart-Typ? Artefakte (wann, wo)? Bildlage/Flackern auf dem CRT?
Damit entscheiden wir, ob die Rezepte aus `EXPERIMENTAL` raus dürfen.
