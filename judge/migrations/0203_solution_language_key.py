from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0202_auto_20260427_0439'),
    ]

    operations = [
        migrations.AddField(
            model_name='solution',
            name='solution_language_key',
            field=models.CharField(default='CPP17', max_length=10, verbose_name='solution language key'),
        ),
    ]

