"""What every offline check runs with: an endpoint nothing listens on.

Not a check itself.  Importing it points the default endpoint
(PROOFREAD_ENDPOINT) at CLOSED_ENDPOINT, before any check runs and whatever
the environment named; processes the check starts inherit it.  So no request
of a check that runs proofread_pages.main() or book_profile.main() without
saying where reaches the machine's model server.  tests/stand_in.py imports
it; so does every check that runs the scripts without it.
"""

from __future__ import annotations

import os

# The discard port, which only root may open: the endpoint of every stand-in
# run, so no request of a check reaches a model server.
CLOSED_ENDPOINT = "http://127.0.0.1:9/v1/chat/completions"
ENDPOINT_ENV = "PROOFREAD_ENDPOINT"
os.environ[ENDPOINT_ENV] = CLOSED_ENDPOINT
