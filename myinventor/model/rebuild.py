# rebuild.py
#
# Part (the recipe) -> Result (what it makes), from scratch every time:
#   1. the parameters' values (model/expressions.py);
#   2. the features in history order: each one gets the values and the
#      results of the features above it (ctx) and returns its own result;
#      features that make or change bodies keep them in ctx.shapes by name
#      (an extrusion: new, join, cut, intersect); the bodies are tessellated
#      once at the end.
#      A feature fails alone: its error is recorded and the features that
#      refer to it fail too ("uses X, which failed"); the others still build.
# Nothing here is saved; the window shows the Result.

from dataclasses import dataclass, field

from model.expressions import ExpressionError, evaluate, evaluate_parameters, names
from model.features import PartialBuild


@dataclass
class Result:
    values: dict = field(default_factory=dict)          # parameter -> value (mm, deg)
    errors: dict = field(default_factory=dict)          # parameter -> message
    feature_errors: dict = field(default_factory=dict)  # feature -> message
    outputs: dict = field(default_factory=dict)         # feature -> what its build returned
    bodies: list = field(default_factory=list)          # Body, named, for the view and STEP
    shapes: dict = field(default_factory=dict)          # body name -> OCP shape (mm): exact, for STEP


@dataclass
class BuildContext:
    part: object
    values: dict
    outputs: dict
    shapes: dict = field(default_factory=dict)          # body name -> OCP shape (mm), in the order made
    parameter_errors: dict = field(default_factory=dict)

    def evaluate(self, formula, what):
        """
        A feature's formula -> float, or ValueError saying what failed:
        "distance uses h, which has an error: ..." or "distance: ...".
        """
        try:
            return evaluate(formula, self.values)
        except ExpressionError as error:
            try:
                broken = sorted(n for n in names(formula) if n in self.parameter_errors)
            except ExpressionError:
                broken = []
            if broken:
                raise ValueError(f"{what} uses {broken[0]}, which has an error: "
                                 f"{self.parameter_errors[broken[0]]}") from None
            raise ValueError(f"{what}: {error}") from None

    @property
    def kernel(self):
        """geometry/kernel.py, imported only when a feature needs OCP (model/ stays OCP-free)."""
        from geometry import kernel
        return kernel


def _color(text):
    from model.part import color_rgb
    return None if text is None else color_rgb(text)


def rebuild(part):
    result = Result()
    result.values, result.errors = evaluate_parameters([(p.name, p.formula) for p in part.parameters])
    ctx = BuildContext(part, result.values, result.outputs, parameter_errors=result.errors)

    for feature in part.features:
        failed = [ref for ref in feature.references() if ref in result.feature_errors]
        if failed:
            result.feature_errors[feature.name] = f"uses {failed[0]!r}, which failed"
            continue
        try:
            result.outputs[feature.name] = feature.build(ctx)
        except PartialBuild as error:       # failed, but still shown (a sketch with a broken polyline)
            result.outputs[feature.name] = error.output
            result.feature_errors[feature.name] = str(error)
        except Exception as error:          # a feature's failure must not stop the rebuild
            result.feature_errors[feature.name] = str(error) or type(error).__name__
    # The bodies as the whole history left them, tessellated once.
    result.bodies = [ctx.kernel.body(name, shape, _color(part.body_color(name)))
                     for name, shape in ctx.shapes.items()] if ctx.shapes else []
    result.shapes = dict(ctx.shapes)
    return result
