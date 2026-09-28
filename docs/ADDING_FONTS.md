# Adding Custom Fonts to indexTTS Worker

## Overview

All subtitle fonts are bundled in `assets/fonts/` and loaded at render time by
**libass** via the FFmpeg `subtitles=fontsdir=` option.  No system-level font
installation is required; the worker is fully self-contained.

---

## Bundled Fonts

| File | Family name | Weight | Used for |
|---|---|---|---|
| `Inter-Regular.ttf` | `Inter` | Regular | Latin / English subtitles |
| `Inter-Bold.ttf` | `Inter` | Bold | — |
| `NotoSansSC-Regular.ttf` | `Noto Sans CJK SC` | Regular | Simplified Chinese (`zh-CN`) |
| `NotoSansSC-Bold.ttf` | `Noto Sans CJK SC` | Bold | — |
| `NotoSansTC-Regular.ttf` | `Noto Sans CJK TC` | Regular | Traditional Chinese (`zh-TW`) |
| `NotoSansTC-Bold.ttf` | `Noto Sans CJK TC` | Bold | — |

> **Important**: The **Family name** column is what goes in the `fontName` field of the
> job payload and in the ASS `Style:` line — it must match the font's internal
> `name` record exactly.  Use `scripts/add_font.py --check` to inspect this.

---

## Adding a New Font

### Step 1 — Obtain the font file(s)

Download or copy the `.ttf` / `.otf` file(s) into `assets/fonts/`:

```bash
cp ~/Downloads/MyFont-Regular.ttf assets/fonts/
cp ~/Downloads/MyFont-Bold.ttf    assets/fonts/
```

### Step 2 — Verify the internal family name

```bash
uv run python scripts/add_font.py --check assets/fonts/MyFont-Regular.ttf
```

Expected output:
```
Family : MyFont
Full   : MyFont Regular
Style  : Regular
PostScript: MyFont-Regular
```

The **Family** value is what callers must pass as `fontName` in the job JSON.

### Step 3 — (Optional) Register as a named alias

Open `services/vox/subtitles.py` and add the font to the module-level registry
comment near `_FONTS_DIR`:

```python
# Bundled fonts (assets/fonts/)
# ─────────────────────────────────────────────────────
# "Inter"            → Inter-Regular/Bold.ttf          (Latin)
# "Noto Sans CJK SC" → NotoSansSC-Regular/Bold.ttf     (Simplified Chinese)
# "Noto Sans CJK TC" → NotoSansTC-Regular/Bold.ttf     (Traditional Chinese)
# "MyFont"           → MyFont-Regular/Bold.ttf          (Custom)
```

This is documentation-only; libass discovers all fonts in `_FONTS_DIR`
automatically.

### Step 4 — Use the font in a job payload

```json
{
  "captionStyle": {
    "fontName": "MyFont",
    "fontSize": 60
  }
}
```

---

## Modifying an Existing Font

Simply replace the `.ttf` file in `assets/fonts/` with the new version.  The
file name does **not** need to stay the same as long as the internal family name
remains consistent.

---

## Auto-Selection Logic (no `captionStyle` provided)

When no `captionStyle` is sent, the worker picks the font automatically based
on the narration text content:

| Condition | Selected font |
|---|---|
| Contains Traditional Chinese (`zh-TW`) | `Noto Sans CJK TC` |
| Contains Simplified Chinese / other CJK | `Noto Sans CJK SC` |
| Latin / other script | `Inter` |

---

## Troubleshooting

### Subtitles show boxes / missing glyphs
The font file is missing or the `fontName` does not match the internal family
name.  Run `--check` on the file:

```bash
uv run python scripts/add_font.py --check assets/fonts/MyFont-Regular.ttf
```

### libass cannot find the font
Ensure the file is in `assets/fonts/` (the directory pointed to by `_FONTS_DIR`
in `services/vox/subtitles.py`).  The worker always passes this directory as
`fontsdir=` to FFmpeg.

### Checking all currently bundled fonts

```bash
uv run python scripts/add_font.py --list
```
