# scripts/debug_import.py
# PowerShell-friendly import check run inside the container:
#   docker compose exec api python scripts/debug_import.py

import os
import sys
import importlib
import pkgutil

print("PYTHONPATH =", os.getenv("PYTHONPATH"))
print("sys.path[:4] =", sys.path[:4])

# 1) Can we import the top-level 'api' package?
try:
    import api
    print("import api -> OK;", "file:", getattr(api, "__file__", "(pkg)"))
except Exception as e:
    print("import api -> FAILED:", repr(e))
    raise

# 2) What subpackages are visible under 'api'?
try:
    names = [m.name for m in pkgutil.iter_modules(api.__path__)]
    print("api subpackages:", names)
except Exception as e:
    print("listing api subpackages FAILED:", repr(e))
    raise

# 3) Try to import the OneDrive integration module
try:
    m = importlib.import_module("api.integrations.ms365")
    print("import api.integrations.ms365 -> OK;", "file:", getattr(m, "__file__", "?"))
except Exception as e:
    print("import api.integrations.ms365 -> FAILED:", repr(e))
    raise

print("DEBUG IMPORT CHECK: OK")
