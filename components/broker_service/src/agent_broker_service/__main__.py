"""Run the broker service in the foreground: ``python -m agent_broker_service``."""
import sys

from .app import main

sys.exit(main())
