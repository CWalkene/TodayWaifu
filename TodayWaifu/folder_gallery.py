from __future__ import annotations

from pathlib import Path


def find_named_role_directory(root: Path, role_name: str) -> Path | None:
    """在图库根目录中安全匹配已经存在的一级角色目录。

    只按名称在一级子目录中查找，不拼接、不规范化为路径：调用方传入的 ``../露西亚``
    这类值会被 casefold 后与真实目录名比较，永远无法匹配，从而把路径穿越挡在比较
    阶段而非依赖后续校验。匹配为精确的整名比较而非子串包含，避免「露」误命中「露西亚」。
    目录不存在或名称全为空白时返回 None，由调用方决定是新建还是报错。
    """
    target = role_name.strip().casefold()
    if not target or not root.is_dir():
        return None
    for path in root.iterdir():
        # 隐藏目录一律排除：它们通常是编辑器或系统的元数据目录，不应被当作角色图库。
        if (
            path.is_dir()
            and not path.name.startswith('.')
            and path.name.strip().casefold() == target
        ):
            return path
    return None


def scan_named_role_directories(
    root: Path,
    image_extensions: set[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """读取“角色名/图片”目录，并返回稳定排序后的角色图库。

    递归扫描而非只看一级文件，是为了兼容「角色/皮肤子目录」的整理方式；排序在角色名
    与文件名两级都用 casefold 后的键，保证同一目录在不同平台上得到一致顺序，否则抽卡
    结果会随文件系统返回顺序漂移。返回绝对路径，避免后续读取受当前工作目录变化影响。

    缺根目录时按需创建：调用方期望此处即可完成初始化，返回空图库而不是抛错。
    """
    root.mkdir(parents=True, exist_ok=True)
    extensions = {suffix.casefold() for suffix in image_extensions}
    roles: list[tuple[str, tuple[str, ...]]] = []

    role_dirs = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith('.')
        ),
        key=lambda path: path.name.casefold(),
    )
    for role_dir in role_dirs:
        role_name = role_dir.name.strip()
        # 纯空白目录名无法作为角色名对外展示，跳过而不清理，以免误删用户数据。
        if not role_name:
            continue

        # seen 以 resolve 后的路径为键：符号链接与真实路径指向同一文件时只收录一次，
        # 避免同一张图在结果中重复出现而抬高其在随机抽取中的权重。
        seen: set[str] = set()
        images: list[str] = []
        for path in sorted(role_dir.rglob('*'), key=lambda item: str(item).casefold()):
            if (
                not path.is_file()
                or path.name.startswith('.')
                or path.suffix.casefold() not in extensions
            ):
                continue
            resolved = str(path.resolve())
            key = resolved.casefold()
            if key not in seen:
                seen.add(key)
                images.append(resolved)

        # 无有效图片的角色不进图库：保留空条目会在抽取时命中无图角色并导致发送失败。
        if images:
            roles.append((role_name, tuple(images)))
    return tuple(roles)
