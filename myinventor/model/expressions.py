# expressions.py
#
# Formulas of parameters and feature fields: "1.6", "L/2 + gap", "sqrt(a^2 + b^2)".
# Parsed with Python's ast and evaluated by walking the tree, so only numbers,
# names, + - * / ^ ** %, parentheses and the functions below can appear: a
# file never runs code. Numbers are floats (10**10**10 overflows at once
# instead of building a huge integer).
#
# Units: lengths in mm, angles in degrees, as in the UI. sin / cos / tan take
# degrees, asin / acos / atan / atan2 return degrees (the CAD habit).
#
# evaluate_parameters: values of a whole parameter table, in dependency
# order (a parameter may use one defined below it); every error names its
# parameter: a syntax error, an unknown name, a cycle, a dependency with an
# error, a value that is not finite.

import ast
import keyword
import math
import re

FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "round": round,
    "floor": math.floor, "ceil": math.ceil, "exp": math.exp, "log": math.log, "log10": math.log10,
    "hypot": math.hypot,
    "sin": lambda deg: math.sin(math.radians(deg)),
    "cos": lambda deg: math.cos(math.radians(deg)),
    "tan": lambda deg: math.tan(math.radians(deg)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
}
CONSTANTS = {"pi": math.pi, "e": math.e}
RESERVED = frozenset(FUNCTIONS) | frozenset(CONSTANTS)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_BINARY = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b,
}
_UNARY = {ast.USub: lambda a: -a, ast.UAdd: lambda a: a}


class ExpressionError(ValueError):
    pass


def name_error(name):
    """Why `name` cannot be a parameter name, or "" if it can."""
    if not isinstance(name, str) or not _NAME.match(name):
        return f"{name!r} is not a name (letters, digits, _; not starting with a digit)"
    if keyword.iskeyword(name) or name in RESERVED:
        return f"{name!r} is reserved"
    return ""


def parse(text):
    """The checked syntax tree of a formula; ExpressionError if it is not one."""
    if not isinstance(text, str) or not text.strip():
        raise ExpressionError("empty formula")
    try:
        tree = ast.parse(text.strip().replace("^", "**"), mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"syntax error in {text!r}: {error.msg}") from None
    for node in ast.walk(tree):
        _check(node, text)
    return tree


def _check(node, text):
    if isinstance(node, (ast.Expression, ast.Load, ast.Name)) or type(node) in _BINARY or type(node) in _UNARY:
        return
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        if type(node.op) in _BINARY or type(node.op) in _UNARY:
            return
        raise ExpressionError(f"operator not allowed in {text!r}")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return
        raise ExpressionError(f"only numbers are allowed in {text!r}")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            return
        name = node.func.id if isinstance(node.func, ast.Name) else "?"
        raise ExpressionError(f"unknown function {name!r} in {text!r}")
    raise ExpressionError(f"not allowed in a formula: {text!r}")


def names(text):
    """The parameter names a formula uses (functions and constants left out)."""
    tree = parse(text)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and id(n) not in called and n.id not in CONSTANTS}


def evaluate(text, values):
    """The value of a formula; `values` holds the parameters it may use."""
    return compile_formula(text)(values)


def compile_formula(text):
    """
    The formula parsed and checked once: a function values -> its value
    (ExpressionError as evaluate); for a formula evaluated many times (a
    curve's x(t), y(t)).
    """
    tree = parse(text)
    return lambda values: _value(tree, text, values)


def _value(tree, text, values):
    try:
        result = _eval(tree.body, values)
    except ExpressionError:
        raise
    except (ArithmeticError, ValueError, TypeError) as error:
        raise ExpressionError(f"{text!r}: {error}") from None
    if not math.isfinite(result):
        raise ExpressionError(f"{text!r} is not a finite number")
    return result


def _eval(node, values):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in values:
            return float(values[node.id])
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise ExpressionError(f"unknown name {node.id!r}")
    if isinstance(node, ast.BinOp):
        return float(_BINARY[type(node.op)](_eval(node.left, values), _eval(node.right, values)))
    if isinstance(node, ast.UnaryOp):
        return float(_UNARY[type(node.op)](_eval(node.operand, values)))
    if isinstance(node, ast.Call):
        return float(FUNCTIONS[node.func.id](*(_eval(a, values) for a in node.args)))
    raise ExpressionError("not allowed in a formula")


def rename(text, old, new):
    """The formula with the parameter `old` renamed to `new` (whole names only)."""
    return re.sub(rf"\b{re.escape(old)}\b", new, text)


def evaluate_parameters(parameters):
    """
    parameters: [(name, formula)] in table order.
    Returns (values {name: float}, errors {name: message}); every name lands in one of them.
    """
    formulas = dict(parameters)
    values, errors, uses = {}, {}, {}
    for name, formula in parameters:
        try:
            uses[name] = sorted(names(formula))
        except ExpressionError as error:
            errors[name] = str(error)

    def visit(name, path):
        if name in values or name in errors:
            return
        if name in path:
            cycle = path[path.index(name):] + [name]
            for member in cycle[:-1]:
                errors[member] = "circular reference: " + " -> ".join(cycle)
            return
        for used in uses[name]:
            if used not in formulas:
                errors[name] = f"unknown name {used!r}"
                return
            visit(used, path + [name])
            if name in errors:                  # it was on a cycle found below
                return
            if used in errors:
                errors[name] = f"uses {used!r}, which has an error"
                return
        try:
            values[name] = evaluate(formulas[name], values)
        except ExpressionError as error:
            errors[name] = str(error)

    for name, _ in parameters:
        visit(name, [])
    return values, errors
