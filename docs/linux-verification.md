# Linux preview verification — 2026-11-04

These results cover the initial GTK3 preview, not the full macOS feature set.
The table below is a historical initial-preview snapshot; see subsequent pass
results at the end for the current MCP/plugin/animation implementation.

## Environment

- Linux Mint 22.3, Ubuntu 24.04 base, XFCE, X11, compositor confirmed active.
- Intel Celeron N4020, 2 logical CPUs, approximately 3.63 GiB RAM.
- Docker 29.1.3; official `ubuntu:24.04` image and distro dependencies.
- Existing host GTK3/PyGObject/Ayatana/WebP libraries reused. No host packages
  were installed, no permissions/group membership or autostart settings changed.
- Docker service was started with user approval; it was not enabled at boot.

## Results

| Check | Result | Boundary |
| --- | --- | --- |
| Python unit tests | 92 passed on host | Validation, config, TTL, IPC, assets and packaging |
| ResourceWarning check | Passed | Host unit tests with `-W error::ResourceWarning` |
| Real XFCE smoke | Passed | Generated sprite fixture, actual GTK window + Unix socket, notify/update/animations/clear/shutdown |
| Real Starcorn run | Passed | WebP asset from exact OpenPetsKit v0.2.1 survey checkout; clean exit, no stderr |
| Container build | Passed | Distro dependencies installed inside image; non-root test user |
| Container unit tests | 92 passed | Current worktree mounted read-only; network disabled, 512 MiB, one CPU |
| Container Xvfb smoke | Passed | Same constraints, with Docker `--init` |
| `.deb` build | Passed | Unprivileged staging, architecture `all`, no bundled pet assets |
| Extracted `.deb` | Passed | CLI help and real GTK smoke without installing on host |
| Installed `.deb` | Passed | Actual `dpkg -i`, installed launcher help + Xvfb smoke in disposable container only |
| Independent review | No outstanding critical/high/medium findings | Reviewer ran 92 tests; GTK execution performed by integrator, not reviewer |
| macOS protected files | 27 byte-identical to HEAD | Swift sources/tests, manifests, packaging and existing release workflow |
| Original checkout | Clean, still on `main` | Separate worktree owns all changes |
| GitHub-hosted Actions | Not run | Workflow is prepared; nothing pushed |
| macOS build/tests | Not run | Requires macOS runner |
| Full HTTP MCP/plugin parity | Not implemented | Explicit backlog, no listener on port 3001 |

## Short resource observation

One Starcorn instance on the actual host, default scale 0.42, after a short
startup warm-up. Process CPU time came from `/proc/PID/stat`; RSS from
`/proc/PID/status`. CPU percentages use **one logical CPU as 100%**.

| Mode | Sampling duration | RSS | CPU |
| --- | --- | --- | --- |
| Idle | 8 seconds | 53.48 MiB | 0.25% |
| `running-right`, looping | 8 seconds | 53.48 MiB | 2.87% |

RSS includes shared libraries and is not unique/private memory. This is a short
observation, not a long-duration benchmark, comparative language benchmark or
promise of identical results for other sprites/themes/machines.

## Operational issues found and handled

- `xvfb-run` as container PID 1 waited indefinitely for X server readiness.
  The owned test container was stopped; retry with Docker `--init` passed.
  The documented commands now include it.
- One rebuild encountered HTTP 403 from Ubuntu APT mirrors. Reusing the
  successfully installed dependency layer completed the build. A fresh rebuild
  still depends on mirror/network availability; signature checks were not
  disabled and no untrusted mirror was substituted.
- Docker reported the legacy builder deprecation. Buildx was not installed
  unnecessarily on the host; the existing builder completed the image.

## Delivery and next gates

The local preview artifact is `openpets_0.1.0_all.deb` (13,710 bytes), not an
upstream release. SHA-256:

```text
9a7961eb47515fc2932d03d38aefe8a5f519b560fb2d7f7c7377f32de123d1c2
```

No commit, push, PR, tag, host app installation or release publication was done.
Before upstream release: agree on the separate Python host architecture, add
HTTP MCP/plugin functionality or explicitly accept preview scope, run hosted
Linux CI and macOS regression tests, then approve release publishing.

Known preview limitations include approximately one-second TTL scheduling
granularity, rectangle-based rather than alpha-pixel click-through, and local
asset check/use races if the same user modifies a pet bundle during loading.

## Animation / compact overlay pass

Baseline before this pass: 269 tests pass using the existing MCP virtualenv
(including GTK and MCP integration); system Python skips five optional SDK tests.
The isolated real-display smoke now sends every canonical animation and checks
the returned state. A dedicated GTK drawing test checks that each animation
advances its frame and paints the atlas, rather than checking only IPC success.
No package installation or global OpenCode configuration change is needed.
Container rerun is unavailable in this pass because the Docker daemon socket is
absent; the daemon was not started and no privileged commands were run.

Final local verification (2026-11-05): **335 tests passed**, no skips, with the
existing MCP virtualenv and `-W error::ResourceWarning`. This includes seven
isolated real-GTK animation/drag tests: all nine rows advance/render, GTK pointer
signals are wired, direction reverses without restarting the frame clock, slow
motion accumulates, active task/TTL survive dragging, a notification arriving
during drag wins on release, and empty overlays collapse. The isolated GTK/IPC
smoke also passed. A rendered Goku preview with fixture plugin/thread data was
visually inspected at `/tmp/opencode/openpets-compact-preview.png`.

Linux frame timings intentionally differ from Swift v0.2.1: horizontal gait
uses 80 ms frames with a 140 ms settle frame; idle uses 180 ms transitions and
600/900 ms rests instead of 2–2.6 second holds. Spontaneous waving/jumping is
scheduled after 4–11 seconds of idle; its timer is absent during active work or
dragging. No unsupported up/down sprite rows are invented: vertical drag does
not jump; it preserves the last horizontal gait or falls back to the underlying
animation.

## Free-roam regression pass

The GTK audit reproduced an idle lock when no monitor workarea was returned;
`_setup_motion` now falls back to the primary monitor (then monitor 0). The
controller also cleared an arrived waypoint before resting, so it could pick a
fresh destination when the rest ends. Waypoints use bounded diagonal angles
instead of full-screen vertical deltas, avoiding repeated edge-clamping and
apparent bottom/right bias. A configured free-roam mode starts walking without
requiring a drag. Final local verification: **449 tests passed**, including
seeded multi-waypoint direction/bounds tests and a GTK regression for the
missing-monitor fallback; no ResourceWarnings. Existing GUI processes must be
restarted to load these Python changes.

## Roam gait / quota refresh polish

Fixed a subpixel quantization stall: low-speed motion increments were converted
to integer coordinates before updating the controller, so steps under 1 px
could collapse to zero and clear the running gait. The controller now retains
floating-point position and rounds only at the GTK window boundary. Mid-route
rests now start a fresh speed segment on resume (no chained immediate rests),
and the mid-walk rest chance is reduced from 25% to 12%. Quota polling is now
60 seconds (previously 300); the Plugins menu also has **Refresh enabled plugins
now**, which only reads already enabled providers and queues one refresh if a
read is in flight. Final local verification: **452 tests passed** with
`-W error::ResourceWarning`; includes regressions for subpixel gait continuity,
fresh speed segments after rests, and manual refresh of enabled-only plugins.

Reference source checked: OpenPetsKit tag `v0.2.1`, commit
`83497ed34d1831dd453130af77552bfecaef264b`, `PetAnimation.swift` and
`OpenPetsHost.swift` (drag tracker). Existing Swift source files were not edited.
