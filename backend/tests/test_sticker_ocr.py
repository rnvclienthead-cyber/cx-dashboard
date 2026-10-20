import unittest
from unittest.mock import patch

from backend.sticker_ocr import parse_sticker_ocr, read_sticker_from_image, vl_model_name


class ParseStickerOcrTests(unittest.TestCase):
    def test_json_two_parts(self):
        self.assertEqual(
            parse_sticker_ocr('{"part_a":"5690311","part_b":"8348"}'),
            "56903118348",
        )

    def test_json_code(self):
        self.assertEqual(parse_sticker_ocr('{"code":"35548064307"}'), "35548064307")

    def test_fenced_json(self):
        self.assertEqual(
            parse_sticker_ocr('```json\n{"code":"39841818852"}\n```'),
            "39841818852",
        )

    def test_plain_two_numbers(self):
        self.assertEqual(parse_sticker_ocr("вижу 5690311 и 8348"), "56903118348")

    def test_ignores_short_zones(self):
        self.assertEqual(parse_sticker_ocr("50 41 5690311 8348"), "56903118348")

    def test_empty(self):
        self.assertEqual(parse_sticker_ocr(""), "")
        self.assertEqual(parse_sticker_ocr('{"code":""}'), "")


class ReadStickerFromImageTests(unittest.TestCase):
    def test_sends_image_to_vl_model(self):
        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", (40, 40), (255, 255, 255)).save(buf, format="JPEG")
        payload = buf.getvalue()

        with patch("backend.sticker_ocr.QwenClient") as client_cls:
            client_cls.return_value.chat_sync.return_value = '{"part_a":"5690311","part_b":"8348"}'
            with patch("backend.sticker_ocr.vl_model_name", return_value="qwen-vl-plus"):
                code = read_sticker_from_image(payload, "image/jpeg")
        self.assertEqual(code, "56903118348")
        client_cls.assert_called_once()
        kwargs = client_cls.return_value.chat_sync.call_args.kwargs
        content = kwargs["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_vl_model_prefers_env(self):
        with patch.dict("os.environ", {"QWEN_VL_MODEL": "qwen-vl-max", "QWEN_MODEL": "qwen-plus"}):
            self.assertEqual(vl_model_name(), "qwen-vl-max")


if __name__ == "__main__":
    unittest.main()
