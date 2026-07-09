# Removes the separate "Burn After Writing" journal (BurnJournal /
# BurnJournalEntry) — that was a misread of the spec. The release ritual is
# now a per-page feature of the one, existing Journal: any JournalPage can be
# archived, locked, or released (burned/dissolved/scattered/etc.) on its own,
# leaving the rest of the journal untouched.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sanasource', '0010_burn_journal_restructure'),
    ]

    operations = [
        migrations.DeleteModel(name='BurnJournalEntry'),
        migrations.DeleteModel(name='BurnJournal'),
        migrations.AddField(
            model_name='journalpage',
            name='is_archived',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='journalpage',
            name='is_locked',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='journalpage',
            name='is_released',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='journalpage',
            name='released_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='journalpage',
            name='release_ritual',
            field=models.CharField(blank=True, choices=[
                ('fire', 'Fire'), ('water', 'Water'), ('wind', 'Wind'),
                ('petals', 'Petals'), ('stars', 'Stars'), ('birds', 'Birds'),
                ('balloons', 'Balloons'), ('tree', 'Tree'),
            ], max_length=20),
        ),
    ]
