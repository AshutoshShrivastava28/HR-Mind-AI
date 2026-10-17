import tempfile
import unittest
import zipfile
from pathlib import Path

from app.services.upload_validation import validate_file


class UploadValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_accepts_utf8_text(self):
        path = self.write(
            "policy.txt",
            "Annual leave requires manager approval.\n".encode("utf-8"),
        )
        validate_file(path)

    def test_rejects_empty_file(self):
        path = self.write("empty.txt", b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            validate_file(path)

    def test_rejects_binary_disguised_as_text(self):
        path = self.write("policy.md", b"\x00\x01\x02")
        with self.assertRaisesRegex(ValueError, "binary"):
            validate_file(path)

    def test_rejects_invalid_utf8(self):
        path = self.write("policy.txt", b"\xff\xfe\xfa")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            validate_file(path)

    def test_rejects_unsupported_extension(self):
        path = self.write("payload.exe", b"MZ" + b"\x00" * 20)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_file(path)

    def test_rejects_pdf_with_wrong_signature(self):
        path = self.write("policy.pdf", b"This is not a PDF.")
        with self.assertRaisesRegex(ValueError, "not a valid PDF"):
            validate_file(path)

    def test_accepts_pdf_signature(self):
        path = self.write("policy.pdf", b"%PDF-1.7\nminimal fixture")
        validate_file(path)

    def test_rejects_invalid_docx_container(self):
        path = self.write("policy.docx", b"not a Word file")
        with self.assertRaisesRegex(ValueError, "not a valid Word"):
            validate_file(path)

    def test_accepts_minimal_docx_container(self):
        path = self.root / "minimal.docx"

        with zipfile.ZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")

        validate_file(path)

    def test_rejects_high_compression_ratio_docx(self):
        path = self.root / "compressed.docx"

        with zipfile.ZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "A" * 500_000)

        with self.assertRaisesRegex(ValueError, "compression ratio"):
            validate_file(path)


if __name__ == "__main__":
    unittest.main()
