# main.py
#
#   python main.py                       -> Qt window, default model
#   python main.py fdtd3d_dipole         -> a model from models/ (file stem)
#   python main.py path/to/my_model.toml -> any model file
#   python main.py --list                -> list the models in models/
#   python main.py --canvas-only [model] -> bare VisPy window, keyboard only

import sys

from vispy import app

from app_controller import AppController
from model_spec import ModelError


def main(argv):
    if "--list" in argv:
        for key, name, _ in AppController.available_models():
            print(f"{key:24s} {name}")
        return 0

    args = [a for a in argv if not a.startswith("--")]
    model = args[0] if args else None

    app.use_app("pyside6")

    try:
        if "--canvas-only" in argv:
            AppController(model=model).run()
            return 0

        vispy_app = app.use_app()
        vispy_app.create()  # the QApplication shared by VisPy and the panels

        from main_window import MainWindow

        window = MainWindow(model=model)
    except ModelError as error:
        print(f"Model error: {error}", file=sys.stderr)
        return 1

    window.show()
    vispy_app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
