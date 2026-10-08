# edit_tool.py
#
# Mouse interaction for editing charges on the 2D canvas. All model changes
# go through AppController's editing API; this class only translates mouse
# events (pick, drag) into those calls.

import time


class ChargeEditTool:
    """
    Left click on a charge selects it; dragging moves it. Clicking empty
    space clears the selection, and dragging there pans as usual.

    Our handlers run before the scene's (position="first"), so a drag that
    starts on a charge can switch the camera off until the button is released.
    """

    # Field recomputation while dragging is limited to ~30 updates per second;
    # the final position is always applied on release.
    DRAG_INTERVAL_S = 1.0 / 30.0

    def __init__(self, controller, renderer):
        self.controller = controller
        self.renderer = renderer
        self._drag = None           # (index, offset_x, offset_y)
        self._last_update = 0.0

        events = renderer.canvas.events
        events.mouse_press.connect(self._on_press, position="first")
        events.mouse_move.connect(self._on_move, position="first")
        events.mouse_release.connect(self._on_release, position="first")

    def detach(self):
        events = self.renderer.canvas.events
        events.mouse_press.disconnect(self._on_press)
        events.mouse_move.disconnect(self._on_move)
        events.mouse_release.disconnect(self._on_release)
        self._end_drag()

    # -------------------------------------------------

    def _on_press(self, event):
        if event.button != 1:
            return

        x, y = self.renderer.screen_to_world(event.pos)
        index = self.controller.charge_at((x, y), self.renderer.charge_icon_radius_world())
        self.controller.select_charge(index)

        if index is not None:
            cx, cy = self.controller.charges()[index].position
            self._drag = (index, cx - x, cy - y)
            self._last_update = 0.0
            self.renderer.view.camera.interactive = False

    def _on_move(self, event):
        if self._drag is None:
            return

        now = time.perf_counter()
        if now - self._last_update < self.DRAG_INTERVAL_S:
            return

        self._last_update = now
        self._apply(event.pos)

    def _on_release(self, event):
        if self._drag is not None:
            self._apply(event.pos)
        self._end_drag()

    def _apply(self, pos):
        index, ox, oy = self._drag
        x, y = self.renderer.screen_to_world(pos)
        self.controller.move_charge(index, (x + ox, y + oy))

    def _end_drag(self):
        self._drag = None
        self.renderer.view.camera.interactive = True
