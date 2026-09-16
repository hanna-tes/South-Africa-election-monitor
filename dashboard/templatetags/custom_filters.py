# dashboard/templatetags/custom_filters.py
# dashboard/templatetags/custom_filters.py
from django import template

register = template.Library()

@register.filter
def split(value, arg):
    """Split a string by delimiter"""
    if not value:
        return []
    return [item.strip() for item in value.split(arg) if item.strip()]

@register.filter  
def cut(value, arg):
    """Remove a substring"""
    return value.replace(arg, '') if value else value

@register.filter
def strip(value):
    """Strip whitespace"""
    return value.strip() if value else value
