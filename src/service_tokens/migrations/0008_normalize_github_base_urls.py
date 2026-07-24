"""Normalize persisted GitHub ServiceToken base URLs to canonical API origins.

Historically GitHub tokens could be stored against either the web origin
(`https://github.com`, `https://ghe.example.com`) or the API origin
(`https://api.github.com`, `https://ghe.example.com/api/v3`). Current lookup
logic treats those as the same logical host, so keeping both rows creates an
ambiguous token configuration.

This migration rewrites GitHub token rows to the canonical API URL and removes
semantic duplicates deterministically so the unique constraint remains valid.
"""
import base64

from cryptography.fernet import Fernet
from django.db import migrations
from django.conf import settings

from service_tokens.url import normalize_service_base_url


def _get_cipher():
    key = settings.ENCRYPTION_KEY.encode()
    key = base64.urlsafe_b64encode(key.ljust(32)[:32])
    return Fernet(key)


def _decrypt(value):
    if not value:
        return None
    return _get_cipher().decrypt(value.encode()).decode()


def _token_material(token):
    return {
        'token': _decrypt(token.encrypted_token),
        'username': _decrypt(token.encrypted_username),
        'last_validation_valid': token.last_validation_valid,
        'last_validation_message': token.last_validation_message or None,
    }


def _material_conflicts(tokens):
    materials = [_token_material(token) for token in tokens]
    token_values = {material['token'] for material in materials if material['token']}
    if len(token_values) > 1:
        return 'different access tokens'

    username_values = {material['username'] for material in materials if material['username']}
    if len(username_values) > 1:
        return 'different usernames'

    return None


def _pick_survivor(tokens, normalized_base_url):
    """Prefer a validated, populated row before canonical/oldest tie-breakers."""
    def _timestamp_or_zero(value):
        return value.timestamp() if value else 0

    return sorted(
        tokens,
        key=lambda token: (
            token.last_validation_valid is not True,
            not bool(token.encrypted_token),
            not bool(token.encrypted_username),
            token.base_url != normalized_base_url,
            token.last_validated_at is None,
            -_timestamp_or_zero(token.last_validated_at),
            _timestamp_or_zero(token.created_at),
            str(token.pk),
        ),
    )[0]


def _merge_survivor_fields(survivor, tokens, normalized_base_url):
    def _timestamp_or_zero(value):
        return value.timestamp() if value else 0

    materials = {token.pk: _token_material(token) for token in tokens}
    token_plaintext = next(
        (materials[token.pk]['token'] for token in tokens if materials[token.pk]['token']),
        None,
    )
    username_plaintext = next(
        (materials[token.pk]['username'] for token in tokens if materials[token.pk]['username']),
        None,
    )
    valid_token = next(
        (token for token in tokens if token.last_validation_valid is True),
        None,
    )
    latest_validation = next(
        (
            token
            for token in sorted(
                tokens,
                key=lambda token: _timestamp_or_zero(token.last_validated_at),
                reverse=True,
            )
            if token.last_validated_at
        ),
        None,
    )

    changed = False
    if survivor.base_url != normalized_base_url:
        survivor.base_url = normalized_base_url
        changed = True
    if token_plaintext and materials[survivor.pk]['token'] != token_plaintext:
        survivor.encrypted_token = _get_cipher().encrypt(token_plaintext.encode()).decode()
        changed = True
    if username_plaintext and materials[survivor.pk]['username'] != username_plaintext:
        survivor.encrypted_username = _get_cipher().encrypt(username_plaintext.encode()).decode()
        changed = True
    if valid_token and survivor.last_validation_valid is not True:
        survivor.last_validation_valid = True
        survivor.last_validation_message = valid_token.last_validation_message
        survivor.last_validated_at = valid_token.last_validated_at
        changed = True
    elif latest_validation and not survivor.last_validated_at:
        survivor.last_validation_valid = latest_validation.last_validation_valid
        survivor.last_validation_message = latest_validation.last_validation_message
        survivor.last_validated_at = latest_validation.last_validated_at
        changed = True

    if changed:
        survivor.save(
            update_fields=[
                'base_url',
                'encrypted_token',
                'encrypted_username',
                'last_validation_valid',
                'last_validation_message',
                'last_validated_at',
            ]
        )


def forwards(apps, schema_editor):
    ServiceToken = apps.get_model('service_tokens', 'ServiceToken')
    github_tokens = list(
        ServiceToken.objects.filter(service_type='github').order_by('created_at', 'pk')
    )

    grouped_tokens = {}
    for token in github_tokens:
        normalized_base_url = normalize_service_base_url('github', token.base_url)
        grouped_tokens.setdefault((token.user_id, normalized_base_url), []).append(token)

    for (_, normalized_base_url), tokens in grouped_tokens.items():
        conflict = _material_conflicts(tokens)
        if conflict:
            token_ids = ', '.join(str(token.pk) for token in tokens)
            raise RuntimeError(
                'Manual ServiceToken deduplication required for normalized GitHub base URL '
                f'{normalized_base_url}: {conflict} across token IDs [{token_ids}]'
            )

        survivor = _pick_survivor(tokens, normalized_base_url)
        duplicate_ids = [token.pk for token in tokens if token.pk != survivor.pk]

        if duplicate_ids:
            # Remove semantic duplicates before rewriting the survivor to the
            # canonical base_url so the unique constraint cannot trip on an
            # intermediate state.
            ServiceToken.objects.filter(pk__in=duplicate_ids).delete()

        _merge_survivor_fields(survivor, tokens, normalized_base_url)


def backwards(apps, schema_editor):
    # No-op: the previous non-canonical value is not preserved.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('service_tokens', '0007_canonicalise_base_url'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
