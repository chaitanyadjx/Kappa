"""Library-API hints for the Kappa KnowledgeDB.

Maps common unbound names to their qualified forms + the import needed.
Populated by the Parser, consulted by the Detector as a suggestion candidate
of last resort (when no window-local, file-level, repo-level, or embedding
candidate reaches the confidence threshold).

This is the KnowledgeDB equivalent of mypy's built-in stubs: it lets the
repair mechanism cover the "undefined name from a known library" class
without requiring the import to be present in the hunk window.
"""

from __future__ import annotations

# {unbound_name: (qualified_form, import_statement)}
LIBRARY_HINTS: dict[str, tuple[str, str]] = {
    # pandas
    "DataFrame": ("pd.DataFrame", "import pandas as pd"),
    "Series": ("pd.Series", "import pandas as pd"),
    "read_csv": ("pd.read_csv", "import pandas as pd"),
    "read_excel": ("pd.read_excel", "import pandas as pd"),
    "concat": ("pd.concat", "import pandas as pd"),
    "merge": ("pd.merge", "import pandas as pd"),
    "to_datetime": ("pd.to_datetime", "import pandas as pd"),
    # numpy
    "ndarray": ("np.ndarray", "import numpy as np"),
    "array": ("np.array", "import numpy as np"),
    "zeros": ("np.zeros", "import numpy as np"),
    "ones": ("np.ones", "import numpy as np"),
    "arange": ("np.arange", "import numpy as np"),
    "linspace": ("np.linspace", "import numpy as np"),
    "nan": ("np.nan", "import numpy as np"),
    "inf": ("np.inf", "import numpy as np"),
    "sqrt": ("np.sqrt", "import numpy as np"),
    "exp": ("np.exp", "import numpy as np"),
    "log": ("np.log", "import numpy as np"),
    # os / sys
    "environ": ("os.environ", "import os"),
    "getenv": ("os.getenv", "import os"),
    "path": ("os.path", "import os"),
    "sep": ("os.sep", "import os"),
    # collections / typing
    "defaultdict": ("collections.defaultdict", "from collections import defaultdict"),
    "OrderedDict": ("collections.OrderedDict", "from collections import OrderedDict"),
    "Counter": ("collections.Counter", "from collections import Counter"),
    "Deque": ("collections.deque", "from collections import deque"),
    "Optional": ("typing.Optional", "from typing import Optional"),
    "List": ("typing.List", "from typing import List"),
    "Dict": ("typing.Dict", "from typing import Dict"),
    "Any": ("typing.Any", "from typing import Any"),
    "Callable": ("typing.Callable", "from typing import Callable"),
}


def lookup(name: str) -> dict | None:
    """Return a suggestion record for `name` if it matches a library hint."""
    hint = LIBRARY_HINTS.get(name)
    if hint is None:
        return None
    qualified, import_statement = hint
    return {
        "kind": "library-hint",
        "current": name,
        "closest": qualified,
        "import_statement": import_statement,
        "score": 0.8,
    }
