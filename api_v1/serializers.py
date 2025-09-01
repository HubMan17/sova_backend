import re
from django.utils import dateparse, timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import check_password

from app.models import AuthUser, Note, Tags, Category, Photo, Video, UserRank, Telemetry, Board

from api_v1.urils.telemetry_utils import maybe_mark_power_on


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



# сериализаторы для бота        

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