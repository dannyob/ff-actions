import importlib.util
import os
import sys
import unittest

# Load the dash-named script as a module.
_HERE = os.path.dirname(__file__)
_SCRIPT = os.path.join(_HERE, "..", "usdfc-liquidity-monitor.py")
_spec = importlib.util.spec_from_file_location("monitor", _SCRIPT)
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)


if __name__ == "__main__":
    unittest.main()
