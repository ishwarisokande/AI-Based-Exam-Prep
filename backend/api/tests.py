import base64
import io
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.db import connection
import pymupdf
from PIL import Image

from api.models import GeneratedMCQ, StudyMaterial, User
from api.services import (
    FallbackContentGenerator,
    ContentExtractionService,
    GeminiApiException,
    GeminiService,
    PdfExtractionService,
    Prompts,
    extract_youtube_video_id,
    fetch_youtube_transcript,
    pytesseract,
)
from api.views import normalize_mcq_payload, parse_mcq_payload, quiz_percentage


class GeneratedMcqSchemaTests(TestCase):
    def test_generated_mcq_uses_existing_database_columns(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW COLUMNS FROM generated_mcqs")
            columns = {row[0] for row in cursor.fetchall()}

        self.assertIn("optiona", columns)
        self.assertIn("optionb", columns)
        self.assertIn("optionc", columns)
        self.assertIn("optiond", columns)

    def test_generated_mcq_can_be_created_with_existing_schema(self):
        user = User.objects.create_user(email="schema@example.com", name="Schema User", password="pw")
        material = StudyMaterial.objects.create(
            user=user,
            title="Schema Test",
            content_text="abc",
            type="PDF",
        )

        mcq = GeneratedMCQ.objects.create(
            material=material,
            question="What is 2+2?",
            option_a="3",
            option_b="4",
            option_c="5",
            option_d="6",
            correct_answer="B",
            difficulty="EASY",
        )

        self.assertEqual(mcq.option_a, "3")
        self.assertEqual(mcq.option_b, "4")


class FallbackContentGeneratorTests(SimpleTestCase):
    def test_generate_basic_notes_uses_title_when_text_is_empty(self):
        generator = FallbackContentGenerator()
        notes = generator.generate_basic_notes("", title="Python Basics")

        self.assertIn("Python Basics", notes)
        self.assertIn("Key points", notes)

    def test_generate_basic_mcqs_creates_questions_when_text_is_empty(self):
        generator = FallbackContentGenerator()
        mcqs = generator.generate_basic_mcqs("", 3, title="Python Basics")

        self.assertEqual(len(mcqs), 3)
        self.assertTrue(any("Python Basics" in item["question"] for item in mcqs))


class PromptBuilderTests(SimpleTestCase):
    def test_notes_prompt_uses_only_provided_material(self):
        prompt = Prompts.build_notes_prompt("")

        self.assertIn("Use ONLY the provided study material", prompt)
        self.assertIn("No readable study material found", prompt)

    def test_mcq_prompt_includes_requested_count_and_difficulty(self):
        prompt = Prompts.build_mcq_prompt("Python basics", 5, "HARD")

        self.assertIn("Generate 5 multiple choice questions", prompt)
        self.assertIn("HARD", prompt)
        self.assertIn("Python basics", prompt)


class ContentProcessingTests(SimpleTestCase):
    def test_scanned_pdf_pages_are_rendered_and_sent_to_ocr(self):
        document = pymupdf.open()
        document.new_page()
        pdf_bytes = document.tobytes()
        document.close()

        with patch("api.services.pytesseract.image_to_string", return_value="Recognized scanned text"):
            text = PdfExtractionService().extract_text_from_pdf(pdf_bytes)

        self.assertEqual(text, "Recognized scanned text")

    def test_scanned_pdf_ocr_only_renders_pages_without_selectable_text(self):
        document = pymupdf.open()
        text_page = document.new_page()
        text_page.insert_text((72, 72), "Printed textbook text")
        document.new_page()
        pdf_bytes = document.tobytes()
        document.close()

        with (
            patch("api.services.GeminiService.extract_image_text", side_effect=GeminiApiException("No key")),
            patch("api.services.extract_with_tesseract", return_value="Handwritten page text") as ocr,
        ):
            text = PdfExtractionService().extract_text_from_pdf(pdf_bytes)

        self.assertIn("Printed textbook text", text)
        self.assertIn("Handwritten page text", text)
        ocr.assert_called_once()

    def test_handwritten_image_prefers_gemini_vision_over_tesseract(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(image_buffer, format="PNG")
        uploaded_image = SimpleNamespace(
            read=lambda: image_buffer.getvalue(),
            content_type="image/png",
        )

        with (
            patch("api.services.GeminiService.extract_image_text", return_value="Transcribed handwritten notes"),
            patch("api.services.extract_with_tesseract") as local_ocr,
        ):
            text, content_type = ContentExtractionService().extract_image_content(uploaded_image)

        self.assertEqual(text, "Transcribed handwritten notes")
        self.assertEqual(content_type, "image")
        local_ocr.assert_not_called()

    def test_image_upload_reports_ocr_setup_when_gemini_and_tesseract_are_unavailable(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(image_buffer, format="PNG")
        uploaded_image = SimpleNamespace(
            read=lambda: image_buffer.getvalue(),
            content_type="image/png",
        )

        with (
            patch(
                "api.services.GeminiService.extract_image_text",
                side_effect=GeminiApiException("Gemini is not configured."),
            ),
            patch(
                "api.services.extract_with_tesseract",
                side_effect=pytesseract.TesseractNotFoundError(),
            ),
        ):
            with self.assertRaisesMessage(ValueError, "Tesseract OCR is not installed"):
                ContentExtractionService().extract_image_content(uploaded_image)

    def test_gemini_image_transcription_sends_inline_image_data(self):
        service = GeminiService()
        service.api_key = "test-key"
        with patch.object(service, "_execute_with_retry", return_value="transcription") as execute:
            result = service.extract_image_text(b"image-bytes", "image/jpeg")

        self.assertEqual(result, "transcription")
        payload = execute.call_args.args[2]
        image_part = payload["contents"][0]["parts"][1]["inline_data"]
        self.assertEqual(image_part["mime_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(image_part["data"]), b"image-bytes")

    def test_mcq_payload_accepts_options_array_and_normalizes_answer(self):
        payload = '```JSON\n[{"question":"Pick the answer","options":["one","two","three","four"],"correctAnswer":"Option B"}]\n```'

        parsed = normalize_mcq_payload(parse_mcq_payload(payload), 5)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["optionB"], "two")
        self.assertEqual(parsed[0]["correctAnswer"], "B")

    def test_mcq_payload_discards_incomplete_questions(self):
        parsed = normalize_mcq_payload(
            [{"question": "Incomplete", "optionA": "A", "correctAnswer": "A"}],
            5,
        )

        self.assertEqual(parsed, [])

    def test_youtube_video_id_supports_watch_and_shorts_urls(self):
        self.assertEqual(extract_youtube_video_id("https://www.youtube.com/watch?v=abcdefghijk"), "abcdefghijk")
        self.assertEqual(extract_youtube_video_id("https://youtu.be/abcdefghijk"), "abcdefghijk")
        self.assertEqual(extract_youtube_video_id("https://youtube.com/shorts/abcdefghijk"), "abcdefghijk")
        self.assertIsNone(extract_youtube_video_id("https://example.com/watch?v=abcdefghijk"))

    def test_youtube_transcript_uses_current_library_fetch_api(self):
        with patch("api.services.YouTubeTranscriptApi") as api_class:
            api_class.return_value.fetch.return_value = [SimpleNamespace(text="Transcript text")]

            transcript = fetch_youtube_transcript("https://youtu.be/abcdefghijk")

        self.assertEqual(transcript, "Transcript text")
        api_class.return_value.fetch.assert_called_once()

    def test_gemini_without_api_key_fails_immediately(self):
        service = GeminiService()
        service.api_key = ""

        with self.assertRaisesMessage(GeminiApiException, "GEMINI_API_KEY"):
            service.generate_content("prompt", "content")

    def test_quiz_percentage_supports_legacy_percentage_records(self):
        self.assertEqual(quiz_percentage(SimpleNamespace(score=80, total=5)), 80)
        self.assertEqual(quiz_percentage(SimpleNamespace(score=4, total=5)), 80)
