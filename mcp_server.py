"""Inicia el servidor MCP desde cualquier carpeta: las rutas de data/ son relativas a la raíz."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app.mcp.server import main  # noqa: E402


if __name__ == "__main__":
    main()
