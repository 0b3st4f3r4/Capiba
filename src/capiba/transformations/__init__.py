"""Named, reusable transformations for declarative pipelines.

Each transformation lives in its own module inside this package and exposes
a ``transform(records, **params) -> list[dict]`` function. The YAML spec
references transformations by module name (e.g. ``filter_by_min_value``);
``capiba.pipeline.registry.get_transformation`` resolves the name to the
callable, importing the module on demand.

Example (YAML):

    transformations:
      - name: filter_by_min_value
        params:
          min_value: 1000
"""

from __future__ import annotations
