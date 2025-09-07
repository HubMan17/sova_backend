import logging
import io, gzip, json, math, traceback
from typing import Dict, Any, List
from datetime import datetime, timezone

from django.utils.dateparse import parse_datetime
from django.db.models import Q
from django.db import IntegrityError, transaction

from django.utils import timezone as dj_tz
from datetime import datetime as dt

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from rest_framework.permissions import IsAuthenticated, IsAdminUser

from api_v1.urils.telemetry_utils import maybe_mark_power_on
from api_v1.urils.notify import tg_send
from api_v1.tasks import send_arm_report_notification

from .permissions import IsSuperUser

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from collections import defaultdict

from app.models import ArmReport, Note, AuthUser, Category, Photo, Video, Board, Telemetry

from .urils.add_reaction import add_reaction

# from djangoBackend.tasks import on_telemetry_ingest

from .serializers import AuthUserSerializer, CategoryDetailSerializerBot, CustomLoginSerializer
from .serializers import NewEntrySerializer, PopularNotesSerializer
from .serializers import CategorySerializer
from .serializers import NotesOfCategorySerializer
from .serializers import PhotoSerializer
from .serializers import VideoSerializer
from .serializers import NoteDetailSerializer
from .serializers import NoteSerializer

from .serializers import NoteSerializerBot
from .serializers import NoteDetailSerializerBot


# обработка запросов с бортов

logger = logging.getLogger(__name__)


def parse_ts_aware(ts_str: str):
    """
    Преобразует строку времени в aware datetime в текущем TZ Django.
    Поддерживает ISO и 'YYYY-mm-dd HH:MM:SS'.
    """
    if not ts_str:
        return dj_tz.now()
    # ISO → datetime
    try:
        d = dt.fromisoformat(ts_str.replace(" ", "T"))
    except Exception:
        try:
            d = dt.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return dj_tz.now()

    if dj_tz.is_naive(d):
        return dj_tz.make_aware(d)
    return dj_tz.localtime(d)


