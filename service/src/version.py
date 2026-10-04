"""Single source of truth for the running service version.

The git release tag is the source: CI passes it to the image build as
``APP_VERSION`` (publish-service.yml → Dockerfile ARG/ENV), the same
convention as the other lab services. Anything else (local runs, untagged
builds) reports ``0.0.0+dev``. Package metadata is deliberately not used: the
image installs dependencies only (``uv sync --no-install-project``), so it
found no ``duar-service`` distribution and every image reported
``0.0.0+unknown``.
"""

import os

__version__: str = os.environ.get("APP_VERSION") or "0.0.0+dev"
