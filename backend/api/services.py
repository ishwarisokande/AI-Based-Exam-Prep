import base64
import io
import logging
import os
import random
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import List

import requests
from django.conf import settings
from pypdf import PdfReader

try:
    from PIL import Image, ImageEnhance, ImageOps
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageEnhance = None
    ImageOps = None

try:
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - optional dependency
    fitz = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:  # pragma: no cover - optional dependency
    YouTubeTranscriptApi = None

logger = logging.getLogger(__name__)


class Prompts:
    NOTES_PROMPT = (
        "You are an expert educational assistant.\n\n"
        "Use ONLY the provided study material.\n\n"
        "Create simple, concise study notes in plain language. Use short sections for "
        "the main idea, key points, important terms, and a brief revision summary. "
        "Explain difficult terms simply and do not add facts absent from the material.\n\n"
        "If the material is empty or unreadable, respond exactly: \"No readable study material found.\""
    )

    MCQ_PROMPT = (
        "You are an exam question generator.\n\n"
        "Use ONLY the provided study material.\n\n"
        "Generate multiple choice questions with four options and one correct answer.\n"
        "Rules:\n"
        "- Each question must have 4 options\n"
        "- Only one correct answer\n"
        "- Mix easy, medium, hard difficulty\n"
        "- Avoid duplicate questions\n"
        "- Output must be STRICT JSON format\n\n"
        "Format:\n"
        "[\n"
        "  {\n"
        "    \"question\": \"\",\n"
        "    \"optionA\": \"\",\n"
        "    \"optionB\": \"\",\n"
        "    \"optionC\": \"\",\n"
        "    \"optionD\": \"\",\n"
        "    \"correctAnswer\": \"\",\n"
        "    \"difficulty\": \"EASY\"\n"
        "  }\n"
        "]\n"
        "Return ONLY the JSON array, no other text."
    )

    QUIZ_PROMPT = (
        "You are an expert educational evaluator.\n\n"
        "Use ONLY the provided study material.\n\n"
        "Generate quiz questions that test understanding and recall.\n"
        "Return JSON as an array of objects with: question, options, correctAnswer, difficulty."
    )

    IMPORTANT_QUESTIONS_PROMPT = (
        "You are an academic examiner.\n\n"
        "Use ONLY the provided study material.\n\n"
        "Generate likely university-style exam questions based on the material."
    )

    @staticmethod
    def build_notes_prompt(material_text=None):
        prompt = Prompts.NOTES_PROMPT
        if material_text:
            prompt = f"{prompt}\n\nStudy Material:\n{material_text}"
        return prompt

    @staticmethod
    def build_mcq_prompt(material_text=None, count=20, difficulty="MEDIUM"):
        prompt = Prompts.MCQ_PROMPT
        if material_text:
            prompt = f"{prompt}\n\nStudy Material:\n{material_text}"
        prompt += f"\n\nGenerate {count} multiple choice questions."
        prompt += f"\nPreferred difficulty: {difficulty}."
        return prompt

    @staticmethod
    def build_quiz_prompt(material_text=None, count=10, difficulty="MEDIUM"):
        prompt = Prompts.QUIZ_PROMPT
        if material_text:
            prompt = f"{prompt}\n\nStudy Material:\n{material_text}"
        prompt += f"\n\nGenerate {count} quiz questions."
        prompt += f"\nPreferred difficulty: {difficulty}."
        return prompt

    @staticmethod
    def build_important_questions_prompt(material_text=None, count=10):
        prompt = Prompts.IMPORTANT_QUESTIONS_PROMPT
        if material_text:
            prompt = f"{prompt}\n\nStudy Material:\n{material_text}"
        return prompt + f"\n\nGenerate {count} important exam questions."


class GeminiApiException(Exception):
    pass


class GeminiQuotaExceededException(Exception):
    pass