class ArmReportIngestView(APIView):
    """
    POST /api/v1/arm-report/
    Ожидает gzip-NDJSON с объектами вида:
      {"ts":"2025-09-06 18:52:28","boat":133,"arms":12,"arm_sec":60.0,"qstab_sec":30.0}
    """

    def post(self, request, *args, **kwargs):
        try:
            # --- читаем тело ---
            body = request.body
            if request.META.get("HTTP_CONTENT_ENCODING", "") == "gzip":
                buf = io.BytesIO(body)
                with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
                    raw = gz.read().decode("utf-8", errors="replace")
            else:
                raw = body.decode("utf-8", errors="replace")

            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            objs = []
            for line in lines:
                try:
                    obj = json.loads(line)
                    objs.append(obj)
                except Exception:
                    logger.warning("arm-report: skip bad JSON line=%r", line[:200])

            if not objs:
                return Response({"error": "empty payload"}, status=status.HTTP_400_BAD_REQUEST)

            saved_ids = []

            with transaction.atomic():
                for r in objs:
                    ts_str = r.get("ts")
                    boat = r.get("boat")
                    arms = r.get("arms")
                    arm_sec = r.get("arm_sec")
                    qstab_sec = r.get("qstab_sec")

                    if not (ts_str and boat is not None and arms is not None):
                        logger.warning("arm-report: skip incomplete row %s", r)
                        continue

                    ts_dt = parse_ts_aware(ts_str)

                    rpt = ArmReport.objects.create(
                        boat_number=int(boat),
                        ts=ts_dt,
                        arms=int(arms),
                        arm_sec=float(arm_sec or 0.0),
                        qstab_sec=float(qstab_sec or 0.0),
                    )
                    saved_ids.append(rpt.id)

            # после коммита → ставим таски
            def _enqueue(ids):
                for rid in ids:
                    try:
                        logger.info("ARM ingest: enqueue send_arm_report_notification(id=%s)", rid)
                        send_arm_report_notification.delay(rid)
                    except Exception:
                        logger.exception("ARM ingest: .delay failed for id=%s", rid)

            transaction.on_commit(lambda ids=list(saved_ids): _enqueue(ids))

            return Response({"ok": True, "saved": len(saved_ids)}, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("ArmReportIngestView.post error")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _nan_to_none(x):
    try:
        if x is None: return None
        if isinstance(x, str) and x.strip().lower() in ("nan","null","none"):
            return None
        if isinstance(x, float) and math.isnan(x): return None
        return x
    except Exception:
        return None

def _to_bool01(x):
    if x in (1,"1",True,"true","True"):   return True
    if x in (0,"0",False,"false","False"): return False
    return False

def _ts_from_payload(obj):
    ts_epoch = obj.get("ts_epoch")
    ts = None
    if ts_epoch is not None:
        try: ts = datetime.fromtimestamp(int(ts_epoch), tz=timezone.utc)
        except Exception: ts = None
    if ts is None:
        ts_str = obj.get("ts")
        if ts_str:
            ts = parse_datetime(ts_str)
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
    return ts or datetime.now(timezone.utc), ts_epoch

class TelemetryFromJsonl(APIView):
    """
    POST text/plain NDJSON (по строке JSON на запись) или application/json (список объектов).
    Поля: boat, ts/ts_epoch?, sess?, seq?, lat, lon, alt_m, gs, hdg, volt, mode, wind_spd, wind_dir, gps, arm.
    """

    def post(self, request, *args, **kwargs):
        try:
            raw = request.body or b""
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

            payloads = []

            ct = (request.META.get("CONTENT_TYPE") or "").lower()
            if "application/json" in ct:
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                    if isinstance(obj, list):
                        payloads.extend(obj)
                    elif isinstance(obj, dict):
                        payloads.append(obj)
                except Exception as e:
                    return Response({"error":"bad json","detail":str(e)}, status=400)
            else:
                # NDJSON
                for ln in raw.decode("utf-8","replace").splitlines():
                    s = ln.strip()
                    if not s: continue
                    try:
                        payloads.append(json.loads(s))
                    except Exception as e:
                        print(f"[telemetry] jsonl parse error: {e} line={s[:120]}")
                        # пропускаем строку

            saved, updated, errors = 0, 0, 0
            boards_touched = set()

            for obj in payloads:
                try:
                    boat = obj.get("boat")
                    if boat is None:
                        errors += 1
                        continue

                    # борт (создадим при первом сообщении)
                    board, _ = Board.objects.get_or_create(
                        boat_number=int(boat),
                        defaults={"status": "active"},
                    )
                    boards_touched.add(board.boat_number)

                    ts, ts_epoch = _ts_from_payload(obj)

                    sess = obj.get("sess") or None
                    seq = _nan_to_none(obj.get("seq"))
                    lat = _nan_to_none(obj.get("lat"))
                    lon = _nan_to_none(obj.get("lon"))
                    alt_m = _nan_to_none(obj.get("alt_m"))
                    gs  = _nan_to_none(obj.get("gs"))
                    hdg = _nan_to_none(obj.get("hdg"))
                    volt = _nan_to_none(obj.get("volt"))
                    mode = obj.get("mode")
                    wind_spd = _nan_to_none(obj.get("wind_spd"))
                    wind_dir = _nan_to_none(obj.get("wind_dir"))
                    gps = obj.get("gps")
                    arm = _to_bool01(obj.get("arm"))

                    # сохраняем телеметрию
                    if sess and seq is not None:
                        try:
                            with transaction.atomic():
                                Telemetry.objects.create(
                                    board=board,
                                    ts=ts, ts_epoch=ts_epoch,
                                    sess=sess, seq=int(seq),
                                    lat=lat, lon=lon, alt_m=alt_m,
                                    gs=gs, hdg=hdg, volt=volt, mode=mode,
                                    wind_spd=wind_spd, wind_dir=wind_dir,
                                    gps=gps, arm=arm,
                                )
                                saved += 1
                        except IntegrityError:
                            q = Telemetry.objects.filter(board=board, sess=sess, seq=int(seq))
                            updated += q.update(
                                ts=ts, ts_epoch=ts_epoch,
                                lat=lat, lon=lon, alt_m=alt_m,
                                gs=gs, hdg=hdg, volt=volt, mode=mode,
                                wind_spd=wind_spd, wind_dir=wind_dir,
                                gps=gps, arm=arm,
                            )
                    else:
                        Telemetry.objects.create(
                            board=board,
                            ts=ts, ts_epoch=ts_epoch,
                            sess=sess, seq=int(seq) if isinstance(seq,(int,float)) else None,
                            lat=lat, lon=lon, alt_m=alt_m,
                            gs=gs, hdg=hdg, volt=volt, mode=mode,
                            wind_spd=wind_spd, wind_dir=wind_dir,
                            gps=gps, arm=arm,
                        )
                        saved += 1

                    # сразу отметим «включился», если был оффлайн
                    try:
                        maybe_mark_power_on(board, obj, ts)
                    except Exception as e:
                        print(f"[telemetry] maybe_mark_power_on error: {e}")

                except Exception as e:
                    errors += 1
                    print(f"[telemetry] item error: {e}  obj={str(obj)[:160]}")

            resp = {"saved": saved, "updated": updated, "errors": errors, "boards": sorted(boards_touched)}
            # print(f"[telemetry] {resp}")   # видно и в runserver, и в gunicorn
            return Response(resp, status=200)

        except Exception as e:
            print(f"[telemetry] fatal: {e}")
            return Response({"error": str(e)}, status=400)

    def get(self, request, *args, **kwargs):
        return Response({"status": "ok"}, status=200)


# апи для бота

class SearchNotesByTagAndQueryAPIView(APIView):
    """
    Представление для поиска записей с тегом категории и строки в названии.
    """

    def get(self, request):
        # Получаем параметры из запроса
        tag = request.query_params.get('tag', None)
        query = request.query_params.get('query', None)

        if not tag or not query:
            return Response({"detail": "Both 'tag' and 'query' parameters are required."}, status=status.HTTP_400_BAD_REQUEST)

        # Фильтруем категории по тегу
        categories = Category.objects.filter(tag=tag)  # Используем filter, чтобы получить все категории с этим тегом

        # Если категории не найдены
        if not categories.exists():
            return Response({"detail": "Category with this tag not found."}, status=status.HTTP_404_NOT_FOUND)

        # Ищем записи, которые принадлежат найденным категориям и содержат строку в названии
        notes = Note.objects.filter(category__in=categories, title__icontains=query)

        # Если таких записей нет
        if not notes.exists():
            return Response({"detail": "No notes found matching the query."}, status=status.HTTP_404_NOT_FOUND)

        # Сериализуем найденные записи
        serializer = NoteSerializer(notes, many=True)

        return Response(serializer.data, status=status.HTTP_200_OK)


class NoteDetailAPIViewBot(APIView):
    """
    Представление для получения данных записи по её id.
    """

    def get(self, request, note_id):
        try:
            # Получаем запись по id
            note = Note.objects.get(id=note_id)

            # Сериализуем данные
            serializer = NoteDetailSerializerBot(note)

            return Response(serializer.data, status=status.HTTP_200_OK)

        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)


