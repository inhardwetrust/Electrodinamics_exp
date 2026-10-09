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

## 3. Wave equation (1D / 2D)

Models "1D wave" and "2D wave" use the leapfrog scheme

    new = 2 u - u_prev + C^2 * (neighbours - 2 or 4 u)

Each cell keeps TWO numbers, the value and the previous value, so it has a
velocity, i.e. inertia. That is why a front travels instead of spreading.
It is the same scheme as the E/H leapfrog on a 1D Yee grid. C is the Courant
number in cells per step; it is stable for C <= 1 in 1D (exact at C = 1) and
C <= 1/sqrt(2) in 2D.

Boundaries:
- `absorbing` (first-order Mur) - the wave leaves;
- `fixed` - reflects with a sign flip;
- `reflect` - reflects with the same sign;
- `periodic`.

dif adds a displacement with zero velocity: a bump splits into two halves.
The first step is a half step (u1 = u0 + C^2/2 L(u0)), so from a single 1 at
C = 0.5 the center loses C^2 = 0.25 and each neighbour gains 0.125.

Pulses in the feed:
- `pulse (hard)`: the cell is set to A only at step "At step" and is free
  otherwise. In the leapfrog scheme this is a velocity **kick** (the previous
  value stays 0), not a displacement. In 1D a kick leaves a plateau that
  grows behind both fronts: at C = 1 it is a checkerboard 1 0 1 0 ..., and
  the sum grows by 1 per step. In diffusion it is the same as "center peak".
- `gaussian pulse (soft)` (waves only): the cell is never overwritten; every
  step it gets D(n) = 2 C (G(n) - G(n-1)) * A added, where G is a gaussian in
  time (duration = Period, width Period / 4). The kicks sum to zero, so
  their plateaus cancel and a clean bump of height A runs out both ways.
  The cell stays transparent for passing waves (like the port in pseudo_cst).
  Checked: height 1.000 at C = 1; at C = 0.5 a short pulse (period 20) loses
  height to dispersion (0.93), a longer one (period 40) keeps 1.00.
The Feed box plots the waveform step by step (per set value 1; gray ticks =
the cell is free, red line = the current step; for the soft pulse also the
amount added each step).
While the step counter is 0, changing the feed rebuilds the start (zeros +
dif + feed), so an old "constant" value does not stay behind as a kick.

Checked:
- at C = 1 the halves move exactly one cell per step (height 0.500);
- at C = 0.5 the peaks drop to 0.445 (numerical dispersion);
- the 2D ring radius is C*t;
- the stability limits are exact.

Compare a cell 20 cells away. The wave stays ~0, peaks at step 40 (20 / C),
then is gone. Diffusion is exactly 0 until step 20 (the stencil's light
cone) but then only creeps up: 1e-12, 1e-6, 4e-4.

```bash
python main.py
```

Keys: Right arrow = Step anywhere in the window (hold it to keep stepping);
on the canvas also Space start / pause, N step, R reset.

## Files

| File | Role |
|---|---|
| `simulation.py` | Contract: reset / step / state / apply_dif / parameters |
| `diffusion.py` | Diffusion1D (double buffer: compute new, then swap) |
| `params.py` | Parameter schema; the Qt form is generated from it |
| `controller.py` | Playback, pending "restart" parameters, color limits |
| `renderer.py` | VisPy heatmap with grid, indices, values in cells (1D / 2D) |
| `feed_plot.py` | Feed box plot: the waveform step by step, current step marked |
| `main_window.py`, `main.py` | Qt shell |
