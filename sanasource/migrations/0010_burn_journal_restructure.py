# Restructures the Burn After Writing feature from flat, per-entry
# expiration-mode records into a proper "notebook" model: a BurnJournal
# (one category, an explicit Keep/Archive/Release lifecycle) containing
# many BurnJournalEntry reflection pages. The old BurnJournalEntry table
# was empty (feature not yet released to users), so this is a clean
# replace rather than a data migration.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sanasource', '0009_burnjournalentry'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(
            name='BurnJournalEntry',
        ),
        migrations.CreateModel(
            name='BurnJournal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[
                    ('love', 'Love'), ('family', 'Family'), ('childhood', 'Childhood'),
                    ('friendships', 'Friendships'), ('anxiety', 'Anxiety'), ('trauma', 'Trauma'),
                    ('dreams', 'Dreams'), ('regrets', 'Regrets'), ('self_esteem', 'Self-esteem'),
                    ('success', 'Success'), ('failure', 'Failure'), ('personal_growth', 'Personal Growth'),
                    ('identity', 'Identity'), ('forgiveness', 'Forgiveness'), ('future', 'Future'),
                ], max_length=20)),
                ('status', models.CharField(choices=[('active', 'Active'), ('archived', 'Archived'), ('released', 'Released')], default='active', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('released_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='burn_journals', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Burn After Writing journal',
                'verbose_name_plural': 'Burn After Writing journals',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='BurnJournalEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.TextField()),
                ('content', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('journal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='sanasource.burnjournal')),
            ],
            options={
                'verbose_name': 'Burn After Writing page',
                'verbose_name_plural': 'Burn After Writing pages',
                'ordering': ['created_at'],
            },
        ),
    ]