class CategoryDetailAPIViewBot(APIView):
    """
    Представление для получения данных записи по её id.
    """

    def get(self, request, category_id):
        try:
            # Получаем запись по id
            category = Category.objects.get(id=category_id)

            # Сериализуем данные
            serializer = CategoryDetailSerializerBot(category)

            return Response(serializer.data, status=status.HTTP_200_OK)

        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)


class NotesByCategoryIdAPIView(APIView):
    """
    Представление для получения всех записей из категории по ID.
    """

    def get(self, request):
        # Получаем id категории из параметров запроса
        category_id = request.query_params.get('tag', None)
        print(category_id)
        
        if not category_id:
            return Response({"detail": "Category ID parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Находим категорию по переданному ID
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return Response({"detail": "Category with this ID not found."}, status=status.HTTP_404_NOT_FOUND)

        # Получаем все записи, которые относятся к этой категории
        notes = Note.objects.filter(category=category)

        # Сериализуем записи
        serializer = NoteSerializerBot(notes, many=True)

        return Response(serializer.data, status=status.HTTP_200_OK)


class NotesByCategoryTagAPIView(APIView):
    """
    Представление для получения всех записей из категории по тегу.
    """

    def get(self, request):
        # Получаем тег категории из параметров запроса
        tag = request.query_params.get('tag', None)

        if not tag:
            return Response({"detail": "Tag parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Находим все категории по тегу
        categories = Category.objects.filter(tag=tag)

        # Если категорий с таким тегом нет
        if not categories.exists():
            return Response({"detail": "Category with this tag not found."}, status=status.HTTP_404_NOT_FOUND)

        # Получаем все записи, которые относятся к найденным категориям
        notes = Note.objects.filter(category__in=categories)

        # Сериализуем записи
        serializer = NoteSerializerBot(notes, many=True)

        return Response(serializer.data, status=status.HTTP_200_OK)


class FilterCategoriesByTagAPIView(APIView):
    """
    Представление для получения категорий по переданному тегу.
    """

    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request):
        # Получаем тег из параметров запроса
        tag = request.query_params.get('tag', None)

        if not tag:
            return Response({"detail": "Tag parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Фильтруем категории по переданному тегу
        categories = Category.objects.filter(tag=tag)

        # Если категорий нет, возвращаем пустой список
        if not categories.exists():
            return Response({"detail": "No categories found for the given tag."}, status=status.HTTP_404_NOT_FOUND)

        # Сериализуем данные
        serializer = CategorySerializer(categories, many=True)

        return Response(serializer.data, status=status.HTTP_200_OK)


class CheckTGIDAPIView(APIView):
    """
    Представление для проверки наличия пользователя по tg_id.
    """
    
    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request, tg_id):
        # Проверяем, существует ли пользователь с таким tg_id
        try:
            user = AuthUser.objects.get(tg_id=tg_id)  # Ищем пользователя по tg_id
            return Response({"detail": "User found."}, status=status.HTTP_200_OK)  # Если найден — возвращаем 200 OK
        except AuthUser.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_403_FORBIDDEN)  # Если не найден — возвращаем 403 Forbidden


# апи для веба
class SearchNotesAPIView(APIView):
    """
    Представление для поиска записей по названию, тегам и категориям.
    """

    def get(self, request):
        query = request.query_params.get('query', '')

        if query:
            # Разбиваем запрос на отдельные слова
            query_words = query.split()

            # Создаем фильтр для комбинированного поиска по каждому слову
            filters = Q()
            for word in query_words:
                filters |= Q(title__icontains=word) | \
                          Q(category__title__icontains=word) | \
                          Q(notetags_set__id_tag__name__icontains=word)  # Используем notetags_set

            # Фильтруем записи по всем словам
            notes = Note.objects.filter(filters).distinct()

            # Сериализуем данные
            serializer = NotesOfCategorySerializer(notes, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)


class AddReactionAPIView(APIView):
    """
    Представление для добавления/обновления реакции пользователя (лайк или дизлайк) прямо в поля Note.
    """

    def post(self, request, note_id):
        reaction_type = request.data.get('reaction_type')  # Получаем тип реакции (like или dislike)
        
        if reaction_type not in ['like', 'dislike']:
            return Response({"detail": "Invalid reaction type."}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            note = Note.objects.get(id=note_id)
        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)

        # Добавляем реакцию
        success, message = add_reaction(request.user, note, reaction_type)

        if success:
            return Response({"detail": message}, status=status.HTTP_200_OK)
        else:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


class NoteDetailAPIView(APIView):
    """
    Представление для получения полной информации о записи Note по ID.
    """

    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request, note_id):
        try:
            note = Note.objects.get(id=note_id)
        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)

        # add views
        note.view_count += 1
        note.save()

        # Сериализуем запись с полной информацией
        serializer = NoteDetailSerializer(note)

        # Возвращаем всю информацию о записи
        return Response(serializer.data)    


class UploadVideosAPIView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, note_id):
        try:
            note = Note.objects.get(id=note_id)
        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)

        # Получаем все видео из request.FILES (множество файлов)
        videos = request.FILES.getlist('videos')  # 'videos' — это имя поля в форме

        # Для каждого видео создаем объект Video
        uploaded_videos = []
        for video in videos:
            video_obj = Video.objects.create(note=note, video=video)
            uploaded_videos.append(video_obj)

        # Сериализуем загруженные видео
        serializer = VideoSerializer(uploaded_videos, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UploadPhotosAPIView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, note_id):
        try:
            note = Note.objects.get(id=note_id)
        except Note.DoesNotExist:
            return Response({"detail": "Note not found."}, status=status.HTTP_404_NOT_FOUND)

        # Получаем все фото из request.FILES (множество файлов)
        photos = request.FILES.getlist('images')  # 'images' — это имя поля в форме

        # Для каждого файла создаем объект Photo
        uploaded_photos = []
        for image in photos:
            photo = Photo.objects.create(note=note, image=image)
            uploaded_photos.append(photo)

        # Сериализуем загруженные фото
        serializer = PhotoSerializer(uploaded_photos, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class NotesByCategoryStrAPIView(APIView):
    """
    Представление для получения всех записей Note для указанной категории.
    """
    
    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    # permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request, category_str):
        # Получаем все категории с данным тегом
        categories = Category.objects.filter(tag=category_str)

        # Если категория не найдена, возвращаем ошибку
        if not categories.exists():
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)

        if category_str == "preArmError": category_str = "Все ошибки"
        elif category_str == "setupInstructions": category_str = "Все инструкции"
            

        # Фильтруем записи Note по категориям
        notes = Note.objects.filter(category__in=categories)

        # Сериализуем данные записей
        notes_serializer = NotesOfCategorySerializer(notes, many=True)

        # Возвращаем результат с двумя вложенностями
        return Response({
            'category': {
                'id': categories.first().id,  # Возвращаем ID первой категории (так как это могут быть несколько категорий)
                'title': category_str  # Название категории, соответствующее тегу
            },
            'notes': notes_serializer.data  # Список всех записей, связанных с категориями
        })


