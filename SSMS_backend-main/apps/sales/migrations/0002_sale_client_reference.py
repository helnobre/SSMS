from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="client_reference",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="sale",
            constraint=models.UniqueConstraint(
                condition=~models.Q(client_reference=""),
                fields=("tenant", "client_reference"),
                name="unique_sale_client_reference_per_tenant",
            ),
        ),
    ]
