from django.db import migrations


def rename_mcq_option_columns(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SHOW COLUMNS FROM generated_mcqs')
        columns = {row[0] for row in cursor.fetchall()}

        renames = [
            ('option_a', 'optiona'),
            ('option_b', 'optionb'),
            ('option_c', 'optionc'),
            ('option_d', 'optiond'),
        ]

        for old_name, new_name in renames:
            if old_name in columns and new_name not in columns:
                cursor.execute(
                    f'ALTER TABLE generated_mcqs CHANGE COLUMN {old_name} {new_name} varchar(500) NOT NULL'
                )


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(rename_mcq_option_columns, migrations.RunPython.noop),
    ]
