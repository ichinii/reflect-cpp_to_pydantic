"""Base class for the generated models.

`scripts/generate_models.sh` passes this to datamodel-code-generator via
`--base-class`, so every generated model picks up the same configuration:

* `validate_default=True` -- the schema's defaults arrive as plain JSON (the
  default for `PointSource.divergence` is `{"type": "Rad", "value": 0.0}`), and
  without this they would stay dicts instead of becoming model instances.
* `extra="forbid"` -- a misspelled keyword is a mistake, not an addition.
  reflect-cpp rejects unknown fields too, so this keeps both sides aligned.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SceneModel(BaseModel):
    model_config = ConfigDict(validate_default=True, extra="forbid")
