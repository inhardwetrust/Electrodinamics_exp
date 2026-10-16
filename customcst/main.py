# main.py
#
#   python main.py                         -> models/patch_2g4.step
#   python main.py path/to/file.step       -> any STEP file
#   python main.py file.step --screenshot out.png   (render, save, quit)

import os
import sys

from vispy import app

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(HERE, "models", "patch_2g4.step")


def main(argv):
    screenshot = None
    if "--screenshot" in argv:
        i = argv.index("--screenshot")
        screenshot = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else DEFAULT_MODEL

    app.use_app("pyside6")
    vispy_app = app.use_app()
    vispy_app.create()

    from view.main_window import MainWindow

    window = MainWindow(path)
    window.show()

    if screenshot:
        from PySide6.QtCore import QTimer

        def grab():
            window.grab().save(screenshot)     # widget grab, never a screen grab
            vispy_app.quit()

        QTimer.singleShot(1500, grab)

    vispy_app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
