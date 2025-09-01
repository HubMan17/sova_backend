from datetime import timedelta
from django.utils import timezone
from django.db.models import F
from app.models import Note, UserReaction


def add_reaction(user, note, reaction_type):
    """
    Эта функция добавляет лайк или дизлайк к записи, обновляя поля `like_count` или `dislike_count`
    в модели Note, а также начисляет очки автору записи.
    """
    # Определяем количество очков, которые будут начисляться
    points_for_like = 10  # Очки за лайк
    points_for_dislike = -5  # Очки за дизлайк
    
    # Проверяем, если реакция была поставлена меньше 3 часов назад
    try:
        user_reaction = UserReaction.objects.get(user=user, note=note)
        
        # Если прошло менее 3 часов с последней реакции, запрещаем повторную
        if timezone.now() - user_reaction.created_at < timedelta(hours=3):
            return False, "Время между реакциями не должно превышать 3 часа."
        
        # Если прошло больше 3 часов, обновляем реакцию
        old_reaction_type = user_reaction.reaction_type  # Сохраняем старую реакцию
        user_reaction.reaction_type = reaction_type
        user_reaction.created_at = timezone.now()  # Обновляем время последней реакции
        user_reaction.save()

        # Обновляем счетчики лайков или дизлайков в Note
        if reaction_type == 'like':
            note.like_count = F('like_count') + 1
            if old_reaction_type == 'dislike':
                note.dislike_count = F('dislike_count') - 1  # Убираем один дизлайк, если ранее был поставлен дизлайк
        elif reaction_type == 'dislike':
            note.dislike_count = F('dislike_count') + 1
            if old_reaction_type == 'like':
                note.like_count = F('like_count') - 1  # Убираем один лайк, если ранее был поставлен лайк

        # Начисляем очки автору записи
        if reaction_type == 'like' and old_reaction_type != 'like':
            note.author.user_rank.points += points_for_like  # Начисляем очки за лайк
        elif reaction_type == 'dislike' and old_reaction_type != 'dislike':
            note.author.user_rank.points += points_for_dislike  # Снимаем очки за дизлайк
        
    except UserReaction.DoesNotExist:
        # Если реакции нет, создаем новую
        UserReaction.objects.create(user=user, note=note, reaction_type=reaction_type)
        
        # Обновляем счетчики лайков или дизлайков в Note
        if reaction_type == 'like':
            note.like_count = F('like_count') + 1
        elif reaction_type == 'dislike':
            note.dislike_count = F('dislike_count') + 1

        # Начисляем очки автору записи
        if reaction_type == 'like':
            note.author.user_rank.points += points_for_like  # Начисляем очки за лайк
        elif reaction_type == 'dislike':
            note.author.user_rank.points += points_for_dislike  # Снимаем очки за дизлайк
    
    # Сохраняем изменения в записи
    note.save()  # Сохраняем изменения в записи
    note.author.user_rank.save()  # Сохраняем изменения в очках пользователя

    return True, "Your reaction has been added."

