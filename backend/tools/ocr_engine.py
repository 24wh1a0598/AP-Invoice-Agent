import io
import logging

import pypdf
import pytesseract
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("ap_agent.ocr")


class OCRError(Exception):
    """Raised when text extraction from a document fails unrecoverably."""


class OCREngine:

    @staticmethod
    def extract_text(file_bytes: bytes, file_type: str) -> str:
        """
        Extract plain text from a PDF or image file.

        Raises:
            OCRError: if the file is corrupt, unreadable, or produces no text.
        """
        if not file_bytes:
            raise OCRError("Received empty file — cannot extract text.")

        if file_type == "application/pdf":
            return OCREngine._handle_pdf(file_bytes)
        else:
            return OCREngine._handle_image(file_bytes)

    @staticmethod
    def _handle_pdf(file_bytes: bytes) -> str:
        try:
            pdf = pypdf.PdfReader(io.BytesIO(file_bytes))
        except Exception as exc:
            raise OCRError(f"Could not open PDF: {exc}") from exc

        if len(pdf.pages) == 0:
            raise OCRError("PDF contains no pages.")

        text = ""
        for i, page in enumerate(pdf.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            except Exception as exc:
                logger.warning(f"Could not extract text from PDF page {i}: {exc}")

        if not text.strip():
            # Scanned PDF — flag clearly rather than returning empty string
            raise OCRError(
                "PDF appears to be scanned (no extractable text layer). "
                "Please provide a text-based PDF or a high-resolution image."
            )

        return text.strip()

    @staticmethod
    def _handle_image(file_bytes: bytes) -> str:
        try:
            image = Image.open(io.BytesIO(file_bytes))
        except UnidentifiedImageError as exc:
            raise OCRError(f"File is not a valid image: {exc}") from exc
        except Exception as exc:
            raise OCRError(f"Could not open image: {exc}") from exc

        try:
            text = pytesseract.image_to_string(image)
        except pytesseract.TesseractNotFoundError:
            raise OCRError(
                "Tesseract OCR engine is not installed or not on PATH. "
                "Install it with: https://github.com/UB-Mannheim/tesseract/wiki"
            )
        except Exception as exc:
            raise OCRError(f"OCR processing failed: {exc}") from exc

        if not text.strip():
            raise OCRError("OCR produced no text — image may be blank or too low resolution.")

        return text.strip()
