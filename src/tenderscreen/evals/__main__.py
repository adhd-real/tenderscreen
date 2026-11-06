"""``python -m tenderscreen.evals <labels_dir> <predictions_dir>``."""

import sys

from tenderscreen.evals.run import main

if __name__ == "__main__":
    sys.exit(main())
