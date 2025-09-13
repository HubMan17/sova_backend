from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('app', '0039_alter_board_flight_controller_alter_board_freq_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                # 1) добавить колонку, если её нет
                migrations.RunSQL(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name = 'boards' AND column_name = 'status_id'
                        ) THEN
                            ALTER TABLE boards ADD COLUMN status_id integer NULL;
                        END IF;
                    END$$;
                    """,
                    reverse_sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name = 'boards' AND column_name = 'status_id'
                        ) THEN
                            ALTER TABLE boards DROP COLUMN status_id;
                        END IF;
                    END$$;
                    """,
                ),
                # 2) создать индекс (если отсутствует)
                migrations.RunSQL(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_class c
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE c.relkind = 'i'
                              AND c.relname = 'boards_status_id_idx'
                        ) THEN
                            CREATE INDEX boards_status_id_idx ON boards (status_id);
                        END IF;
                    END$$;
                    """,
                    reverse_sql="DROP INDEX IF EXISTS boards_status_id_idx;",
                ),
                # 3) добавить FK к board_statuses(id), если отсутствует
                migrations.RunSQL(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM information_schema.table_constraints tc
                            WHERE tc.table_name = 'boards'
                              AND tc.constraint_type = 'FOREIGN KEY'
                              AND tc.constraint_name = 'boards_status_id_fk'
                        ) THEN
                            ALTER TABLE boards
                            ADD CONSTRAINT boards_status_id_fk
                            FOREIGN KEY (status_id)
                            REFERENCES board_statuses (id)
                            DEFERRABLE INITIALLY DEFERRED;
                        END IF;
                    END$$;
                    """,
                    reverse_sql="""
                    ALTER TABLE boards
                    DROP CONSTRAINT IF EXISTS boards_status_id_fk;
                    """,
                ),
            ],
        ),
    ]
