# The Afterfact demo film

A 2:04 film of the running product. Every frame inside a browser window is a
recording of `http://127.0.0.1:5173` against the local store — no mock-ups, no
stock footage, no invented dashboard. The narration is local Kokoro-82M. The
edit is a HyperFrames composition with one paused GSAP timeline.

```
film/afterfact-1080p.mp4      the film — 82.4 MB, 2:04.4, 1920x1080, 30 fps,
                              H.264 + AAC 48 kHz stereo
film/vo/afterfact.srt         captions, generated from the same two files that
                              drive the mix, so they cannot drift
```

## What it claims, and what it refuses to claim

The film is built on the repository's own evidence rules.

- **No live model call is implied.** Bifrost was not running on `:8080` when the
  takes were made, so no take shows a model answering. Every model-shaped number
  on screen (`155 recorded calls`, `$0.000109 avg`, `55% supported share`) is a
  value already stored in `tev.model_calls` and returned by
  `GET /api/compare/models`. The comparison act shows the `pasted answers` band
  with `no model recorded`, because that is what the store holds.
- **The verdict card is a real claim.** Act 06 opens claim `656a6289…` from run
  `11a9baec…`: `contradicted`, reason `contradicted_by_source`, with the stored
  revision text quoted at the offset the extractor recorded.
- **Failures stay in the edit.** Act 07 opens the failed filter and shows five
  reported failures plus `8 reported failures`. They were not deleted for a
  prettier take (D-035).
- **Nothing is speed-ramped.** The narration defines the act length; where the
  footage was shorter than the act, playback was slowed (0.72–0.97), never
  accelerated. No `4x` chip is needed because nothing is running fast.
- **The extraction suite's 100% is not in the film** and never will be: it is
  circular. The film quotes no accuracy figure at all. The independent suite's
  60.4% lives in `docs/DEMO-VIDEO-AND-DOSSIER-PLAN.md` §9.2 with its measurement
  named, not in the video.

## Shape

| # | Act | Screen | Line |
|---|---|---|---|
| 01 | the problem | `/` hero + dossier playing | one paragraph · twenty claims |
| 02 | four clocks | `/` scene 01 | publication date ≠ event date |
| 03 | intake | `/check` | recorded, not guessed |
| 04 | the run | `/runs/:id` header, register, trace | every stage is a row |
| 05 | the quote | run, `formatted` → `source` | the same characters, twice |
| 06 | the card | run, contradicted claim | the model proposes · the code decides |
| 07 | failures | `/runs`, failed filter | hidden by a filter · never deleted |
| 08 | measurement | `/compare` | measured · pasted · catalogue |
| 09 | the close | home finale → end card | four verdicts · no fifth |

124.4 s total. Nine narration clips, 98.2 s of speech, silence between them.

## How it was made

```powershell
# 1. shoot — 9 takes, 1920x1080, one per act
backend\.venv\Scripts\python.exe film\scripts\capture_app.py

# 2. voice — Kokoro-82M, am_michael, 0.98, one file per act
backend\.venv\Scripts\python.exe film\scripts\narrate.py

# 3. captions from the mix, not from a second copy of the script
backend\.venv\Scripts\python.exe film\scripts\make_srt.py

# 4. gate and render
cd film; npm run check; npm run render
```

The delivered master was rendered with screenshot capture, not the faster
`canvas` path, because the composition is nine `<video>` elements rather than
one animated scene:

```powershell
cd film
npx --yes hyperframes@0.9.46 render -q delivery --video-frame-format png -o afterfact-1080p.mp4
```

It printed `3732` frames captured and encoded, then
`82.4 MB · 2m 4.4s video · rendered in 20m 55.8s`, with `captureMode:
screenshot` in the trace. Expect roughly twenty minutes on this machine; a
re-render is not a two-minute operation.

The capture harness drives a real Chromium at 1920×1080, injects a cursor that
follows the real pointer, hides scrollbars and the text caret, and converts each
webm to H.264 with FFmpeg. `AF_RUN` / `AF_CLAIM` point it at a different case.

## Two defects this shoot found in the product

Both were fixed, not worked around:

1. **The home header still said "Temporal Evidence Engine".** Only
   `VerifyShell.jsx` had been renamed (D-038); `home/Home.jsx` has its own
   `Brand` component. Both now read `Afterfact · claim-level verification`.
2. **The claim mark was a `<span role="button">`, not a `<mark>`.** A capture
   selector written against `<mark data-claim>` found nothing and the take
   stalled for 30 s. The app is right and the selector was wrong — the span
   exists so a marked run keeps its trailing space when it is copied. Worth
   knowing before writing a test against it.

## Known rough edges

Two, both consequences of filming a real page rather than staging one, and both
accepted rather than hidden:

1. **Content scrolls under the chrome band.** At 0:21 the section headline sits
   in the scrim's gradient and reads dimmed; at 1:16 the contradicted badge has
   scrolled up behind the band while the lower half of the card (freshness,
   corroboration, evidence) is on screen. A real page scrolls under a sticky
   header the same way. Fixing it means re-shooting acts 02 and 06 and paying
   another 21-minute render, so it stays.
2. **The dossier on the home hero is the designed sample case** (`Acme Pro`,
   `$99 → $29`), while the bottom-left line names the filmed run. That is
   deliberate: the store has no `changed` claims at all, so the `changed` state
   can only be shown through the designed case, and the film never pretends the
   Nvidia run produced one.

## What the master actually is

```powershell
ffprobe -v error -show_entries format=duration,size,bit_rate `
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels `
  -of default=noprint_wrappers=1 film\afterfact-1080p.mp4
```

```
codec_name=h264          width=1920   height=1080   r_frame_rate=30/1
codec_name=aac           sample_rate=48000         channels=2
duration=124.400000      size=86404170  bit_rate=5556538
```

`volumedetect` over the whole file: `mean_volume: -25.0 dB`, `max_volume: -1.0
dB`. There is a mix, it has headroom, and it is not clipped.

Frames read back out of the delivered MP4 at 3, 21, 62, 69, 72, 76, 100, 120 and
123.9 s: the hero and dossier, the four clocks, the formatted answer with its
claim marks, the contradicted card, the measured comparison and the end card.
The end card holds unchanged from 117 s to the last frame.

## Checks actually run

| Command | Printed |
|---|---|
| `pytest backend\tests -q` | no failures; see the gate section of `README.md` |
| `npm test` | every suite `# fail 0` |
| `npm run build` | `✓ built in 12.20s` (12.20 s of Vite work, not a page metric) |
| `cd film; npm run check` | `Check passed`, `0 errors` |
| `cd film; npx hyperframes snapshot --at …` | read before rendering |
| `ffprobe` on the master | the block above |
| `ffmpeg -af volumedetect` on the master | `mean_volume: -25.0 dB`, `max_volume: -1.0 dB` |
| `ffmpeg -vf blackdetect` on the master | no black segment ≥ 0.08 s, so no gap between acts |

The film itself makes no claim about the backend gate. If you quote a number
from this project, quote the command that printed it — see `AGENTS.md`.
