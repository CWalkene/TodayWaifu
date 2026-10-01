"""JSON 落盘的原子性与容错回归测试。

数据文件是插件记录的唯一真值源，两类故障都不可从进程内恢复：直接覆写目标文件时若写入
被中断（断电、进程被杀），读者会看到被截断的半截 JSON，而读取侧一律降级为空字典，
表现为全部记录凭空消失；替换失败时若已破坏旧文件，则一次写失败即等于数据清空。
提交 a982e77 引入「同目录临时文件 + fsync + os.replace」正是为消除这两个窗口。

本文件同时固定读取侧的容错约定：文件缺失、被手工编辑坏或形状不符时返回空字典而非抛错，
否则一次编辑失误会让插件永久无法加载。
"""
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "storage.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_storage", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load storage module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageTests(unittest.TestCase):
    def test_atomic_write_preserves_unicode_and_schema(self) -> None:
        # 中文名与嵌套结构必须原样往返：写入路径若不指定 ensure_ascii=False 或丢失
        # 嵌套层级，记录内容会被改写，而这类损坏只会在下次读取时才被发现。
        # 同时断言目录内无临时文件残留，覆盖成功路径上的清理。
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            payload = {"days": {"2026-07-11": {"bot:group": {"wives": {"1": {"name": "今汐"}}}}}}
            storage.atomic_write_json(path, payload)
            self.assertEqual(storage.read_json_dict(path), payload)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_read_invalid_json_returns_empty_dict(self) -> None:
        # 损坏文件降级为空字典是刻意的：调用方无法区分「首次运行」与「文件损坏」，
        # 而两者都应按空数据继续启动，避免一次手工编辑失误导致插件彻底不可用。
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(storage.read_json_dict(path), {})

    def test_failed_replace_keeps_previous_file(self) -> None:
        # 替换失败（磁盘满、权限、目标被占用）时旧内容必须完好：这是先写临时文件再
        # 原子替换的全部意义所在。若实现退化为直接覆写目标，此处会读到截断内容。
        # 注入 os.replace 异常只是把偶发的系统级失败变成可复现用例。
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            path.write_text('{"days":{"old":{}}}', encoding="utf-8")
            original_replace = storage.os.replace

            def fail_replace(source: Path, target: Path) -> None:
                raise OSError("replace failed")

            storage.os.replace = fail_replace
            try:
                with self.assertRaises(OSError):
                    storage.atomic_write_json(path, {"days": {"new": {}}})
            finally:
                storage.os.replace = original_replace
            self.assertEqual(path.read_text(encoding="utf-8"), '{"days":{"old":{}}}')
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_each_write_uses_a_unique_temporary_file(self) -> None:
        # 临时文件名必须唯一：若改用固定的 `.<name>.tmp`，两处并发写入会共用同一个
        # 中间文件，后写者覆盖先写者的内容，先写者 replace 出去的可能是他人的数据。
        # 这里拦截 os.replace 记录实际传入的临时文件名，直接断言两次写入不同名。
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            names: list[str] = []
            original_replace = storage.os.replace

            def record_replace(source: Path, target: Path) -> None:
                names.append(Path(source).name)
                original_replace(source, target)

            storage.os.replace = record_replace
            try:
                storage.atomic_write_json(path, {"days": {"first": {}}})
                storage.atomic_write_json(path, {"days": {"second": {}}})
            finally:
                storage.os.replace = original_replace
            self.assertEqual(len(names), 2)
            self.assertNotEqual(names[0], names[1])


if __name__ == "__main__":
    unittest.main()
