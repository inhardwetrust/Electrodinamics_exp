# main.py
#
#   python main.py                -> Qt window with control panels
#   python main.py fdtd           -> start in a given model
#                                    (analytic | grid | oscillating | fdtd)
#   python main.py --canvas-only  -> bare VisPy window, keyboard only

import sys

from vispy import app

from app_controller import AppController


def main(argv):
    app.use_app("pyside6")

    modes = [a for a in argv if a in AppController.FIELD_MODES]
    field_mode = modes[0] if modes else None

    if "--canvas-only" in argv:
        AppController(field_mode=field_mode).run()
        return

    vispy_app = app.use_app()
    vispy_app.create()  # the QApplication shared by VisPy and the panels

    from main_window import MainWindow

    window = MainWindow(field_mode=field_mode)
    window.show()
    vispy_app.run()


if __name__ == "__main__":
    main(sys.argv[1:])
