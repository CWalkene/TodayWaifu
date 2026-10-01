"""图片体积压缩，以及压缩结果的磁盘缓存。

零点高峰的 CPU 大头不在 Python 代码里，而在 Pillow 的解码 + WebP 编码：
实测一张 8.9MB 立绘压到 2MB 要 1.3 秒，8 张并行在 4 核机器上要 3.8 秒，
整个进程（含 Core 与其它插件）一起被抢走 CPU。这部分本来就跑在 C 里，
用 Cython 重写 Python 层拿不回任何时间，只能「少做」：

- 压缩结果按 (原图内容哈希, 阈值, 格式) 落盘缓存：同一张图只压一次，
  之后每次发送只是一次读盘。预热时顺带压好，00:00 直接命中。
- 先按体积比例估算目标边长再编码，通常 1~2 次编码就达标，
  而不是逐档尝试（旧实现最坏 30 次）。
- 用线程信号量限制同时进行的压缩数量，给 Core 留出 CPU 核。

本模块只依赖标准库与 Pillow，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import io
import os
import math
import hashlib
import tempfile
import threading
from pathlib import Path

from PIL import Image

# 与旧实现一致：输出最长边不超过 1920，最小不低于 640
MAX_SIDE = 1920
MIN_SIDE = 640
QUALITIES = (85, 65, 45)

# 估算目标边长时预留的余量：体积与像素数并非严格线性
_SIZE_SAFETY = 0.9

# 同时进行的压缩数量上限：至少 1，最多占一半 CPU 核
MAX_CONCURRENT_SHRINKS = max(1, (os.cpu_count() or 2) // 2)
_SHRINK_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SHRINKS)

SHRINK_CACHE_PREFIX = 'shrink_'


def _output_format() -> str:
    return 'WEBP' if 'WEBP' in Image.SAVE else 'JPEG'


def _prepare(opened: Image.Image, fmt: str) -> Image.Image:
    has_alpha = opened.mode in ('RGBA', 'LA', 'PA') or (
        opened.mode == 'P' and 'transparency' in opened.info
    )
    if fmt == 'WEBP':
        # WebP 支持 alpha，保留透明通道
        return opened.convert('RGBA' if has_alpha else 'RGB')
    if has_alpha:
        # JPEG 会丢 alpha，合成到白底，避免透明区域变黑
        rgba = opened.convert('RGBA')
        working = Image.new('RGB', rgba.size, (255, 255, 255))
        working.paste(rgba, mask=rgba.split()[-1])
        return working
    return opened.convert('RGB')


def _encode(image: Image.Image, fmt: str, quality: int) -> bytes:
    buffer = io.BytesIO()
    if fmt == 'WEBP':
        image.save(buffer, format='WEBP', quality=quality, method=4)
    else:
        image.save(buffer, format='JPEG', quality=quality, optimize=True)
    return buffer.getvalue()


def _resized(image: Image.Image, side: int) -> Image.Image:
    if max(image.size) <= side:
        return image
    copy = image.copy()
    copy.thumbnail((side, side))
    return copy


def shrink_image_bytes(raw: bytes, limit: int) -> bytes:
    """把图片压到 `limit` 字节以内；无需压缩、动图、解码失败或压不动时原样返回。"""
    if limit <= 0 or len(raw) <= limit:
        return raw
    fmt = _output_format()
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if bool(getattr(opened, 'is_animated', False)):
                return raw
            working = _prepare(opened, fmt)
    except Exception:
        # 解码失败（含 DecompressionBombError）一律原样返回，不影响发送
        return raw

    side = min(MAX_SIDE, max(working.size))
    while True:
        candidate = _resized(working, side)
        data = b''
        for quality in QUALITIES:
            data = _encode(candidate, fmt, quality)
            if len(data) <= limit:
                return data
        if side <= MIN_SIDE:
            return raw
        # 体积大致与像素数成正比：按最后一次（最低画质）的结果估算下一档边长，
        # 至少缩小 20%，保证循环快速收敛
        ratio = math.sqrt(limit / len(data) * _SIZE_SAFETY)
        side = max(MIN_SIDE, int(side * min(ratio, 0.8)))


def shrink_cache_path(cache_root: Path, raw: bytes, limit: int) -> Path:
    digest = hashlib.sha256(raw).hexdigest()
    return cache_root / f'{SHRINK_CACHE_PREFIX}{_output_format().lower()}_{limit}_{digest}'


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def shrink_image_cached(raw: bytes, limit: int, cache_root: Path | None) -> bytes:
    """带磁盘缓存与并发上限的压缩（阻塞，须在线程池里调用）。

    缓存只是加速：读写失败都退回直接压缩，不影响发送。
    原图本身已达标时不落盘，避免无意义地复制一份。
    """
    if limit <= 0 or len(raw) <= limit:
        return raw
    path = shrink_cache_path(cache_root, raw, limit) if cache_root is not None else None
    if path is not None:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path.read_bytes()
        except OSError:
            pass
    with _SHRINK_SLOTS:
        data = shrink_image_bytes(raw, limit)
    if path is not None and data is not raw:
        try:
            _write_atomic(path, data)
        except OSError:
            pass
    return data
