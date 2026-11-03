import os
import time
import json
import base64
import jwt
import logging
import re
from django.conf import settings
from django.contrib.auth import authenticate
from django.http import HttpResponse, Http404
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, BasePermission
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from api.models import User, StudyMaterial, GeneratedMCQ, GeneratedNotes, QuizResult
from api.services import (
    ContentExtractionService,
    GeminiService,
    FallbackContentGenerator,
    Prompts,
    GeminiQuotaExceededException,
)

logger = logging.getLogger(__name__)

# --- Custom Admin Permission ---
class IsAdminUserRole(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user 
            and request.user.is_authenticated 
            and request.user.role == 'ADMIN'
        )


# --- Helper functions ---
def generate_jwt_token(user):
    secret_key_base64 = settings.JWT_SECRET
    try:
        secret_key = base64.b64decode(secret_key_base64)
    except Exception:
        secret_key = secret_key_base64.encode('utf-8')
        
    payload = {
        'sub': user.email,
        'iat': int(time.time()),
        # 24 hours validity: 1000 * 60 * 60 * 24 milliseconds in Java = 86400 seconds
        'exp': int(time.time()) + 24 * 60 * 60
    }
    return jwt.encode(payload, secret_key, algorithm='HS256')

def extract_json_array(text):
    if not text:
        return None
    start = text.find('[')
    end = text.rfind(']')
    if start >= 0 and end > start:
        return text[start:end+1]
    return text


def parse_mcq_payload(payload_text):
    if not payload_text:
        return []

    cleaned = re.sub(r"```(?:json)?", "", payload_text, flags=re.IGNORECASE).strip()
    cleaned = extract_json_array(cleaned)
    if not cleaned:
        return []

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse MCQ response: %s", exc)
        return []

    if isinstance(payload, dict):
        payload = payload.get("questions", payload.get("mcqs", []))
    return payload if isinstance(payload, list) else []


def normalize_mcq_payload(items, count, default_difficulty="MEDIUM"):
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        raw_options = item.get("options", {})
        if isinstance(raw_options, list):
            raw_options = dict(zip(("A", "B", "C", "D"), raw_options))
        if not isinstance(raw_options, dict):
            raw_options = {}

        options = {}
        for letter in ("A", "B", "C", "D"):
            value = item.get(
                f"option{letter}",
                item.get(
                    f"option_{letter.lower()}",
                    raw_options.get(
                        letter,
                        raw_options.get(
                            letter.lower(),
                            raw_options.get(f"option{letter}", raw_options.get(f"option_{letter.lower()}")),
                        ),
                    ),
                ),
            )
            if isinstance(value, str):
                value = value.strip()
            if not isinstance(value, str) or not value or len(value) > 500:
                break
            options[letter] = value
        if len(options) != 4:
            continue

        question = item.get("question")
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > 1000:
            continue

        raw_answer = item.get("correctAnswer", item.get("correct_answer", item.get("answer", "")))
        answer_text = str(raw_answer).strip()
        answer_match = re.match(
            r"^(?:ANSWER\s*[:=-]?\s*)?(?:OPTION\s*)?([A-D])(?:\b|[.:)])",
            answer_text,
            re.IGNORECASE,
        )
        if answer_match:
            correct_answer = answer_match.group(1).upper()
        else:
            correct_answer = next(
                (letter for letter, option in options.items() if option.casefold() == answer_text.casefold()),
                None,
            )
        if correct_answer not in options:
            continue

        difficulty = str(item.get("difficulty", default_difficulty)).upper()
        if difficulty not in {"EASY", "MEDIUM", "HARD"}:
            difficulty = default_difficulty

        normalized.append({
            "question": question.strip(),
            "optionA": options["A"],
            "optionB": options["B"],
            "optionC": options["C"],
            "optionD": options["D"],
            "correctAnswer": correct_answer,
            "difficulty": difficulty,
        })
        if len(normalized) >= count:
            break

    return normalized


