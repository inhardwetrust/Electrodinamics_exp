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
# Nothing here is saved; the window shows the Result. While a sketch is
# edited the window rebuilds only up to it (upto): no bodies tessellated.

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
    colors: dict = field(default_factory=dict)          # body name -> (r, g, b): its own, else an import's
    upto: str = None                                    # built only up to this feature (no bodies), or None


@dataclass
class BuildContext:
    part: object
    values: dict
    outputs: dict
    shapes: dict = field(default_factory=dict)          # body name -> OCP shape (mm), in the order made
    parameter_errors: dict = field(default_factory=dict)
    folder: str = None                                  # the part file's folder (an import's file is in it)
    colors: dict = field(default_factory=dict)          # body name -> (r, g, b) by default (an import's)

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


def rebuild(part, upto=None, folder=None, tessellate=True, placement=True):
    """
    The part's Result; upto: only the features up to that one (inclusive),
    the bodies not tessellated; folder: the part file's (imports' files);
    tessellate=False: the shapes only (a preview needs no triangles);
    placement=False: the bodies as the features built them (what modeling
    sees: a sketch, its faces), no transform applied.
    """
    result = Result(upto=upto)
    result.values, result.errors = evaluate_parameters([(p.name, p.formula) for p in part.parameters])
    ctx = BuildContext(part, result.values, result.outputs, parameter_errors=result.errors, folder=folder)

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
        if feature.name == upto:
            result.shapes = dict(ctx.shapes)
            return result
    # Placement: the transforms, in order, on the bodies as built (model/transforms.py).
    from model.transforms import order_error

    for i, transform in enumerate(part.transforms if placement else []):
        failed = [ref for ref in transform.references() if ref in result.feature_errors]
        try:
            if failed:
                raise ValueError(f"uses {failed[0]!r}, which failed")
            error = order_error(part.transforms, part, i)
            if error:
                raise ValueError(error)
            result.outputs[transform.name] = transform.apply(ctx)
        except ValueError as error:
            result.feature_errors[transform.name] = str(error)
    # The bodies as the whole history left them, tessellated once.
    result.colors = {name: _color(part.body_color(name)) or ctx.colors.get(name) for name in ctx.shapes}
    result.bodies = [ctx.kernel.body(name, shape, result.colors[name])
                     for name, shape in ctx.shapes.items()] if ctx.shapes and tessellate else []
    result.shapes = dict(ctx.shapes)
    return result
