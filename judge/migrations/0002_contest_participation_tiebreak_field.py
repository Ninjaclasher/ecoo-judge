# Generated by Django 2.2.6 on 2019-11-06 18:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contestparticipation',
            name='tiebreaker',
            field=models.FloatField(default=0.0, verbose_name='tie-breaking field'),
        ),
    ]
