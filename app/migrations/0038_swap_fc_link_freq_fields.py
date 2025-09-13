from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('app', '0037_add_fc_link_freq_fk'),
    ]
    operations = [
        # если Django ещё «знает» про старые текстовые поля:
        migrations.RemoveField(model_name='board', name='flight_controller'),
        migrations.RemoveField(model_name='board', name='link_type'),
        migrations.RemoveField(model_name='board', name='freq'),

        migrations.RenameField(model_name='board', old_name='flight_controller_fk', new_name='flight_controller'),
        migrations.RenameField(model_name='board', old_name='link_type_fk', new_name='link_type'),
        migrations.RenameField(model_name='board', old_name='freq_fk', new_name='freq'),
    ]
