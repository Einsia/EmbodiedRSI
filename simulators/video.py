"""Encode raw frames already present in public policy observations."""

from pathlib import Path

import numpy as np


def write_videos(
    frames_by_view: dict[str, list[np.ndarray]],
    out_dir: Path,
    *,
    stem: str,
    fps: int = 30,
    native_size: bool = False,
) -> dict[str, str]:
    """Write one mp4 per view; return view -> path. Empty views are skipped."""
    import imageio.v3 as iio

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for view, frames in frames_by_view.items():
        if not frames:
            continue
        path = out_dir / f"{stem}_{view}.mp4"
        options = {"macro_block_size": 1} if native_size else {}
        iio.imwrite(path, np.stack(frames), fps=fps, codec="libx264", **options)
        written[view] = str(path)
    return written
