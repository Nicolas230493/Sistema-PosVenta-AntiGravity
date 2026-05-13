from django.contrib import admin
from .models import PaymentMethod, CashSession, CashExpense

@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_digital', 'active')
    list_filter = ('is_digital', 'active')
    search_fields = ('name',)

@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'start_date', 'end_date', 'initial_amount', 'expected_final_amount', 'real_final_amount', 'is_open')
    list_filter = ('is_open', 'start_date')

@admin.register(CashExpense)
class CashExpenseAdmin(admin.ModelAdmin):
    list_display = ('description', 'amount', 'session', 'date')
