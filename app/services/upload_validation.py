"""Defensive validation for HR Mind AI document uploads."""

import codecs
import zipfile
from pathlib import Path

SUPPORTED = {".pdf", ".txt", ".md", ".docx"}

MAX_DOCX_ENTRIES = 1000
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_SINGLE_FILE_BYTES = 25 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 100


def validate_file(path: Path) -> None:
    """Validate file type, encoding, and supported document structure."""
    if not path.exists() or not path.is_file():
        raise ValueError("The uploaded document could not be read.")

    suffix = path.suffix.lower()

    if suffix not in SUPPORTED:
        raise ValueError(
            f"Unsupported file type. Supported types: {', '.join(sorted(SUPPORTED))}."
        )

    if path.stat().st_size == 0:
        raise ValueError("The uploaded file is empty.")

    if suffix == ".pdf":
        with path.open("rb") as handle:
            header = handle.read(1024)

        if b"%PDF-" not in header:
            raise ValueError(
                "The file extension is PDF, but the file is not a valid PDF."
            )
        return

    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path, "r") as archive:
                entries = archive.infolist()

                if len(entries) > MAX_DOCX_ENTRIES:
                    raise ValueError(
                        "The Word document contains too many embedded files."
                    )

                names = {entry.filename for entry in entries}

                if (
                    "[Content_Types].xml" not in names
                    or "word/document.xml" not in names
                ):
                    raise ValueError(
                        "The file extension is DOCX, but the file is not a valid Word document."
                    )

                total_uncompressed = 0

                for entry in entries:
                    if entry.flag_bits & 0x1:
                        raise ValueError("Encrypted Word documents are not supported.")

                    if entry.file_size > MAX_DOCX_SINGLE_FILE_BYTES:
                        raise ValueError(
                            "The Word document contains an oversized embedded file."
                        )

                    total_uncompressed += entry.file_size

                    if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            "The Word document expands beyond the allowed size."
                        )

                    if entry.file_size and entry.compress_size == 0:
                        raise ValueError(
                            "The Word document contains an invalid compressed entry."
                        )

                    if entry.compress_size:
                        ratio = entry.file_size / entry.compress_size

                        if ratio > MAX_DOCX_COMPRESSION_RATIO:
                            raise ValueError(
                                "The Word document compression ratio is too high."
                            )

                if archive.testzip() is not None:
                    raise ValueError("The Word document archive is damaged.")

        except zipfile.BadZipFile:
            raise ValueError(
                "The file extension is DOCX, but the file is not a valid Word document."
            ) from None

        return

    # Incremental UTF-8 validation avoids loading the whole file into memory.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()

    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)

                if not chunk:
                    break

                if b"\x00" in chunk:
                    raise ValueError("Text documents must not contain binary data.")

                decoder.decode(chunk, final=False)

            decoder.decode(b"", final=True)

    except UnicodeDecodeError:
        raise ValueError("Text documents must be encoded as UTF-8.") from None
