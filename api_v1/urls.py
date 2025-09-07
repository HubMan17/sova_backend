
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from .views import AuthUserDetailView, CategoryDetailAPIViewBot
from .views import CustomLoginAPIView
from .views import NewEntryAPIView
from .views import PopularNotesAPIView
from .views import CategoriesAPIView
from .views import NotesByCategoryAPIView
from .views import NotesByCategoryStrAPIView
from .views import UploadPhotosAPIView, UploadVideosAPIView
from .views import NoteDetailAPIView
from .views import AddReactionAPIView
from .views import SearchNotesAPIView


from .views import CheckTGIDAPIView
from .views import FilterCategoriesByTagAPIView
from .views import NotesByCategoryTagAPIView
from .views import NoteDetailAPIViewBot
from .views import SearchNotesByTagAndQueryAPIView
from .views import NotesByCategoryIdAPIView
from .views import TelemetryFromJsonl
from .views_map import (
    board_session_map, board_session_data,
    list_boards, list_sessions,
    export_gpx, export_kml,
)
from .views import ArmReportIngestView
from .views import TelemetryFromJsonl

urlpatterns = [
    
    path("arm-report/", ArmReportIngestView.as_view(), name="arm-report"),
    
    path("track/board/<int:board_id>/session/<str:sess>/", board_session_map),
    path("track/data/board/<int:board_id>/session/<str:sess>/", board_session_data),

    path("track/boards/", list_boards),
    path("track/sessions/board/<int:board_id>/", list_sessions),

    path("track/export/gpx/board/<int:board_id>/session/<str:sess>/", export_gpx),
    path("track/export/kml/board/<int:board_id>/session/<str:sess>/", export_kml),
    
    # телеметрия с бортов
    path("telemetry/", TelemetryFromJsonl.as_view(), name="telemetry_ingest"),
    
    # бот пути
    
    # Новый маршрут для поиска записей по тегу 'preArmError' и строке в названии
    path('notes/search_by_tag_and_query/', SearchNotesByTagAndQueryAPIView.as_view(), name='search_notes_by_prearm_error_tag'),
    
    # Добавляем маршрут для получения записи по id
    path('current_note/<int:note_id>/', NoteDetailAPIViewBot.as_view(), name='note_detail'),
    
    # Добавляем маршрут для получения категории по id
    path('current_category/<int:category_id>/', CategoryDetailAPIViewBot.as_view(), name='category_detail'),
    
    # Добавляем маршрут для проверки tg_id
    path('check_tg_id/<str:tg_id>/', CheckTGIDAPIView.as_view(), name='check_tg_id'),
    
    # Добавляем маршрут для фильтрации категорий по тегу
    path('categories/filter_by_tag/', FilterCategoriesByTagAPIView.as_view(), name='filter_categories_by_tag'),
    
    # Добавляем маршрут для получения записей по тегу категории
    path('notes/by_category_tag/', NotesByCategoryTagAPIView.as_view(), name='notes_by_category_tag'),
    
    # Добавляем маршрут для получения записей по айди категории
    path('notes/by_category_id/', NotesByCategoryIdAPIView.as_view(), name='notes_by_category_tag'),
    
    # веб пути
    
    # auth part
    path('user/<int:user_id>/', AuthUserDetailView.as_view(), name='AuthUserView_v1'),
    path('login/', CustomLoginAPIView.as_view(), name='custom_login'),
    
    # note part
    path('new_entry/', NewEntryAPIView.as_view(), name='new_entry'),
    path('popular_notes/', PopularNotesAPIView.as_view(), name='popular_notes'),
    
    # category part
    path('categories/', CategoriesAPIView.as_view(), name='categories'),
    
    # Добавляем новый путь для получения записей по категории
    path('notes/category/<int:category_id>/', NotesByCategoryAPIView.as_view(), name='notes-by-category'),
    path('notes/categoryStr/<str:category_str>/', NotesByCategoryStrAPIView.as_view(), name='notes-by-category'),
    
    # Путь для получения полной информации о записи
    path('note/<int:note_id>/', NoteDetailAPIView.as_view(), name='note-detail'),
     
    # Путь для добавления реакции
    path('notes/<int:note_id>/add_reaction/', AddReactionAPIView.as_view(), name='add-reaction'),
    
    path('search/', SearchNotesAPIView.as_view(), name='search_notes'),  # Добавляем маршрут для поиска
    
    
    # load photo and video
    # Путь для загрузки фото
    path('notes/<int:note_id>/upload_photos/', UploadPhotosAPIView.as_view(), name='upload-photo'),
    
    # Путь для загрузки видео
    path('notes/<int:note_id>/upload_videos/', UploadVideosAPIView.as_view(), name='upload-video'),
]


# Это нужно для того, чтобы при разработке файлы были доступны через MEDIA_URL
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)