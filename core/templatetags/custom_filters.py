from django import template
from django.utils import timezone
from datetime import timedelta

register = template.Library()

@register.filter
def remaining_trial_days(date_joined):
    if not date_joined:
        return 0
    expiration_date = date_joined + timedelta(days=30)
    remaining = (expiration_date - timezone.now()).days
    return max(0, remaining)
