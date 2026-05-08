from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wiki', '0021_multi_task_branches'),
    ]

    operations = [
        migrations.AddField(
            model_name='space',
            name='bot_usernames',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Bot username prefixes to exclude from reviewer filters (e.g., ["coderabbitai", "dependabot"])',
            ),
        ),
    ]
