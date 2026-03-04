#!/usr/bin/env python3
from __future__ import annotations

"""
Compatibility wrapper.

`services/gateway_service.py` remains the stable entrypoint while the runtime
implementation now lives in:
- services/gateway_adapter_service.py
- services/gateway_core_service.py
"""

from services.gateway_adapter_service import *  # noqa: F401,F403


if __name__ == "__main__":
    main()

