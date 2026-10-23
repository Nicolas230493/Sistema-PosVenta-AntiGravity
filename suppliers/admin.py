from django.contrib import admin

from .models import Supplier, SupplierAccountMovement


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'balance')
    search_fields = ('name',)


@admin.register(SupplierAccountMovement)
class SupplierAccountMovementAdmin(admin.ModelAdmin):
    list_display = ('supplier', 'movement_type', 'amount', 'date', 'due_date', 'payment_source', 'balance_after')
    list_filter = ('movement_type', 'payment_source')
    search_fields = ('supplier__name', 'reference')
