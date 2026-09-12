# Hi-Res-Kandidaten: SummerCart64-Hardware-Testplan

Stand: 2026-09-12. Beide Kandidaten sind emulatorverifiziert; was nur
echte Hardware beantworten kann, steht je Kandidat unten.

## Kandidat 1: OoT (USA) Rev 0 — 640x240p

Bauen:

    cd scripts/oot_hires
    echo 640p > flavor.txt
    python makeoot.py > cand_oot_640p_hw.z64

Flashen und prüfen:

1. Boot: N64-Logo vollbreit, danach Titelbildschirm (Link auf Epona,
   PRESS START) vollbreit. Kein Interlace-Flimmern (progressiv).
2. START → File Select vollbreit. File 1 öffnen → Intro/„Haus“:
   3D vollbreit, C-Buttons rechts oben (HUD-Slice 1), Herzen/Rupee-
   zähler noch links (bekannt).
3. Ein paar Minuten spielen (Feld betreten, Menü öffnen): Stabilität,
   Flackern, Farben.

Hardware-spezifische Risiken (Emulator kann das nicht zeigen):

- **gZBuffer-Overflow**: der 640-breite Scissor schreibt 0x25800 Bytes
  über den 320x240-Z-Buffer hinaus in den gGfxSPTaskOutputBuffer.
  Emulator: folgenlos. Hardware: möglich sind Grafikartefakte,
  Instabilität oder gar nichts. Beobachten und berichten.
- **Expansion Pak zwingend**: ohne 8 MB läuft der 4MB-Branch
  (sSysCfbEnd bleibt 0x80400000, der Spiel-Heap schrumpft um 0x4B000).
  Ohne Paket erwartet: Boot evtl. bis Titel, danach Heap-Mangel. Nicht
  als Fehler werten — der Patch targetiert 8 MB.
- **VI-Timing**: die statischen Tabellen (xScale 1:1, origin 1280)
  auf einem echten Fernseher/CRT: Bildlage, Rauschen am Rand.

## Kandidat 2: Majora's Mask (US) — 576x454p

Bauen:

    cd scripts/mm_hires
    python makemm.py > cand_mm_hires_hw.z64

Flashen und prüfen:

1. Boot → Intro-Cutscene: vollbild 576x454 progressiv (der native
   „Notebook“-Modus, den Nintendo für die Bombers-Herz-Wolke gebaut
   hat — auf CRT ohne Interlace geprüft werden).
2. Titel/File-Select: derzeit bekannt corrupt im Emulator (320er-UI im
   Hi-Res-Framebuffer) — Hardware-Ergebnis interessiert trotzdem.

Hardware-spezifische Risiken:

- Die Hi-Res-Buffer liegen fest bei 0x807EA800 (Expansion Pak) — ohne
  Paket wird der Modus Müll liefern. MM braucht ohnehin das Paket.
- 454-Zeilen + yScale-Deflicker-Mathe auf echtem CRT: Zeilenbild,
  Flimmern, Bildhöhe.

## Berichtsschablone

Je Kandidat: Boot ok? Titel ok? File-Select ok? Gameplay ok + stabil
nach N Minuten? Artefakte (wann, wo)? Bildlage/Flackern auf dem CRT?
Damit entscheiden wir, ob die Rezepte aus `EXPERIMENTAL` raus dürfen.
