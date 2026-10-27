# Kinora

Kinora makes old home videos sharp again. It upscales low-resolution video (320×240, 640×480)
to 1080p, can keep a person's face true to reference photos, and has a video editor to trim, join,
speed up and add texts. Everything runs offline on this PC.

```
input ─► prep (section, crop, even timing, find real/damaged pictures) ─► RIFE in-between frames* ─► SeedVR2 ─► FaceFusion face lock ─► 1080p + audio
                                                                                                                   ▲
                                                            refs\<name>\*.jpg ─► cleaned face crops (only the shared person)
* only when the frame rate goes up or the clip has repeated/damaged frames; with Motion quality "fast" it runs after the face lock instead
```

## Installation

Kinora needs an NVIDIA GPU with a current driver (developed on an RTX 5090, 32 GB; SeedVR2 7B wants
a lot of VRAM). The installers are small: the first start downloads about 25 GB once, into
Kinora's data folder: SeedVR2 and FaceFusion (pinned commits), two Pythons and their packages
(`tools\requirements\`, not system-wide), RIFE and the model weights. That step can be re-run
safely (finished steps are skipped). After it, nothing uses the internet.

The program and your data are kept apart. The data folder holds your videos (`input\`), results
(`output\`), edit projects (`projects\`), reference photos (`refs\`), the work cache (`work\`) and
the downloaded tools and models (`tools\`, `models\`). Paths like `output\` in this README are in
that folder. Set `KINORA_DATA` to use another folder.

### Windows 10/11

1. Install git, uv and ffmpeg once, in a terminal:

   ```bat
   winget install Git.Git astral-sh.uv Gyan.FFmpeg
   ```

2. Download `Kinora-<version>-setup.exe` from the
   [releases](https://github.com/dreamtheater484/kinora/releases) and run it. It installs for your
   user only (no administrator rights) into `%LOCALAPPDATA%\Programs\Kinora`, with a
   Start-menu shortcut.
3. Start **Kinora** from the Start menu. The first start runs `install.ps1`.

Data folder: `%LOCALAPPDATA%\Kinora`. Uninstalling (Settings → Apps → Kinora) asks whether to
delete it too. Answer *No* to keep your videos and the 25 GB of downloads for a later install.

### Ubuntu 22.04 and newer

Download `kinora_<version>_amd64.deb` from the
[releases](https://github.com/dreamtheater484/kinora/releases) and install it:

```bash
sudo apt install ./kinora_<version>_amd64.deb
```

It installs the program into `/opt/kinora`, the `kinora` command and a *Kinora* entry in the app
menu, and pulls in git, curl, ffmpeg and Vulkan. The NVIDIA driver comes from *Software & Updates
→ Additional Drivers*. Start Kinora from the menu or with `kinora`. The first start runs
`install.sh`, the Linux version of `install.ps1` with the same pinned versions. It uses `uv` from
PATH, or downloads a pinned copy into the data folder.

Data folder: `~/.local/share/kinora`. `sudo apt remove kinora` removes the program but never your
data folder: delete `~/.local/share/kinora` yourself if you want it gone.

### Portable (a git checkout)

```bat
git clone https://github.com/dreamtheater484/kinora.git
```

Then double-click **`start.bat`** (Windows, with git, uv and ffmpeg as above), or run
`./start.sh` (Linux, after `sudo apt install git curl ffmpeg libvulkan1 libgl1`). A checkout keeps
its data next to the program, in the layout at the end of this README. An installed copy is told
apart by the `installed.txt` that the installers put next to `app.py` (see `paths.py`).

### Building the installers

A pushed `v*` tag makes GitHub Actions build both and attach them to the tag's release
(`.github/workflows/release.yml`). Locally:

```bat
ISCC /DAppVersion=1.0.0 packaging\windows\kinora.iss
```

```bash
KINORA_VERSION=1.0.0 nfpm package --config packaging/linux/nfpm.yaml --packager deb --target dist/
```

Both write to `dist\`. The Inno Setup script needs Inno Setup 6. Build the `.deb` from a checkout
with LF line endings, e.g. in WSL.

## Front end

Start **Kinora** (Start menu, `kinora`, or `start.bat` / `./start.sh` in a checkout). A small
window opens (keep it open) and Kinora appears in your browser at http://127.0.0.1:8766 (this PC only). The rail on the left switches between three
screens: **Upscale**, **Edit** and **Library**. While videos are being made, a ring and the
percentage on the Upscale icon show the progress from the other screens.

### Upscale

**Your videos** (the row at the bottom). Drop videos or whole folders anywhere on the window (they
are copied into `input\`, keeping the folder name; identical files are reused), or use *Add videos*
/ *Add folder* (file dialogs, no copying). The videos are done one by one, left to right: change
the order with the arrows on a video, remove one with ✕, re-run a finished or failed one with ↺.
The list and the settings survive a restart.

**The chosen video** (top). Click a video in the row to see it. Under the player are three tools,
one at a time:
- **Section**: drag the handles on the strip of frames, type times (`1:58`, `2:35.5`), or play to a
  spot and press *Set start/end* (keys `I` / `O`). `←`/`→` step one frame.
- **Crop**: *Auto* removes black borders, *None* keeps everything, *Custom*: *Draw on video*
  (drag a rectangle, move it, resize by the corners) or *Find picture area* (the part of the
  image that changes, e.g. the webcam picture inside a screen recording). *This video* / *All
  videos* chooses whether the crop is per video or shared: with *All videos* every video that
  isn't made yet uses the crop, also videos added later, and a change on any video changes it for
  all. Videos of another size get the same part of the picture; finished videos keep their crop.
- **Brightness**: *Compare on this frame* (key `B`) shows the paused frame split in two, the
  original left of the divider and the chosen brightness right of it, rendered with the same
  filter the upscaler uses. Drag the divider, move the slider (the change shows instantly) and
  pick other frames on the strip. It is the same setting as *All settings → Picture →
  Brightness*, so it applies to every video in the list.

**The recipe** (right). The main settings read as one sentence, for example *Make each video
1080p at the original frame rate with best motion, keep Grandpa's face true to 3 photos, and save
it as full quality.* Click an underlined value to change it. Below the sentence: smoothing of
damaged frames, the original sound and side-by-side copies. *All settings* has everything: face
lock (person, identity strength), output (resolution, frame rate, motion quality), file size,
picture (noise reduction, brightness) and, under *Advanced*, the model and face options. The
settings apply to every video in the list.

*Takes about* shows how long the list takes and when it will be ready. *Also join them into one
video* joins all finished videos in list order into one file (optional name), with a chapter per
video; different sizes are pillar/letterboxed, frame rates evened out, silence added where a part
has none.

**Progress** takes the place of the estimate while the list runs: overall %, time left, ready-at
time, the current video and its steps. The estimate learns from every finished run.

**Pause** stops the video that is being made, so the GPU is free for other things; *Resume*
continues where it stopped. Finished steps are kept, and the upscale step (the longest) is done in
parts of about 5 minutes, so a pause costs at most the part in progress; the other steps start
again from their beginning. Closing the app while a video is being made works like a pause.

### Library

All finished videos, newest first and grouped by day, with filters (upscaled, edited, joined,
smaller copies) and a search box. Click a video for its details on the right: *Play* (with a
side-by-side tab when there is a comparison video), *Edit*, *Side by side*, *Show* (in its folder)
and *Make a smaller copy* for sharing. A double-click plays it. *Make smaller…* at the top does
several videos at once.

## Edit: trimming, joining, speeding up, texts

**Edit** is a video editor for the finished videos, the videos in the Upscale list, or any other
video. To start, click *Edit this video* on the Upscale screen or *Edit* in the Library; or open
Edit and click one of the videos it offers. More
videos: **+** next to a video in the list on the left, drag it onto the timeline, or drop video
files on the page (they are copied into `input\`).

Videos that browsers can play (MP4 with H.264, such as screen recordings) open at once. Other
formats (old AVI/MOV, H.265) get a playable copy first, once, made on the graphics card; its progress
shows on the player.

Everything plays straight away while you edit; nothing is rendered until **Export video…**.

The Edit screen fills the window at any size: the player gets all the room the panels and the timeline leave.
The video list on the left folds into a slim rail (« in its corner; » or **+** on the rail), which it
does by itself on narrower windows. **Full screen**: the button at the right of the player's controls,
`F`, or double-click the video; the controls fade out while it plays (move the mouse to see them), and
`Esc` or `F` leaves full screen. The edit's name, *Edits ▾* and **Export video…** are in the top bar.
*Keys* (above the timeline) lists all keyboard keys.

**Right-click** anything on the timeline for a menu of what you can do there, as in other video
editors. On a clip: split, join with the clip before or after (parts of a video that were split),
speed, sound, the transitions on both sides, trim to here, add text, insert a video after it, cut,
copy, paste, duplicate and delete. On a round transition button: the transition and its length. On
a selected part: its speed, split at both ends, add text during it, cut it out or keep only it. On a
text: change it, quick style, place, size, bold/italic/underline, timing, fade, copy and delete. On
the time ruler or an empty spot: play from here, split, add text, select a part, insert a video,
undo. Right-clicking a clip or the ruler moves the playhead there first, so the player shows where a
split or trim goes. The menus work with the keyboard as well: the menu key or `Shift+F10` opens the
menu of what is selected, the arrow keys move, `Enter` chooses, `Esc` closes.

- **Trim**: drag a clip's left or right edge on the timeline (the player shows the frame you cut at),
  or put the playhead somewhere and press *Start here* / *End here*.
- **Split**: `S` (or *Split*) cuts the clip at the playhead. **Delete** removes the selected clip.
  Drag a clip to move it. *Join* (right-click) puts split parts back together. `Ctrl+C` / `Ctrl+X` /
  `Ctrl+V` copy, cut and paste clips and texts (also into another edit). `Ctrl+Z` / `Ctrl+Y` undo and
  redo.
- **Speed up a part**: drag over the time ruler (above the clips) to select a part, or press `I` and
  `O` at its start and end, then pick a speed on the right: 1½×, 2×, 3×, 4×, 6×, 8×, 12×, 16×, 20×,
  30×. The part becomes a clip of its own. Or select a clip and pick its speed. Above 2× the sound is
  switched off (it can be switched back on; it is sped up with its pitch kept). Browsers play at most
  16× as fast, so for 20× and 30× a sped-up preview copy is made in the background (seconds).
- **Transitions**: click a round button between two clips: cut, crossfade, dip to black, wipe or
  slide, ½, 1 or 2 seconds; it plays right away. The buttons at the very start and end fade in from
  and out to black. A transition takes at most half of each clip it joins.
- **Picture per clip**: brightness (the upscaler's filter, shown live) and crop (*Draw on video*).
- **Texts**: *Add text* (key `T`, or right-click the picture where it should go) puts a text on the
  video at the playhead, ready to type in. Drag it on the video to move it (it snaps to the middle),
  drag a corner to resize it, double-click it to change it. The panel on the right has quick styles
  (title, subtitle, name band, big and bold, handwritten, camcorder date stamp), the font (the
  Windows fonts, by kind), size, bold, italic, underline, alignment, colour, a band behind the text,
  outline, shadow, nine places on the picture, when it shows and a fade in and out. Each text has a
  bar under the clips: drag it to move the text in time, drag its edges to change when it shows
  (they snap to the clips' edges). A new text gets the look of the previous one. The export draws
  the texts with the same code as the player, so they look exactly as in the preview.
- **Export**: name, file size and format, sound. These are the same *File size* and *Sound* settings
  as on the Upscale screen. The frame has the shape that most of the edit has, at the size of the
  largest clip of that shape (other clips get black bars); the frame rate is the highest of the clips
  (lower rates repeat frames). Making videos larger or smoother is the Upscale screen's job. The result
  lands in `output\` (`<name>.mp4`, or `<name>_<level>-<format>.mp4` when made smaller) and shows in
  the Library. Every frame is compressed once, straight from the source videos. The export is made in
  parts (each clip, each transition, with the texts that show during it) that are kept in
  `work\_edit\`, so exporting again after a small change only makes the parts that changed.

Edits are saved automatically in `work\_ui\edits\`; *Edits ▾* opens another one or starts a new one.

**Project files.** To keep an edit somewhere of your own (to work on it later, keep it with the videos,
or take it to another PC), save it as a project file: the save button next to *Edits ▾*, or `Ctrl+S`.
The first time a Save window asks where (`projects\` by default); after that `Ctrl+S` saves to the
same file, and *Edits ▾ → Save as…* (`Ctrl+Shift+S`) makes a copy elsewhere. Next to the edit's name it
says whether the file is up to date (a dot on the save button when it is not). *Edits ▾ → Open project
file…* (`Ctrl+O`), or dropping a `.vedit` file on the page, opens one again: everything comes back
(clips, splits, speeds, transitions, texts, brightness, crop), and opening a file again replaces the
edit that came from it. The videos themselves are not in the file: they are found where they were,
or next to the project file when the folder was moved along with them.

## Command line

The `.bat` files are next to `start.bat` (in an installed copy: `%LOCALAPPDATA%\Programs\Kinora`).
On Linux, run the scripts with Kinora's own Python, e.g.
`~/.local/share/kinora/tools/seedvr2/.venv/bin/python /opt/kinora/upscale.py --help`.

```bat
upscale.bat input\clip.avi --refs refs\person
upscale.bat input\clip.avi --refs refs\person --start 1:58 --end 2:35 --fps 30 :: a section, at 30 fps
upscale.bat input\clip.avi                                                     :: upscale only, no face stage
```

Results land in `output\`:

| file | what |
|---|---|
| `<name>[_<start>-<end>]_1080p[_30fps].mp4` | the result, with original audio (`--no-audio`: silent) |
| `<merged name>.mp4` | (front end, optional) all finished videos joined, with chapters |
| `compare\..._compare.mp4` | side by side: original (plain resize) │ SeedVR2 │ final (`--no-compare` to skip) |
| `compare\..._compare_25/50/75.png` | stills from the comparison |

Join videos yourself: `merge.bat output\all.mp4 output\a.mp4 output\b.mp4 …`

## Smaller files for sharing

Full quality is ~9 Mbit/s (a 27 s 1080p video ≈ 31 MB). Three levels × three formats, measured
with VMAF (Netflix's perceptual quality score: 95+ = indistinguishable) on an upscaled home video:

| level | AV1 (smallest) | H.265 (iPhones) | H.264 (plays everywhere) | what you see |
|---|---|---|---|---|
| Near lossless | −84% · 98.2 | −79% · 98.5 | −54% · 98.8 | no visible difference |
| Balanced | −89% · 94.9 | −87% · 94.7 | −75% · 94.7 | only visible side by side |
| Small | −92% · 90.1 | −91% · 89.4 | −83% · 89.0 | slightly softer up close |

- **While upscaling**: the recipe's file size, or *All settings → File size* (the joined video uses it too; it is made from
  full-quality copies so it is compressed only once). CLI: `--size balanced --format av1`.
- **Existing videos**: Library → *Make a smaller copy* on a video, or *Make smaller…* for several. Writes
  `<name>_<level>-<format>.mp4` next to it and measures the real size, reduction and VMAF.
  CLI: `shrink.bat --quality balanced --format av1 video.mp4 …`
- Playback: AV1 – Windows 10/11, Android, Mac, browsers; iPhone only from 15 Pro. H.265 – all
  iPhones/Macs, most phones and TVs; Windows may need the HEVC extension (or VLC). H.264 – everything.
- Going down to 720p is not worth it: at the same file size 1080p AV1 scores higher.

Each stage caches its result in `work\<name>\`. Changing only face options re-runs only the
face stage (seconds to minutes), not the slow SeedVR2 stage.

## Useful options

| option | default | when to change |
|---|---|---|
| `--fps 30` | source | higher frame rate via RIFE v4.26 interpolation (lower = frames dropped) |
| `--no-smooth` | smooth on | keep repeated frames as they are (see below) |
| `--motion fast` | best | *best* upscales and face-locks every in-between frame (sharper, ~2–3× longer); *fast* blends finished frames |
| `--crop 350:286:6:22` | auto | W:H:X:Y in source pixels; `auto` = black borders, `off` = none |
| `--gamma 1.3` | 1.0 | dark footage: brighten before upscaling |
| `--denoise medium` | off | only for really noisy footage (`off`, `light`, `medium`, `strong`); SeedVR2 already repairs compression damage and every filter pass costs sharpness |
| `--model 7b-sharp` | 7b | crisper detail (needs `download_models.bat 7b-sharp` once) |
| `--batch 45` | 33 | more frames processed together = steadier result, more VRAM (must be 4n+1) |
| `--identity 0.4` | 0.5 | 0.5 = like the reference photos; lower keeps more of how the person looks in the video, higher pushes past the references |
| `--enhance 30` | 50 | face looks too smooth / plastic |
| `--face-enhancer codeformer` | gfpgan_1.4 | alternative face texture model |
| `--expression` | off | keep the original facial expression more faithfully |
| `--detect-score 0.35` | 0.5 | turned or tiny faces are being skipped |
| `--faces many` | one | more than one person should get the same face (rare) |

Run `upscale.bat --help` for everything.

## Choppy clips (old webcams, phones, screen recordings)

Many old clips claim 10–15 fps but only contain 2–5 *real* pictures per second, with copies in
between and sometimes skipped time slots. The pipeline:

1. evens out the timing (skipped slots are filled, so picture and sound stay in sync);
2. finds the real pictures (on the untouched source: a frame is new when it clearly differs from
   the last real picture);
3. skips damaged pictures (block garbage from transmission errors in video chats: a picture that
   differs strongly from both neighbours while those resemble each other);
4. lightly evens out the moments of the real pictures (webcams capture at a steady pace; the
   network and the screen recorder add jitter);
5. renders every output frame at its exact moment between the two real pictures around it
   (`tools\interpolate.py`, RIFE v4.26), showing a real picture instead of a blend whenever one
   falls within half an output frame. Hard scene cuts are not blended.

This also runs at the original frame rate when at least 10% of frames are repeats or any picture
is damaged (turn off with *Smooth damaged frames* / `--no-smooth`). With ~4 real pictures per
second, slow and moderate movement becomes smooth. Limits: motion blur that the old camera
recorded cannot be undone, and very fast movements can still smear a little.

The face lock uses FaceFusion's face tracker (neighbouring frames), so a face missed in one blurry
frame is filled in from the frames around it.

## Reference photos

One folder per person in `refs\`. Same period as the videos, sharp, varied angles and
expressions. Group photos are fine: the face that appears across most photos is kept and other
faces are blanked out automatically (crops in `work\_refs\`).

## Offline guarantee

- The installation (`install.ps1` / `install.sh`, run by the first start) is the only step that uses the
  internet: GitHub (SeedVR2, FaceFusion, RIFE release 20250112), PyPI/PyTorch (packages),
  Hugging Face/GitHub (model weights). Extra models later: `download_models.bat 7b-sharp`.
- The front end listens on 127.0.0.1 only and loads nothing from the internet; its typefaces
  (Krona One, Instrument Sans, Martian Mono) are in `ui\fonts\`, under the SIL Open Font License.
- `upscale.bat` starts both tools behind a guard that refuses any non-local network connection,
  and FaceFusion's model-URL checks are disabled.
- Hard block (Windows): run `firewall_block.ps1` once in an Administrator PowerShell. It blocks
  outbound traffic for this project's own Python interpreters only (`tools\python`, the two `.venv`s).

## Layout

The data folder (next to the program in a checkout, see [Installation](#installation)):

```
input\      source videos           refs\       reference photos, one folder per person
output\     results                 work\       per-video stage cache (safe to delete)
models\     SeedVR2 weights         tools\      SeedVR2, FaceFusion, Pythons, RIFE (installed)
projects\   edit project files (.vedit), unless saved elsewhere
```

The program:

```
app.py      the front end's server  editor.py   Edit's export (and preview copies)
ui\         the front end page      tools\      launchers + helpers; requirements\: pinned Python packages
            (editor.js/.css: Edit; fonts\: typefaces)
paths.py    where the data folder is
start.bat, install.ps1   Windows     start.sh, install.sh   Linux
packaging\  the Windows installer (Inno Setup) and the Ubuntu package (nfpm)
```

Not in git: `input\`, `output\`, `work\`, `projects\` (personal videos and edits), the photos in
`refs\` and everything `install.ps1` / `install.sh` download.