class NotesByCategoryAPIView(APIView):
    """
    Представление для получения всех записей Note для указанной категории.
    """
    
    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    # permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request, category_id):

        # Проверяем, существует ли категория с данным ID
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)

        # Фильтруем записи Note по категории
        notes = Note.objects.filter(category=category)

        # Сериализуем данные
        category_serializer = CategorySerializer(category)
        notes_serializer = NotesOfCategorySerializer(notes, many=True)

        # Возвращаем результат с двумя вложенностями
        return Response({
            'category': category_serializer.data,
            'notes': notes_serializer.data
        })


class CategoriesAPIView(APIView):
    """
    Представление для получения данных категорий. Доступно только аутентифицированным пользователям.
    """

    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    # permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request):
        # Извлекаем все категории как список
        categories = Category.objects.all()

        # Группируем категории по тегам
        grouped_categories = defaultdict(list)
        for category in categories:
            tag = category.tag
            grouped_categories[tag].append(category)

        # Сериализуем каждую группу категорий
        serialized_data = {}
        for tag, categories_list in grouped_categories.items():
            # Сериализуем группу категорий по тегу
            serialized_data[tag] = CategorySerializer(categories_list, many=True).data

        # Получаем 8 самых популярных категорий по visit_count
        popular_categories = Category.objects.all().order_by('-visit_count')[:8]

        # Добавляем вкладку popularNote
        serialized_data['popularNote'] = CategorySerializer(popular_categories, many=True).data

        return Response(serialized_data)


