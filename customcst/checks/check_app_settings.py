# check_app_settings.py
#
# Application settings (app_settings.py), in a temporary file via
# CUSTOMCST_SETTINGS (never the user's real settings):
#   - no file -> defaults; round trip; the folder is created;
#   - recent files: newest first, no duplicates (case-insensitive on
#     Windows), at most MAX_RECENT, the newest EXISTING one is picked;
#   - a damaged file -> defaults with an error text, never an exception;
#   - unknown keys survive a save.
#
#   python checks/check_app_settings.py

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "sub", "settings.json")
    from app_settings import MAX_RECENT, AppSettings, settings_path    # after the override

    try:
        check("path override", settings_path() == os.environ["CUSTOMCST_SETTINGS"], settings_path())
        s = AppSettings.load()
        check("no file -> defaults", s.recent_files == [] and not s.error and s.last_existing() is None)

        files = []
        for n in range(3):
            p = os.path.join(folder, f"m{n}.stp")
            open(p, "w").close()
            files.append(p)
        for p in files:
            s.add_recent(p)
        s.add_recent(files[0].upper() if os.name == "nt" else files[0])     # same file again
        check("newest first, no duplicates", len(s.recent_files) == 3
              and os.path.normcase(s.recent_files[0]) == os.path.normcase(files[0]), str(s.recent_files))
        check("save creates the folder", s.save() == "" and os.path.isfile(settings_path()))

        back = AppSettings.load()
        check("round trip", back.recent_files == s.recent_files)
        os.remove(files[0])
        check("last existing skips a deleted file", back.last_existing() == s.recent_files[1],
              str(back.last_existing()))
        back.remove_recent(files[0])
        check("remove", len(back.recent_files) == 2)

        for n in range(MAX_RECENT + 5):
            back.add_recent(os.path.join(folder, f"x{n}.stp"))
        check(f"at most {MAX_RECENT}", len(back.recent_files) == MAX_RECENT)

        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "recent_files": [files[1]], "future_option": {"w": 1400}}, f)
        kept = AppSettings.load()
        kept.add_recent(files[2])
        kept.save()
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        check("unknown keys kept", data.get("future_option") == {"w": 1400}, str(data))

        kept.window = {"x": -1900, "y": 40, "width": 1600, "height": 1000, "maximized": True}
        kept.save()
        check("window round trip", AppSettings.load().window == kept.window, str(AppSettings.load().window))
        for label, bad in (("missing key", {"x": 1, "y": 1, "width": 800}),
                           ("text", {"x": "a", "y": 1, "width": 800, "height": 600}),
                           ("too small", {"x": 1, "y": 1, "width": 50, "height": 600}),
                           ("not a table", [1, 2, 3])):
            with open(settings_path(), "w", encoding="utf-8") as f:
                json.dump({"recent_files": [files[1]], "window": bad}, f)
            loaded = AppSettings.load()
            check(f"bad window ({label}) forgotten, the rest kept",
                  loaded.window is None and loaded.recent_files == [files[1]] and not loaded.error)

        for label, text in (("not JSON", "{oops"), ("not an object", "[1, 2]"),
                            ("bad recent list", '{"recent_files": "x"}')):
            with open(settings_path(), "w", encoding="utf-8") as f:
                f.write(text)
            damaged = AppSettings.load()
            check(f"damaged file ({label}) -> defaults + error",
                  damaged.recent_files == [] and damaged.error != "", damaged.error)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
