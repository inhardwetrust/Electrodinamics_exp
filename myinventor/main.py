# main.py
#
#   python main.py                         -> the last part opened (app_settings.py),
#                                             or a new part if there is none
#   python main.py path/to/name.part.toml  -> that part
#   python main.py --screenshot out.png    (render, save, quit)

import os
import sys

from vispy import app

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    screenshot = None
    if "--screenshot" in argv:
        i = argv.index("--screenshot")
        screenshot = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    from app_settings import AppSettings

    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else AppSettings.load().last_existing()

    app.use_app("pyside6")
    vispy_app = app.use_app()
    vispy_app.create()

    from view.main_window import MainWindow

    window = MainWindow(path)
    if screenshot:
        window.show()                           # a fixed size for screenshots
    else:
        window.show_restored()                  # size / maximized as last closed

    if screenshot:
        from PySide6.QtCore import QTimer

        def grab():
            window.grab().save(screenshot)      # widget grab, never a screen grab
            vispy_app.quit()

        QTimer.singleShot(1000, grab)

    vispy_app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