class PopularNotesAPIView(APIView):
    """
    Представление для получения данных новых записей. Доступно только аутентифицированным пользователям.
    """

    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request):
        # Извлекаем 6 последних записей, сортируя по `view_count` в убывающем порядке
        latest_notes = Note.objects.all().order_by('-view_count')[:6]

        # Сериализуем эти записи
        serializer = PopularNotesSerializer(latest_notes, many=True)

        # Отправляем данные
        return Response(serializer.data)


class NewEntryAPIView(APIView):
    """
    Представление для получения данных новых записей. Доступно только аутентифицированным пользователям.
    """

    authentication_classes = [JWTAuthentication]  # Указываем, что для аутентификации используется JWT
    permission_classes = [IsAuthenticated]  # Доступ только для аутентифицированных пользователей

    def get(self, request):
        # Извлекаем 6 последних записей, сортируя по `created_at` в убывающем порядке
        latest_notes = Note.objects.all().order_by('-id')[:6]

        # Сериализуем эти записи
        serializer = NewEntrySerializer(latest_notes, many=True)

        # Отправляем данные
        return Response(serializer.data)


class AuthUserDetailView(APIView):
    """
    Представление для получения данных о пользователе.
    """

    permission_classes = [IsAuthenticated]  # Для защиты API через авторизацию

    def get(self, request, user_id):
        try:
            user = AuthUser.objects.get(id=user_id)  # Получаем пользователя по ID
        except AuthUser.DoesNotExist:
            return Response({"detail": "User not found."}, status=404)

        serializer = AuthUserSerializer(user)  # Сериализуем данные пользователя
        return Response(serializer.data)  # Возвращаем данные

    
    