def quiz_percentage(quiz):
    if quiz.score > quiz.total:
        return quiz.score
    return (quiz.score / quiz.total) * 100


# --- Authentication Views ---
@api_view(['POST'])
@permission_classes([AllowAny])
@parser_classes([JSONParser])
def register_user(request):
    email = request.data.get('email')
    password = request.data.get('password')
    name = request.data.get('name')
    
    if not email or not password or not name:
        return Response(
            {"message": "Name, email, and password are required."},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    if User.objects.filter(email=email).exists():
        return Response(
            {"message": "Email is already registered. Please login."},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    user = User.objects.create_user(
        email=email,
        name=name,
        password=password,
        role='USER' # Default role
    )
    
    token = generate_jwt_token(user)
    
    return Response({
        "token": token,
        "name": user.name,
        "email": user.email,
        "role": user.role
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
@parser_classes([JSONParser])
def login_user(request):
    email = request.data.get('email')
    password = request.data.get('password')
    
    if not email or not password:
        return Response(
            {"message": "Email and password are required."},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {"message": "Bad credentials"},
            status=status.HTTP_401_UNAUTHORIZED
        )
        
    if not user.check_password(password):
        return Response(
            {"message": "Bad credentials"},
            status=status.HTTP_401_UNAUTHORIZED
        )
        
    token = generate_jwt_token(user)
    
    return Response({
        "token": token,
        "name": user.name,
        "email": user.email,
        "role": user.role
    }, status=status.HTTP_200_OK)


# --- Dashboard Views ---
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_dashboard_stats(request):
    user = request.user
    materials = StudyMaterial.objects.filter(user=user)
    total_materials = materials.count()
    total_notes = total_materials  # Notes are 1:1 with materials
    
    quizzes = QuizResult.objects.filter(user=user, total__gt=0)
    total_quizzes = quizzes.count()
    
    avg_score = 0.0
    if total_quizzes > 0:
        total_pct = sum(quiz_percentage(q) for q in quizzes)
        avg_score = total_pct / total_quizzes
        
    return Response({
        "totalMaterials": total_materials,
        "totalNotes": total_notes,
        "totalQuizzes": total_quizzes,
        "averageScore": avg_score
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_performance_analytics(request):
    user = request.user
    quizzes = QuizResult.objects.filter(user=user, total__gt=0).order_by('-date')
    
    if not quizzes.exists():
        return Response({
            "overallProgress": 0.0,
            "geminiSummary": "Start taking quizzes to see your analysis!",
            "weakAreas": []
        })
        
    # Group by material (topic)
    topic_quizzes = {}
    for quiz in quizzes:
        if quiz.material:
            topic_quizzes.setdefault(quiz.material, []).append(quiz)
            
    weak_areas = []
    stats_lines = []
    
    for material, topic_q_list in topic_quizzes.items():
        topic_avg = sum(quiz_percentage(q) for q in topic_q_list) / len(topic_q_list)
        stats_lines.append(f"{material.title}: {round(topic_avg)}%")
        
        if topic_avg < 70.0:
            weak_areas.append({
                "topic": material.title,
                "averageScore": topic_avg,
                "attempts": len(topic_q_list),
                "suggestion": f"Focus on {material.title} basics."
            })
            
    stats_content = "\n".join(stats_lines)
    prompt = "Based on the following quiz performance data (Topic: Average Score), provide a short summary of weak areas and 3 specific study recommendations. Keep it concise."
    
    gemini_service = GeminiService()
    try:
        gemini_insight = gemini_service.generate_content(prompt, stats_content)
    except GeminiQuotaExceededException:
        gemini_insight = (
            "AI-generated insights are temporarily unavailable (quota limit reached). "
            "Based on your scores, focus on the topics listed under \"Weak Areas\" below."
        )
    except Exception:
        gemini_insight = "Analytics review is temporarily unavailable."
        
    overall_progress = sum(quiz_percentage(q) for q in quizzes) / quizzes.count()
    
    return Response({
        "weakAreas": weak_areas,
        "geminiSummary": gemini_insight,
        "overallProgress": overall_progress
    })


# --- Study Material Views ---
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_material(request):
    user = request.user

    file_obj = request.FILES.get('file')
    title = request.data.get('title')
    upload_type = str(request.data.get('type', 'pdf')).strip().lower()
    youtube_url = request.data.get('youtubeUrl')

    try:
        questions = int(request.data.get('questions', '10'))
    except (TypeError, ValueError):
        return Response({"message": "Questions must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
    if not 1 <= questions <= 50:
        return Response({"message": "Questions must be between 1 and 50."}, status=status.HTTP_400_BAD_REQUEST)

    difficulty = str(request.data.get('difficulty', 'MEDIUM')).upper()
    if difficulty not in {'EASY', 'MEDIUM', 'HARD'}:
        return Response({"message": "Difficulty must be EASY, MEDIUM, or HARD."}, status=status.HTTP_400_BAD_REQUEST)
    normalized_title = title.strip() if title and title.strip() else "Untitled Material"

    logger.info("Upload request received: type=%s title=%s", upload_type, normalized_title)

    try:
        extraction_service = ContentExtractionService()
        extracted_text, detected_type = extraction_service.extract_content(upload_type, file_obj=file_obj, youtube_url=youtube_url)
    except ValueError as exc:
        logger.error("Extraction failed: %s", exc)
        return Response({"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.exception("Unexpected extraction failure: %s", exc)
        return Response({"message": "Failed to extract study material."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    logger.info("Extraction complete for %s, text length=%s", detected_type, len(extracted_text or ""))
    logger.info("Extraction preview: %s", (extracted_text or "")[:500])

    material = StudyMaterial.objects.create(
        user=user,
        title=normalized_title,
        content_text=extracted_text,
        type=upload_type.upper()
    )

    gemini_service = GeminiService()
    fallback_generator = FallbackContentGenerator()
    generation_warnings = []

    try:
        generated_notes_text = gemini_service.generate_content_from_chunks(
            Prompts.build_notes_prompt(),
            extracted_text,
        )
    except GeminiQuotaExceededException:
        generated_notes_text = fallback_generator.generate_basic_notes(extracted_text, title=normalized_title)
        generation_warnings.append("Gemini quota was exceeded; simple fallback notes were generated.")
    except Exception as exc:
        logger.exception("Notes generation failed: %s", exc)
        generated_notes_text = fallback_generator.generate_basic_notes(extracted_text, title=normalized_title)
        generation_warnings.append("AI notes were unavailable; simple fallback notes were generated.")

    GeneratedNotes.objects.update_or_create(
        material=material,
        defaults={"notes_text": generated_notes_text}
    )

    try:
        mcq_payload = gemini_service.generate_content_from_chunks(
            Prompts.build_mcq_prompt(count=questions, difficulty=difficulty),
            extracted_text,
        )
        mcq_dtos = normalize_mcq_payload(
            parse_mcq_payload(mcq_payload),
            questions,
            default_difficulty=difficulty,
        )
        if not mcq_dtos:
            raise ValueError("No MCQs were returned")
    except GeminiQuotaExceededException:
        mcq_dtos = fallback_generator.generate_basic_mcqs(extracted_text, questions, title=normalized_title)
        generation_warnings.append("Gemini quota was exceeded; basic fallback quiz questions were generated.")
    except Exception as exc:
        logger.exception("MCQ generation failed: %s", exc)
        mcq_dtos = fallback_generator.generate_basic_mcqs(extracted_text, questions, title=normalized_title)
        generation_warnings.append("AI quiz generation was unavailable; basic fallback questions were generated.")

    mcqs_saved = []
    for item in mcq_dtos:
        mcq = GeneratedMCQ.objects.create(
            material=material,
            question=item.get("question", "Question"),
            option_a=item.get("optionA", "Option A"),
            option_b=item.get("optionB", "Option B"),
            option_c=item.get("optionC", "Option C"),
            option_d=item.get("optionD", "Option D"),
            correct_answer=item.get("correctAnswer", "A"),
            difficulty=item.get("difficulty", "MEDIUM")
        )
        mcqs_saved.append(mcq)

    return Response({
        "materialId": material.id,
        "title": material.title,
        "notes": generated_notes_text,
        "aiGenerationAvailable": bool(
            settings.GEMINI_API_KEY and "YOUR_GEMINI_API_KEY" not in settings.GEMINI_API_KEY
        ),
        "generationWarnings": generation_warnings,
        "mcqs": [
            {
                "id": m.id,
                "question": m.question,
                "optionA": m.option_a,
                "optionB": m.option_b,
                "optionC": m.option_c,
                "optionD": m.option_d,
                "correctAnswer": m.correct_answer,
                "difficulty": m.difficulty
            } for m in mcqs_saved
        ]
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_user_materials(request):
    user = request.user
    materials = StudyMaterial.objects.filter(user=user).order_by('-uploaded_at')
    
    result = []
    for m in materials:
        result.append({
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "uploadedAt": m.uploaded_at.isoformat(),
            "mcqCount": m.mcqs.count(),
        })
        
    return Response(result, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_material_content(request, material_id):
    user = request.user
    try:
        material = StudyMaterial.objects.get(id=material_id)
    except StudyMaterial.DoesNotExist:
        return Response({"message": "Material not found"}, status=status.HTTP_404_NOT_FOUND)

    if material.user != user:
        return Response({"message": "Unauthorized access to material"}, status=status.HTTP_403_FORBIDDEN)

    try:
        notes = GeneratedNotes.objects.get(material=material)
        notes_text = notes.notes_text
    except GeneratedNotes.DoesNotExist:
        notes_text = ""
        
    mcqs = GeneratedMCQ.objects.filter(material=material)
    
    return Response({
        "materialId": material.id,
        "title": material.title,
        "notes": notes_text,
        "mcqs": [
            {
                "id": m.id,
                "question": m.question,
                "optionA": m.option_a,
                "optionB": m.option_b,
                "optionC": m.option_c,
                "optionD": m.option_d,
                "correctAnswer": m.correct_answer,
                "difficulty": m.difficulty
            } for m in mcqs
        ]
    }, status=status.HTTP_200_OK)


# --- Quiz Result Views ---
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser])
def save_quiz_result(request):
    user = request.user
    score = request.data.get('score')
    total = request.data.get('total')
    material_id = request.data.get('materialId')
    
    if score is None or total is None:
        return Response({"message": "Score and total are required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        score = int(score)
        total = int(total)
    except (TypeError, ValueError):
        return Response({"message": "Score and total must be whole numbers."}, status=status.HTTP_400_BAD_REQUEST)
    if total <= 0 or score < 0 or score > total:
        return Response({"message": "Score must be between 0 and a total greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

    material = None
    if material_id:
        try:
            material = StudyMaterial.objects.get(id=material_id, user=user)
        except (StudyMaterial.DoesNotExist, TypeError, ValueError):
            return Response({"message": "Material not found."}, status=status.HTTP_404_NOT_FOUND)
            
    quiz_result = QuizResult.objects.create(
        user=user,
        material=material,
        score=score,
        total=total
    )
    
    return Response({
        "id": quiz_result.id,
        "materialId": quiz_result.material.id if quiz_result.material else None,
        "materialTitle": quiz_result.material.title if quiz_result.material else None,
        "score": quiz_result.score,
        "total": quiz_result.total,
        "date": quiz_result.date.isoformat()
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_quiz_history(request):
    user = request.user
    quizzes = QuizResult.objects.filter(user=user, total__gt=0).order_by('-date')
    
    result = []
    for q in quizzes:
        result.append({
            "id": q.id,
            "materialId": q.material.id if q.material else None,
            "materialTitle": q.material.title if q.material else None,
            "score": q.score,
            "total": q.total,
            "date": q.date.isoformat()
        })
        
    return Response(result, status=status.HTTP_200_OK)


# --- Admin Panel Views ---
@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_admin_stats(request):
    total_users = User.objects.count()
    total_materials = StudyMaterial.objects.count()
    total_quizzes = QuizResult.objects.count()
    
    # Match Spring Boot placeholder logic
    active_users_today = User.objects.filter(role='USER').count()
    
    return Response({
        "totalUsers": total_users,
        "totalMaterials": total_materials,
        "totalQuizzes": total_quizzes,
        "activeUsersToday": active_users_today
    })


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_all_users(request):
    users = User.objects.all().order_by('id')
    
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "createdAt": u.created_at.isoformat()
        })
    return Response(result)


@api_view(['DELETE'])
@permission_classes([IsAdminUserRole])
def delete_user(request, id):
    try:
        user = User.objects.get(id=id)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    except User.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_all_content(request):
    materials = StudyMaterial.objects.all().order_by('-uploaded_at')
    
    result = []
    for m in materials:
        result.append({
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "uploadedAt": m.uploaded_at.isoformat()
        })
    return Response(result)


@api_view(['DELETE'])
@permission_classes([IsAdminUserRole])
def delete_content(request, id):
    try:
        material = StudyMaterial.objects.get(id=id)
        material.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    except StudyMaterial.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_all_mcqs(request):
    mcqs = GeneratedMCQ.objects.all().order_by('id')
    
    result = []
    for m in mcqs:
        result.append({
            "id": m.id,
            "question": m.question,
            "optionA": m.option_a,
            "optionB": m.option_b,
            "optionC": m.option_c,
            "optionD": m.option_d,
            "correctAnswer": m.correct_answer,
            "difficulty": m.difficulty
        })
    return Response(result)


@api_view(['DELETE'])
@permission_classes([IsAdminUserRole])
def delete_mcq(request, id):
    try:
        mcq = GeneratedMCQ.objects.get(id=id)
        mcq.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    except GeneratedMCQ.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_detailed_analytics(request):
    return Response({
        "totalUsers": User.objects.count(),
        "totalUploads": StudyMaterial.objects.count(),
        "totalQuizzes": QuizResult.objects.count(),
        "totalMCQs": GeneratedMCQ.objects.count()
    })


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def get_system_logs(request):
    # Mock logs corresponding to Spring Boot's controller response
    return Response([
        {"event": "New User Registered", "time": "10 mins ago"},
        {"event": "Gemini Generated 5 MCQs", "time": "25 mins ago"},
        {"event": "Admin accessed secure panel", "time": "1 hour ago"}
    ])


# --- Static Frontend Serving ---
@api_view(['GET'])
@permission_classes([AllowAny])
def serve_frontend(request, path=''):
    path = os.path.normpath(path).lstrip(os.path.sep)
    if path == '.' or path == '':
        path = 'index.html'
        
    frontend_dir = os.path.abspath(os.path.join(settings.BASE_DIR, '..', 'frontend'))
    file_path = os.path.abspath(os.path.join(frontend_dir, path))
    
    # Prevent traversal attacks
    if not file_path.startswith(frontend_dir):
        return HttpResponse("Forbidden", status=403)
        
    if os.path.exists(file_path) and os.path.isfile(file_path):
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        
        # Override mimetypes for stability on Windows
        if path.endswith('.js'):
            mime_type = 'application/javascript'
        elif path.endswith('.css'):
            mime_type = 'text/css'
            
        with open(file_path, 'rb') as f:
            return HttpResponse(f.read(), content_type=mime_type)
            
    # Standard fallback support for extensionless file urls (e.g. /login -> /login.html)
    if not path.endswith('.html'):
        html_file = file_path + '.html'
        if os.path.exists(html_file) and os.path.isfile(html_file):
            with open(html_file, 'rb') as f:
                return HttpResponse(f.read(), content_type='text/html')
                
    return HttpResponse("Not Found", status=404)
