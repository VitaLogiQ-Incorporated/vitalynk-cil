"""Process entrypoint: ``cil`` console script / ``python -m cil``.

Boots the FastAPI app under uvicorn using the runtime settings.
"""

from __future__ import annotations

import uvicorn

from cil.config import get_settings
from cil.logging import configure_logging


def run() -> None:
    """Run the CIL service."""
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "cil.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,  # logging is configured by cil.logging
    )


if __name__ == "__main__":
    run()
