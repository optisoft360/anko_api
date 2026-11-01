import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Knowledge gaps detected per topic from finished Simulate attempts."""

    dependencies = [
        ("api", "0063_user_username_case_insensitive_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgegap",
            name="tag_group",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="knowledge_gaps", to="api.taggroup"),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="battery",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="knowledge_gaps", to="api.battery"),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="learning_assignment",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="knowledge_gaps", to="api.learningpathassignment"),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="source",
            field=models.CharField(choices=[("detected", "Detected from Simulate attempts"), ("retention", "Detected from retention"), ("seed", "Demo data")], db_index=True, default="retention", max_length=20),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="correct_percent",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="questions_total",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="knowledgegap",
            name="questions_missed",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
