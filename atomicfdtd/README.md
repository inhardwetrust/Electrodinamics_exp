# atomic FDTD

Step-by-step building blocks for understanding FDTD, starting from the
simplest local update rule. Same structure as `../pseudo_cst_3d`: model,
controller (no Qt), VisPy renderer and a thin Qt shell.

## 1. Local diffusion (1D)

An array of n cells (default 7). Every step computes a NEW array from the
OLD one, then replaces it:

    new[i] = old[i] + D * (old[i-1] - 2 old[i] + old[i+1])

D = 0.5 gives new[i] = (old[i-1] + old[i+1]) / 2. The scheme is stable for
D <= 0.5; larger D is allowed on purpose, so you can watch it blow up (the
same kind of limit as the Courant number in FDTD).

Life cycle: Reset gives all zeros, then `state += dif`. The dif is the
initial perturbation: center peak, left peak, two peaks, step or random.
"Apply dif now" adds it again at any moment. Changing n takes effect on
Reset.

Boundaries:
- `fixed` - 0 outside, so heat leaks out;
- `reflect` - no flux, the sum is conserved;
- `periodic` - the ends join into a ring.

### Feed (set)

The set is an array of the state's shape: a number where a value is forced,
and NaN where the state stays free. After every step,
`state[mask] = set[mask]`. Reset order: zeros, then + dif, then set. The
choice takes effect immediately, and forced cells are outlined.

Examples:
- "left = 1, right = 0" relaxes to a straight line, which is stationary heat
  conduction;
- "center = 1" with fixed ends relaxes to a tent shape.

The feed can oscillate:

    forced = A * set * w(n) + offset,   w = 1 (constant) or sin(2 pi n / period)

Here n is the step index, so the sine starts at 0 after Reset. In diffusion a
sine source makes a **thermal wave**: it is damped as exp(-x/delta) and lags
in phase by x/delta, with delta = sqrt(2 D / omega). This was checked in 1D
to within 1-3%. delta grows like sqrt(period): at period 20 it is ~1.8 cells,
at period 400 ~5 cells. Use the "signed" palette (purple - black - yellow)
to see the sign.

### Numbers and colors

By default ("match numbers") a cell's color comes from the same rounded value
that is written in it, so equal numbers always have equal colors (a quantized
heatmap). "Digits" and "Format" (fixed / significant) control that rounding.

## 2. Local diffusion (2D)

Select "2D diffusion" in the Model box. The grid is nx x ny (default 7 x 7).

    new = old + D * ( a * (4 face neighbours - 4 old) + b * (4 diagonals - 4 old) )

Neighbour stencils, normalized to the same spreading rate:

| Stencil | a (faces) | b (diagonals) | Stable D |
|---|---|---|---|
| 5-point (faces) | 1 | 0 | 0.25 |
| 9-point isotropic | 2/3 | 1/6 (1/4 of a face) | 0.375 |
| 9-point equal weights | 1/3 | 1/3 | 0.5 |

Diagonal cells only touch at a corner. There is no shared face, so in the
standard scheme they do not exchange at all (Yee FDTD works the same way).
The isotropic 9-point stencil gives them 1/4 of the face weight to cancel
the leading direction-dependent error. For diffusion the difference is small
and shows mostly in the first steps (a "+" versus a 3x3 square) and in the
stability limit. It matters more for waves (numerical dispersion).

```bash
python main.py
```

Keys: Space start / pause, N step, R reset.

## Files

| File | Role |
|---|---|
| `simulation.py` | Contract: reset / step / state / apply_dif / parameters |
| `diffusion.py` | Diffusion1D (double buffer: compute new, then swap) |
| `params.py` | Parameter schema; the Qt form is generated from it |
| `controller.py` | Playback, pending "restart" parameters, color limits |
| `renderer.py` | VisPy heatmap with grid, indices, values in cells (1D / 2D) |
| `main_window.py`, `main.py` | Qt shell |
