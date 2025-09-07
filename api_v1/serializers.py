import re
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from django.db.models import Q

from typing import Tuple, Optional

from django.utils import dateparse, timezone
from django.contrib.auth.hashers import check_password
from django.contrib.auth import get_user_model
from django.db import transaction

from app.models import AuthUser, BoardMovement, BoardSection, BoardSectionTransfer, BoardStatus, Note, Tags, Category, Photo, Video, UserRank, Telemetry, Board

from api_v1.urils.telemetry_utils import maybe_mark_power_on


User = get_user_model()


# сериализатор для телема
class TelemetryInSerializer(serializers.Serializer):
    # идентификаторы/штампы
    ts = serializers.CharField(required=False)
    ts_epoch = serializers.IntegerField(required=False, allow_null=True)
    raw = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    sess = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    boat = serializers.IntegerField(required=True)
    seq = serializers.IntegerField(required=False, allow_null=True)

    # данные
    mode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    lat = serializers.FloatField(required=False, allow_null=True)
    lon = serializers.FloatField(required=False, allow_null=True)
    alt_m = serializers.FloatField(required=False, allow_null=True)
    gs = serializers.FloatField(required=False, allow_null=True)
    hdg = serializers.FloatField(required=False, allow_null=True)
    volt = serializers.FloatField(required=False, allow_null=True)
    airspd = serializers.FloatField(required=False, allow_null=True)
    wind_n = serializers.FloatField(required=False, allow_null=True)
    wind_e = serializers.FloatField(required=False, allow_null=True)
    wind_spd = serializers.FloatField(required=False, allow_null=True)
    wind_dir = serializers.FloatField(required=False, allow_null=True)
    gps = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    arm = serializers.IntegerField(required=False, allow_null=True)

    def _parse_ts(self, ts_str, ts_epoch):
        if ts_epoch is not None:
            try:
                return timezone.datetime.fromtimestamp(int(ts_epoch), tz=timezone.utc)
            except Exception:
                pass
        if not ts_str:
            return timezone.now()
        dt = dateparse.parse_datetime(ts_str) or dateparse.parse_datetime(ts_str.replace(" ", "T"))
        if dt is None:
            return timezone.now()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    def create(self, v):
        board, _ = Board.objects.get_or_create(boat_number=v["boat"])
        ts = self._parse_ts(v.get("ts"), v.get("ts_epoch"))

        tel = Telemetry.objects.create(
            board=board,
            ts=ts,
            ts_epoch=v.get("ts_epoch"),
            raw=v.get("raw"),
            sess=v.get("sess"),
            seq=v.get("seq"),
            mode=v.get("mode"),
            lat=v.get("lat"),
            lon=v.get("lon"),
            alt_m=v.get("alt_m"),
            gs=v.get("gs"),
            hdg=v.get("hdg"),
            volt=v.get("volt"),
            airspd=v.get("airspd"),
            wind_n=v.get("wind_n"),
            wind_e=v.get("wind_e"),
            wind_spd=v.get("wind_spd"),
            wind_dir=v.get("wind_dir"),
            gps=v.get("gps"),
            arm=bool(v.get("arm", 0)),
        )

        # обновим только «сводку» в борте (без «last_*» служебных полей)
        maybe_mark_power_on(board, v, ts)

        return tel


"""
Сериализатор для бота
"""      
def resolve_submitted_user(raw_author: str | None):
    if not raw_author:
        return None, None
    a = (raw_author or "").strip()
    if a.lower().startswith("id:"):
        return None, a
    handle = a.lstrip("@").strip()
    display = f"@{handle}" if handle else None
    q = Q(username__iexact=handle)
    if any(f.name == "name" for f in User._meta.get_fields()):
        q |= Q(name__iexact=handle)
    user = User.objects.filter(q).first()
    return user, display

def resolve_user(author_str: Optional[str]) -> Tuple[Optional[User], Optional[str]]:
    if not author_str:
        return None, None
    original = author_str.strip()
    if not original:
        return None, None
    handle = original.lstrip("@").strip()  # убираем '@'
    user = User.objects.filter(username__iexact=handle).first()
    if user:
        return user, None
    # (по желанию можно расширить поиском по name/email)
    return None, handle  # сохраним подпись без '@'

