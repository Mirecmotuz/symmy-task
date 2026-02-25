from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ProductSyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sku', models.CharField(max_length=50, unique=True)),
                ('content_hash', models.CharField(max_length=64)),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
                ('synced_as_new', models.BooleanField(default=True)),
            ],
        ),
    ]
