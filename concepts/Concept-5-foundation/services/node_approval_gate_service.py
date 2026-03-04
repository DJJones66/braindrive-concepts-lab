#!/usr/bin/env python3
from __future__ import annotations

import os

os.environ["NODE_KIND"] = "approval_gate"

from node_service import main


if __name__ == "__main__":
    main()
