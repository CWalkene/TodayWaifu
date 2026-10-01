"""角色对照表的 JSON 读写（TXT 仅作为旧数据兼容入口）。

对照表统一为 JSON：

- 内置 ``role_id_map.json`` 按模式分节：``{"version": 1, "wife": {...}, "husband": {...}, "nte": {...}}``
- 用户上传的自定义老婆 ``custom_role_map.json`` 是扁平结构：``{"900001": "达妮娅"}``

为兼容老版本，本模块仍可解析 TXT（``1234：角色名``），且按内容而非后缀判断格式：
用户把旧 TXT 直接改名为 ``.json``、或把配置项指向旧 TXT 时仍能读到内容，无需手工转换。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import os
import re
import json
import tempfile
from pathlib import Path

# 旧的 TXT 行格式，同时兼容全角与半角冒号：历史数据两种写法混杂
ROLE_MAP_LINE_RE = re.compile(r'^\s*(\d+)\s*[:：]\s*(.+?)\s*$')

# 内置合并对照表的模式分节名
ROLE_MAP_SECTIONS = ('wife', 'husband', 'nte')


def parse_role_map_text(text: str) -> dict[str, str]:
    """解析旧 TXT 对照表内容。

    无法匹配的行（注释、表头、空行）直接跳过而非报错：旧文件由用户手工维护，
    宽容解析可以避免个别脏行导致整表不可用。
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = ROLE_MAP_LINE_RE.match(line)
        if not match:
            continue
        role_id, role_name = match.groups()
        role_name = role_name.strip()
        if role_name:
            result[role_id] = role_name
    return result


def _select_json_section(raw: object, section: str | None) -> dict[str, str]:
    # 顶层不是对象时结构已损坏，返回空表比抛出异常更安全：调用方据此走「无对照表」分支
    if not isinstance(raw, dict):
        return {}
    if section is not None:
        nested = raw.get(section)
        if isinstance(nested, dict):
            raw = nested
        elif any(isinstance(value, dict) for value in raw.values()):
            # 分节结构里没有这一节（例如把老公表配到了异环的 JSON 上）
            return {}
    result: dict[str, str] = {}
    for role_id, role_name in raw.items():
        # 非字符串值（嵌套节、版本号等元数据）不是角色条目；ID 统一转字符串以匹配目录名
        if not isinstance(role_name, str):
            continue
        key = str(role_id).strip()
        name = role_name.strip()
        if key and name:
            result[key] = name
    return result


def loads_role_map(text: str, section: str | None = None) -> dict[str, str]:
    """解析对照表内容：JSON 优先，兼容旧的 TXT 文本。

    以首个非空白字符判断格式而非文件后缀，因此后缀名与实际内容不一致（如旧 TXT
    被改名为 ``.json``）时仍能正确解析。JSON 语法错误同样返回空表，交由调用方
    决定是否提示用户，避免在读取路径上抛出异常。
    """
    stripped = text.lstrip('\ufeff').strip()
    if not stripped:
        return {}
    if stripped.startswith('{'):
        try:
            raw = json.loads(stripped)
        except ValueError:
            return {}
        return _select_json_section(raw, section)
    return parse_role_map_text(stripped)


def dumps_role_map(mapping: dict[str, str]) -> str:
    """把扁平对照表序列化为 JSON 文本；数字 ID 按数值排序，保证文件稳定可 diff。"""
    ordered = {
        key: mapping[key]
        for key in sorted(
            mapping,
            key=lambda item: (0, int(item)) if item.isdigit() else (1, item),
        )
    }
    return json.dumps(ordered, ensure_ascii=False, indent=2) + '\n'


def write_role_map(path: Path, mapping: dict[str, str]) -> None:
    """原子写入对照表 JSON，避免写入过程中中断而留下不完整文件。

    用户上传会频繁重写该文件，而读取方可能随时并发读取；同目录临时文件配合
    ``os.replace`` 保证读者看到的始终是完整的旧版本或新版本，不会解析到半截 JSON。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps_role_map(mapping)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.{path.name}.',
        suffix='.tmp',
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def migrate_legacy_text_map(txt_path: Path, json_path: Path) -> bool:
    """把旧 TXT 对照表一次性迁移成 JSON，成功后将 TXT 改名为 ``.migrated.bak``。

    幂等：JSON 已存在或 TXT 不存在时直接返回 False。

    迁移不成功（读取或写入失败）时保留原 TXT 并返回 False，避免用户数据被删除；
    改名失败同样不视为失败，因为 JSON 已写入完成，重复调用会因 JSON 存在而短路。
    """
    if json_path.is_file() or not txt_path.is_file():
        return False
    try:
        text = txt_path.read_text(encoding='utf-8-sig')
    except OSError:
        return False
    try:
        write_role_map(json_path, parse_role_map_text(text))
    except OSError:
        return False
    try:
        os.replace(txt_path, txt_path.with_name(f'{txt_path.name}.migrated.bak'))
    except OSError:
        pass
    return True
