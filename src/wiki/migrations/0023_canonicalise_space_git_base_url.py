"""One-off backfill: canonicalise existing Space.git_base_url values.

Going forward `Space.save()` runs the same canonicalisation, so this
migration only needs to rewrite the rows that already exist when the
deployment moves past this point.
"""
from django.db import migrations

from service_tokens.url import canonical_base_url


def forwards(apps, schema_editor):
    Space = apps.get_model('wiki', 'Space')
    for space in Space.objects.all():
        canonical = canonical_base_url(space.git_base_url)
        if canonical != space.git_base_url:
            space.git_base_url = canonical
            space.save(update_fields=['git_base_url'])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('wiki', '0022_space_bot_usernames'),
        ('service_tokens', '0007_canonicalise_base_url'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
