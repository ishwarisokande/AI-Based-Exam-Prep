from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0002_alter_generatedmcq_option_columns'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='generatedmcq',
                    name='option_a',
                    field=models.CharField(db_column='optiona', max_length=500),
                ),
                migrations.AlterField(
                    model_name='generatedmcq',
                    name='option_b',
                    field=models.CharField(db_column='optionb', max_length=500),
                ),
                migrations.AlterField(
                    model_name='generatedmcq',
                    name='option_c',
                    field=models.CharField(db_column='optionc', max_length=500),
                ),
                migrations.AlterField(
                    model_name='generatedmcq',
                    name='option_d',
                    field=models.CharField(db_column='optiond', max_length=500),
                ),
            ],
        ),
    ]
