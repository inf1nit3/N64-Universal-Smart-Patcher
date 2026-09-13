# Hi-Res-Kandidaten: Hardware-Testplan (SC64 / EverDrive)

Stand: 2026-09-13, nach dem ersten Hardware-Bericht „bootet nicht"
(schwarzes Bild, mupen läuft). Die CRCs aller Kandidaten sind verifiziert
(Algorithmus reproduziert an beiden Baseroms exakt Nintendos Header-Werte,
Kandidaten selbstkonsistent gestempelt) — der Boot-Fehler lag nicht an der
CRC. Der gemeldete 640p-Kandidat hatte zwei dokumentierte Hardware-Risiken:
8MB-Zwang (Framebuffer-Paar im Expansion-Bereich) und den gZBuffer-Overflow
(640-breiter Scissor schrieb 0x25800 Bytes über den 320x240-Z-Buffer hinaus
in den gGfxSPTaskOutputBuffer — mupen überlebte, Hardware nicht). Der neue
Build `640pdbg-zrel` behebt den Overflow (Z-Buffer-Relocation, siehe
scripts/oot_hires/README.md).

## Die Bisect-Leiter (OoT, nacheinander flashen, Reihenfolge beachten)

Ziel: mit jedem Schritt eingrenzen, wo es klemmt — Cart/Setup, unsere
Gameplay-Patches oder die 640-breite Video-Ausgabe. Alle vier liegen in
`scripts/oot_hires/` fertig gebaut (CRC-geprüft) und müssen nur noch auf
den Stick, wenn er wieder gemountet ist:

| Stufe | Datei | Was sie zeigt |
|---|---|---|
| L1 | `cand_oot_baseline.z64` (= clean.z64, unmodifiziert) | Cart, Console, CIC/CRC, TV-Sync im Stock-Modus |
| L2 | `cand_oot_240pdbg.z64` | Unsere Gameplay-/Chain-Patches auf **Standard-320-Signal**, passt in 4MB |
| L3 | `cand_oot_640pdbg_zrel.z64` | 640p **mit Z-Buffer-Fix**, braucht 8MB |
| L4 | `cand_oot_640pdbg.z64` (alter Stand) | 640p ohne Z-Fix — nur zum Vergleich, ist der bekannte Ausfall |

Interpretation:

- **L1 bootet nicht** → Cart/Dateisystem/Console-Problem (nicht unsere
  Patches): Datei sichtbar? Format .z64? Andere ROM (240pSuite) startet?
- **L1 ja, L2 schwarz** → unsere Code-Patches crashen auf Hardware
  (unerwartet — mupen läuft, Standard-Video). Danach: Auto-Chain-
  Varianten ohne dbg bauen und erneut testen.
- **L2 ja, L3 schwarz** → 640p-spezifisch: (a) hat die Console ein
  **Expansion Pak**? Ohne 8MB ist L3 nicht bootfähig (by design).
  (b) Mit 8MB: TV akzeptiert die 640-Active-Pixel-Zeile nicht
  (Signal läuft, TV zeigt schwarz — auf Composite/RGB prüfen,
  idealerweise CRT). Dann: 240p-Signal bleibt der Fallback, 640p nur
  auf syncing-fähigen TVs.
- **L3 ja, L4 schwarz** → der Z-Buffer-Overflow war der Boot-Killer;
  Z-Fix verifiziert, Fall abgeschlossen.

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
