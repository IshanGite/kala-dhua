"""The window shell: edge detection, pinning hides the window and shows the raven at the edge, clicking the raven restores, snapping takes half the screen."""

from __future__ import annotations

from ragnarok.daemon.shell import Box, Shell


class Win:
    def __init__(self, x=0, y=0, w=390, h=660):
        self.x, self.y, self.width, self.height = x, y, w, h
        self.visible, self.log = True, []

    def move(self, x, y):
        self.x, self.y = x, y

    def resize(self, w, h):
        self.width, self.height = w, h

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def restore(self):
        self.log.append("restore")

    def minimize(self):
        self.log.append("minimize")

    def evaluate_js(self, js):
        self.log.append(js)


def make(main_x=500, main_y=90):
    main, mascot = Win(main_x, main_y), Win(0, 0, 110, 112)
    mascot.visible = False
    sh = Shell(main, mascot)
    sh._screens = lambda: [(Box(0, 0, 1440, 900), (0, 38, 0, 70))]  # type: ignore[method-assign]
    return sh, main, mascot


def test_near_edge_only_when_close():
    sh, main, _ = make()
    assert sh.near_edge() is None
    main.move(1440 - 390 - 10, 90)
    assert sh.near_edge() == "right"
    main.move(12, 90)
    assert sh.near_edge() == "left"


def test_pin_hides_window_and_places_raven_at_the_edge():
    sh, main, mascot = make(main_x=1440 - 390 - 5)
    r = sh.pin()
    assert r["pinned"] == "right" and not main.visible and mascot.visible
    assert mascot.x + 110 >= 1440 - 2  # sits on the right edge


def test_clicking_the_raven_brings_the_window_back_where_it_was():
    sh, main, mascot = make(main_x=1440 - 390 - 5)
    home = (main.x, main.y)
    sh.pin()
    main.move(0, 0)
    sh.restore()
    assert main.visible and not mascot.visible and (main.x, main.y) == home and sh.pinned is None


def test_minimize_shows_the_raven_and_restore_hides_it():
    sh, main, mascot = make()
    sh.minimize()
    assert mascot.visible and "minimize" in main.log
    sh.restore()
    assert not mascot.visible


def test_snap_takes_half_the_visible_screen():
    sh, main, _ = make()
    sh.snap("right")
    assert (main.x, main.width) == (720, 720) and main.height == 900 - 38 - 70
    sh.snap("left")
    assert main.x == 0


def test_prompt_is_offered_once_per_edge():
    sh, main, _ = make(main_x=1440 - 390 - 5)
    sh._maybe_prompt()
    sh._maybe_prompt()
    assert sh.take_prompt()["prompt"] == "right" and sh.take_prompt()["prompt"] is None
    main.move(500, 90)
    sh._maybe_prompt()  # left the edge: forgotten
    main.move(1440 - 390 - 5, 90)
    sh._maybe_prompt()
    assert sh.take_prompt()["prompt"] == "right"
