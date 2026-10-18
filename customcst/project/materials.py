# materials.py
#
# The material library of a project: named dielectric presets (eps_r,
# tan_d at f_ref, mu_r) that dielectric bodies can use.
#
# Built-in presets come from the program (BUILTIN). The project's library
# starts from them and is saved into the model's TOML, so a model carries
# the exact values it was computed with:
#
#   [materials.FR4]
#   eps_r = 4.3
#   tan_d = 0.02
#   f_ref = 3.0   # GHz
#   mu_r = 1.0
#
# A preset in the TOML overrides the built-in one of the same name.
# A dielectric body names its material (`material = "FR4"`) and carries a
# copy of its values: the solver only ever reads the body's values. Editing
# a value of the body by hand turns it into CUSTOM (its own values);
# editing a preset updates every body that uses it.
#
# Built-in presets can be edited and reset, but not renamed or deleted
# (they would come back from the program on the next load).

CUSTOM = "Custom"

# The dielectric parameters a preset defines (names of the dielectric
# type's Param objects, see object_types.py).
MATERIAL_PARAMS = ("eps_r", "tan_d", "f_ref", "mu_r")

BUILTIN = {
    # The dielectric type's defaults: vacuum / air.
    "Default": {"eps_r": 1.0, "tan_d": 0.0, "f_ref": 10.0, "mu_r": 1.0},
    # Typical FR4 laminate in the low GHz range (datasheets: Dk 4.2-4.6,
    # Df 0.015-0.025, both drifting with frequency and resin content).
    "FR4": {"eps_r": 4.3, "tan_d": 0.02, "f_ref": 3.0, "mu_r": 1.0},
    # PTFE (Teflon), the dielectric of SMA / N connectors and RF cables.
    "PTFE": {"eps_r": 2.1, "tan_d": 0.0002, "f_ref": 10.0, "mu_r": 1.0},
}


def builtin_library():
    """A fresh copy of the built-in presets."""
    return {name: dict(values) for name, values in BUILTIN.items()}