def encode_image_for_ocr(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def configure_tesseract():
    if pytesseract is None:
        raise ValueError("pytesseract is not installed.")

    executable = shutil.which("tesseract")
    if not executable:
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates = [
            Path(program_files) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tesseract-OCR" / "tesseract.exe",
        ]
        executable = next((str(path) for path in candidates if path.is_file()), None)

    if not executable:
        raise ValueError(
            "Tesseract OCR is not installed. Configure GEMINI_API_KEY for handwriting recognition, "
            "or install Tesseract OCR and add it to PATH."
        )

    pytesseract.pytesseract.tesseract_cmd = executable


def extract_with_tesseract(image):
    if pytesseract is None:
        raise ValueError("pytesseract is not installed.")
    configure_tesseract()

    prepared = ImageOps.exif_transpose(image).convert("RGB")
    grayscale = ImageOps.grayscale(prepared)
    grayscale = ImageOps.autocontrast(grayscale)
    if max(grayscale.size) < 2200:
        grayscale = grayscale.resize(
            (grayscale.width * 2, grayscale.height * 2),
            Image.Resampling.LANCZOS,
        )
    enhanced = ImageEnhance.Contrast(grayscale).enhance(1.5)

    candidates = [
        pytesseract.image_to_string(enhanced, config="--oem 3 --psm 6"),
        pytesseract.image_to_string(enhanced, config="--oem 3 --psm 11"),
    ]
    return max(
        candidates,
        key=lambda text: (len(re.findall(r"\w+", text)), len(text.strip())),
    ).strip()


class PdfExtractionService:
    def extract_text_from_pdf(self, file_bytes):
        if not file_bytes:
            raise ValueError("No PDF content was uploaded.")

        logger.info("PDF upload detected with %s bytes", len(file_bytes))
        pdf_file = io.BytesIO(file_bytes)
        try:
            reader = PdfReader(pdf_file)
            page_count = len(reader.pages)
            logger.info("PDF page count: %s", page_count)

            extracted_pages: List[str] = []
            pages_needing_ocr = []
            for index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                extracted_pages.append(page_text.strip())
                if len(page_text.strip()) < 20:
                    pages_needing_ocr.append(index - 1)
                logger.info("PDF page %s extracted length: %s", index, len(page_text or ""))

            if pages_needing_ocr:
                logger.info("PDF pages without selectable text: %s", len(pages_needing_ocr))
                ocr_pages = self._ocr_pdf_fallback(file_bytes, pages_needing_ocr)
                for page_index, page_text in ocr_pages.items():
                    extracted_pages[page_index] = "\n".join(
                        part for part in (extracted_pages[page_index], page_text) if part
                    )

            text = "\n\n".join(part for part in extracted_pages if part).strip()
            if text:
                logger.info("PDF extracted text length: %s", len(text))
                logger.info("PDF extracted text preview: %s", text[:500])
                return text

            raise ValueError("No readable study material found in the PDF.")
        except ValueError:
            raise
        except Exception as exc:
            logger.exception("PDF parsing failed: %s", exc)
            raise ValueError(f"Failed to parse PDF: {exc}") from exc

    def _ocr_pdf_fallback(self, file_bytes, page_indexes=None):
        if Image is None:
            raise ValueError("Pillow is required to scan this PDF.")

        images = []
        try:
            if fitz is not None:
                document = fitz.open(stream=file_bytes, filetype="pdf")
                try:
                    selected_pages = set(page_indexes) if page_indexes is not None else None
                    for page_index, page in enumerate(document):
                        if selected_pages is not None and page_index not in selected_pages:
                            continue
                        pixmap = page.get_pixmap(
                            matrix=fitz.Matrix(300 / 72, 300 / 72),
                            colorspace=fitz.csRGB,
                            alpha=False,
                        )
                        images.append((page_index, Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)))
                finally:
                    document.close()
            else:  # pragma: no cover - used only when PyMuPDF is not installed
                from pdf2image import convert_from_bytes

                rendered_images = convert_from_bytes(file_bytes, dpi=300)
                selected_pages = set(page_indexes) if page_indexes is not None else None
                images = [
                    (index, image)
                    for index, image in enumerate(rendered_images)
                    if selected_pages is None or index in selected_pages
                ]
        except Exception as exc:
            logger.exception("PDF page rendering for OCR failed: %s", exc)
            raise ValueError(
                "Could not prepare scanned PDF pages for OCR. Install the PyMuPDF dependency."
            ) from exc

        logger.info("PDF OCR fallback image page count: %s", len(images))
        ocr_pages = {}
        for page_index, image in images:
            page_number = page_index + 1
            try:
                page_text = GeminiService().extract_image_text(encode_image_for_ocr(image), "image/jpeg")
                logger.info("Gemini handwriting transcription succeeded for PDF page %s", page_number)
            except GeminiApiException as exc:
                logger.info("Gemini vision unavailable for PDF page %s: %s", page_number, exc)
                page_text = ""
            except GeminiQuotaExceededException as exc:
                logger.warning("Gemini vision quota exhausted for PDF page %s: %s", page_number, exc)
                page_text = ""

            if not page_text.strip():
                if pytesseract is None:
                    raise ValueError(
                        "This scanned PDF needs handwriting OCR. Configure GEMINI_API_KEY in backend/.env, "
                        "or install the Tesseract OCR application and pytesseract."
                    )
                try:
                    page_text = extract_with_tesseract(image)
                except pytesseract.TesseractNotFoundError as exc:
                    raise ValueError(
                        "Tesseract OCR is not installed. Configure GEMINI_API_KEY for handwriting recognition, "
                        "or install Tesseract OCR and restart the app."
                    ) from exc
                except ValueError:
                    raise
                except Exception as exc:
                    logger.exception("PDF OCR failed on page %s: %s", page_number, exc)
                    raise ValueError(f"Could not read scanned PDF page {page_number}.") from exc
            logger.info("PDF OCR page %s length: %s", page_number, len(page_text or ""))
            if page_text.strip():
                ocr_pages[page_index] = page_text.strip()

        if ocr_pages:
            text = "\n\n".join(ocr_pages.values()).strip()
            logger.info("OCR text length: %s", len(text))
            return ocr_pages
        raise ValueError("No readable study material found in the PDF.")