def get_status_by_code_or_name(code: str, fallback_name: Optional[str] = None) -> Optional[BoardStatus]:
    # сперва по code
    obj = BoardStatus.objects.filter(code=code, is_active=True).first()
    if obj:
        return obj
    # опционально по имени (если кодов ещё нет)
    if fallback_name:
        return BoardStatus.objects.filter(name=fallback_name, is_active=True).first()
    return None

# маппинг: куда перевели -> какой статус ставим
SECTION_TO_STATUS = {
    "section4": ("to_section4", "Передан на 4-й участок"),
    "section5": ("accepted_section5", "Принят на 5-й участок"),
    # при необходимости добавь другие: "section3": ("accepted_section3", "Принят на 3-й участок")
}

class BoardSectionTransferCreateSerializer(serializers.Serializer):
    boat = serializers.IntegerField()
    to_section_code = serializers.SlugField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(required=False, allow_blank=True, default="api")
    author = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    context = serializers.JSONField(required=False)
    effective_at = serializers.DateTimeField(required=False, allow_null=True)
    from_section_code = serializers.SlugField(required=False, allow_blank=True, allow_null=True)

    @transaction.atomic
    def create(self, validated_data):
        from app.models import Board, BoardSection, BoardSectionTransfer  # поправь путь
        boat = validated_data["boat"]
        to_code = validated_data["to_section_code"]
        notes = validated_data.get("notes") or ""
        source = validated_data.get("source") or "api"
        context = validated_data.get("context") or {}
        effective_at = validated_data.get("effective_at")
        from_code = validated_data.get("from_section_code") or None

        board = Board.objects.get(boat_number=boat)
        to_section = BoardSection.objects.get(code=to_code)

        # определить from_section: явный параметр или текущее поле у board (если есть)
        from_section = None
        if from_code:
            from_section = BoardSection.objects.filter(code=from_code).first()
        elif hasattr(board, "current_section_id"):
            from_section = board.current_section

        submitted_by, submitted_display = resolve_submitted_user(validated_data.get("author"))

        tr = BoardSectionTransfer.objects.create(
            board=board,
            from_section=from_section,
            to_section=to_section,
            notes=notes,
            source=source,
            submitted_by=submitted_by,
            submitted_display=submitted_display,
            context=context,
            effective_at=effective_at,
        )

        # синхронизируем текущее поле у Board, если есть
        if hasattr(board, "current_section_id") and board.current_section_id != to_section.id:
            board.current_section = to_section
            board.save(update_fields=["current_section"])

        # (если нужен автостатус по маппингу, оставь как было)
        return tr

class BoardSectionTransferOutSerializer(serializers.ModelSerializer):
    board = serializers.SerializerMethodField()
    from_section = serializers.SerializerMethodField()
    to_section = serializers.SerializerMethodField()
    submitted_by = serializers.SerializerMethodField()

    class Meta:
        model = BoardSectionTransfer
        fields = (
            "id", "board", "from_section", "to_section",
            "notes", "source", "submitted_by", "submitted_display",
            "context", "effective_at", "created_at",
        )
        read_only_fields = fields

    def get_board(self, obj):
        return getattr(obj.board, "boat_number", None)

    def get_from_section(self, obj):
        return getattr(obj.from_section, "code", None)

    def get_to_section(self, obj):
        return getattr(obj.to_section, "code", None)

    def get_submitted_by(self, obj):
        return getattr(obj.submitted_by, "username", None)



