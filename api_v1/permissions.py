from rest_framework.permissions import BasePermission

class IsSuperUser(BasePermission):
    """
    Разрешает доступ только суперпользователям (is_superuser=True).
    """

    def has_permission(self, request, view):
        # Проверяем, что пользователь аутентифицирован и является суперпользователем
        return request.user and request.user.is_superuser
