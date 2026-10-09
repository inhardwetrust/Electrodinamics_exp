# main.py
#
#   python main.py   -> Qt window: 1D local diffusion, heatmap + controls

import sys

from vispy import app


def main():
    app.use_app("pyside6")
    vispy_app = app.use_app()
    vispy_app.create()  # the QApplication shared by VisPy and the panel

    from main_window import MainWindow

    window = MainWindow()
    window.show()
    vispy_app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
