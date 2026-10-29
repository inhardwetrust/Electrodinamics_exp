# rebuild.py
#
# Part (the recipe) -> Result (what it makes), from scratch every time:
#   1. the parameters' values (model/expressions.py);
#   2. the features in history order: each one gets the values and the
#      results of the features above it (ctx) and returns its own result;
#      a result with a `body` (an extrusion) adds that body.
#      A feature fails alone: its error is recorded and the features that
#      refer to it fail too ("uses X, which failed"); the others still build.
# Nothing here is saved; the window shows the Result.

from dataclasses import dataclass, field

from model.expressions import evaluate_parameters


@dataclass
class Result:
    values: dict = field(default_factory=dict)          # parameter -> value (mm, deg)
    errors: dict = field(default_factory=dict)          # parameter -> message
    feature_errors: dict = field(default_factory=dict)  # feature -> message
    outputs: dict = field(default_factory=dict)         # feature -> what its build returned
    bodies: list = field(default_factory=list)          # Body, named, for the view and STEP


@dataclass
class BuildContext:
    part: object
    values: dict
    outputs: dict

    @property
    def kernel(self):
        """geometry/kernel.py, imported only when a feature needs OCP (model/ stays OCP-free)."""
        from geometry import kernel
        return kernel


def rebuild(part):
    result = Result()
    result.values, result.errors = evaluate_parameters([(p.name, p.formula) for p in part.parameters])
    ctx = BuildContext(part, result.values, result.outputs)

    for feature in part.features:
        failed = [ref for ref in feature.references() if ref in result.feature_errors]
        if failed:
            result.feature_errors[feature.name] = f"uses {failed[0]!r}, which failed"
            continue
        try:
            result.outputs[feature.name] = feature.build(ctx)
        except Exception as error:          # a feature's failure must not stop the rebuild
            result.feature_errors[feature.name] = str(error) or type(error).__name__
    result.bodies = [out.body for out in result.outputs.values() if getattr(out, "body", None) is not None]
    return result