def resolve_user_by_author_str(author_str: Optional[str]) -> Tuple[Optional[User], Optional[str]]:
    """
    Принимает строку автора (напр. '@Deim0sAA' или 'Deim0sAA').
    Возвращает (user|None, display|None). display сохраняем ТОЛЬКО если user не найден.
    Алгоритм:
      - срезаем ведущий '@'
      - ищем по username (без учёта регистра)
      - если в кастомной модели пользователя есть поле 'name' — ищем по нему
      - пробуем по email
      - если не нашли — возвращаем display БЕЗ '@'
    """
    if not author_str:
        return None, None

    original = author_str.strip()
    if not original:
        return None, None

    handle = original.lstrip("@").strip()  # <<< убрали '@'

    # 1) username (основной кейс)
    user = User.objects.filter(username__iexact=handle).first()
    if user:
        return user, None

    # 2) кастомное поле 'name', если оно есть в модели
    user_field_names = {f.name for f in User._meta.get_fields()}
    if "name" in user_field_names:
        user = User.objects.filter(name__iexact=handle).first()
        if user:
            return user, None
        # Если в БД 'name' хранится вместе с '@', попробуем и с оригиналом
        if original.startswith("@"):
            user = User.objects.filter(name__iexact=original).first()
            if user:
                return user, None

    # 3) email (на всякий)
    if "email" in user_field_names:
        user = User.objects.filter(email__iexact=handle).first()
        if user:
            return user, None

    # 4) не нашли — сохраняем подпись без '@'
    return None, handle


class BoardMovementCreateSerializer(serializers.Serializer):
    boat = serializers.IntegerField()
    status_code = serializers.SlugField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(required=False, allow_blank=True, default="api")
    author = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    context = serializers.JSONField(required=False)
    effective_at = serializers.DateTimeField(required=False, allow_null=True)

    @transaction.atomic
    def create(self, validated_data):
        # поправь импорт под твои пути
        from app.models import Board, BoardStatus, BoardMovement

        boat = validated_data["boat"]
        new_code = validated_data["status_code"]
        notes = validated_data.get("notes") or ""
        source = validated_data.get("source") or "api"
        context = validated_data.get("context") or {}
        effective_at = validated_data.get("effective_at")

        # блокируем строку борта, чтобы не было гонок
        board = Board.objects.select_for_update().get(boat_number=boat)

        # новый статус — объект
        new_status = BoardStatus.objects.get(code=new_code)

        # предыдущий статус борта хранится у Board как строковый код -> ищем объект (или None)
        prev_status_obj = None
        prev_code = getattr(board, "status", None)
        if prev_code:
            prev_status_obj = BoardStatus.objects.filter(code=prev_code).first()

        submitted_by, submitted_display = resolve_submitted_user(validated_data.get("author"))

        mv = BoardMovement.objects.create(
            board=board,
            previous_status=prev_status_obj,  # <-- ВАЖНО: объект, а не строка
            new_status=new_status,
            notes=notes,
            source=source,
            submitted_by=submitted_by,
            submitted_display=submitted_display,
            context=context,
            effective_at=effective_at,
        )

        # синхронизируем текущий код статуса в самой Board
        board.status = new_status.code
        board.save(update_fields=["status"])

        return mv


class BoardMovementOutSerializer(serializers.ModelSerializer):
    board = serializers.SerializerMethodField()
    previous_status = serializers.SerializerMethodField()
    new_status = serializers.SerializerMethodField()
    new_status_name = serializers.SerializerMethodField()
    submitted_by = serializers.SerializerMethodField()

    class Meta:
        model = BoardMovement
        fields = (
            "id",
            "board",
            "previous_status",
            "new_status",
            "new_status_name",
            "notes",
            "source",
            "submitted_by",
            "submitted_display",
            "context",
            "effective_at",
            "created_at",
        )
        read_only_fields = fields

    def get_board(self, obj):  # boat_number
        return getattr(obj.board, "boat_number", None)

    def get_previous_status(self, obj):
        return getattr(obj.previous_status, "code", None)

    def get_new_status(self, obj):
        return getattr(obj.new_status, "code", None)

    def get_new_status_name(self, obj):
        return getattr(obj.new_status, "name", None)

    def get_submitted_by(self, obj):
        return getattr(obj.submitted_by, "username", None)


class NoteDetailSerializerBot(serializers.ModelSerializer):
    """
    Сериализатор для Note, возвращающий только id, title и description.
    """
    class Meta:
        model = Note
        fields = "__all__"  # Возвращаем только нужные поля


class CategoryDetailSerializerBot(serializers.ModelSerializer):
    """
    Сериализатор для Category, возвращающий только id, title и description.
    """
    class Meta:
        model = Category
        fields = "__all__"  # Возвращаем только нужные поля


class NoteSerializerBot(serializers.ModelSerializer):
    """
    Сериализатор для Note, возвращающий только id и title.
    """
    class Meta:
        model = Note
        fields = ['id', 'title', 'description']  # Возвращаем только поля id и title


