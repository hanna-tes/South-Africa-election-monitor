from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0014_processedpost_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostAIDetection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("category", models.CharField(max_length=100)),
                ("confidence", models.FloatField(default=0.0)),
                ("severity", models.CharField(default="medium", max_length=20)),
                (
                    "model_name",
                    models.CharField(default="AFRO-XLMR", max_length=100),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_detections",
                        to="dashboard.processedpost",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["post", "model_name"],
                        name="dashboard_p_post_id_75f367_idx",
                    )
                ],
                "unique_together": {("post", "model_name")},
            },
        ),
    ]
