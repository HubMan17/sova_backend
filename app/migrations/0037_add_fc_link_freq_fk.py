from django.db import migrations, models
import django.db.models.deletion

PRESET_FC = ["барсук", "cuav v5 plus"]
PRESET_LINK = ["MESH", "китайская связь"]
PRESET_FREQ = ["1400 (китай)", "1300-1500", "1600-1800", "1800-2000", "2000-2200", "1350-1450"]

def seed_and_migrate(apps, schema_editor):
    Board = apps.get_model("app", "Board")
    FlightController = apps.get_model("app", "FlightController")
    LinkType = apps.get_model("app", "LinkType")
    FrequencyBand = apps.get_model("app", "FrequencyBand")

    for n in PRESET_FC:    FlightController.objects.get_or_create(name=n)
    for n in PRESET_LINK:  LinkType.objects.get_or_create(name=n)
    for n in PRESET_FREQ:  FrequencyBand.objects.get_or_create(name=n)

    conn = schema_editor.connection
    with conn.cursor() as cur:
        for b in Board.objects.all().only("id", "flight_controller", "link_type", "freq"):
            txt = (b.flight_controller or "").strip()
            if txt:
                obj = FlightController.objects.filter(name__iexact=txt).first() or FlightController.objects.create(name=txt)
                cur.execute("UPDATE boards SET flight_controller_fk_id=%s WHERE id=%s", [obj.id, b.id])

            txt = (b.link_type or "").strip()
            if txt:
                obj = LinkType.objects.filter(name__iexact=txt).first() or LinkType.objects.create(name=txt)
                cur.execute("UPDATE boards SET link_type_fk_id=%s WHERE id=%s", [obj.id, b.id])

            txt = (b.freq or "").strip()
            if txt:
                obj = FrequencyBand.objects.filter(name__iexact=txt).first() or FrequencyBand.objects.create(name=txt)
                cur.execute("UPDATE boards SET freq_fk_id=%s WHERE id=%s", [obj.id, b.id])

class Migration(migrations.Migration):
    # dependencies Django подставил сам — НЕ трогаем
    dependencies = [
        ('app', '0036_board_current_section'),  # ← это твоя предыдущая миграция
    ]
    operations = [
        migrations.CreateModel(
            name="FlightController",
            fields=[("id", models.AutoField(primary_key=True, serialize=False)),
                    ("name", models.CharField(max_length=100, unique=True, db_index=True))],
            options={"db_table": "flight_controllers", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="LinkType",
            fields=[("id", models.AutoField(primary_key=True, serialize=False)),
                    ("name", models.CharField(max_length=100, unique=True, db_index=True))],
            options={"db_table": "link_types", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="FrequencyBand",
            fields=[("id", models.AutoField(primary_key=True, serialize=False)),
                    ("name", models.CharField(max_length=100, unique=True, db_index=True))],
            options={"db_table": "frequency_bands", "ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="board",
            name="flight_controller_fk",
            field=models.ForeignKey(null=True, blank=True, to="app.FlightController",
                                    on_delete=django.db.models.deletion.SET_NULL, related_name="+"),
        ),
        migrations.AddField(
            model_name="board",
            name="link_type_fk",
            field=models.ForeignKey(null=True, blank=True, to="app.LinkType",
                                    on_delete=django.db.models.deletion.SET_NULL, related_name="+"),
        ),
        migrations.AddField(
            model_name="board",
            name="freq_fk",
            field=models.ForeignKey(null=True, blank=True, to="app.FrequencyBand",
                                    on_delete=django.db.models.deletion.SET_NULL, related_name="+"),
        ),
        migrations.RunPython(seed_and_migrate, reverse_code=migrations.RunPython.noop),
    ]
