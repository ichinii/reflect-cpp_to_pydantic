"""numpy arrays as out-of-band buffer references.

A large array never travels inside the JSON. On serialization it is appended to
a list of buffers supplied through the Pydantic serialization context and only a
reference to it is written:

    {"$buffer": 0, "dtype": "float64", "shape": [100000, 3]}

`toyscene.simulate` passes that list of numpy arrays straight to the extension
module, which wraps each one as a `toyscene::Array<double>` view -- no copy in
either direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema


@dataclass(frozen=True)
class ArraySpec:
    """`Annotated` metadata turning a field into a numpy array of fixed rank.

    Validation normalises whatever was passed in (`np.ascontiguousarray`) and
    checks the rank. Element-wise rules -- finiteness, non-negative weights,
    matching row counts -- are deliberately left to C++: they are cross-field or
    bulk-data rules, and C++ is where the single definition of them lives.
    """

    dtype: Any
    ndim: int

    @property
    def dtype_name(self) -> str:
        return np.dtype(self.dtype).name

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            self._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                self._serialize, info_arg=True, when_used="json"
            ),
        )

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "object",
            "properties": {
                "$buffer": {"type": "integer", "minimum": 0},
                "dtype": {"const": self.dtype_name},
                "shape": {"type": "array", "items": {"type": "integer", "minimum": 0}},
            },
            "required": ["$buffer", "dtype", "shape"],
            "x-dtype": self.dtype_name,
            "x-ndim": self.ndim,
        }

    def _validate(self, value: Any) -> np.ndarray:
        if isinstance(value, dict):
            raise ValueError(
                "expected an array, got a buffer reference; buffer references are "
                "produced on serialization and cannot be passed back in"
            )
        try:
            array = np.ascontiguousarray(value, dtype=self.dtype)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"could not be read as a {self.dtype_name} array: {exc}"
            ) from exc
        if array.ndim != self.ndim:
            raise ValueError(
                f"expected a {self.ndim}-dimensional array, but got "
                f"{array.ndim} dimension(s) with shape {array.shape}"
            )
        return array

    def _serialize(self, value: np.ndarray, info: Any) -> dict[str, Any]:
        context = info.context
        if not isinstance(context, dict) or "buffers" not in context:
            raise ValueError(
                "arrays are passed out of band, so serializing a scene needs a "
                'buffer sink: model_dump_json(context={"buffers": []}). '
                "toyscene.simulate() does this for you."
            )
        buffers = context["buffers"]
        array = np.ascontiguousarray(value, dtype=self.dtype)
        buffers.append(array)
        return {
            "$buffer": len(buffers) - 1,
            "dtype": self.dtype_name,
            "shape": list(array.shape),
        }


Float64Array1D = Annotated[np.ndarray, ArraySpec(np.float64, ndim=1)]
Float64Array2D = Annotated[np.ndarray, ArraySpec(np.float64, ndim=2)]
