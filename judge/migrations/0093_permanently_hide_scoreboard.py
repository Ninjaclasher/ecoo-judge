# Generated by Django 2.1.12 on 2019-10-12 15:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0092_contest_clone'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='permanently_hide_scoreboard',
            field=models.BooleanField(default=False, help_text='Whether the scoreboard should remain hidden permanently. Requires "hide scoreboard" to be set as well to have any effect.', verbose_name='permanently hide scoreboard'),
        ),
    ]