class ContentExtractionService:
    def extract_content(self, upload_type, file_obj=None, youtube_url=None):
        if upload_type == "youtube":
            return self.extract_youtube_content(youtube_url)
        if upload_type == "image":
            return self.extract_image_content(file_obj)
        if upload_type == "pdf":
            return self.extract_pdf_content(file_obj)
        raise ValueError(f"Unsupported upload type: {upload_type}")

    def extract_youtube_content(self, youtube_url):
        logger.info("YouTube upload received: %s", youtube_url)
        if not youtube_url:
            raise ValueError("A YouTube URL is required.")

        transcript_text = fetch_youtube_transcript(youtube_url)
        if transcript_text and transcript_text.strip():
            logger.info("YouTube transcript extracted length: %s", len(transcript_text))
            logger.info("YouTube transcript preview: %s", transcript_text[:500])
            return transcript_text, "youtube"

        logger.warning("YouTube transcript unavailable for %s", youtube_url)
        raise ValueError("No transcript could be extracted from the provided video. Ensure the video has captions enabled and try again.")

    def extract_image_content(self, file_obj):
        if not file_obj:
            raise ValueError("An image file is required.")

        logger.info("Image upload detected, content type: %s", getattr(file_obj, "content_type", "unknown"))
        file_bytes = file_obj.read()
        if not file_bytes:
            raise ValueError("The uploaded image is empty.")

        if Image is None:
            raise ValueError("Pillow is required to read image notes.")

        try:
            with Image.open(io.BytesIO(file_bytes)) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGB")
                image_bytes = encode_image_for_ocr(image)
                try:
                    text = GeminiService().extract_image_text(image_bytes, "image/jpeg")
                    logger.info("Gemini handwriting transcription succeeded for uploaded image")
                    return text.strip(), "image"
                except GeminiApiException as exc:
                    logger.info("Gemini vision unavailable for uploaded image: %s", exc)
                except GeminiQuotaExceededException as exc:
                    logger.warning("Gemini vision quota exhausted for uploaded image: %s", exc)

                if pytesseract is None:
                    raise ValueError(
                        "Handwritten notes need Gemini vision. Add GEMINI_API_KEY to backend/.env. "
                        "For printed text, install pytesseract and the Tesseract OCR application."
                    )
                text = extract_with_tesseract(image)
            logger.info("Image OCR text length: %s", len(text or ""))
            if text and text.strip():
                return text.strip(), "image"
        except ValueError:
            raise
        except pytesseract.TesseractNotFoundError as exc:
            raise ValueError(
                "Tesseract OCR is not installed. Add GEMINI_API_KEY for handwritten notes, "
                "or install Tesseract OCR and restart the app."
            ) from exc
        except Exception as exc:
            logger.exception("Image OCR failed: %s", exc)
            raise ValueError("OCR failed for the uploaded image.") from exc

        raise ValueError("No readable study material found in the image.")

    def extract_pdf_content(self, file_obj):
        if not file_obj:
            raise ValueError("A PDF file is required.")

        file_bytes = file_obj.read()
        if not file_bytes:
            raise ValueError("The uploaded PDF is empty.")

        extractor = PdfExtractionService()
        text = extractor.extract_text_from_pdf(file_bytes)
        return text, "pdf"


