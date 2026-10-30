# main.py
#
#   python main.py                         -> the last part opened (app_settings.py),
#                                             or a new part if there is none
#   python main.py path/to/name.part.toml  -> that part
#   python main.py --screenshot out.png    (render, save, quit)
#   python main.py name.part.toml --locked -> opened under Claude's control (view/control.py):
#                                             the user only looks until "Take control back"
#   python main.py name.part.toml --report [--json] [--export-step out.step] [--set NAME=FORMULA ...]
#                                          -> no window: cli.py (--set: a parameter's formula for
#                                             this run only, the file not written)

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    if "--report" in argv or "--export-step" in argv or "--set" in argv:
        return headless(argv)
    from vispy import app

    return windowed(argv, app)


def headless(argv):
    step = None
    if "--export-step" in argv:
        i = argv.index("--export-step")
        if i + 1 >= len(argv):
            print("--export-step needs a file name")
            return 2
        step = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    formulas = {}
    while "--set" in argv:
        i = argv.index("--set")
        given = argv[i + 1] if i + 1 < len(argv) else ""
        name, _, formula = given.partition("=")
        if not name.strip() or not formula.strip():
            print(f"--set needs NAME=FORMULA, not {given!r}")
            return 2
        formulas[name.strip()] = formula.strip()
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("usage: main.py name.part.toml --report [--json] [--export-step out.step] [--set NAME=FORMULA ...]")
        return 2
    import cli

    return cli.run(args[0], "--report" in argv, "--json" in argv, step, formulas=formulas)


def windowed(argv, app):
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
    if "--locked" in argv:
        window.set_locked(True, "started by Claude")

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