class CustomLoginAPIView(APIView):
    
    def post(self, request):
        
        serializer = CustomLoginSerializer(data=request.data)
        if serializer.is_valid():
            return Response(serializer.validated_data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    

    
# user = AuthUser.objects.create_user(
#     username='tg_bot',
#     email='stuff@me.com',
#     password="DHUS&^2b&*2b3129kjhDKBhjaBHJLBwDPJU@!Y!H(*!#Hbdw)",  # Просто передаем пароль как строку
#     tg_id='telegram_id'
# )

# from django.core.files import File
# # Загружаем локальную картинку
# with open('photo_2025-03-30_23-01-03.jpg', 'rb') as img:
#     note = Note.objects.get(id=3)
#     # Присваиваем локальное изображение
#     note.logo.save('photo_2025-03-30_23-01-03.jpg', File(img), save=True)



# 
#  Заполнение параметров
# 
# import pandas as pd

# # Читаем файл Excel
# df = pd.read_excel("params.xlsx")

# # Показываем первые строки
# # print(df.head())

# # Если хочешь работать с конкретными колонками:
# for index, row in df.iterrows():

#     if type(row["Category"]) == float:
#         break
    
#     if type(row["Desc category"]) != float:
#         category = Category.objects.get_or_create(title=row["Category"], site_description=row["Desc category"].capitalize(), tag="ArdupilotParam")
#     else:
#         category = Category.objects.get_or_create(title=row["Category"])
    
#     note = Note.objects.get_or_create(title=row["Param"], description=row["Desc param"].capitalize(), category=category[0], category_id=category[0].id, main_tag="ArdupilotParam")
