"""
Service token models for storing encrypted credentials for various services.
"""
import uuid
import logging
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from cryptography.fernet import Fernet
import base64

from .url import canonical_base_url

logger = logging.getLogger(__name__)


class ServiceType(models.TextChoices):
    """Service types for tokens."""
    # Git providers
    GITHUB = 'github', 'GitHub'
    BITBUCKET_SERVER = 'bitbucket_server', 'Bitbucket Server'
    # Other services
    JIRA = 'jira', 'JIRA'
    CUSTOM_HEADER = 'custom_header', 'Custom Header Token'


class ServiceToken(models.Model):
    """
    Encrypted service credentials.
    
    Stores access tokens and credentials for various services with encryption.
    Supports Git providers (GitHub, Bitbucket), JIRA, and custom header tokens.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='service_tokens')
    service_type = models.CharField(
        max_length=50,
        choices=ServiceType.choices,
        help_text='Service type (Git providers, JIRA, Custom Header, etc.)'
    )
    base_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Base URL for the service (e.g., https://jira.acme.com) - not required for custom header tokens'
    )
    encrypted_token = models.TextField(
        help_text='Encrypted access token'
    )
    encrypted_username = models.TextField(
        null=True,
        blank=True,
        help_text='Encrypted username/email (for JIRA and Git providers)'
    )
    # Custom header token fields
    header_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Custom header name for token (e.g., X-Auth-Token, X-API-Key, Authorization)'
    )
    name = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Descriptive name for this token'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_validated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the last token validation check'
    )
    last_validation_valid = models.BooleanField(
        null=True,
        blank=True,
        help_text='Result of the last validation check (True=valid, False=invalid, None=never checked)'
    )
    last_validation_message = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Human-readable message from the last validation check'
    )
    
    class Meta:
        # For custom_header tokens, we need header_name in the unique constraint
        # to allow multiple custom headers per user
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'service_type', 'base_url', 'header_name'],
                condition=models.Q(service_type='custom_header'),
                name='unique_custom_header_per_user'
            ),
            models.UniqueConstraint(
                fields=['user', 'service_type', 'base_url'],
                condition=~models.Q(service_type='custom_header'),
                name='unique_service_token_per_user'
            ),
        ]
        verbose_name = 'Service Token'
        verbose_name_plural = 'Service Tokens'
        ordering = ['-created_at']
    
    def __str__(self):
        if self.service_type == ServiceType.CUSTOM_HEADER:
            return f"{self.user.username} - {self.service_type} - {self.name or 'Unnamed'}"
        return f"{self.user.username} - {self.service_type} - {self.base_url}"
    
    def save(self, *args, **kwargs):
        # Canonicalise base_url so lookups against Space.git_base_url and
        # other ServiceToken rows compare equal regardless of trailing
        # slash or scheme/host casing.
        self.base_url = canonical_base_url(self.base_url)
        super().save(*args, **kwargs)

    @staticmethod
    def _get_cipher():
        """Get Fernet cipher for encryption/decryption."""
        key = settings.ENCRYPTION_KEY.encode()
        # Ensure key is properly formatted for Fernet (32 bytes base64-encoded)
        # Pad or truncate to 32 bytes, then base64 encode
        key = base64.urlsafe_b64encode(key.ljust(32)[:32])
        return Fernet(key)
    
    def set_token(self, token):
        """Encrypt and store access token."""
        cipher = self._get_cipher()
        self.encrypted_token = cipher.encrypt(token.encode()).decode()
    
    def get_token(self):
        """Decrypt and return access token."""
        cipher = self._get_cipher()
        return cipher.decrypt(self.encrypted_token.encode()).decode()
    
    def set_username(self, username):
        """Encrypt and store username."""
        if username:
            cipher = self._get_cipher()
            self.encrypted_username = cipher.encrypt(username.encode()).decode()
    
    def get_username(self):
        """Decrypt and return username."""
        if self.encrypted_username:
            cipher = self._get_cipher()
            return cipher.decrypt(self.encrypted_username.encode()).decode()
        return None
    
    @classmethod
    def get_default_zta_header(cls):
        """Get default ZTA header name."""
        return 'X-Zero-Trust-Token'


# Signal handlers for tracking ServiceToken operations
@receiver(post_save, sender=ServiceToken)
def log_service_token_save(sender, instance, created, **kwargs):
    """Log when a service token is created or updated."""
    action = "CREATED" if created else "UPDATED"
    
    if instance.service_type == ServiceType.CUSTOM_HEADER:
        logger.debug(
            f"[SERVICE_TOKEN] {action} CUSTOM_HEADER token: "
            f"id={instance.id}, user={instance.user.username}, "
            f"name={instance.name}, header_name={instance.header_name}, "
            f"base_url={instance.base_url}"
        )
    else:
        logger.debug(
            f"[SERVICE_TOKEN] {action}: "
            f"id={instance.id}, user={instance.user.username}, "
            f"type={instance.service_type}, base_url={instance.base_url}"
        )


@receiver(pre_delete, sender=ServiceToken)
def log_service_token_delete(sender, instance, **kwargs):
    """Log when a service token is about to be deleted."""
    if instance.service_type == ServiceType.CUSTOM_HEADER:
        logger.debug(
            f"[SERVICE_TOKEN] DELETING CUSTOM_HEADER token: "
            f"id={instance.id}, user={instance.user.username}, "
            f"name={instance.name}, header_name={instance.header_name}, "
            f"base_url={instance.base_url}"
        )
    else:
        logger.debug(
            f"[SERVICE_TOKEN] DELETING: "
            f"id={instance.id}, user={instance.user.username}, "
            f"type={instance.service_type}, base_url={instance.base_url}"
        )
