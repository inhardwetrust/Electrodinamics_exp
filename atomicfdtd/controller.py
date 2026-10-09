# controller.py
#
# Frontend logic without Qt: owns the simulation, the playback timer and the
# renderer; the Qt window only calls these methods and listens to events.

from vispy import app

from diffusion import Diffusion1D, Diffusion2D
from renderer import ArrayRenderer


class Controller:
    """
    Events for listeners fn(kind):
        "state"     the array changed (step, reset, dif)
        "playback"  play / pause / speed changed
        "params"    a parameter changed (pending restart values included)
        "model"     another model was selected (the panel must be rebuilt)
    """

    MODELS = {"1D diffusion": Diffusion1D, "2D diffusion": Diffusion2D}
    DEFAULT_MODEL = "1D diffusion"

    DEFAULT_DIF = "center peak"
    DEFAULT_STEPS_PER_SECOND = 4.0
    COLOR_LIMITS = ("initial", "auto")

    def __init__(self, embedded=False):
        self._listeners = []

        # Values that need a Reset to take effect (e.g. the array size).
        self.pending = {}

        self.dif = self.DEFAULT_DIF
        self.set_name = "none"
        self.color_limits = "initial"   # set per model in set_model / below
        self.steps_per_second = self.DEFAULT_STEPS_PER_SECOND
        self.playing = False

        self.model_name = self.DEFAULT_MODEL
        self.MODEL = self.MODELS[self.model_name]
        self.simulation = self.MODEL()
        self.color_limits = getattr(self.MODEL, "DEFAULT_COLOR_LIMITS", "initial")
        self.renderer = ArrayRenderer(show=not embedded)
        self.renderer.canvas.events.key_press.connect(self._on_key)

        self._timer = app.Timer(interval=1.0 / self.steps_per_second,
                                connect=self._on_timer, start=False)
        _use_precise_qt_timer(self._timer)

        self.reset()

    # =====================================================
    # Listeners
    # =====================================================

    def add_listener(self, callback):
        self._listeners.append(callback)

    def _notify(self, kind):
        for callback in list(self._listeners):
            callback(kind)

    # =====================================================
    # Parameters
    # =====================================================

    def parameters(self):
        return self.simulation.parameters()

    def get_parameter(self, name):
        """Shows the pending value if one is waiting for Reset."""
        if name in self.pending:
            return self.pending[name]
        return self.simulation.get_parameter(name)

    def is_pending(self, name):
        return name in self.pending

    def set_parameter(self, name, value):
        param = next(p for p in self.parameters() if p.name == name)

        if param.restart:
            if value == self.simulation.get_parameter(name):
                self.pending.pop(name, None)
            else:
                self.pending[name] = value
        else:
            self.simulation.set_parameter(name, value)
            if param.group == "feed":
                # New feed values: show them now, not only after the next step.
                self.simulation.apply_set()
                self._refresh()

        self._notify("params")

    def set_model(self, name):
        """Switch to another model (fresh state, its own parameters)."""
        if name == self.model_name:
            return
        self.set_playing(False)
        self.model_name = name
        self.MODEL = self.MODELS[name]
        self.simulation = self.MODEL()
        self.color_limits = getattr(self.MODEL, "DEFAULT_COLOR_LIMITS", "initial")
        self.pending.clear()
        if self.dif not in self.simulation.dif_presets():
            self.dif = self.DEFAULT_DIF
        if self.set_name not in self.simulation.set_presets():
            self.set_name = "none"
        self._notify("model")
        self.reset()

    def set_dif(self, name):
        self.dif = name
        self._notify("params")

    def set_set(self, name):
        """Choose the set ("feed"); it takes effect immediately, no Reset."""
        self.set_name = name
        self.simulation.set_source(name)
        self._refresh()
        self._notify("params")

    def set_palette(self, name):
        self.renderer.palette = name
        self._initial_clim = _clim_of(self.simulation.state, symmetric=self._symmetric())
        self._refresh()

    def _symmetric(self):
        return self.renderer.palette == "signed"

    def set_color_limits(self, mode):
        self.color_limits = mode
        self._refresh()

    def set_number_format(self, digits=None, number_format=None, color_mode=None):
        """Cell labels and whether colors are quantized to them."""
        self.renderer.set_number_format(digits, number_format, color_mode)
        self._refresh()

    # =====================================================
    # Life cycle
    # =====================================================

    def reset(self):
        """Recreate the array (with pending values), all zeros, then + dif."""
        self.set_playing(False)

        if self.pending:
            live = {p.name: self.simulation.get_parameter(p.name)
                    for p in self.parameters() if not p.restart}
            restart = {p.name: self.pending.get(p.name, self.simulation.get_parameter(p.name))
                       for p in self.parameters() if p.restart}
            self.simulation = self.MODEL(**restart, **live)
            self.pending.clear()

        self.simulation.set_name = self.set_name
        self.simulation.reset(dif=self.dif)
        self._initial_clim = _clim_of(self.simulation.state, symmetric=self._symmetric())
        self.renderer.set_shape(self.simulation.state.shape)
        self._refresh()
        self._notify("params")

    def apply_dif(self):
        """state += dif, at any moment (a kick)."""
        self.simulation.apply_dif(self.dif)
        self._initial_clim = _clim_of(self.simulation.state, self._initial_clim,
                                      symmetric=self._symmetric())
        self._refresh()

    def step(self, n=1):
        for _ in range(int(n)):
            self.simulation.step()
        self._refresh()

    def _refresh(self):
        state = self.simulation.state
        clim = (self._initial_clim if self.color_limits == "initial"
                else _clim_of(state, symmetric=self._symmetric()))
        self.renderer.set_state(state, clim)
        self.renderer.set_marks(self.simulation.set_mask())
        self.renderer.set_status(self.status_text())
        self._notify("state")

    def status_text(self):
        d = self.simulation.diagnostics()
        return "   ".join(
            f"{k} = {v}" if isinstance(v, int) else f"{k} = {v:.4g}"
            for k, v in d.items()
        )

    # =====================================================
    # Playback
    # =====================================================

    def set_playing(self, playing):
        self.playing = bool(playing)
        if self.playing:
            self._timer.start(1.0 / self.steps_per_second)
        else:
            self._timer.stop()
        self._notify("playback")

    def set_steps_per_second(self, value):
        self.steps_per_second = max(0.1, float(value))
        if self.playing:
            self._timer.start(1.0 / self.steps_per_second)
        self._notify("playback")

    def _on_timer(self, event=None):
        self.step()

    def _on_key(self, event):
        key = event.key.name.upper() if event.key is not None else ""
        if key == "SPACE":
            self.set_playing(not self.playing)
        elif key == "N" and not self.playing:
            self.step()
        elif key == "R":
            self.reset()

    def shutdown(self):
        self._timer.stop()


def _clim_of(state, previous=None, symmetric=False):
    """
    Color limits covering the array (and the previous limits if given).
    symmetric: -m .. +m with m = max |value| (zero in the palette middle).
    """
    lo, hi = float(state.min()), float(state.max())
    if previous is not None:
        lo, hi = min(lo, previous[0]), max(hi, previous[1])
    if symmetric:
        m = max(abs(lo), abs(hi))
        lo, hi = -m, m
    if hi <= lo:
        hi = lo + 1.0
    return (lo, hi)


def _use_precise_qt_timer(timer):
    """Coarse Qt timers snap to the 15.6 ms Windows tick; use PreciseTimer."""
    backend = getattr(timer, "_backend", None)
    if hasattr(backend, "setTimerType"):
        from PySide6.QtCore import Qt
        backend.setTimerType(Qt.TimerType.PreciseTimer)