class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.max_attempts = settings.GEMINI_RETRY_MAX_ATTEMPTS
        self.initial_backoff = settings.GEMINI_RETRY_INITIAL_BACKOFF_MS / 1000.0
        self.backoff_multiplier = settings.GEMINI_RETRY_BACKOFF_MULTIPLIER
        self.max_backoff = settings.GEMINI_RETRY_MAX_BACKOFF_MS / 1000.0

    def generate_content(self, prompt, content):
        if not self.api_key or "YOUR_GEMINI_API_KEY" in self.api_key:
            raise GeminiApiException("Gemini is not configured. Add GEMINI_API_KEY to backend/.env.")

        full_prompt = prompt or ""
        if content:
            full_prompt = f"{full_prompt}\n\nStudy Material:\n{content}".strip()

        logger.info("Gemini request size: %s characters", len(full_prompt))
        logger.info("Gemini request preview: %s", full_prompt[:500])

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
        return self._execute_with_retry(url, headers, payload)

    def extract_image_text(self, image_bytes, mime_type):
        if not self.api_key or "YOUR_GEMINI_API_KEY" in self.api_key:
            raise GeminiApiException("Gemini is not configured. Add GEMINI_API_KEY to backend/.env.")

        prompt = (
            "Transcribe the text in this study-notes image as accurately and completely as possible. "
            "The writing may be handwritten. Preserve headings, bullet points, equations, and reading order. "
            "Do not summarize, correct, or add information. If a word cannot be read, write [illegible]. "
            "Return only the transcription."
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }},
                ],
            }],
        }
        return self._execute_with_retry(url, headers, payload)

    def generate_content_from_chunks(self, prompt, content, chunk_size=9000):
        if not content:
            return "No readable study material found."

        if len(content) <= chunk_size:
            return self.generate_content(prompt, content)

        chunks = self.chunk_text(content, chunk_size)
        logger.info("Gemini content split into %s chunks", len(chunks))
        chunk_summaries = []
        for index, chunk in enumerate(chunks, start=1):
            logger.info("Gemini chunk %s/%s size: %s", index, len(chunks), len(chunk))
            chunk_summary = self.generate_content(prompt, chunk)
            if chunk_summary and chunk_summary.strip() and chunk_summary.strip().lower() != "no readable study material found.":
                chunk_summaries.append(chunk_summary.strip())

        if not chunk_summaries:
            return "No readable study material found."

        combined_material = "\n\n".join(chunk_summaries)
        return self.generate_content(prompt, combined_material)

    def chunk_text(self, text, max_chars=9000):
        if not text:
            return []

        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) <= max_chars:
            return [cleaned]

        chunks = []
        start = 0
        while start < len(cleaned):
            end = min(start + max_chars, len(cleaned))
            if end < len(cleaned):
                break_index = max(cleaned.rfind(". ", start, end), cleaned.rfind("\n", start, end))
                if break_index <= start:
                    break_index = end
                chunk = cleaned[start:break_index].strip()
                if chunk:
                    chunks.append(chunk)
                start = break_index
            else:
                chunk = cleaned[start:end].strip()
                if chunk:
                    chunks.append(chunk)
                break
        return chunks

    def _execute_with_retry(self, url, headers, payload):
        backoff = self.initial_backoff

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=60)
                logger.info("Gemini response status: %s", response.status_code)

                if response.status_code == 200:
                    data = response.json()
                    try:
                        result = data["candidates"][0]["content"]["parts"][0]["text"]
                        logger.info("Gemini response: %s", result[:1000])
                        return result
                    except (KeyError, IndexError):
                        logger.exception("Gemini returned an unexpected payload: %s", data)
                        raise GeminiApiException("No content found in Gemini response")

                elif response.status_code == 429:
                    logger.warning("Gemini 429 RESOURCE_EXHAUSTED (attempt %s/%s)", attempt, self.max_attempts)
                    if attempt == self.max_attempts:
                        raise GeminiQuotaExceededException("Gemini free-tier quota exhausted after retries.")

                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else backoff
                    time.sleep(wait_time)
                    backoff = min(backoff * self.backoff_multiplier, self.max_backoff)

                elif response.status_code >= 500:
                    logger.warning("Gemini server error %s (attempt %s/%s)", response.status_code, attempt, self.max_attempts)
                    if attempt == self.max_attempts:
                        raise GeminiApiException(f"Gemini API server error after retries: {response.status_code}")
                    time.sleep(backoff)
                    backoff = min(backoff * self.backoff_multiplier, self.max_backoff)

                else:
                    try:
                        error_msg = response.json().get("error", {}).get("message", "Client Error")
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"
                    raise GeminiApiException(f"Gemini API error ({response.status_code}): {error_msg}")

            except requests.RequestException as exc:
                logger.warning("Network error calling Gemini (attempt %s/%s): %s", attempt, self.max_attempts, exc)
                if attempt == self.max_attempts:
                    raise GeminiApiException("Could not reach Gemini API after retries") from exc
                time.sleep(backoff)
                backoff = min(backoff * self.backoff_multiplier, self.max_backoff)

        raise GeminiApiException("Gemini API call failed after retries")


