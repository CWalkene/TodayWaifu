from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path


def read_json_dict(path: Path) -> dict[str, object]:
    """读回一个 JSON 对象，任何形式的损坏都降级为空字典。

    读取方（旧数据迁移、用户自定义角色表）都工作在「文件可能不存在、可能被手工
    编辑坏、可能是历史版本写下的其他类型」这一前提下。此处不抛异常是刻意的：
    调用方无法区分「首次运行」与「文件损坏」，而两者都应按空数据处理并继续启动，
    否则一次编辑失误就会让插件永久无法加载。
    """
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    # 统一键为字符串：JSON 允许数字键，而调用方一律按字符串索引。
    return {str(key): item for key, item in value.items()}


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    """以「同目录临时文件 + 原子替换」写入 JSON，避免读者看到半截文件。

    临时文件必须与目标同目录：`os.replace` 仅在源与目标位于同一文件系统时才是
    原子的，跨设备会退化为复制。写入后 `fsync` 则确保替换发生前数据已落盘——
    否则断电可能发生在目录项更新之后、数据块落盘之前，留下长度正确但内容为空的新文件。

    失败路径上删除临时文件，避免中断反复发生后在数据目录内堆积残留。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        # 替换成功后临时文件已不存在，此分支只覆盖写入或替换失败的场景。
        if temporary.exists():
            temporary.unlink()
