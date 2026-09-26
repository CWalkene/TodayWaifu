import base64
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "image_input.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_image_input", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load image_input module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImageInputTests(unittest.TestCase):
    def test_collect_image_refs_deduplicates_in_original_order(self) -> None:
        image_input = _load_module()
        event = SimpleNamespace(
            content=[SimpleNamespace(type="image", data=" a "), SimpleNamespace(type="text", data="x")],
            image_list=["a", "b"],
            image="c",
        )
        self.assertEqual(image_input.collect_image_refs(event), ("a", "b", "c"))

    def test_collect_image_refs_allows_missing_optional_event_fields(self) -> None:
        image_input = _load_module()
        event = SimpleNamespace(content=[SimpleNamespace(type="image", data=" a ")])
        self.assertEqual(image_input.collect_image_refs(event), ("a",))

    def test_detect_image_suffix_rejects_signature_only_data(self) -> None:
        image_input = _load_module()
        self.assertEqual(image_input.detect_image_suffix(b"\x89PNG\r\n\x1a\nrest", "fake.jpg"), "")
        self.assertEqual(image_input.detect_image_suffix(b"RIFFxxxxWEBPrest", "fake.jpg"), "")

    def test_read_image_bytes_rejects_encoded_payload_before_decode(self) -> None:
        image_input = _load_module()
        encoded = "base64://" + ("A" * 10000)
        with patch.object(image_input.base64, "b64decode", side_effect=AssertionError):
            self.assertIsNone(image_input.read_image_bytes(encoded, 64))

    def test_read_image_bytes_validates_actual_image_not_extension(self) -> None:
        image_input = _load_module()
        self.assertIsNone(image_input.read_image_bytes("fake.png", 1024))
        self.assertIsNone(image_input.read_image_bytes("base64://" + base64.b64encode(b"not png").decode(), 1024))

    def test_read_image_bytes_preserves_valid_png_jpeg_webp(self) -> None:
        image_input = _load_module()
        # Use Pillow-generated fixtures to avoid relying on signature-only bytes.
        from io import BytesIO

        from PIL import Image
        for fmt, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "red").save(buf, format=fmt)
            encoded = "base64://" + base64.b64encode(buf.getvalue()).decode()
            result = image_input.read_image_bytes(encoded, 1024 * 1024)
            self.assertIsNotNone(result)
            self.assertEqual(result[1], suffix)

    def test_image_hash_id_uses_filename_for_backward_compatibility(self) -> None:
        image_input = _load_module()
        self.assertEqual(image_input.image_hash_id(Path("/tmp/example.png")), "ca75a0b8")


if __name__ == "__main__":
    unittest.main()