def fetch_youtube_transcript(youtube_url):
    video_id = extract_youtube_video_id(youtube_url)
    if not video_id:
        return None

    logger.info("Attempting YouTube transcript extraction for video ID: %s", video_id)
    if YouTubeTranscriptApi is not None:
        try:
            transcript_api = YouTubeTranscriptApi()
            languages = ["en", "en-US", "en-GB"]
            if hasattr(transcript_api, "fetch"):
                transcript_data = transcript_api.fetch(video_id, languages=languages)
            elif hasattr(transcript_api, "list"):
                transcript = transcript_api.list(video_id).find_transcript(languages)
                transcript_data = transcript.fetch()
            else:  # Compatibility with youtube-transcript-api versions before 1.0
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)

            transcript_text = " ".join(
                (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")).strip()
                for item in transcript_data
                if (item.get("text") if isinstance(item, dict) else getattr(item, "text", ""))
            )
            if transcript_text.strip():
                logger.info("YouTube transcript extracted with library, length: %s", len(transcript_text))
                return transcript_text
        except Exception as exc:
            logger.warning("Transcript library extraction failed: %s", exc)

    try:
        transcript_url = f"https://youtubetranscript.com/?server_vid={urllib.parse.quote(video_id)}"
        response = requests.get(transcript_url, timeout=20)
        if response.status_code != 200:
            logger.warning("YouTube transcript fallback returned status %s", response.status_code)
            return None

        html = response.text
        if "Please stop using a bot on YouTubeTranscript.com" in html:
            logger.warning("YouTubeTranscript.com rejected the request")
            return None

        patterns = [
            re.compile(r'<div class="text".*?>(.*?)</div>', re.DOTALL),
            re.compile(r'<p class="text".*?>(.*?)</p>', re.DOTALL),
            re.compile(r'>([^<>]{20,})<', re.DOTALL),
        ]

        transcript_parts = []
        for pattern in patterns:
            matches = pattern.findall(html)
            for match in matches:
                clean_text = re.sub(r"<.*?>", "", match).strip()
                clean_text = re.sub(r"\s+", " ", clean_text).strip()
                if clean_text and len(clean_text) > 20:
                    transcript_parts.append(clean_text)

        if transcript_parts:
            result = " ".join(transcript_parts[:40]).strip()
            logger.info("YouTube transcript fallback extracted length: %s", len(result))
            return result if result else None
    except Exception as exc:
        logger.warning("YouTube transcript fallback failed: %s", exc)

    return None


def extract_youtube_video_id(youtube_url):
    if not youtube_url:
        return None
    try:
        parsed_url = urllib.parse.urlparse(youtube_url.strip())
    except ValueError:
        return None

    host = (parsed_url.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed_url.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        if parsed_url.path == "/watch":
            candidate = urllib.parse.parse_qs(parsed_url.query).get("v", [""])[0]
        else:
            path_parts = parsed_url.path.strip("/").split("/")
            candidate = path_parts[1] if len(path_parts) > 1 and path_parts[0] in {"embed", "shorts", "live"} else ""
    else:
        return None

    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "for",
    "and", "or", "with", "that", "this", "it", "as", "by", "be", "at", "from",
    "which", "these", "those", "there", "their", "about", "into", "through"
}


def split_sentences(text):
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


FALLBACK_PROMPT_MARKERS = [
    "Please summarize and generate notes and questions from the following YouTube transcript:",
    "Please summarize and generate notes and questions from the following YouTube video:",
    "Please summarize and generate notes and questions from the following study material:",
]


def normalize_fallback_text(text):
    if not text:
        return ""
    normalized = text
    for marker in FALLBACK_PROMPT_MARKERS:
        idx = normalized.find(marker)
        if idx != -1:
            normalized = normalized[idx + len(marker):]
            break
    return normalized.strip()


def extract_keywords(text):
    if not text:
        return []
    words = re.findall(r"[A-Za-z]{6,}", text)
    return [w for w in words if w.lower() not in STOPWORDS]


def find_blank_candidate(sentence):
    words = [re.sub(r"[^A-Za-z0-9]", "", w) for w in sentence.split()]
    for w in words:
        if len(w) > 5 and w.lower() not in STOPWORDS:
            return w
    return None


class FallbackContentGenerator:
    def generate_basic_notes(self, text, title=None):
        content = normalize_fallback_text(text)
        sentences = split_sentences(content if content else text)
        sb = [
            "⚠️ AI note generation is temporarily unavailable, so here is a fallback summary.",
            "Here is an auto-generated summary of your material instead:\n"
        ]
        if title:
            sb.append(f"Topic: {title}")
        if not sentences:
            sb.append("Key points:")
            if title:
                sb.append(f"• {title} was uploaded successfully.")
                sb.append("• Review the uploaded content and add your own details for deeper study.")
            else:
                sb.append("• Uploaded content could not be extracted automatically.")
                sb.append("• Use this as a placeholder until the content is processed further.")
        else:
            for s in sentences[:10]:
                sb.append(f"• {s}")
        return "\n".join(sb)

    def generate_basic_mcqs(self, text, count, title=None):
        content = normalize_fallback_text(text)
        source_text = content if content else text
        sentences = [s for s in split_sentences(source_text) if len(s.split()) >= 6]
        keyword_pool = extract_keywords(source_text)
        mcqs = []
        random_gen = random.Random()

        for sentence in sentences:
            if len(mcqs) >= count:
                break
            target = find_blank_candidate(sentence)
            if not target:
                continue

            question_text = re.sub(re.escape(target), "_____", sentence, count=1)

            distractors = list(set([k for k in keyword_pool if k.lower() != target.lower()]))
            random_gen.shuffle(distractors)
            distractors = distractors[:3]
            while len(distractors) < 3:
                distractors.append("None of the above")

            options = distractors + [target]
            random_gen.shuffle(options)

            correct_letter = chr(ord("A") + options.index(target))

            mcqs.append({
                "question": f"Fill in the blank: {question_text}",
                "optionA": options[0],
                "optionB": options[1],
                "optionC": options[2],
                "optionD": options[3],
                "correctAnswer": correct_letter,
                "difficulty": "EASY"
            })

        if not mcqs:
            if title:
                topic_text = title
            else:
                topic_text = "the uploaded material"
            for _ in range(count):
                mcqs.append({
                    "question": f"What is an important concept related to {topic_text}?",
                    "optionA": "Key idea",
                    "optionB": "Supporting detail",
                    "optionC": "Example",
                    "optionD": "Summary",
                    "correctAnswer": "A",
                    "difficulty": "EASY"
                })

        return mcqs
