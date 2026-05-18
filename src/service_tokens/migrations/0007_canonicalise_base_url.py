"""One-off backfill: canonicalise existing ServiceToken.base_url values.

Going forward `ServiceToken.save()` runs the same canonicalisation, so this
migration only needs to rewrite the rows that already exist when the
deployment moves past this point.
"""
from django.db import migrations

from service_tokens.url import canonical_base_url


def forwards(apps, schema_editor):
    ServiceToken = apps.get_model('service_tokens', 'ServiceToken')
    for token in ServiceToken.objects.all():
        canonical = canonical_base_url(token.base_url)
        if canonical != token.base_url:
            token.base_url = canonical
            token.save(update_fields=['base_url'])


def backwards(apps, schema_editor):
    # No-op: we don't preserve the original (non-canonical) form.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('service_tokens', '0006_add_validation_fields'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
