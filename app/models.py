from django.utils import timezone
from django.db import models
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager


# работа с бортами
from django.db import models

class Board(models.Model):
    boat_number = models.IntegerField(unique=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    flight_controller = models.CharField(max_length=100, blank=True, null=True)
    link_type = models.CharField(max_length=100, blank=True, null=True)
    freq = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # online-«сводка»
    is_online = models.BooleanField(default=False, db_index=True)
    online_since = models.DateTimeField(blank=True, null=True)
    last_telemetry_at = models.DateTimeField(blank=True, null=True)
    last_mode = models.CharField(max_length=64, blank=True, null=True)
    last_volt = models.FloatField(blank=True, null=True)

    class Meta:
        verbose_name = 'Board'
        verbose_name_plural = 'Boards'
        db_table = 'boards'

    def __str__(self):
        return f"Board #{self.boat_number}"


class Telemetry(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="telemetry")

    # время
    ts = models.DateTimeField(default=timezone.now)
    ts_epoch = models.BigIntegerField(blank=True, null=True)

    # потоковые идентификаторы
    sess = models.CharField(max_length=64, blank=True, null=True)
    seq = models.IntegerField(blank=True, null=True)

    # данные
    lat = models.FloatField(blank=True, null=True)
    lon = models.FloatField(blank=True, null=True)
    alt_m = models.FloatField(blank=True, null=True)
    gs = models.FloatField(blank=True, null=True)      # ground speed
    hdg = models.FloatField(blank=True, null=True)     # heading
    volt = models.FloatField(blank=True, null=True)
    mode = models.CharField(max_length=32, blank=True, null=True)
    wind_spd = models.FloatField(blank=True, null=True)
    wind_dir = models.FloatField(blank=True, null=True)
    gps = models.CharField(max_length=16, blank=True, null=True)
    arm = models.BooleanField(default=False)

    class Meta:
        db_table = "telemetry"   # <<< добавь это, если хочешь ровно public.telemetry
        indexes = [
            models.Index(fields=["board", "ts"]),
            models.Index(fields=["sess", "seq"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["board", "sess", "seq"],
                name="uniq_board_sess_seq",
                deferrable=models.Deferrable.DEFERRED,
            )
        ]

    def __str__(self):
        return f"TEL #{self.board.boat_number} @ {self.ts}"


class AuthGroup(models.Model):
    name = models.CharField(unique=True, max_length=150)

    class Meta:
        managed = False
        db_table = 'auth_group'


class AuthGroupPermissions(models.Model):
    id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)
    permission = models.ForeignKey('AuthPermission', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_group_permissions'
        unique_together = (('group', 'permission'),)


class AuthPermission(models.Model):
    name = models.CharField(max_length=255)
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING)
    codename = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'auth_permission'
        unique_together = (('content_type', 'codename'),)


class CustomAuthUserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        """
        Создает пользователя с хешированным паролем.
        """
        if not email:
            raise ValueError('The Email field must be set')
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)  # Хешируем пароль
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        """
        Создает суперпользователя.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, email, password, **extra_fields)


class AuthUser(AbstractBaseUser):
    
    username = models.CharField(max_length=150, unique=True)
    email = models.CharField(max_length=254)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    password = models.CharField(max_length=255)
    
    last_login = models.DateTimeField(blank=True, null=True)
    
    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    
    is_active = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    
    name = models.CharField(max_length=255, blank=True, null=False) # delete field
    tg_id = models.CharField(max_length=255, blank=True, null=False)    

    objects = CustomAuthUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']  # Указываем обязательные поля для создания суперпользователя


    user_rank = models.OneToOneField('UserRank', on_delete=models.SET_NULL, null=True, blank=True)  # Связь с моделью UserRank


    def set_password(self, password):
        """
        Метод для хеширования пароля.
        """
        self.password = make_password(password)

    def __str__(self):
        return self.username


class Category(models.Model):
    title = models.CharField(max_length=255, unique=False)
    description = models.TextField(null=True)

    tag = models.CharField(max_length=255, null=True)
    site_description = models.TextField(null=True)
    icon = models.CharField(max_length=255, null=True)
    
    visit_count = models.IntegerField(default=0)
    

    class Meta:
        db_table = 'category'


class Note(models.Model):
    logo = models.ImageField(upload_to='logos/', null=True, blank=True)  # Храним файл лого
    
    title = models.CharField(max_length=255)
    
    description = models.TextField()
    asset = models.CharField(max_length=255, null=True)
    videoforwardid = models.CharField(max_length=255, null=True)
    
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
    main_tag = models.CharField(max_length=255, null=True)
    
    view_count = models.IntegerField(default=0)
    like_count = models.IntegerField(default=0)
    dislike_count = models.IntegerField(default=0)
    
    author = models.ForeignKey(AuthUser, on_delete=models.SET_NULL, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'note'



class Rank(models.Model):
    name = models.CharField(max_length=255)
    required_points = models.IntegerField()

    def __str__(self):
        return self.name

class UserRank(models.Model):
    user = models.OneToOneField(AuthUser, on_delete=models.CASCADE)  # Связь с AuthUser
    points = models.IntegerField(default=0)  # Количество очков
    rank = models.ForeignKey(Rank, on_delete=models.SET_NULL, null=True)  # Ранг пользователя

    def __str__(self):
        return f'{self.user.username} - {self.rank.name if self.rank else "No Rank"}'

    def save(self, *args, **kwargs):
        """
        Переопределяем метод save для автоматического обновления ранга пользователя
        в зависимости от его очков.
        """
        rank = Rank.objects.filter(required_points__lte=self.points).order_by('-required_points').first()
        self.rank = rank
        super(UserRank, self).save(*args, **kwargs)


class UserReaction(models.Model):
    LIKE = 'like'
    DISLIKE = 'dislike'
    REACTION_CHOICES = [
        (LIKE, 'Like'),
        (DISLIKE, 'Dislike'),
    ]
    
    user = models.ForeignKey(AuthUser, on_delete=models.CASCADE)  # Ссылка на пользователя
    note = models.ForeignKey(Note, on_delete=models.CASCADE)  # Ссылка на запись
    reaction_type = models.CharField(max_length=10, choices=REACTION_CHOICES)  # Лайк или дизлайк
    created_at = models.DateTimeField(auto_now_add=True)  # Время, когда был поставлен лайк или дизлайк

    class Meta:
        unique_together = ('user', 'note')  # Один пользователь может поставить только один лайк или дизлайк на запись

    def __str__(self):
        return f'{self.user.username} reacted to {self.note.title} with {self.reaction_type}'
    

class Photo(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name='photos')  # Связываем с Note
    image = models.ImageField(upload_to='notes_photos/')  # Храним фото в папке notes_photos
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'photo'

class Video(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name='videos')  # Связываем с Note
    video = models.FileField(upload_to='notes_videos/')  # Храним видео в папке notes_videos
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'video'


class Tags(models.Model):
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'tags'
        
        
class NoteTags(models.Model):
    id_note = models.ForeignKey(Note, on_delete=models.SET_NULL, null=True, related_name='notetags_set')  # Обратная связь для получения тегов
    id_tag = models.ForeignKey(Tags, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'notetags'


class AuthUserGroups(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_groups'
        unique_together = (('user', 'group'),)


class AuthUserUserPermissions(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    permission = models.ForeignKey(AuthPermission, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_user_permissions'
        unique_together = (('user', 'permission'),)


class DjangoAdminLog(models.Model):
    action_time = models.DateTimeField()
    object_id = models.TextField(blank=True, null=True)
    object_repr = models.CharField(max_length=200)
    action_flag = models.SmallIntegerField()
    change_message = models.TextField()
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING, blank=True, null=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'django_admin_log'


class DjangoContentType(models.Model):
    app_label = models.CharField(max_length=100)
    model = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'django_content_type'
        unique_together = (('app_label', 'model'),)


class DjangoMigrations(models.Model):
    id = models.BigAutoField(primary_key=True)
    app = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    applied = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_migrations'


class DjangoSession(models.Model):
    session_key = models.CharField(primary_key=True, max_length=40)
    session_data = models.TextField()
    expire_date = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_session'