# сериализаторы для веба
class PhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Photo
        fields = ['id', 'image', 'created_at']  # Мы возвращаем id, ссылку на изображение и дату создания
        
        
class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = ['id', 'video', 'created_at']  # Мы возвращаем id, ссылку на видео, превью и дату создания


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tags
        fields = ['id', 'name']
        
        
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'title', 'tag']  # Мы хотим получить только id и title


class NoteSerializer(serializers.ModelSerializer):
    photos = PhotoSerializer(many=True, read_only=True)  # Список всех фото
    videos = VideoSerializer(many=True, read_only=True)  # Список всех видео
    logo_url = serializers.SerializerMethodField()  # Это поле для возврата URL логотипа

    class Meta:
        model = Note
        fields = ['id', 'title', 'description', 'photos', 'videos', 'logo', 'logo_url']  # Добавляем logo_url для ссылки на файл

    def get_logo_url(self, obj):
        if obj.logo:
            return obj.logo.url  # Получаем URL для изображения
        return None


class NotesOfCategorySerializer(serializers.ModelSerializer):
    category = CategorySerializer()  # Включаем информацию о категории
    tags = serializers.SerializerMethodField()  # Включаем теги через кастомный метод
    description = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()  # Это поле для возврата URL логотипа

    class Meta:
        model = Note
        fields = ['id', 'title', 'logo', 'logo_url', 'description', 'asset', 'videoforwardid', 'main_tag', 'view_count', 'like_count', 'dislike_count', 'created_at', 'category', 'tags']

    def get_logo_url(self, obj):
        if obj.logo:
            return obj.logo.url  # Получаем URL для изображения
        return None

    def get_tags(self, obj):
        """
        Получаем все теги для записи Note.
        """
        # Извлекаем все теги для записи, используя модель NoteTags
        note_tags = obj.notetags_set.all()  # Используем обратную связь для связи с тегами
        tag_serializer = TagSerializer([note_tag.id_tag for note_tag in note_tags], many=True)  # Сериализуем все теги
        return tag_serializer.data

    def get_description(self, obj):
        """
        Этот метод удаляет символы '*' и все их вхождения.
        """
        # Убираем все символы '*'
        clean_description = re.sub(r'\*+', '', obj.description)  # Убираем один или несколько символов '*'

        return clean_description


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category  # Замени на правильную модель категории, если она отличается
        fields = ['id', 'title', 'site_description', 'icon', 'tag']


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthUser
        fields = ['id', 'first_name', 'last_name', 'name']  # Вернем id и name пользователя
        
        
class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tags
        fields = ['id', 'name']  # Вернем id и name для тега


class PopularNotesSerializer(serializers.ModelSerializer):
    author = AuthorSerializer()  # Включаем информацию о пользователе
    tags = serializers.SerializerMethodField()  # Включаем теги через кастомный метод
    description = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = ['id', 'title', 'description', 'asset', 'videoforwardid', 'main_tag', 'view_count', 'like_count', 'dislike_count', 'created_at', 'category', 'author', 'tags']

    def get_tags(self, obj):
        # Извлекаем теги для записи через модель NoteTags
        tags = obj.notetags_set.all()  # Используем обратную связь для связи с тегами (NoteTags)
        
        # Получаем только связанные объекты Tag (из таблицы Tags), а не сами объекты NoteTags
        tag_serializer = TagSerializer([note_tag.id_tag for note_tag in tags], many=True)  # Сериализуем теги
        return tag_serializer.data
    
    def get_description(self, obj):
        """
        Этот метод удаляет символы '*' и все их вхождения.
        """
        # Убираем все символы '*'
        clean_description = re.sub(r'\*+', '', obj.description)  # Убираем один или несколько символов '*'

        return clean_description


