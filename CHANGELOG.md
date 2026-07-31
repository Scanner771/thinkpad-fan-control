# Changelog

Dates are commit dates. Releases before 2.2 all reported version `2.1` in-app; the entries
below are reconstructed from the commit history rather than from tags.

## 2.2 — 2026-07-31

### Added
- **GPU tab** — Intel iGPU frequency caps: min/max clock spinners, Quiet / Balanced /
  Performance / Max presets, and "Restore hardware default". Optional auto-cap that follows
  the AC/battery power preset.
  - Every cap opens a 15-second keep-or-revert dialog and restores the previous
    min/max/boost on timeout, Esc, or close.
  - Busy% is derived from RC6 idle residency, so it works without root — `intel_gpu_top`
    needs `CAP_PERFMON`, which most systems deny.
  - Values are clamped to the hardware-reported `RPn..RP0` range, and the i915 card is
    resolved by driver symlink rather than a hardcoded `cardN` (the number isn't stable
    across boots).
  - Nothing persists: a reboot always restores stock GPU clocks.
  - Degrades to an explanatory note when there's no i915 device.
- `fanctl` gains `gpu-min`, `gpu-max`, `gpu-boost`, and `gpu-reset`.
- GPU busy% joins the 1 Hz history and the CSV export; the benchmark table gains GPU busy%
  and GPU clock sampled in the same steady-state window.
- Keyboard shortcuts and uninstall instructions documented in the README.

### Fixed
- **Only one instance can run at a time** (`QLockFile`, 30 s stale reclaim). An XDG autostart
  entry plus a desktop session restore could previously race into two live instances both
  driving the fan.
- The bundled systemd unit pointed at `~/fan-control.py`; it now matches the README's clone
  path (`~/thinkpad-fan-control/`).

## 2.1 — 2026-07-26

### Added
- Responsive layout: wrapping button rows, capped field widths, a shared label column, and a
  900 px centred content column. No horizontal overflow from 360×380 up to 1500×1000.
- Pill-style tabs with a filled active state; compact mode under 420×560 drops the title block
  and shrinks the graph.
- Window position and last active tab persist across restarts; off-screen coordinates from an
  unplugged dock are ignored.
- Keyboard shortcuts: `Ctrl+1..n`, `Ctrl+Tab`, `Ctrl+R` / `F5`, `Esc` / `Ctrl+W`, `Ctrl+Q`.

### Fixed
- Curve mode re-wrote the fan level every 60 s even when the EC hadn't drifted. It now compares
  the current level first and only writes on an actual change.
- Readout labels word-wrap. A non-wrapping `QLabel` reports its full text width as its *minimum*
  width, so one long status line used to drag every tab past the viewport.

## 2.1 — 2026-07-21

Initial public release: fan control (manual / curve / target), sensors, battery charge
thresholds, Intel RAPL power limits with an in-app benchmark, live graph with CSV export,
auto-profile by workload, critical-temp failsafe, and optional Prometheus/ntfy telemetry
(off by default).
