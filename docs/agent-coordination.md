# Concurrent agent coordination

More than one coding agent works in this repository. This file is the shared
claim board; it is not a feature spec.

## Rules

- Edit in your own `git worktree`:
  `git worktree add ../<repo>-<agent> -b <branch> origin/dev`. Keep the
  primary checkout clean so the other agent is never editing against a tree
  someone else is moving.
- Claim files before touching them, release them when done, by editing the
  table below. One file, one owner at a time.
- Coordinate through GitHub: file claims and design questions go on the open
  pull request or issue. Do not write into another agent's session log,
  scratch directory, or transcript.
- Re-read this file at the start of every task. A stale row means the owner
  is gone; ask on the PR before taking over their files.
- Branch naming and the `dev` -> `main` flow are unchanged; see `AGENTS.md`.

## Claims

| Owner | Files | Branch | Claimed |
|---|---|---|---|
| Hermes | `tools/csi_detect.py`, `tools/csi_live_plot.py`, `tools/csi_plot_csv.py` | `fix/live-detect-freeze` | 2026-11-19 |
| pi | `README.md`, `tools/csi_detect.json`, `src/`, `platformio.ini` | `feat/csi-host-analysis` | 2026-11-19 |

## Why the split

The live detector work has two independent halves. The detector core and the
plot/feed plumbing (`tools/csi_detect.py`, `tools/csi_live_plot.py`) and the
heatmap axis (`tools/csi_plot_csv.py`) are one person's; documentation,
thresholds and firmware are the other's. Touching `README.md` or
`tools/csi_detect.json` from both sides means one of us silently reverts the
other's wording.

## Current open items

Tracked on PR #7 (`feat/csi-host-analysis`): the live `--detect` panel stops
updating once the GUI window fills, `LiveDetector` raises on a payload width
change, and the CSV heatmap x-axis is frame index rather than time. See the
PR thread for reproduction numbers.