class NewEntrySerializer(serializers.ModelSerializer):
    author = AuthorSerializer()  # Включаем информацию о пользователе
    tags = serializers.SerializerMethodField()  # Включаем теги через кастомный метод
    description = serializers.SerializerMethodField()  # Добавляем метод для чистого текста

    class Meta:
        model = Note
        fields = ['id', 'title', 'description', 'asset', 'videoforwardid', 'main_tag', 'view_count', 'like_count', 'dislike_count', 'created_at', 'category', 'author', 'tags']

    def get_tags(self, obj):
        # Извлекаем теги для записи через модель NoteTags
        tags = obj.notetags_set.all()  # Используем обратную связь для связи с тегами (NoteTags)
        
        # Получаем только связанные объекты Tag (из таблицы Tags), а не сами объекты NoteTags
        tag_serializer = TagSerializer([note_tag.id_tag for note_tag in tags], many=True)  # Сериализуем теги
        return tag_serializer.data
    
    def get_description(self, obj):
        """
        Этот метод удаляет символы '*' и все их вхождения.
        """
        # Убираем все символы '*'
        clean_description = re.sub(r'\*+', '', obj.description)  # Убираем один или несколько символов '*'

        return clean_description
    

class AuthorRankSerializer(serializers.ModelSerializer):
    """
    Сериализатор для данных о пользователе, его ранге и очках.
    """
    rank_name = serializers.CharField(source='user_rank.rank.name')  # Название ранга через связь с UserRank
    points = serializers.IntegerField(source='user_rank.points')  # Количество очков через связь с UserRank
    
    # Добавляем данные о пользователе
    id = serializers.IntegerField()  # Убираем source, так как 'id' уже существует в AuthUser
    first_name = serializers.CharField()  # Убираем source, так как 'first_name' уже существует
    last_name = serializers.CharField()  # Убираем source, так как 'last_name' уже существует
    name = serializers.CharField()  # Имя пользователя (можно использовать username)

    class Meta:
        model = AuthUser  # Используем AuthUser для получения информации о пользователе
        fields = ['id', 'rank_name', 'points', 'first_name', 'last_name', 'name']


class NoteDetailSerializer(serializers.ModelSerializer):
    category = CategorySerializer()  # Сериализуем категорию
    photos = PhotoSerializer(many=True, read_only=True)  # Все фото для записи
    videos = VideoSerializer(many=True, read_only=True)  # Все видео для записи
    tags = serializers.SerializerMethodField()  # Все теги для записи
    author = AuthorRankSerializer()  # Включаем данные о пользователе и его ранге через UserRank
    description = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = ['id', 'title', 'description', "logo", 'main_tag', 'view_count', 'like_count', 'dislike_count', 'created_at', 'category', 'tags', 'photos', 'videos', 'author']

    def get_tags(self, obj):
        # Извлекаем теги для записи через модель NoteTags
        tags = obj.notetags_set.all()  # Используем обратную связь для связи с тегами (NoteTags)
        
        # Получаем только связанные объекты Tag (из таблицы Tags), а не сами объекты NoteTags
        tag_serializer = TagSerializer([note_tag.id_tag for note_tag in tags], many=True)  # Сериализуем теги
        return tag_serializer.data

    def get_description(self, obj):
        """
        Этот метод удаляет символы '*' и все их вхождения.
        """
        # Убираем все символы '*'
        clean_description = re.sub(r'\*+', '', obj.description)  # Убираем один или несколько символов '*' 

        return clean_description
    

class AuthUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthUser
        fields = ['id', 'first_name', 'last_name', 'email']  # Используем все поля модели


class CustomLoginSerializer(serializers.Serializer):
    """
    Сериализатор для аутентификации пользователя
    """
    
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        username = data.get('username')
        password = data.get('password')

        if username and password:
            try:
                user = AuthUser.objects.get(username=username)
                
                # Проверяем пароль
                if check_password(password, user.password):
                    return self._generate_tokens(username)
                else:
                    raise serializers.ValidationError('Неверный логин или пароль')

            except AuthUser.DoesNotExist:
                raise serializers.ValidationError('Неверный логин или пароль')

        else:
            raise serializers.ValidationError('Укажите логин и пароль')

    def _generate_tokens(self, username):
        """
        Генерируем и возвращаем токены для пользователя.
        """
        # Получаем пользователя
        user = AuthUser.objects.get(username=username)

        # Генерируем токены
        refresh = RefreshToken.for_user(user)

        return {
            'access': str(refresh.access_token),  # Токен доступа
            'refresh': str(refresh),  # Токен обновления,
            'user_id': user.id
        }