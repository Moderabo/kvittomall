"""Write-then-validate-then-rename helper so no stage ever leaves a half-written file
that could be mistaken for a finished one.
"""

import os
from typing import Callable
from uuid import uuid4


def atomic_write(dst_path: str, writer: Callable[[str], None], validate: Callable[[str], None]) -> None:
    """Call writer(tmp_path) to produce the file, validate(tmp_path) to check it, then
    atomically rename it into place. The temp file lives next to dst_path so the final
    os.replace() is a same-filesystem atomic rename. On any failure the temp file is
    removed and dst_path is left untouched.
    """
    tmp_path = f"{dst_path}.tmp.{uuid4().hex[:8]}"
    try:
        writer(tmp_path)
        validate(tmp_path)
        os.replace(tmp_path, dst_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def no_validation(_path: str) -> None:
    """Placeholder validator for writes that have nothing meaningful to check."""
