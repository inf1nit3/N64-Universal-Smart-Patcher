# mm_hires — The Legend of Zelda: Majora's Mask (US), native hi-res

Development workspace for the in-project MM hi-res build. MM ships its
own hi-res machinery (added for the Bombers Notebook screen): statically
allocated 576x454 framebuffers and z-buffer in the Expansion Pak area
plus a progressive "notebook" VI mode built at runtime by
ViMode_Configure. Retail only enters it while the notebook is open —
the patch makes it permanent.

## Status

- **Switch**: `makemm.py` flips Play_Draw's `sBombersNotebookOpen`
  branch (beq->bne) so the first Play_Draw calls
  `SysCfb_SetHiResMode()` and stays hi-res.
- **Viewport**: MM's View_Init hardcodes a 320x240 viewport (same
  OoT-family issue). Patched to 576x454 so the 3D fills the framebuffer.
  Before the switch the scissor (gScreenWidth=320) still bounds
  rendering, so boot states are unaffected.
- **Verified in mupen64plus**: the intro cutscene renders full-screen
  576x454 progressive (frames 900+). ROM CRCs unchanged (code segment
  outside the CIC window).
- **Auto-input (--dbg)**: the file-select overlay's press&(START|A)
  andi checks are forced (4 sites, two handler families at +0x1B8/0x1D4
  and +0x8B4/0x8D0), and the nor-folded START/A bit tests in
  FileSelect_UpdateMainMenu (code+0x83B0/0x83C8) and
  FileSelect_ConfirmFile (code+0x0E848/0x0E860) are forced nonzero -
  so UpdateMainMenu takes the occupied branch (File 1, the real save
  inside mupen's .fla) and ConfirmFile answers YES, chaining
  SM_FADE_OUT -> FileSelect_LoadGame. Every A-check in the code
  segment (37 andi sites) is additionally ori-forced so dialogs and
  cutscene prompts auto-advance (a 107-byte ASCII note-table donates
  the recompression bytes; ocarina note rendering degrades in this
  throwaway build).
- **Loop status**: with all forces the flow now reaches and idles in a
  live Play state at the Clock Tower (animated sky; frames 20000-45000
  nearly static = camera idle). The gameplay HUD does not appear
  because gSaveContext.hudVisibility starts at none/idle after
  LoadGame and the z_parameter.c visibility state machine only fades
  the HUD in through its own transition chain (z_parameter.c:1837+,
  4080+). Remaining: force that visibility chain (or patch Interface
  alphas) - after that the unattended run ends in visible day-1
  gameplay. NOTE: mupen rewrites the .fla on exit, so re-run the
  seed (or restore .fla.bak) before every verification run.
- **Nameset forces (latest)**: the name-entry keyboard's nor-folded
  START/A bit tests (overlay +0x55D0/+0x5920) are forced and the
  validName gate at +0x5B80 NOPed, so an empty name registers. The
  nor-folding (nor reg, press, at with at=~mask instead of andi) is
  why andi-scans keep missing MM input checks - always disassemble
  with capstone.
- Mapping aids: overlay vrom 0xC7E4F0 (yaz0, rom 0xB28DA0..0xB326E0,
  decompressed 0x10E70); FileSelectState fields live at state+0x20000 +
  regs-style offsets (buttonIndex 0x4480, configMode 0x4486, selectMode
  0x448C, kbdX 0x4518, kbdY 0x451A); sSelectModeUpdateFuncs = 8 words at
  file+0x1076C (last entry = FileSelect_LoadGame), sConfigModeUpdateFuncs
  = 45 words at file+0x10558.

## Facts worth keeping

- code segment: vrom 0xB3C000, yaz0, rom 0xA684D0..0xB26590,
  decompressed 0x13E4E0 bytes, code vram base **0x800A5AC0**
  (derived: SysCfb_Init's `jal SysCfb_SetLoResMode` at code+0xD2EFC
  targets 0x80178750; boot vram 0x80080060 confirmed via the
  osViModeNtscLan1 table at VA 0x80097FC0).
- SysCfb_SetLoResMode = code+0xD2C90, SysCfb_SetHiResMode =
  code+0xD2D58, SysCfb_Init = code+0xD2EB8; the Play_Draw switch pair
  sits at code+0x0C333C (SetHiResMode) / code+0x0C33C8 (SetLoResMode).
- SetHiResMode writes gCfbWidth=576 / gCfbHeight=454 /
  gCfbLeftAdjust=30 (bss 0x8020BBCC+) and selects the notebook
  ViMode; the hi-res buffers live at 0x807EA800 (Expansion Pak).
- The recompressed code segment fits its ROM slot with ~2 bytes to
  spare — edits must stay minimal.

## Rebuilding

    python makemm.py > cand_mm_hires.z64
    mupen64plus --testshots 500,900,1500,2400,3600 --sshotdir /tmp/shots cand_mm_hires.z64

Reads the clean dump from `../work/mm/baseroms/n64-us/baserom.z64`.
