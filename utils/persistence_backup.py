"""Rotated backups for the PicklePersistence file.

All subscriptions, brief times and alert state live in a single pickle. PTB
rewrites it in place, so a crash or full disk mid-write can leave it truncated
and every user silently loses their subscriptions. Keeping a few generations
costs nothing and makes that recoverable.
"""

import pickle
import shutil
from pathlib import Path

from loguru import logger


def _pickle_loads_cleanly(source: Path) -> bool:
    """Whether the persistence file deserializes at all.

    The rotation runs on every startup and Docker restarts on crash, so a
    truncated pickle would otherwise overwrite bak1..N with corrupt copies
    within three restarts — destroying the backups exactly when they are
    needed. chat_data holds only stdlib types, so a bare load is safe.
    """
    try:
        with source.open("rb") as handle:
            pickle.load(handle)
        return True
    except Exception as error:
        logger.error(
            f"持久化文件损坏，跳过备份轮转以保住现有备份: {source.name}: {error}。"
            f"可用 .bak1..N 手动恢复。"
        )
        return False


def rotate_persistence_backups(path: str | Path, keep: int = 3) -> None:
    """Shift ``file.bak1..bakN`` down and copy the live file into ``bak1``.

    A missing/empty/corrupt source is skipped: backing up garbage would only
    push good generations out of the window.
    """
    if keep <= 0:
        return

    source = Path(path)
    try:
        if not source.exists() or source.stat().st_size == 0:
            return
        if not _pickle_loads_cleanly(source):
            return

        # Drop the oldest, then shift the rest down one slot.
        oldest = source.with_suffix(source.suffix + f".bak{keep}")
        if oldest.exists():
            oldest.unlink()
        for index in range(keep - 1, 0, -1):
            older = source.with_suffix(source.suffix + f".bak{index}")
            if older.exists():
                older.replace(source.with_suffix(source.suffix + f".bak{index + 1}"))

        shutil.copy2(source, source.with_suffix(source.suffix + ".bak1"))
        logger.info(f"持久化文件已备份: {source.name} -> {source.name}.bak1（保留 {keep} 份）")
    except Exception as error:
        # A failed backup must never stop the bot from starting.
        logger.warning(f"持久化备份失败（不影响启动）: {error}")
