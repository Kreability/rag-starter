from django.db import migrations

import api.models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0002_organization"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", api.models.UserManager())],
        ),
    ]
